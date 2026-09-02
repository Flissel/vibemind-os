/**
 * END-TO-END PROOF, PART III: a provider ACCEPTING a released call.
 *
 * Parts I and II proved the full mechanical chain up to the provider's own
 * refusal (GitHub 401 on a fake token, Cloudflare 401 with no token). This
 * test closes the last open claim: with a REAL credential in the isolated
 * OpenFang daemon's resolution chain, the same chain ends in a 200.
 *
 *   InstallPluginUseCase (componentDigests = [the github MCP component])
 *     -> AddPluginToolUseCase
 *     -> PluginToolRuntime
 *          -> OpenFangWriteReleasePolicy      (approval raised + approved)
 *          -> OpenFangCredentialResolver      (POST /api/credentials/issue
 *                                              for GITHUB_PAT_TOKEN -- a real
 *                                              value this time)
 *          -> HttpMcpProvider                 (real HTTPS to
 *                                              https://api.githubcopilot.com/mcp/)
 *     <- ProviderResult { status: "success" }
 *     -> receipt in plugin_receipts with status "success" and the approvalId
 *
 * The operation is `get_me` -- the authenticated-user lookup, chosen because
 * it has NO side effects on GitHub while still classifying as a write here
 * (the pinned github component declares no readOnlyOperations, so the real
 * classifier routes every operation through the release gate). The proof is
 * maximal on purpose: release, approval, issuance and provider acceptance in
 * one call.
 *
 * The credential value never appears in this test's process: the daemon
 * resolves GITHUB_PAT_TOKEN from its own environment and puts it on the wire
 * itself. This test holds only the daemon's api_key.
 *
 * Opt-in needs one MORE variable than Parts I/II, because a daemon holding a
 * real token must never be assumed:
 *   ROWBOAT_LIVE_MONGO_URL=mongodb://127.0.0.1:27017/rowboat
 *   ROWBOAT_LIVE_OPENFANG_URL=http://127.0.0.1:4273
 *   OPENFANG_API_KEY=<the isolated daemon's api_key>
 *   ROWBOAT_LIVE_ACCEPTANCE=1
 * and the daemon side needs OPENFANG_ISSUABLE_CREDENTIALS=GITHUB_PAT_TOKEN
 * plus a valid GITHUB_PAT_TOKEN in its process environment.
 */
const LIVE_URL = (process.env.ROWBOAT_LIVE_MONGO_URL ?? "").trim();
const OPENFANG_URL = (process.env.ROWBOAT_LIVE_OPENFANG_URL ?? "").trim();
const OPENFANG_KEY = (process.env.OPENFANG_API_KEY ?? "").trim();
const ACCEPTANCE = (process.env.ROWBOAT_LIVE_ACCEPTANCE ?? "").trim();
if (LIVE_URL !== "") process.env.MONGODB_CONNECTION_STRING = LIVE_URL;
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

const SKIP = LIVE_URL === "" || OPENFANG_URL === "" || OPENFANG_KEY === "" || ACCEPTANCE !== "1";
const EVIDENCE = (process.env.ROWBOAT_E2E_EVIDENCE ?? "").trim();

let step = 0;
const log = (message: string): void => {
  const line = `  ${String(++step).padStart(2, "0")}  ${message}`;
  console.log(line);
  if (EVIDENCE !== "") appendFileSync(EVIDENCE, `${line}\n`, "utf8");
};

// `get_me` takes no arguments, so the arguments digest in the approval's
// action_summary covers `{}` and there is trivially no argument value to leak.
const OPERATION = "get_me";
const ARGUMENTS = Object.freeze({});

interface OpenFangApproval {
  readonly id: string;
  readonly status: string;
  readonly tool_name: string;
  readonly action_summary: string;
  readonly decided_at?: string;
}

async function listApprovals(): Promise<readonly OpenFangApproval[]> {
  const response = await fetch(`${OPENFANG_URL}/api/approvals`);
  const body = (await response.json()) as { approvals?: OpenFangApproval[] };
  return body.approvals ?? [];
}

async function approve(id: string): Promise<unknown> {
  const response = await fetch(`${OPENFANG_URL}/api/approvals/${id}/approve`, {
    method: "POST",
    headers: { Authorization: `Bearer ${OPENFANG_KEY}` },
  });
  return { status: response.status, body: await response.json() };
}

async function approveWhenRaised(deadlineMs: number): Promise<OpenFangApproval | undefined> {
  const cutoff = Date.now() + deadlineMs;
  while (Date.now() < cutoff) {
    const pending = (await listApprovals()).find(candidate => candidate.status === "pending");
    if (pending !== undefined) {
      log(`OpenFang approval raised   id=${pending.id} tool_name=${pending.tool_name}`);
      log(`  action_summary  = ${pending.action_summary}`);
      log(`  approve() -> ${JSON.stringify(await approve(pending.id))}`);
      return pending;
    }
    await new Promise(done => setTimeout(done, 250));
  }
  return undefined;
}

let client: MongoClient;
const projectId = randomUUID();
const identity = Object.freeze({ kind: "user" as const, userId: "guest_user" });

describe.skipIf(SKIP)("live credential acceptance, end to end", () => {
  beforeAll(async () => {
    client = new MongoClient(LIVE_URL);
    await client.connect();
  });
  afterAll(async () => { await client?.close(); });

  it("carries a released github call through real issuance to a 200 from the provider", async () => {
    const database = client.db("rowboat");

    // ---------------------------------------------------------------- 1.
    const { ensureAllIndexes } = await import("@/src/infrastructure/mongodb/ensure-indexes");
    await ensureAllIndexes(database);
    const workflow = { agents: [], prompts: [], tools: [], startAgent: "", lastUpdatedAt: new Date().toISOString() };
    await database.collection("projects").insertOne({
      _id: projectId, name: "credential-acceptance-e2e", createdAt: new Date().toISOString(), createdByUserId: "guest_user",
      secret: "e2e-secret", draftWorkflow: workflow, liveWorkflow: workflow,
    } as never);
    const { MongodbPluginsRepository, MongoPluginTransactionRunner } = await import("@/src/infrastructure/repositories/mongodb.plugins.repository");
    const { MongodbProjectsRepository } = await import("@/src/infrastructure/repositories/mongodb.projects.repository");
    const plugins = new MongodbPluginsRepository({ pluginsDatabase: database, pluginTransactionRunner: new MongoPluginTransactionRunner({ pluginsMongoClient: client }) });
    const projects = new MongodbProjectsRepository({ projectMembersRepository: {} as never });
    log(`project ${projectId} inserted`);

    // ---------------------------------------------------------------- 2.
    const { readCatalogLock } = await import("@/scripts/load-plugin-catalog");
    const lock = await readCatalogLock("config/openai-plugin-catalog.lock.json", resolve(process.cwd(), "..", ".."));
    if ((await plugins.getCatalog(PINNED_PLUGIN_CATALOG_DIGEST)) === null) await plugins.putCatalog(lock);
    const seeded = await plugins.getCatalog(PINNED_PLUGIN_CATALOG_DIGEST);
    expect(seeded?.entries).toHaveLength(180);

    // ---------------------------------------------------------------- 3.
    // github: 8 components, 1 admitted -- the MCP server with the
    // GITHUB_PAT_TOKEN credential slot. Component-scoped install (W3).
    const entry = seeded!.entries.find(candidate => candidate.name === "github")!;
    const admitted = entry.components.filter(candidate => candidate.admission.status === "admitted");
    const component = admitted.find(candidate => candidate.component.kind === "mcp")!;
    const componentDigest = component.component.metadata.bindingDigest as string;
    log(`github: ${entry.components.length} components, ${admitted.length} admitted, selecting ${component.component.name} ${componentDigest.slice(0, 12)}...`);
    log(`  credentialSlots = ${JSON.stringify(component.component.metadata.credentialSlots)}`);
    expect(admitted).toHaveLength(1);

    const { InstallPluginUseCase } = await import("@/src/application/use-cases/plugins/install-plugin.use-case");
    const install = new InstallPluginUseCase({ pluginsRepository: plugins, pluginApiAuthorizationPolicy: { authenticate: async () => identity, authorizeProject: async () => undefined } });
    const receipt = await install.execute({
      identity, projectId, pluginName: "github", catalogDigest: PINNED_PLUGIN_CATALOG_DIGEST,
      expectedRevision: 0, idempotencyKey: randomUUID(), componentDigests: [componentDigest],
    });
    log(`install with [${componentDigest.slice(0, 12)}...] -> receipt ${receipt.receiptId} status=${receipt.status}`);
    const installation = (await plugins.getInstallation(projectId, "github"))!;
    const providerBindings = installation.providerBindings ?? [];
    expect(providerBindings).toHaveLength(1);

    // ---------------------------------------------------------------- 4.
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
    const addition = await addTool.execute({ identity, projectId, pluginName: "github", componentDigest });
    log(`tool bound: ${addition.toolName} added=${addition.added}`);
    expect(addition.added).toBe(true);

    // ---------------------------------------------------------------- 5.
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
      releaseWrite: async (request, signal) => resolveOpenFangReleaseWrite(request, signal, { approvalWindowMs, fetchImpl: fetch, OpenFangWriteReleasePolicyImpl: OpenFangWriteReleasePolicy }),
    };
    const runtime = new PluginToolRuntime(dependencies);
    const executionBinding = { installationId: installation.id, pluginName: "github", componentDigest, providerBindingId: providerBindings[0]!.binding.id, capability: "write" as const };
    log(`runtime composed against ${process.env.OPENFANG_URL}; operation=${OPERATION} (no readOnlyOperations declared -> classified write)`);

    // ---------------------------------------------------------------- 6.
    const approver = approveWhenRaised(30_000);
    let outcome = "success";
    let result: unknown;
    const started = Date.now();
    try {
      result = await runtime.invoke(executionBinding, ARGUMENTS, { projectId, operationName: OPERATION });
    } catch (error) {
      outcome = error instanceof Error ? error.message : "unknown";
    }
    const elapsed = Date.now() - started;
    const raised = await approver;
    log(`invocation outcome: ${outcome} after ${elapsed}ms; approval raised: ${raised === undefined ? "NONE" : raised.id}`);
    expect(raised).toBeDefined();
    // The argument digest in the summary covers `{}`; no argument VALUES exist
    // to leak, and the summary must still carry the component digest.
    expect(raised!.action_summary).toContain(componentDigest.slice(0, 16));

    // THE claim of this proof: the provider accepted the released call.
    expect(outcome).toBe("success");

    // The provider's answer is GitHub's own: the MCP tool result carries the
    // authenticated user. Log only the login, never the raw payload -- the
    // full profile is not evidence, the acceptance is.
    // The MCP tool result nests GitHub's JSON as text content, so by the time
    // it is stringified here its quotes are escaped -- match both layers.
    const text = JSON.stringify(result ?? "");
    const login = /\\?"login\\?"\s*:\s*\\?"([^"\\]+)/.exec(text)?.[1] ?? "(no login field)";
    log(`provider result: status=success, authenticated github login=${login}`);
    expect(login).not.toBe("(no login field)");

    // ---------------------------------------------------------------- 7.
    await new Promise(done => setTimeout(done, 2_000));
    const stored = await database.collection("plugin_receipts").find({}).toArray();
    const payloads = stored
      .filter(document => typeof document.payload === "string" && (document.payload as string).includes(projectId))
      .map(document => JSON.parse(document.payload as string) as Record<string, unknown>)
      .filter(payload => payload.type === "execution");
    expect(payloads).toHaveLength(1);
    const execution = payloads[0]!;
    const output = execution.output as { approvalId?: string; componentDigest?: string } | undefined;
    log(`execution receipt: status=${String(execution.status)} reason=${String(execution.reason)} approvalId=${String(output?.approvalId)}`);
    expect(execution.status).toBe("success");
    expect(output?.approvalId).toBe(raised!.id);
    expect(output?.componentDigest).toBe(componentDigest);

    // ---------------------------------------------------------------- 8.
    const decided = (await listApprovals()).find(candidate => candidate.id === raised!.id);
    log(`decision for ${raised!.id}: status=${decided?.status} decided_at=${decided?.decided_at}`);
    expect(decided?.status).toBe("approved");
  }, 120_000);
});
