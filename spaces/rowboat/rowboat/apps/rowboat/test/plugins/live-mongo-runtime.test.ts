/**
 * Live integration gate against a real MongoDB.
 *
 * Opt-in: it runs only when ROWBOAT_LIVE_MONGO_URL points at a reachable
 * MongoDB, and reports itself skipped otherwise. It exists because a fake
 * collection cannot show what the real driver does to a document it is handed,
 * and what the real storage shape looks like on the way back:
 *
 *   docker run -d --name rowboat-rs -p 127.0.0.1:27017:27017 mongo:7 --replSet rs0 --bind_ip_all
 *   docker exec rowboat-rs mongosh --quiet --eval 'rs.initiate({_id:"rs0",members:[{_id:0,host:"127.0.0.1:27017"}]})'
 *   ROWBOAT_LIVE_MONGO_URL=mongodb://127.0.0.1:27017/rowboat npx vitest run test/plugins/live-mongo-runtime.test.ts
 *
 * A replica set is required: the catalog and every installation are written in
 * a transaction, which a standalone mongod refuses.
 *
 * It uses the real repositories, the real index bootstrap, and the real use
 * cases; nothing about the cutover logic is faked here.
 */
const LIVE_URL = (process.env.ROWBOAT_LIVE_MONGO_URL ?? "").trim();
// The app hardcodes the "rowboat" database name, so this gate uses it too.
if (LIVE_URL !== "") process.env.MONGODB_CONNECTION_STRING = LIVE_URL;

import { randomUUID } from "node:crypto";
import { resolve } from "node:path";
import { MongoClient } from "mongodb";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { PINNED_OPENAI_PLUGINS_COMMIT, PINNED_PLUGIN_CATALOG_DIGEST, type PluginMigrationRecord, type PluginReceipt } from "@rowboat/openai-plugin-runtime";
import type { PluginToolRuntimeDependencies } from "@/src/application/services/plugin-tool-runtime";
import { LEGACY_PLUGIN_RECIPES } from "@/src/application/services/legacy-plugin-recipes";
import { materializeWorkflowBindings } from "@/src/application/services/plugin-binding-materialization";
import githubIssueToSlack from "@/app/lib/prebuilt-cards/github-issue-to-slack.json";

const projectId = randomUUID();
const migrationRecordId = randomUUID();
const installationId = randomUUID();
const parityReceiptId = `parity:${"c".repeat(64)}`;
const digest = (seed: string) => seed.repeat(64).slice(0, 64);

// The real prebuilt card, so the recipe's tool ordinals and identity digests
// are the ones a migrated project actually carries.
const recipe = LEGACY_PLUGIN_RECIPES["github-issue-to-slack"];
const target = recipe.capabilities[0]!.target!;
const boundOrdinal = recipe.capabilities[0]!.legacyAction.ordinal;
const workflow = Object.freeze({ ...structuredClone(githubIssueToSlack), lastUpdatedAt: "2026-08-01T10:00:00.000Z" }) as Record<string, unknown>;

let client: MongoClient;
let step = 0;
const log = (message: string) => console.log(`  ${String(++step).padStart(2, "0")}  ${message}`);

describe.skipIf(LIVE_URL === "")("live plugin runtime cutover against a real MongoDB", () => {
  beforeAll(async () => {
    client = new MongoClient(LIVE_URL);
    await client.connect();
    await client.db("rowboat").dropDatabase();
  });

  afterAll(async () => {
    await client.close();
  });

  it("runs the whole phase 4 mechanism end to end", async () => {
    const database = client.db("rowboat");

    // 1. Index bootstrap, exactly as `npm run mongodb-ensure-indexes` does it.
    const { ensureAllIndexes } = await import("@/src/infrastructure/mongodb/ensure-indexes");
    await ensureAllIndexes(database);
    const receiptIndexes = await database.collection("plugin_receipts").indexes();
    log(`indexes ensured: plugin_receipts has ${receiptIndexes.map(index => index.name).join(", ")}`);
    expect(receiptIndexes.some(index => index.name === "receiptId_unique")).toBe(true);

    // 2. A project stored before the plugin runtime existed: no pluginRuntime field.
    await database.collection("projects").insertOne({
      _id: projectId, name: "demo", createdAt: "2026-08-01T10:00:00.000Z", createdByUserId: "user-1",
      secret: "demo-secret", draftWorkflow: workflow, liveWorkflow: workflow,
    } as never);
    const { parsePluginRuntimeState } = await import("@/src/entities/models/project");
    const stored = await database.collection("projects").findOne({ _id: projectId } as never);
    log(`legacy project read: pluginRuntime field present=${Object.prototype.hasOwnProperty.call(stored!, "pluginRuntime")}, resolved=${JSON.stringify(parsePluginRuntimeState((stored as { pluginRuntime?: unknown }).pluginRuntime))}`);
    expect(parsePluginRuntimeState((stored as { pluginRuntime?: unknown }).pluginRuntime)).toEqual({ mode: "legacy", revision: 0 });

    // 3. Real repositories.
    const { MongodbPluginsRepository, MongoPluginTransactionRunner } = await import("@/src/infrastructure/repositories/mongodb.plugins.repository");
    const { MongodbProjectsRepository } = await import("@/src/infrastructure/repositories/mongodb.projects.repository");
    const plugins = new MongodbPluginsRepository({ pluginsDatabase: database, pluginTransactionRunner: new MongoPluginTransactionRunner({ pluginsMongoClient: client }) });
    const projects = new MongodbProjectsRepository({ projectMembersRepository: {} as never });

    // 3b. Catalog seed, exactly as `npm run plugins:catalog-load` does it. The
    // app reads its catalog from the database, so without this step every
    // plugin path fails with catalog_digest_mismatch.
    const { readCatalogLock } = await import("@/scripts/load-plugin-catalog");
    const lock = await readCatalogLock("config/openai-plugin-catalog.lock.json", resolve(process.cwd(), "..", ".."));
    await plugins.putCatalog(lock);
    const seeded = await plugins.getCatalog(PINNED_PLUGIN_CATALOG_DIGEST);
    log(`catalog seeded: ${seeded === null ? "MISSING" : `${seeded.entries.length} entries at ${seeded.catalogDigest.slice(0, 12)}...`}`);
    expect(seeded?.entries).toHaveLength(180);
    // Loading again is a no-op through the documented guarded path. putCatalog
    // is not idempotent on its own: the immutable-insert conflict check
    // re-captures the stored document, and a real catalog entry payload (up to
    // ~44 KB) exceeds the 16 KB string capture limit, so a blind second write
    // raises catalog_entry_conflict. The loader therefore reads before writing,
    // exactly as scripts/load-plugin-catalog.ts does.
    const already = await plugins.getCatalog(PINNED_PLUGIN_CATALOG_DIGEST);
    if (already === null) await plugins.putCatalog(lock);
    expect((await plugins.getCatalog(PINNED_PLUGIN_CATALOG_DIGEST))?.entries).toHaveLength(180);
    await expect(plugins.putCatalog(lock)).rejects.toThrow("catalog_entry_conflict");

    // 4. Cutover evidence written through the real repository.
    const record: PluginMigrationRecord = Object.freeze({
      id: migrationRecordId, projectId, recipeId: recipe.recipeId, recipeDigest: digest("d"),
      sourceProjectRevision: Date.parse("2026-08-01T10:00:00.000Z"), sourceDigest: digest("e"), sourceInventoryDigest: digest("f"),
      targetCatalogDigest: PINNED_PLUGIN_CATALOG_DIGEST, targetSourceCommit: PINNED_OPENAI_PLUGINS_COMMIT, targetPolicyVersion: "rowboat-plugin-policy-v1",
      targetInstallationIds: Object.freeze([installationId]), rollbackSnapshotDigest: digest("a"), status: "applied",
      blockers: Object.freeze([]), createdAt: "2026-08-26T10:00:00.000Z",
    }) as PluginMigrationRecord;
    const parity: PluginReceipt = Object.freeze({
      type: "execution", receiptId: parityReceiptId, projectId, pluginName: "github", status: "success", redactions: Object.freeze([]),
    }) as PluginReceipt;
    await plugins.putMigrationRecord(record);
    await plugins.putReceipt(parity);
    // The installation is pinned to the catalog entry the repository validates
    // against, so its provenance and its component digest come from the seeded
    // catalog rather than from invented values.
    const entry = seeded!.entries.find(candidate => candidate.name === target.pluginName)!;
    const component = entry.components.find(candidate => candidate.component.id === target.componentId)!;
    const componentDigest = component.component.metadata.bindingDigest as string;
    await plugins.putInstallation({
      id: installationId, projectId, pluginName: entry.pluginName, pluginVersion: entry.pluginVersion,
      sourceCommit: entry.sourceCommit, manifestDigest: entry.manifestDigest, treeDigest: entry.treeDigest,
      policyVersion: entry.policyVersion, enabled: true, revision: 1,
      providerBindings: [{ componentId: target.componentId, binding: { id: "slack.app", providerKind: target.providerKind, componentDigest } }],
    });
    // The retained rollback snapshot the apply writes; the only authority for
    // what the legacy workflow was.
    await database.collection("plugin_migration_rollbacks").insertOne({
      _id: migrationRecordId, projectId, draftWorkflow: workflow, liveWorkflow: workflow,
      rollbackSnapshotDigest: record.rollbackSnapshotDigest,
    } as never);
    log(`evidence stored: migration ${record.status}, parity ${parity.status}, installation ${target.pluginName} with 1 provider binding`);

    // 5. The real use case on the real repositories.
    const { SetPluginRuntimeModeUseCase } = await import("@/src/application/use-cases/plugins/set-plugin-runtime-mode.use-case");
    const receipts: PluginReceipt[] = [];
    const service = new SetPluginRuntimeModeUseCase({
      authorizeProject: async () => undefined,
      loadRuntimeState: async id => {
        const project = await projects.fetch(id);
        return project === null ? null : parsePluginRuntimeState(project.pluginRuntime);
      },
      loadMigrationRecord: id => plugins.getMigrationRecord(id),
      loadReceipt: id => plugins.getReceipt(id),
      saveRuntimeState: (id, expectedRevision, state, workflows) => projects.setPluginRuntimeState(id, expectedRevision, state, workflows),
      putReceipt: async receipt => { receipts.push(receipt); await plugins.putReceipt(receipt); },
      // The same implementations the container wires in production.
      materializeWorkflows: async ({ projectId: id, migrationRecordId: recordId }) => {
        const stored = await plugins.getMigrationRecord(recordId);
        const project = await projects.fetch(id);
        const installations = await plugins.listInstallations(id);
        const draft = materializeWorkflowBindings({ workflow: project!.draftWorkflow, recipeId: stored!.recipeId, installations });
        const live = materializeWorkflowBindings({ workflow: project!.liveWorkflow, recipeId: stored!.recipeId, installations });
        return { draftWorkflow: draft.workflow, liveWorkflow: live.workflow };
      },
      restoreWorkflows: async ({ projectId: id, migrationRecordId: recordId, rollbackSnapshotDigest }) => {
        const row = await database.collection("plugin_migration_rollbacks").findOne({ _id: recordId } as never, { projection: { _id: 0 } });
        if (row === null || row.projectId !== id || row.rollbackSnapshotDigest !== rollbackSnapshotDigest) throw new Error("rollback_snapshot_unavailable");
        return { draftWorkflow: row.draftWorkflow, liveWorkflow: row.liveWorkflow };
      },
      now: () => new Date(),
    });
    const identity = Object.freeze({ kind: "user" as const, userId: "user-1" });
    const evidence = { catalogDigest: PINNED_PLUGIN_CATALOG_DIGEST, migrationRecordId, parityReceiptId };

    await expect(service.execute({ identity, projectId, mode: "openai", expectedRevision: 0, ...evidence }))
      .rejects.toThrow("runtime_mode_transition_rejected");
    log("legacy -> openai  REJECTED (runtime_mode_transition_rejected)");

    const shadow = await service.execute({ identity, projectId, mode: "shadow", expectedRevision: 0 });
    log(`legacy -> shadow  OK   (revision ${shadow.revision})`);

    await expect(service.execute({ identity, projectId, mode: "openai", expectedRevision: 1, catalogDigest: PINNED_PLUGIN_CATALOG_DIGEST, migrationRecordId }))
      .rejects.toThrow("cutover_evidence_required");
    log("shadow -> openai  REJECTED without parity receipt (cutover_evidence_required)");

    await expect(service.execute({ identity, projectId, mode: "openai", expectedRevision: 0, ...evidence }))
      .rejects.toThrow("plugin_runtime_state_conflict");
    log("shadow -> openai  REJECTED on a stale revision (plugin_runtime_state_conflict)");

    const cutover = await service.execute({ identity, projectId, mode: "openai", expectedRevision: 1, ...evidence });
    log(`shadow -> openai  OK   (revision ${cutover.revision}, receipt ${cutover.receiptId.slice(0, 26)}...)`);

    const persisted = await database.collection("projects").findOne({ _id: projectId } as never);
    log(`persisted state: ${JSON.stringify((persisted as { pluginRuntime?: unknown }).pluginRuntime)}`);

    const boundTool = ((persisted as unknown as { liveWorkflow: { tools: Record<string, unknown>[] } }).liveWorkflow.tools)[boundOrdinal]!;
    log(`materialized: tool "${String(boundTool.name)}" -> ${JSON.stringify(boundTool.pluginBinding)}`);
    expect(boundTool.pluginBinding).toEqual({ installationId, pluginName: target.pluginName, componentDigest, providerBindingId: "slack.app", capability: "write", origin: "migration" });
    expect(cutover.workflowsWritten).toBe(true);
    const untouched = ((persisted as unknown as { liveWorkflow: { tools: Record<string, unknown>[] } }).liveWorkflow.tools).filter((tool, index) => index !== boundOrdinal && "pluginBinding" in tool);
    expect(untouched).toEqual([]);

    // 6. Rollback, and proof that it never touched the legacy workflow.
    const rollback = await service.execute({ identity, projectId, mode: "legacy", expectedRevision: 2 });
    const afterRollback = await database.collection("projects").findOne({ _id: projectId } as never);
    log(`openai -> legacy  OK   (rolledBack=${rollback.rolledBack}, workflows restored=${rollback.workflowsWritten}, revision ${rollback.revision})`);
    expect((afterRollback as { draftWorkflow?: unknown }).draftWorkflow).toEqual(workflow);
    expect((afterRollback as { liveWorkflow?: unknown }).liveWorkflow).toEqual(workflow);
    expect(((afterRollback as unknown as { liveWorkflow: { tools: Record<string, unknown>[] } }).liveWorkflow.tools).some(tool => "pluginBinding" in tool)).toBe(false);
    log("legacy workflow restored byte-identical from the retained snapshot, no binding left");

    // 6b. The UI path: install an admitted plugin and add its component as a
    // tool. Nothing about this depends on a migration.
    const { AddPluginToolUseCase } = await import("@/src/application/use-cases/plugins/add-plugin-tool.use-case");
    const githubEntry = seeded!.entries.find(candidate => candidate.name === "github")!;
    const githubComponent = githubEntry.components.find(candidate => candidate.component.kind === "mcp" && candidate.admission.status === "admitted")!;
    const githubInstallationId = randomUUID();
    await plugins.putInstallation({
      id: githubInstallationId, projectId, pluginName: githubEntry.pluginName, pluginVersion: githubEntry.pluginVersion,
      sourceCommit: githubEntry.sourceCommit, manifestDigest: githubEntry.manifestDigest, treeDigest: githubEntry.treeDigest,
      policyVersion: githubEntry.policyVersion, enabled: true, revision: 1,
      providerBindings: [{
        componentId: githubComponent.component.id,
        binding: githubComponent.component.metadata.providerBinding as { id: string; providerKind: "mcp-http"; componentDigest: string },
      }],
    });
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
      identity, projectId, pluginName: githubEntry.pluginName,
      componentDigest: githubComponent.component.metadata.bindingDigest as string,
    });
    const repeated = await addTool.execute({
      identity, projectId, pluginName: githubEntry.pluginName,
      componentDigest: githubComponent.component.metadata.bindingDigest as string,
    });
    const withTool = await database.collection("projects").findOne({ _id: projectId } as never);
    const addedTool = ((withTool as unknown as { draftWorkflow: { tools: Record<string, unknown>[] } }).draftWorkflow.tools)
      .find(candidate => candidate.name === addition.toolName)!;
    log(`ui add-tool: ${addition.toolName} added=${addition.added}, repeat added=${repeated.added}, binding=${JSON.stringify(addedTool.pluginBinding)}`);
    expect(addition.added).toBe(true);
    expect(repeated.added).toBe(false);
    expect(addedTool.pluginBinding).toMatchObject({ installationId: githubInstallationId, origin: "native", capability: "write" });

    // The project is back on the legacy runtime after the rollback, and the
    // natively added tool stays executable there.
    const { planShadowToolConfig } = await import("@/src/application/services/plugin-shadow-parity");
    const planned = planShadowToolConfig("legacy", { [addition.toolName]: addedTool });
    log(`ui add-tool survives legacy mode: ${planned.toolConfig[addition.toolName]!.pluginBinding !== undefined}`);
    expect(planned.toolConfig[addition.toolName]!.pluginBinding).toEqual(addedTool.pluginBinding);

    // 6c. The execution path: a call now reaches its provider. It fails on the
    // credential, which is the OpenFang handoff point, not on a missing
    // provider, which is what the composition returned before it was wired.
    await plugins.putAdmissions([{
      installationId: githubInstallationId,
      componentDigest: githubComponent.component.metadata.bindingDigest as string,
      componentKind: "mcp", componentName: githubComponent.component.name,
      status: "admitted", policyVersion: githubEntry.policyVersion,
    }]);
    const { PluginToolRuntime } = await import("@/src/application/services/plugin-tool-runtime");
    const { resolvePluginProvider, UnreleasedCredentialResolver } = await import("@/src/infrastructure/plugins/provider-resolution");
    const runtimeDependencies: PluginToolRuntimeDependencies = {
      pluginsRepository: plugins,
      authorizationContext: { caller: "user", userId: "guest_user" },
      authorizeProject: async () => undefined,
      classifyOperation: () => "write",
      // Forwards the runtime's own per-call policy (elevated only for a
      // write OpenFang just released, see plugin-tool-runtime.ts) through to
      // the real kernel provider, exactly as di/plugins-container.ts's own
      // resolveProvider wiring does. Dropping `policy` here would silently
      // fall back to resolvePluginProvider's DEFAULT_POLICY and reintroduce
      // the gap this gate exists to catch.
      resolveProvider: async ({ component, entry: catalogEntry, binding, policy }) => resolvePluginProvider(
        { component, entry: catalogEntry, binding },
        { credentialResolver: new UnreleasedCredentialResolver(), policy },
      ),
    };
    const toolRuntime = new PluginToolRuntime(runtimeDependencies);
    const executionBinding = {
      installationId: githubInstallationId,
      pluginName: githubEntry.pluginName,
      componentDigest: githubComponent.component.metadata.bindingDigest as string,
      providerBindingId: (githubComponent.component.metadata.providerBinding as { id: string }).id,
      capability: "write" as const,
    };
    let invocationError = "none";
    try {
      await toolRuntime.invoke(executionBinding, { query: "issues" }, { projectId, operationName: "list_issues" });
    } catch (error) {
      invocationError = error instanceof Error ? error.message : "unknown";
    }
    log(`invocation reaches the provider: error=${invocationError}`);
    // The provider is no longer the blocker. What stops this unreleased call
    // is the review gate in front of it: every operation is classified write
    // until a trusted classifier exists, and the default policy sends a
    // write to review. This must be exact, not a tolerant either/or: if the
    // policy were elevated unconditionally (the Task 4-8 gap re-opened), an
    // unreleased call would sail past review and fail at the credential
    // instead, reporting "credential_missing" here too -- a set that
    // accepted both outcomes would report green on a fail-open runtime. The
    // released/unreleased pair below is only evidence that release is what
    // gates the credential if this half is exact.
    expect(invocationError).toBe("write_review_required");

    // 6d. A release configured and approving this exact call carries the
    // elevated per-call policy into the real HttpMcpProvider (threaded
    // through resolveProvider() at `runtimeDependencies.resolveProvider`
    // above, `policy` supplied at :280) -- proven end to end here, not by any
    // mocked-provider unit test. The provider's own independent write-
    // capability check now agrees with the outer gate instead of re-refusing
    // what it just admitted, so the call clears review and reaches the next
    // gate: the credential, which UnreleasedCredentialResolver never
    // releases in this composition.
    const releasedRuntime = new PluginToolRuntime({
      ...runtimeDependencies,
      releaseWrite: async () => ({ status: "approved" as const, approvalId: "3f0f8a1e-0000-4000-8000-00000000000a" }),
    });
    let releasedError = "none";
    try {
      await releasedRuntime.invoke(executionBinding, { query: "issues" }, { projectId, operationName: "list_issues" });
    } catch (error) {
      releasedError = error instanceof Error ? error.message : "unknown";
    }
    log(`released write reaches the credential: ${releasedError}`);
    expect(releasedError).toBe("credential_missing");

    const storedReceipts = await database.collection("plugin_receipts").find({}).toArray();
    log(`receipts in plugin_receipts: ${storedReceipts.length} (${receipts.map(receipt => receipt.type).join(", ")} + parity)`);

    // 7. The removal gate against the real project collection.
    const { LegacyPluginRemovalGate } = await import("@/src/application/services/legacy-plugin-removal-gate");
    const gate = new LegacyPluginRemovalGate({
      listProjectRuntimeStates: async () => (await database.collection("projects").find({}, { projection: { _id: 1, pluginRuntime: 1 } }).toArray())
        .map(document => ({ projectId: String(document._id), state: parsePluginRuntimeState((document as { pluginRuntime?: unknown }).pluginRuntime) })),
      loadMigrationRecord: id => plugins.getMigrationRecord(id),
      loadReceipt: id => plugins.getReceipt(id),
      now: () => new Date(),
    });
    const report = await gate.report();
    log(`removal gate: ready=${report.ready} reasons=[${report.reasons.join(", ")}]`);
    expect(report.ready).toBe(false);

    // 8. Shadow parity: a write-capable shadow provider is never invoked.
    const { parity: shadowParity } = await import("@/src/application/services/plugin-shadow-parity");
    let activeCalls = 0;
    let shadowCalls = 0;
    const parityReport = await shadowParity.compareAndRun({
      active: { effect: "write", invoke: async () => { activeCalls += 1; return { issue: 42 }; } },
      shadow: { effect: "write", invoke: async () => { shadowCalls += 1; throw new Error("must never run"); } },
      request: { projectId, pluginName: "github", operationName: "create_issue", input: { title: "demo", token: "sk-live-never-persisted" } },
      receipts: { putReceipt: receipt => plugins.putReceipt(receipt) },
    });
    log(`shadow parity: active=${activeCalls} shadow=${shadowCalls} execution=${parityReport.shadow.execution} comparedOutput=${parityReport.shadow.comparedOutput}`);
    expect(shadowCalls).toBe(0);

    const parityStored = await database.collection("plugin_receipts").findOne({ receiptId: parityReport.receipt.receiptId });
    const serialized = JSON.stringify(parityStored);
    log(`parity receipt persisted, contains the argument token: ${serialized.includes("sk-live-never-persisted")}`);
    expect(serialized).not.toContain("sk-live-never-persisted");
    expect(serialized).not.toContain("demo");
  }, 120_000);
});
