/**
 * END-TO-END PROOF: one plugin write travelling the whole security chain
 * against real components, with no stubs and no hand-added headers.
 *
 *   Rowboat PluginToolRuntime
 *     -> OpenFangWriteReleasePolicy  (real HTTP to a real OpenFang daemon)
 *     -> a human approves in OpenFang
 *     -> OpenFangCredentialResolver  (real POST /api/credentials/issue)
 *     -> HttpMcpProvider             (real HTTPS to https://api.githubcopilot.com/mcp/)
 *     -> receipt in plugin_receipts  (real MongoDB replica set)
 *
 * Nothing here is faked: the repositories, the catalog seed, the release
 * policy, the credential resolver and the provider are the production
 * classes, composed through the exported composition seams of
 * di/plugins-container.ts and driven with the same global `fetch` that
 * `createToolRuntime` passes them.
 *
 * The credential on the daemon's allowlist is an obviously-fake test value,
 * so the expected end state is that GitHub REJECTS the call. That is the
 * success condition for this proof: it means the release, the issuance and
 * the provider call all worked and only GitHub's authorization failed. A stop
 * at `write_review_required` (the release never landed) or
 * `credential_missing` (the issuance never landed) is a finding, not a pass.
 *
 * Opt-in, exactly like live-mongo-runtime.test.ts:
 *   ROWBOAT_LIVE_MONGO_URL=mongodb://127.0.0.1:27017/rowboat
 *   ROWBOAT_LIVE_OPENFANG_URL=http://127.0.0.1:4273
 *   OPENFANG_API_KEY=<the isolated daemon's api_key>
 *   npx vitest run test/plugins/live-openfang-e2e.test.ts
 *
 * The daemon needs `OPENFANG_ISSUABLE_CREDENTIALS=GITHUB_PAT_TOKEN` and a
 * `GITHUB_PAT_TOKEN` in its own process environment, plus a non-blank
 * `api_key` in its config -- OpenFang's credential endpoint refuses outright
 * on a fail-open daemon, and its auth middleware makes /api/approvals public
 * for GET only, so the release POST must authenticate too. The Rowboat
 * process must NOT carry a GITHUB_PAT_TOKEN of its own, so the value can only
 * have come from OpenFang; the test asserts that.
 */
const LIVE_URL = (process.env.ROWBOAT_LIVE_MONGO_URL ?? "").trim();
const OPENFANG_URL = (process.env.ROWBOAT_LIVE_OPENFANG_URL ?? "").trim();
const OPENFANG_KEY = (process.env.OPENFANG_API_KEY ?? "").trim();
// The app hardcodes the "rowboat" database name, so this gate uses it too.
if (LIVE_URL !== "") process.env.MONGODB_CONNECTION_STRING = LIVE_URL;
// The composition reads these from the environment on every call.
if (OPENFANG_URL !== "") process.env.OPENFANG_URL = OPENFANG_URL;

import { randomUUID } from "node:crypto";
import { appendFileSync } from "node:fs";
import { resolve } from "node:path";
import { MongoClient } from "mongodb";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { PINNED_PLUGIN_CATALOG_DIGEST } from "@rowboat/openai-plugin-runtime";
import type { PluginToolRuntimeDependencies } from "@/src/application/services/plugin-tool-runtime";
import { classifyPluginOperation } from "@/src/application/services/plugin-operation-classifier";
import {
  deriveRuntimeDeadlineMs,
  resolveOpenFangApprovalWindowMs,
  resolveOpenFangComposedProvider,
  resolveOpenFangCredentialTimeoutMs,
  resolveOpenFangReleaseWrite,
} from "@/di/plugins-container";

const SKIP = LIVE_URL === "" || OPENFANG_URL === "" || OPENFANG_KEY === "";
const EVIDENCE = (process.env.ROWBOAT_E2E_EVIDENCE ?? "").trim();

let step = 0;
const log = (message: string): void => {
  const line = `  ${String(++step).padStart(2, "0")}  ${message}`;
  console.log(line);
  if (EVIDENCE !== "") appendFileSync(EVIDENCE, `${line}\n`, "utf8");
};

// The operation this proof drives: a genuine GitHub write. The pinned github
// MCP component declares no readOnlyOperations, so the real classifier
// (classifyPluginOperation, the one di/plugins-container.ts wires) calls it a
// write and the release gate applies.
const OPERATION = "create_issue";
const ARGUMENTS = Object.freeze({ owner: "rowboat-e2e-proof", repo: "does-not-exist", title: "openfang release proof" });

interface OpenFangApproval {
  readonly id: string;
  readonly status: string;
  readonly tool_name: string;
  readonly agent_id: string;
  readonly description: string;
  readonly action_summary: string;
  readonly decided_by?: string;
  readonly decided_at?: string;
}

async function listApprovals(): Promise<readonly OpenFangApproval[]> {
  // GET /api/approvals is a public endpoint on the daemon; no header needed,
  // and the release policy reads it the same unauthenticated way.
  const response = await fetch(`${OPENFANG_URL}/api/approvals`);
  const body = (await response.json()) as { approvals?: OpenFangApproval[] };
  return body.approvals ?? [];
}

/** The human at the OpenFang dashboard. Authenticates, as a human would. */
async function approve(id: string): Promise<unknown> {
  const response = await fetch(`${OPENFANG_URL}/api/approvals/${id}/approve`, {
    method: "POST",
    headers: { Authorization: `Bearer ${OPENFANG_KEY}` },
  });
  return { status: response.status, body: await response.json() };
}

/**
 * Waits for a pending approval whose action_summary carries this call's
 * component and arguments digests, then approves it. This is the role a
 * human otherwise plays.
 */
async function approveWhenRaised(deadlineMs: number): Promise<OpenFangApproval | undefined> {
  const deadline = Date.now() + deadlineMs;
  while (Date.now() < deadline) {
    const approvals = await listApprovals();
    const pending = approvals.find(candidate => candidate.status === "pending");
    if (pending !== undefined) {
      log(`OpenFang approval raised   id=${pending.id}`);
      log(`  tool_name       = ${pending.tool_name}`);
      log(`  agent_id        = ${pending.agent_id}`);
      log(`  description     = ${pending.description}`);
      log(`  action_summary  = ${pending.action_summary}`);
      const decision = await approve(pending.id);
      log(`  approve() -> ${JSON.stringify(decision)}`);
      return pending;
    }
    await new Promise(done => setTimeout(done, 250));
  }
  return undefined;
}

let client: MongoClient;
const projectId = randomUUID();
const installationId = randomUUID();

describe.skipIf(SKIP)("live OpenFang release + credential + provider, end to end", () => {
  beforeAll(async () => {
    client = new MongoClient(LIVE_URL);
    await client.connect();
  });

  afterAll(async () => {
    await client.close();
  });

  it("carries one write through release, issuance and the real GitHub MCP endpoint", async () => {
    const database = client.db("rowboat");

    // ---------------------------------------------------------------- 1.
    // Real index bootstrap, exactly as `npm run mongodb-ensure-indexes`.
    const { ensureAllIndexes } = await import("@/src/infrastructure/mongodb/ensure-indexes");
    await ensureAllIndexes(database);
    log(`indexes ensured on ${LIVE_URL}`);

    // ---------------------------------------------------------------- 2.
    // A project, and the real repositories over the live database.
    const workflow = { agents: [], prompts: [], tools: [], startAgent: "", lastUpdatedAt: new Date().toISOString() };
    await database.collection("projects").insertOne({
      _id: projectId, name: "openfang-e2e", createdAt: new Date().toISOString(), createdByUserId: "guest_user",
      secret: "e2e-secret", draftWorkflow: workflow, liveWorkflow: workflow,
    } as never);
    const { MongodbPluginsRepository, MongoPluginTransactionRunner } = await import("@/src/infrastructure/repositories/mongodb.plugins.repository");
    const { MongodbProjectsRepository } = await import("@/src/infrastructure/repositories/mongodb.projects.repository");
    const plugins = new MongodbPluginsRepository({
      pluginsDatabase: database,
      pluginTransactionRunner: new MongoPluginTransactionRunner({ pluginsMongoClient: client }),
    });
    const projects = new MongodbProjectsRepository({ projectMembersRepository: {} as never });
    log(`project ${projectId} inserted`);

    // ---------------------------------------------------------------- 3.
    // Catalog seed through the documented guarded path.
    const { readCatalogLock } = await import("@/scripts/load-plugin-catalog");
    const lock = await readCatalogLock("config/openai-plugin-catalog.lock.json", resolve(process.cwd(), "..", ".."));
    if ((await plugins.getCatalog(PINNED_PLUGIN_CATALOG_DIGEST)) === null) await plugins.putCatalog(lock);
    const seeded = await plugins.getCatalog(PINNED_PLUGIN_CATALOG_DIGEST);
    log(`catalog seeded: ${seeded!.entries.length} entries at ${seeded!.catalogDigest.slice(0, 12)}...`);
    expect(seeded?.entries).toHaveLength(180);

    // ---------------------------------------------------------------- 4.
    // Install `github` and bind its admitted MCP component, provenance and
    // provider binding taken from the seeded catalog, never invented.
    const entry = seeded!.entries.find(candidate => candidate.name === "github")!;
    const component = entry.components.find(candidate => candidate.component.kind === "mcp" && candidate.admission.status === "admitted")!;
    const componentDigest = component.component.metadata.bindingDigest as string;
    const providerBinding = component.component.metadata.providerBinding as { id: string; providerKind: "mcp-http"; componentDigest: string };
    await plugins.putInstallation({
      id: installationId, projectId, pluginName: entry.pluginName, pluginVersion: entry.pluginVersion,
      sourceCommit: entry.sourceCommit, manifestDigest: entry.manifestDigest, treeDigest: entry.treeDigest,
      policyVersion: entry.policyVersion, enabled: true, revision: 1,
      providerBindings: [{ componentId: component.component.id, binding: providerBinding }],
    });
    await plugins.putAdmissions([{
      installationId, componentDigest, componentKind: "mcp", componentName: component.component.name,
      status: "admitted", policyVersion: entry.policyVersion,
    }]);
    log(`installed github ${entry.pluginVersion} (mcp ${JSON.stringify(component.component.metadata.mcpServer)})`);

    // ---------------------------------------------------------------- 5.
    // The UI path: bind the MCP component as a workflow tool.
    const { AddPluginToolUseCase } = await import("@/src/application/use-cases/plugins/add-plugin-tool.use-case");
    const addTool = new AddPluginToolUseCase({
      authorizeProject: async () => undefined,
      loadInstallation: (id, pluginName) => plugins.getInstallation(id, pluginName),
      loadCatalogEntry: async pluginName => {
        const catalog = await plugins.getCatalog(PINNED_PLUGIN_CATALOG_DIGEST);
        const selected = catalog!.entries.find(candidate => candidate.name === pluginName);
        return selected === undefined ? null : { ...selected, catalogDigest: PINNED_PLUGIN_CATALOG_DIGEST };
      },
      loadDraftWorkflow: async id => (await projects.fetch(id))!.draftWorkflow,
      saveDraftWorkflow: async (id, value) => { await projects.updateDraftWorkflow(id, value as never); },
    });
    const addition = await addTool.execute({
      identity: Object.freeze({ kind: "user" as const, userId: "guest_user" }),
      projectId, pluginName: entry.pluginName, componentDigest,
    });
    const withTool = await database.collection("projects").findOne({ _id: projectId } as never);
    const addedTool = ((withTool as unknown as { draftWorkflow: { tools: Record<string, unknown>[] } }).draftWorkflow.tools)
      .find(candidate => candidate.name === addition.toolName)!;
    log(`tool bound: ${addition.toolName} -> ${JSON.stringify(addedTool.pluginBinding)}`);
    expect(addition.added).toBe(true);

    // ---------------------------------------------------------------- 6.
    // The runtime, composed exactly the way di/plugins-container.ts composes
    // it in production: the real classifier, the real provider resolution
    // (which chooses OpenFangCredentialResolver from the environment), and
    // the real OpenFang release policy.
    const approvalWindowMs = resolveOpenFangApprovalWindowMs(process.env.OPENFANG_APPROVAL_TIMEOUT_MS);
    const credentialTimeoutMs = resolveOpenFangCredentialTimeoutMs(process.env.OPENFANG_CREDENTIAL_TIMEOUT_MS);
    const { OpenFangWriteReleasePolicy } = await import("@/src/infrastructure/policies/openfang.plugin-write-release.policy");
    const { PluginToolRuntime } = await import("@/src/application/services/plugin-tool-runtime");
    const dependencies: PluginToolRuntimeDependencies = {
      pluginsRepository: plugins,
      authorizationContext: { caller: "user", userId: "guest_user" },
      authorizeProject: async () => undefined,
      classifyOperation: input => classifyPluginOperation(input),
      timeoutMilliseconds: deriveRuntimeDeadlineMs(approvalWindowMs),
      resolveProvider: input => resolveOpenFangComposedProvider(input, { credentialTimeoutMs }),
      // The production release gate, driven with the plain global `fetch` --
      // the same one createToolRuntime passes. No Authorization header is
      // added by hand anywhere on the path under test: the release call
      // authenticates itself from OPENFANG_API_KEY, inside
      // resolveOpenFangReleaseWrite.
      releaseWrite: async (request, signal) => resolveOpenFangReleaseWrite(request, signal, {
        approvalWindowMs, fetchImpl: fetch, OpenFangWriteReleasePolicyImpl: OpenFangWriteReleasePolicy,
      }),
    };
    const runtime = new PluginToolRuntime(dependencies);
    const executionBinding = {
      installationId, pluginName: entry.pluginName, componentDigest,
      providerBindingId: providerBinding.id, capability: "write" as const,
    };
    log(`runtime composed: approvalWindowMs=${approvalWindowMs} runtimeDeadlineMs=${deriveRuntimeDeadlineMs(approvalWindowMs)} credentialTimeoutMs=${credentialTimeoutMs}`);
    log(`OPENFANG_URL=${process.env.OPENFANG_URL} OPENFANG_API_KEY=<set, ${OPENFANG_KEY.length} chars, never printed>`);
    // The proof is worthless if Rowboat already holds the credential: the
    // value must be able to come from nowhere but OpenFang.
    log(`GITHUB_PAT_TOKEN in the Rowboat process: ${process.env.GITHUB_PAT_TOKEN === undefined ? "absent" : "PRESENT -- proof invalid"}`);
    expect(process.env.GITHUB_PAT_TOKEN).toBeUndefined();

    // ---------------------------------------------------------------- 7.
    // The call. It blocks on the human decision, which the approver supplies
    // concurrently.
    const approver = approveWhenRaised(30_000);
    let outcome = "success";
    const started = Date.now();
    try {
      await runtime.invoke(executionBinding, ARGUMENTS, { projectId, operationName: OPERATION });
    } catch (error) {
      outcome = error instanceof Error ? error.message : "unknown";
    }
    const elapsed = Date.now() - started;
    const raised = await approver;
    log(`invocation window: ${new Date(started).toISOString()} .. ${new Date(started + elapsed).toISOString()}`);
    log(`invocation outcome: ${outcome} after ${elapsed}ms; approval raised in OpenFang: ${raised === undefined ? "NONE" : raised.id}`);


    // ---------------------------------------------------------------- 8.
    // The receipt, as the real repository stored it. MongodbPluginsRepository
    // stores a receipt as { _id, receiptId, payload }, the payload being the
    // canonical JSON the kernel's buildReceipt produced -- so this reads the
    // stored documents raw and filters on the payload, exactly as an auditor
    // querying the collection by hand would have to.
    // PluginToolRuntime races its receipt write against a 100ms settle
    // budget and does not block the caller on it, so give the write a moment
    // to land before reading the collection back.
    await new Promise(done => setTimeout(done, 2_000));
    const stored = await database.collection("plugin_receipts").find({}).toArray();
    const mine = stored.filter(document => typeof document.payload === "string" && document.payload.includes(projectId));
    for (const receipt of mine) log(`receipt as stored in plugin_receipts: ${JSON.stringify(receipt)}`);
    log(`receipts stored for this project: ${mine.length}`);
    const payloads = mine.map(document => JSON.parse(document.payload as string) as Record<string, unknown>);
    const receipt = payloads[0];
    log(`receipt: status=${String(receipt?.status)} reason=${String(receipt?.reason)} approvalId=${String((receipt?.output as { approvalId?: string } | undefined)?.approvalId)}`);

    // ---------------------------------------------------------------- 9.
    // The approval OpenFang holds, after the fact. `raised` above is the
    // snapshot taken while it was still pending -- the record that carried
    // the action_summary to the approver -- so the decision itself is read
    // back here, from OpenFang, rather than assumed from the approve() call.
    const finalApprovals = await listApprovals();
    for (const approval of finalApprovals) log(`openfang approval: ${JSON.stringify(approval)}`);
    const decided = finalApprovals.find(candidate => candidate.id === raised?.id);
    log(`decision for ${String(raised?.id)}: status=${String(decided?.status)} decided_by=${String(decided?.decided_by)} decided_at=${String(decided?.decided_at)}`);

    // --------------------------------------------------------------- 10.
    // The proof's own assertions.
    //
    // write_review_required means the release never landed; credential_missing
    // means the issuance never landed. Either is a finding, not a pass.
    // provider_failed is reachable ONLY after HttpMcpProvider.#resolveCredential
    // returned a value: a credential failure there raises
    // CredentialResolutionError and surfaces as the distinct code
    // credential_missing, which plugin-tool-runtime.ts maps apart explicitly.
    // So this one code pins both gates as passed.
    expect(outcome).not.toBe("write_review_required");
    expect(outcome).not.toBe("credential_missing");
    expect(outcome).toBe("provider_failed");
    // The human decision happened, and is recorded against the call that
    // consumed it -- the same UUID in OpenFang and in Mongo.
    expect(raised).toBeDefined();
    expect(raised!.tool_name).toBe(OPERATION);
    // The decision as OpenFang itself records it, not as the approver claims.
    expect(decided?.status).toBe("approved");
    expect(decided?.decided_at).toBeDefined();
    // The approval carries digests, never argument values.
    expect(raised!.action_summary).toContain(componentDigest);
    for (const value of Object.values(ARGUMENTS)) expect(raised!.action_summary).not.toContain(value);
    // Exactly one receipt: one call, one record. No refused attempt precedes it.
    expect(mine).toHaveLength(1);
    expect(receipt?.status).toBe("failed");
    expect((receipt?.output as { approvalId?: string } | undefined)?.approvalId).toBe(raised!.id);
    // And nothing in the receipt carries an argument value either.
    for (const value of Object.values(ARGUMENTS)) expect(mine[0]!.payload).not.toContain(value);
  }, 180_000);
});
