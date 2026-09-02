/**
 * END-TO-END PROOF: a component-scoped installation travelling the real
 * install path and on to a real provider, with no hand-composed rows.
 *
 *   InstallPluginUseCase (componentDigests = [the one admitted MCP component])
 *     -> MongodbPluginsRepository.installIdempotently   (real replica set)
 *     -> AddPluginToolUseCase                           (the UI's add-tool path)
 *     -> PluginToolRuntime
 *          -> OpenFangWriteReleasePolicy               (real HTTP to a real daemon)
 *          -> a human approves in OpenFang
 *          -> HttpMcpProvider                          (real HTTPS to https://mcp.cloudflare.com/mcp)
 *     -> receipt in plugin_receipts
 *
 * Before this branch the install use case refused `cloudflare` outright:
 * thirteen of its fourteen components are `review_required`, and the gate was
 * whole-plugin. The proof here is that the selection travels: the use case
 * admits exactly the selected component, stores exactly one admission row and
 * one provider binding, refuses the same idempotency key with a different
 * selection, and still refuses the whole plugin when no selection is given.
 *
 * The pinned cloudflare MCP component declares `credentialSlots: []`, which is
 * NOT "no credential": the provider is OAuth-gated and answers 401 to an
 * unauthenticated `initialize` (proven directly in E2E-PROOF.md II.3, when
 * this test originally ended at Cloudflare's own 401). Since the
 * credential-name rules landed, a server that declares nothing is treated as
 * an OAuth resource at its own URL, so the expected end state is now a
 * fail-closed `credential_missing` before any network I/O: the daemon
 * allowlists no `OAUTH_BEARER_MCP_CLOUDFLARE_COM_MCP`. The install, the tool
 * binding and the release still run and are still asserted. A stop at
 * `write_review_required` means the release never landed.
 *
 * Opt-in, exactly like live-openfang-e2e.test.ts:
 *   ROWBOAT_LIVE_MONGO_URL=mongodb://127.0.0.1:27017/rowboat
 *   ROWBOAT_LIVE_OPENFANG_URL=http://127.0.0.1:4273
 *   OPENFANG_API_KEY=<the isolated daemon's api_key>
 *   npx vitest run test/plugins/live-component-install-e2e.test.ts
 *
 * The daemon needs no credential allowlist for this proof (there is no slot
 * to issue), only a non-blank `api_key` so the release POST can authenticate.
 */
const LIVE_URL = (process.env.ROWBOAT_LIVE_MONGO_URL ?? "").trim();
const OPENFANG_URL = (process.env.ROWBOAT_LIVE_OPENFANG_URL ?? "").trim();
const OPENFANG_KEY = (process.env.OPENFANG_API_KEY ?? "").trim();
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

const SKIP = LIVE_URL === "" || OPENFANG_URL === "" || OPENFANG_KEY === "";
const EVIDENCE = (process.env.ROWBOAT_E2E_EVIDENCE ?? "").trim();

let step = 0;
const log = (message: string): void => {
  const line = `  ${String(++step).padStart(2, "0")}  ${message}`;
  console.log(line);
  if (EVIDENCE !== "") appendFileSync(EVIDENCE, `${line}\n`, "utf8");
};

// The Cloudflare API MCP server exposes `search` and `execute`. The pinned
// component declares no readOnlyOperations, so the real classifier calls
// even `search` a write and the release gate applies.
const OPERATION = "search";
const ARGUMENTS = Object.freeze({ query: "rowboat-component-install-proof zones list" });

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
  const response = await fetch(`${OPENFANG_URL}/api/approvals/${id}/approve`, { method: "POST", headers: { Authorization: `Bearer ${OPENFANG_KEY}` } });
  return { status: response.status, body: await response.json() };
}

async function approveWhenRaised(deadlineMs: number): Promise<OpenFangApproval | undefined> {
  const deadline = Date.now() + deadlineMs;
  while (Date.now() < deadline) {
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

const failure = async (run: () => Promise<unknown>): Promise<string> => {
  try { await run(); return "no_error"; } catch (error) { return error instanceof Error ? error.message : "unknown"; }
};

let client: MongoClient;
const projectId = randomUUID();
const identity = Object.freeze({ kind: "user" as const, userId: "guest_user" });

describe.skipIf(SKIP)("live component-scoped install + release + provider, end to end", () => {
  beforeAll(async () => {
    client = new MongoClient(LIVE_URL);
    await client.connect();
  });
  afterAll(async () => { await client?.close(); });

  it("installs cloudflare by its one admitted MCP component and fails closed at the unprovisioned OAuth credential", async () => {
    const database = client.db("rowboat");

    // ---------------------------------------------------------------- 1.
    const { ensureAllIndexes } = await import("@/src/infrastructure/mongodb/ensure-indexes");
    await ensureAllIndexes(database);
    log(`indexes ensured on ${LIVE_URL}`);

    // ---------------------------------------------------------------- 2.
    const workflow = { agents: [], prompts: [], tools: [], startAgent: "", lastUpdatedAt: new Date().toISOString() };
    await database.collection("projects").insertOne({
      _id: projectId, name: "component-install-e2e", createdAt: new Date().toISOString(), createdByUserId: "guest_user",
      secret: "e2e-secret", draftWorkflow: workflow, liveWorkflow: workflow,
    } as never);
    const { MongodbPluginsRepository, MongoPluginTransactionRunner } = await import("@/src/infrastructure/repositories/mongodb.plugins.repository");
    const { MongodbProjectsRepository } = await import("@/src/infrastructure/repositories/mongodb.projects.repository");
    const plugins = new MongodbPluginsRepository({ pluginsDatabase: database, pluginTransactionRunner: new MongoPluginTransactionRunner({ pluginsMongoClient: client }) });
    const projects = new MongodbProjectsRepository({ projectMembersRepository: {} as never });
    log(`project ${projectId} inserted`);

    // ---------------------------------------------------------------- 3.
    const { readCatalogLock } = await import("@/scripts/load-plugin-catalog");
    const lock = await readCatalogLock("config/openai-plugin-catalog.lock.json", resolve(process.cwd(), "..", ".."));
    if ((await plugins.getCatalog(PINNED_PLUGIN_CATALOG_DIGEST)) === null) await plugins.putCatalog(lock);
    const seeded = await plugins.getCatalog(PINNED_PLUGIN_CATALOG_DIGEST);
    log(`catalog seeded: ${seeded!.entries.length} entries at ${seeded!.catalogDigest.slice(0, 12)}...`);
    expect(seeded?.entries).toHaveLength(180);

    // ---------------------------------------------------------------- 4.
    // The real install use case over the real repository. First the old
    // refusal, then the selection.
    const entry = seeded!.entries.find(candidate => candidate.name === "cloudflare")!;
    const admitted = entry.components.filter(candidate => candidate.admission.status === "admitted");
    const component = admitted.find(candidate => candidate.component.kind === "mcp")!;
    const componentDigest = component.component.metadata.bindingDigest as string;
    log(`cloudflare: ${entry.components.length} components, ${admitted.length} admitted, selecting ${component.component.name} ${componentDigest.slice(0, 12)}...`);
    expect(admitted).toHaveLength(1);

    const { InstallPluginUseCase } = await import("@/src/application/use-cases/plugins/install-plugin.use-case");
    const install = new InstallPluginUseCase({ pluginsRepository: plugins, pluginApiAuthorizationPolicy: { authenticate: async () => identity, authorizeProject: async () => undefined } });
    const base = { identity, projectId, pluginName: "cloudflare", catalogDigest: PINNED_PLUGIN_CATALOG_DIGEST, expectedRevision: 0 };

    const wholePlugin = await failure(() => install.execute({ ...base, idempotencyKey: randomUUID() }));
    log(`install without a selection -> ${wholePlugin}`);
    expect(wholePlugin).toBe("component_not_admitted");

    const idempotencyKey = randomUUID();
    const receipt = await install.execute({ ...base, idempotencyKey, componentDigests: [componentDigest] });
    log(`install with [${componentDigest.slice(0, 12)}...] -> receipt ${receipt.receiptId} status=${receipt.status}`);
    const installation = (await plugins.getInstallation(projectId, "cloudflare"))!;
    const providerBindings = installation.providerBindings ?? [];
    const admissions = await plugins.listAdmissions(installation.id);
    log(`installation ${installation.id}: revision=${installation.revision} providerBindings=${JSON.stringify(providerBindings.map(binding => binding.binding.componentDigest.slice(0, 12)))} admissions=${JSON.stringify(admissions.map(row => `${row.componentName}:${row.status}`))}`);
    expect(providerBindings).toHaveLength(1);
    expect(providerBindings[0]!.binding.componentDigest).toBe(componentDigest);
    expect(admissions).toHaveLength(1);
    expect(admissions[0]!.componentDigest).toBe(componentDigest);
    expect(admissions[0]!.status).toBe("admitted");

    // Admission is checked before the idempotent write, so on cloudflare a
    // second selection that adds a non-admitted component is refused as
    // `component_not_admitted` before the key is ever compared. Fail-closed
    // either way; the two refusals are pinned separately.
    const other = entry.components.find(candidate => candidate.admission.status !== "admitted")!.component.metadata.bindingDigest as string;
    const widened = await failure(() => install.execute({ ...base, idempotencyKey, componentDigests: [componentDigest, other] }));
    log(`same idempotency key, selection widened by a non-admitted component -> ${widened}`);
    expect(widened).toBe("component_not_admitted");
    const again = await install.execute({ ...base, idempotencyKey, componentDigests: [componentDigest] });
    log(`same idempotency key, same selection -> receipt ${again.receiptId} (${again.receiptId === receipt.receiptId ? "replayed" : "NEW -- not idempotent"})`);
    expect(again.receiptId).toBe(receipt.receiptId);

    // The idempotency conflict needs two selections that are BOTH admitted.
    // `linear` has five admitted components (skill, mcp, app, two assets):
    // install its MCP component alone, then reuse the key with the MCP
    // component plus the app.
    const linear = seeded!.entries.find(candidate => candidate.name === "linear")!;
    const linearAdmitted = linear.components.filter(candidate => candidate.admission.status === "admitted");
    const linearMcp = linearAdmitted.find(candidate => candidate.component.kind === "mcp")!.component.metadata.bindingDigest as string;
    const linearApp = linearAdmitted.find(candidate => candidate.component.kind === "app")!.component.metadata.bindingDigest as string;
    const linearKey = randomUUID();
    const linearReceipt = await install.execute({ ...base, pluginName: "linear", idempotencyKey: linearKey, componentDigests: [linearMcp] });
    log(`linear: ${linear.components.length} components, ${linearAdmitted.length} admitted; install [${linearMcp.slice(0, 12)}...] -> receipt ${linearReceipt.receiptId}`);
    const conflict = await failure(() => install.execute({ ...base, pluginName: "linear", idempotencyKey: linearKey, componentDigests: [linearMcp, linearApp] }));
    log(`linear: same idempotency key, different admitted selection [mcp, app] -> ${conflict}`);
    expect(conflict).toBe("idempotency_conflict");
    const linearAdmissions = await plugins.listAdmissions((await plugins.getInstallation(projectId, "linear"))!.id);
    log(`linear admissions after the conflict: ${JSON.stringify(linearAdmissions.map(row => `${row.componentName}:${row.status}`))}`);
    expect(linearAdmissions).toHaveLength(1);

    // ---------------------------------------------------------------- 5.
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
    const addition = await addTool.execute({ identity, projectId, pluginName: "cloudflare", componentDigest });
    log(`tool bound: ${addition.toolName} added=${addition.added}`);
    expect(addition.added).toBe(true);
    // A non-selected component cannot be bound: the installation carries no binding for it.
    const unbound = await failure(() => addTool.execute({ identity, projectId, pluginName: "cloudflare", componentDigest: other }));
    log(`add-tool for a non-selected component -> ${unbound}`);
    expect(unbound).not.toBe("no_error");

    // ---------------------------------------------------------------- 6.
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
    const executionBinding = { installationId: installation.id, pluginName: "cloudflare", componentDigest, providerBindingId: providerBindings[0]!.binding.id, capability: "write" as const };
    log(`runtime composed against ${process.env.OPENFANG_URL}; component mcpServer=${JSON.stringify(component.component.metadata.mcpServer)}`);

    // ---------------------------------------------------------------- 7.
    const approver = approveWhenRaised(30_000);
    let outcome = "success";
    const started = Date.now();
    try { await runtime.invoke(executionBinding, ARGUMENTS, { projectId, operationName: OPERATION }); } catch (error) { outcome = error instanceof Error ? error.message : "unknown"; }
    const raised = await approver;
    log(`invocation outcome: ${outcome} after ${Date.now() - started}ms; approval raised: ${raised === undefined ? "NONE" : raised.id}`);

    // ---------------------------------------------------------------- 8.
    await new Promise(done => setTimeout(done, 2_000));
    const stored = await database.collection("plugin_receipts").find({}).toArray();
    // The execution receipt is the only receipt whose payload carries the
    // project id; it names the component it ran, not the operation.
    const payloads = stored
      .filter(document => typeof document.payload === "string" && document.payload.includes(projectId))
      .map(document => JSON.parse(document.payload as string) as Record<string, unknown>)
      .filter(payload => payload.type === "execution" && (payload.output as { componentDigest?: string } | undefined)?.componentDigest === componentDigest);
    const mine = payloads;
    for (const payload of payloads) log(`runtime receipt: type=${String(payload.type)} status=${String(payload.status)} reason=${String(payload.reason)} componentDigest=${String((payload.output as { componentDigest?: string }).componentDigest).slice(0, 12)}... approvalId=${String((payload.output as { approvalId?: string } | undefined)?.approvalId)}`);
    const decided = (await listApprovals()).find(candidate => candidate.id === raised?.id);
    log(`decision for ${String(raised?.id)}: status=${String(decided?.status)} decided_at=${String(decided?.decided_at)}`);

    // ---------------------------------------------------------------- 9.
    // Since the credential-name rules landed, a server that declares no
    // credential is treated as an OAuth resource at its own URL, so this
    // released call now fails CLOSED at `credential_missing` (the daemon
    // allowlists no OAUTH_BEARER_MCP_CLOUDFLARE_COM_MCP) before any network
    // I/O -- it no longer reaches Cloudflare's 401. The release mechanics
    // above are unchanged and still asserted: approval raised, approved,
    // receipt stamped.
    expect(outcome).not.toBe("write_review_required");
    expect(outcome).toBe("credential_missing");
    expect(raised).toBeDefined();
    expect(raised!.tool_name).toBe(OPERATION);
    expect(raised!.action_summary).toContain(componentDigest);
    for (const value of Object.values(ARGUMENTS)) expect(raised!.action_summary).not.toContain(value);
    expect(decided?.status).toBe("approved");
    expect(mine).toHaveLength(1);
    expect(payloads[0]?.status).toBe("failed");
    expect((payloads[0]?.output as { approvalId?: string } | undefined)?.approvalId).toBe(raised!.id);
  }, 180_000);
});
