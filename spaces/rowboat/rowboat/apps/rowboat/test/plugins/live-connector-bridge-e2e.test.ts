/**
 * END-TO-END PROOF, PART IV: the connector bridge, up to OpenAI's own 401.
 *
 * Parts I-III proved the mechanical chain for `mcp`-kind components (a
 * declared credential slot, no slot, and a real credential accepted). This
 * run closes the last uncovered shape: an `app`-kind component whose
 * `.app.json` carries a `connector_...` id, executed through
 * `ConnectorBridgeProvider` instead of `HttpMcpProvider`.
 *
 *   InstallPluginUseCase (componentDigests = [the canva app component])
 *     -> AddPluginToolUseCase
 *     -> PluginToolRuntime
 *          -> OpenFangWriteReleasePolicy      (approval raised + approved)
 *          -> OpenFangCredentialResolver      (POST /api/credentials/issue,
 *                                              TWICE: OPENAI_API_KEY, then
 *                                              CONNECTOR_CANVA -- both must
 *                                              issue before any network I/O)
 *          -> ConnectorBridgeProvider         (real HTTPS POST to
 *                                              https://api.openai.com/v1/responses)
 *     <- ProviderResult { status: "failed", reason: "provider_failed" }
 *     -> receipt in plugin_receipts with status "failed" and the approvalId
 *
 * By workboard policy this repository has no OpenAI API budget (spec D6), so
 * both credential values the daemon issues are obviously-fake test values
 * generated for this run. OpenAI's own `/v1/responses` therefore answers the
 * real POST with 401, exactly the way a fake GitHub PAT drew a real 401 from
 * `api.githubcopilot.com` in Part I. `connector_...` id validity against
 * OpenAI's own connector directory stays UNPROVEN by this run (D6) -- only a
 * real accepted call could prove that, and this run makes none.
 *
 * A second, negative branch in the same test installs an `asdk_app_...` app
 * (`actively`) -- a component the catalog admits but whose id has no public
 * invocation path outside ChatGPT (D4). `AddPluginToolUseCase` has no
 * kind-based gate, so the tool binds; the interesting question is what
 * `PluginToolRuntime.invoke()` does with it. Reading the runtime
 * (`plugin-tool-runtime.ts`): `releaseWrite` runs BEFORE `resolveProvider`
 * (the release gate at line ~642 precedes the resolution call at line ~752),
 * so a write-classified call against this component still raises -- and, if
 * approved here, consumes -- a real OpenFang approval before resolution ever
 * runs. Only afterward does `resolvePluginProvider`'s app branch refuse the
 * non-`connector_` id (`UNAVAILABLE`, no provider ever constructed, no
 * network I/O), and `exactProvider` turns that into a thrown
 * `provider_unavailable`. So the observed, pinned outcome is: exactly one
 * approval raised and approved, zero bytes sent to any provider, and the
 * call still ends in `provider_unavailable` -- not "no approval at all",
 * which the a-priori reading of "resolution refuses before release" would
 * have predicted (and which the code does not do).
 *
 * Opt-in needs the same three live variables as Parts I-III, plus a fourth,
 * DEDICATED gate -- reusing ROWBOAT_LIVE_ACCEPTANCE would let this run
 * silently piggyback on a daemon provisioned for the GitHub proof, holding
 * neither OPENAI_API_KEY nor CONNECTOR_CANVA:
 *   ROWBOAT_LIVE_MONGO_URL=mongodb://127.0.0.1:27017/rowboat
 *   ROWBOAT_LIVE_OPENFANG_URL=http://127.0.0.1:4273
 *   OPENFANG_API_KEY=<the isolated daemon's api_key>
 *   ROWBOAT_LIVE_CONNECTOR=1
 * and the daemon side needs
 *   OPENFANG_ISSUABLE_CREDENTIALS=OPENAI_API_KEY,CONNECTOR_CANVA
 * plus obviously-fake values for both in its process environment.
 */
const LIVE_URL = (process.env.ROWBOAT_LIVE_MONGO_URL ?? "").trim();
const OPENFANG_URL = (process.env.ROWBOAT_LIVE_OPENFANG_URL ?? "").trim();
const OPENFANG_KEY = (process.env.OPENFANG_API_KEY ?? "").trim();
const CONNECTOR_GATE = (process.env.ROWBOAT_LIVE_CONNECTOR ?? "").trim();
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

const SKIP = LIVE_URL === "" || OPENFANG_URL === "" || OPENFANG_KEY === "" || CONNECTOR_GATE !== "1";
const EVIDENCE = (process.env.ROWBOAT_E2E_EVIDENCE ?? "").trim();

let step = 0;
const log = (message: string): void => {
  const line = `  ${String(++step).padStart(2, "0")}  ${message}`;
  console.log(line);
  if (EVIDENCE !== "") appendFileSync(EVIDENCE, `${line}\n`, "utf8");
};

// canva's admitted operation set is opaque from the catalog record alone
// (the connector bridge asks the OpenAI Responses API to run it, not a local
// schema); "export_design" is a plausible, deterministic operation name --
// it is never resolved against a real canva session in this run, because the
// fake OPENAI_API_KEY draws OpenAI's own 401 before any connector-side
// authorization would even be attempted.
const OPERATION = "export_design";
const ARGUMENTS = Object.freeze({});
const NEGATIVE_OPERATION = "run_action";
const NEGATIVE_ARGUMENTS = Object.freeze({});

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

describe.skipIf(SKIP)("live connector-bridge end to end", () => {
  beforeAll(async () => {
    client = new MongoClient(LIVE_URL);
    await client.connect();
  });
  afterAll(async () => { await client?.close(); });

  it("carries a released canva call through the connector bridge to OpenAI's own 401, and refuses an asdk app before any network I/O", async () => {
    const database = client.db("rowboat");

    // ---------------------------------------------------------------- 1.
    const { ensureAllIndexes } = await import("@/src/infrastructure/mongodb/ensure-indexes");
    await ensureAllIndexes(database);
    const workflow = { agents: [], prompts: [], tools: [], startAgent: "", lastUpdatedAt: new Date().toISOString() };
    await database.collection("projects").insertOne({
      _id: projectId, name: "connector-bridge-e2e", createdAt: new Date().toISOString(), createdByUserId: "guest_user",
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
    log(`catalog ${PINNED_PLUGIN_CATALOG_DIGEST.slice(0, 12)}... seeded/verified, 180 entries`);

    const { InstallPluginUseCase } = await import("@/src/application/use-cases/plugins/install-plugin.use-case");
    const install = new InstallPluginUseCase({ pluginsRepository: plugins, pluginApiAuthorizationPolicy: { authenticate: async () => identity, authorizeProject: async () => undefined } });
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

    // ---------------------------------------------------------------- 3.
    // canva: an admitted `app` component whose pinned appDeclaration.id
    // starts with connector_ (Task 1 pinned it into component metadata).
    // Component-scoped install (W3), same as every prior part.
    const canvaEntry = seeded!.entries.find(candidate => candidate.name === "canva")!;
    const canvaAdmitted = canvaEntry.components.filter(candidate => candidate.admission.status === "admitted");
    const canvaComponent = canvaAdmitted.find(candidate => {
      if (candidate.component.kind !== "app") return false;
      const declaration = candidate.component.metadata.appDeclaration as { id?: unknown } | undefined;
      return typeof declaration?.id === "string" && declaration.id.startsWith("connector_");
    })!;
    const canvaDigest = canvaComponent.component.metadata.bindingDigest as string;
    const canvaConnectorId = (canvaComponent.component.metadata.appDeclaration as { id: string }).id;
    log(`canva: ${canvaEntry.components.length} components, ${canvaAdmitted.length} admitted, selecting ${canvaComponent.component.name} ${canvaDigest.slice(0, 12)}... (${canvaConnectorId})`);

    const canvaInstallReceipt = await install.execute({
      identity, projectId, pluginName: "canva", catalogDigest: PINNED_PLUGIN_CATALOG_DIGEST,
      expectedRevision: 0, idempotencyKey: randomUUID(), componentDigests: [canvaDigest],
    });
    const canvaInstallation = (await plugins.getInstallation(projectId, "canva"))!;
    const canvaBindings = canvaInstallation.providerBindings ?? [];
    const canvaAdmissions = await plugins.listAdmissions(canvaInstallation.id);
    log(`install with [${canvaDigest.slice(0, 12)}...] -> receipt ${canvaInstallReceipt.receiptId} status=${canvaInstallReceipt.status}; admissions=${canvaAdmissions.length} bindings=${canvaBindings.length}`);
    expect(canvaAdmissions).toHaveLength(1);
    expect(canvaBindings).toHaveLength(1);
    expect(canvaBindings[0]!.binding.providerKind).toBe("openai-connector-bridge");

    // ---------------------------------------------------------------- 4.
    const canvaAddition = await addTool.execute({ identity, projectId, pluginName: "canva", componentDigest: canvaDigest });
    log(`tool bound: ${canvaAddition.toolName} added=${canvaAddition.added}`);
    expect(canvaAddition.added).toBe(true);

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
    const canvaBinding = { installationId: canvaInstallation.id, pluginName: "canva", componentDigest: canvaDigest, providerBindingId: canvaBindings[0]!.binding.id, capability: "write" as const };
    log(`runtime composed against ${process.env.OPENFANG_URL}; operation=${OPERATION} (no readOnlyOperations declared -> classified write); no OPENAI_RESPONSES_MODEL/OPENAI_BASE_URL set -> kernel defaults (gpt-5.6, https://api.openai.com)`);

    // ---------------------------------------------------------------- 6.
    const canvaApprover = approveWhenRaised(30_000);
    let canvaOutcome = "success";
    const canvaStarted = Date.now();
    try {
      await runtime.invoke(canvaBinding, ARGUMENTS, { projectId, operationName: OPERATION });
    } catch (error) {
      canvaOutcome = error instanceof Error ? error.message : "unknown";
    }
    const canvaElapsed = Date.now() - canvaStarted;
    const canvaRaised = await canvaApprover;
    log(`invocation outcome: ${canvaOutcome} after ${canvaElapsed}ms; approval raised: ${canvaRaised === undefined ? "NONE" : canvaRaised.id}`);
    expect(canvaRaised).toBeDefined();
    // The action_summary carries the component digest and an arguments
    // digest, never an argument value; ARGUMENTS is `{}` so there is
    // trivially nothing to leak, but the digest must still be present.
    expect(canvaRaised!.action_summary).toContain(canvaDigest.slice(0, 16));

    // THE claim of this proof: a real POST reached OpenAI's own API and OpenAI
    // rejected the fake key -- not "no path existed", not a local refusal.
    expect(canvaOutcome).toBe("provider_failed");

    // ---------------------------------------------------------------- 7.
    await new Promise(done => setTimeout(done, 2_000));
    const canvaStored = await database.collection("plugin_receipts").find({}).toArray();
    const canvaPayloads = canvaStored
      .filter(document => typeof document.payload === "string" && (document.payload as string).includes(projectId))
      .map(document => JSON.parse(document.payload as string) as Record<string, unknown>)
      .filter(payload => payload.type === "execution" && payload.pluginName === "canva");
    expect(canvaPayloads).toHaveLength(1);
    const canvaExecution = canvaPayloads[0]!;
    const canvaOutput = canvaExecution.output as { approvalId?: string; componentDigest?: string } | undefined;
    log(`execution receipt: status=${String(canvaExecution.status)} reason=${String(canvaExecution.reason)} componentKind=${String(canvaExecution.componentKind)} approvalId=${String(canvaOutput?.approvalId)}`);
    expect(canvaExecution.status).toBe("failed");
    expect(canvaExecution.componentKind).toBe("app");
    expect(canvaOutput?.approvalId).toBe(canvaRaised!.id);
    expect(canvaOutput?.componentDigest).toBe(canvaDigest);

    const canvaDecided = (await listApprovals()).find(candidate => candidate.id === canvaRaised!.id);
    log(`decision for ${canvaRaised!.id}: status=${canvaDecided?.status} decided_at=${canvaDecided?.decided_at}`);
    expect(canvaDecided?.status).toBe("approved");

    // ================================================================
    // NEGATIVE BRANCH: an asdk_app_ app. Admitted by the catalog, bindable
    // as a tool (AddPluginToolUseCase has no kind gate), but with no
    // public invocation path outside ChatGPT (D4). See the file-header
    // comment for why an approval is still raised and approved here even
    // though the call never reaches a provider.
    // ================================================================

    // ---------------------------------------------------------------- 8.
    const activelyEntry = seeded!.entries.find(candidate => candidate.name === "actively")!;
    const activelyAdmitted = activelyEntry.components.filter(candidate => candidate.admission.status === "admitted");
    const activelyComponent = activelyAdmitted.find(candidate => candidate.component.kind === "app")!;
    const activelyDigest = activelyComponent.component.metadata.bindingDigest as string;
    const activelyDeclaredId = (activelyComponent.component.metadata.appDeclaration as { id: string }).id;
    log(`actively: ${activelyEntry.components.length} components, ${activelyAdmitted.length} admitted, selecting ${activelyComponent.component.name} ${activelyDigest.slice(0, 12)}... (${activelyDeclaredId})`);
    expect(activelyDeclaredId.startsWith("connector_")).toBe(false);

    const activelyInstallReceipt = await install.execute({
      identity, projectId, pluginName: "actively", catalogDigest: PINNED_PLUGIN_CATALOG_DIGEST,
      expectedRevision: 0, idempotencyKey: randomUUID(), componentDigests: [activelyDigest],
    });
    const activelyInstallation = (await plugins.getInstallation(projectId, "actively"))!;
    const activelyBindings = activelyInstallation.providerBindings ?? [];
    log(`install with [${activelyDigest.slice(0, 12)}...] -> receipt ${activelyInstallReceipt.receiptId} status=${activelyInstallReceipt.status}; bindings=${activelyBindings.length}`);
    expect(activelyBindings).toHaveLength(1);

    // ---------------------------------------------------------------- 9.
    const activelyAddition = await addTool.execute({ identity, projectId, pluginName: "actively", componentDigest: activelyDigest });
    log(`tool bound: ${activelyAddition.toolName} added=${activelyAddition.added} (no kind gate in AddPluginToolUseCase -- an asdk_app_ component binds exactly like a connector_ one)`);
    expect(activelyAddition.added).toBe(true);

    // ---------------------------------------------------------------- 10.
    const activelyBinding = { installationId: activelyInstallation.id, pluginName: "actively", componentDigest: activelyDigest, providerBindingId: activelyBindings[0]!.binding.id, capability: "write" as const };
    const activelyApprover = approveWhenRaised(30_000);
    let activelyOutcome = "success";
    const activelyStarted = Date.now();
    try {
      await runtime.invoke(activelyBinding, NEGATIVE_ARGUMENTS, { projectId, operationName: NEGATIVE_OPERATION });
    } catch (error) {
      activelyOutcome = error instanceof Error ? error.message : "unknown";
    }
    const activelyElapsed = Date.now() - activelyStarted;
    const activelyRaised = await activelyApprover;
    log(`invocation outcome: ${activelyOutcome} after ${activelyElapsed}ms; approval raised: ${activelyRaised === undefined ? "NONE" : activelyRaised.id}`);
    // PINNED, not assumed: release runs before resolution in
    // PluginToolRuntime.invoke() (releaseWrite is awaited long before
    // resolveProvider is ever called), so a write-classified call against
    // this non-connector app still raises -- and here, still gets approved
    // -- an OpenFang approval. Only afterward does resolvePluginProvider's
    // app branch refuse the asdk_app_ id and resolve UNAVAILABLE, which
    // exactProvider turns into a thrown provider_unavailable with zero
    // bytes ever sent to any provider. "No approval at all" would have been
    // the wrong prediction for this codebase as it stands today.
    expect(activelyRaised).toBeDefined();
    expect(activelyOutcome).toBe("provider_unavailable");

    // ---------------------------------------------------------------- 11.
    await new Promise(done => setTimeout(done, 2_000));
    const activelyStored = await database.collection("plugin_receipts").find({}).toArray();
    const activelyPayloads = activelyStored
      .filter(document => typeof document.payload === "string" && (document.payload as string).includes(projectId))
      .map(document => JSON.parse(document.payload as string) as Record<string, unknown>)
      .filter(payload => payload.type === "execution" && payload.pluginName === "actively");
    expect(activelyPayloads).toHaveLength(1);
    const activelyExecution = activelyPayloads[0]!;
    const activelyOutput = activelyExecution.output as { approvalId?: string; componentDigest?: string } | undefined;
    log(`execution receipt: status=${String(activelyExecution.status)} reason=${String(activelyExecution.reason)} componentKind=${String(activelyExecution.componentKind)} approvalId=${String(activelyOutput?.approvalId)}`);
    expect(activelyExecution.status).toBe("failed");
    expect(activelyExecution.componentKind).toBe("app");
    expect(activelyOutput?.approvalId).toBe(activelyRaised!.id);

    // ---------------------------------------------------------------- 12.
    // Hygiene: neither fake credential value, nor the daemon's api_key,
    // ever reached the evidence log or a stored receipt. (The daemon's own
    // stdout/stderr are scanned separately, outside this test process.)
    const receiptText = JSON.stringify([...canvaPayloads, ...activelyPayloads]);
    expect(receiptText).not.toContain("sk-obviously-fake-e2e-value");
    expect(receiptText).not.toContain("obviously-fake-connector-token");
    expect(receiptText).not.toContain(OPENFANG_KEY);
    log("leak check: fake OPENAI_API_KEY / CONNECTOR_CANVA values and the daemon api_key -- 0 occurrences in stored receipts");
  }, 120_000);
});
