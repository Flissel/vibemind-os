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

    const boundTool = ((persisted as { liveWorkflow: { tools: Record<string, unknown>[] } }).liveWorkflow.tools)[boundOrdinal]!;
    log(`materialized: tool "${String(boundTool.name)}" -> ${JSON.stringify(boundTool.pluginBinding)}`);
    expect(boundTool.pluginBinding).toEqual({ installationId, pluginName: target.pluginName, componentDigest, providerBindingId: "slack.app", capability: "write" });
    expect(cutover.workflowsWritten).toBe(true);
    const untouched = ((persisted as { liveWorkflow: { tools: Record<string, unknown>[] } }).liveWorkflow.tools).filter((tool, index) => index !== boundOrdinal && "pluginBinding" in tool);
    expect(untouched).toEqual([]);

    // 6. Rollback, and proof that it never touched the legacy workflow.
    const rollback = await service.execute({ identity, projectId, mode: "legacy", expectedRevision: 2 });
    const afterRollback = await database.collection("projects").findOne({ _id: projectId } as never);
    log(`openai -> legacy  OK   (rolledBack=${rollback.rolledBack}, workflows restored=${rollback.workflowsWritten}, revision ${rollback.revision})`);
    expect((afterRollback as { draftWorkflow?: unknown }).draftWorkflow).toEqual(workflow);
    expect((afterRollback as { liveWorkflow?: unknown }).liveWorkflow).toEqual(workflow);
    expect(((afterRollback as { liveWorkflow: { tools: Record<string, unknown>[] } }).liveWorkflow.tools).some(tool => "pluginBinding" in tool)).toBe(false);
    log("legacy workflow restored byte-identical from the retained snapshot, no binding left");

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
