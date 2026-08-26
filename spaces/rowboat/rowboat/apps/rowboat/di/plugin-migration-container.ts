import { randomUUID } from "node:crypto";
import { types as utilTypes } from "node:util";
import { PINNED_PLUGIN_CATALOG_DIGEST, ZPluginMigrationRecord, type PluginCatalogLock } from "@rowboat/openai-plugin-runtime";
import { db, mongoClient } from "@/app/lib/mongodb";
import { PreviewPluginMigrationUseCase } from "@/src/application/use-cases/plugins/preview-plugin-migration.use-case";
import { ApplyPluginMigrationUseCase } from "@/src/application/use-cases/plugins/apply-plugin-migration.use-case";
import { actorTuple, migrationDigest, signMigrationConfirmation, verifyMigrationConfirmation, type MigrationConfirmationClaims } from "@/src/application/use-cases/plugins/plugin-migration.shared";
import { LegacyPluginMigration, type LegacyPluginMigrationSource, type PluginMigrationPreview } from "@/src/application/services/legacy-plugin-migration";
import { LEGACY_PLUGIN_RECIPES, buildLegacyExecutableInventory } from "@/src/application/services/legacy-plugin-recipes";
import { Auth0PluginApiAuthorizationPolicy, Auth0PluginUserSessionProvider, ExistingProjectApiKeyVerifier, JoseAuth0UserTokenVerifier } from "@/src/infrastructure/policies/auth0.plugin-api-authorization.policy";
import { MongoDBUsersRepository } from "@/src/infrastructure/repositories/mongodb.users.repository";
import { MongoDBApiKeysRepository } from "@/src/infrastructure/repositories/mongodb.api-keys.repository";
import { MongoDBProjectMembersRepository } from "@/src/infrastructure/repositories/mongodb.project-members.repository";
import { MongodbPluginsRepository, MongoPluginTransactionRunner } from "@/src/infrastructure/repositories/mongodb.plugins.repository";
import { PLUGIN_COLLECTIONS } from "@/src/infrastructure/repositories/mongodb.plugins.indexes";
import { PreviewPluginMigrationController } from "@/src/interface-adapters/controllers/plugins/preview-plugin-migration.controller";
import { ApplyPluginMigrationController } from "@/src/interface-adapters/controllers/plugins/apply-plugin-migration.controller";
import type { PluginApiIdentity } from "@/src/application/policies/plugin-api-authorization.policy";
import type { ClientSession } from "mongodb";

interface RawProject { readonly _id: string; readonly draftWorkflow?: unknown; readonly liveWorkflow?: unknown; readonly pluginMigrationPointer?: unknown }
type ReadyPreview = PluginMigrationPreview;
const migration = new LegacyPluginMigration();

function requiredSecret(): string {
  const value = process.env.PLUGIN_MIGRATION_CONFIRMATION_SECRET;
  if (value === undefined) throw new Error("migration_confirmation_secret_invalid");
  return value;
}
function adminIds(): ReadonlySet<string> {
  const raw = process.env.PLUGIN_MIGRATION_ADMIN_USER_IDS;
  if (raw === undefined || raw.length === 0 || /[\u0000-\u001f\u007f-\u009f]/u.test(raw)) return new Set();
  return new Set(raw.split(",").filter(value => /^[A-Za-z0-9._|:-]{1,256}$/.test(value)));
}
function workflowTimestamp(workflow: unknown): string {
  if (workflow === null || typeof workflow !== "object" || Array.isArray(workflow)) throw new Error("migration_project_invalid");
  const descriptor = Object.getOwnPropertyDescriptor(workflow, "lastUpdatedAt");
  if (descriptor === undefined || !("value" in descriptor) || typeof descriptor.value !== "string" || new Date(descriptor.value).toISOString() !== descriptor.value) throw new Error("migration_project_invalid");
  return descriptor.value;
}
function sourceFrom(document: RawProject): LegacyPluginMigrationSource {
  if (utilTypes.isProxy(document) || (Object.getPrototypeOf(document) !== Object.prototype && Object.getPrototypeOf(document) !== null)) throw new Error("migration_project_invalid");
  const idDescriptor = Object.getOwnPropertyDescriptor(document, "_id"); const workflowDescriptor = Object.getOwnPropertyDescriptor(document, "draftWorkflow");
  if (idDescriptor === undefined || !("value" in idDescriptor) || !idDescriptor.enumerable || typeof idDescriptor.value !== "string"
    || workflowDescriptor === undefined || !("value" in workflowDescriptor) || !workflowDescriptor.enumerable) throw new Error("migration_project_invalid");
  const projectId = idDescriptor.value; const workflow = workflowDescriptor.value;
  const updatedAt = workflowTimestamp(workflow);
  const inventory = buildLegacyExecutableInventory(workflow);
  const match = Object.values(LEGACY_PLUGIN_RECIPES).find(recipe => recipe.inventory.digest === inventory.digest);
  return Object.freeze({ projectId, sourceProjectRevision: Date.parse(updatedAt), sourceUpdatedAt: updatedAt,
    legacyCardId: match?.cardId ?? "custom-project", sourceConfiguration: workflow });
}
function previewDigest(preview: ReadyPreview): string {
  const { installations: _installations, mutationsApplied: _mutationsApplied, ...record } = preview;
  return migrationDigest("rowboat:plugin-migration-preview:v1", record);
}

async function createComposition() {
  const usersRepository = new MongoDBUsersRepository(); const apiKeysRepository = new MongoDBApiKeysRepository(); const projectMembersRepository = new MongoDBProjectMembersRepository();
  const authorization = new Auth0PluginApiAuthorizationPolicy({ pluginUserSessionProvider: new Auth0PluginUserSessionProvider({ usersRepository }),
    pluginProjectApiKeyVerifier: new ExistingProjectApiKeyVerifier({ apiKeysRepository }), pluginUserTokenVerifier: new JoseAuth0UserTokenVerifier({ usersRepository }),
    projectMembersRepository, pluginAuthEnabled: process.env.USE_AUTH === "true" });
  const pluginsRepository = new MongodbPluginsRepository({ pluginsDatabase: db, pluginTransactionRunner: new MongoPluginTransactionRunner({ pluginsMongoClient: mongoClient }) });
  let catalogCache: PluginCatalogLock | undefined;
  const getCatalog = async () => {
    const selected = catalogCache ?? await pluginsRepository.getCatalog(PINNED_PLUGIN_CATALOG_DIGEST);
    if (selected === null) throw new Error("catalog_digest_mismatch"); catalogCache = selected; return selected;
  };
  const fetchSource = async (projectId: string) => {
    const document = await db.collection<RawProject>("projects").findOne({ _id: projectId }, { projection: { _id: 1, draftWorkflow: 1, liveWorkflow: 1, pluginMigrationPointer: 1 } });
    if (document === null) throw new Error("project_not_found"); return sourceFrom(document);
  };
  const pending = new Map<string, Readonly<{ source: LegacyPluginMigrationSource; preview: ReadyPreview; catalog: PluginCatalogLock }>>();
  const buildPreview = async (projectId: string) => {
    const source = await fetchSource(projectId); const catalog = await getCatalog();
    const preview = migration.preview(source, catalog); pending.set(projectId, Object.freeze({ source, preview, catalog })); return preview;
  };
  const authorizeProject = (actor: PluginApiIdentity, projectId: string) => authorization.authorizeProject(actor, projectId);
  const authorizeAll = async (actor: PluginApiIdentity) => {
    const selected = actorTuple(actor); if (selected.actorKind !== "user" || !adminIds().has(selected.actorId)) throw new Error("forbidden");
  };
  let snapshotRows: readonly RawProject[] | undefined;
  const preview = new PreviewPluginMigrationUseCase({ authorizeProject, authorizeAll, getPinnedCatalog: getCatalog,
    listProjectsPage: async (_actor, cursor) => {
      if (snapshotRows === undefined) snapshotRows = Object.freeze(await db.collection<RawProject>("projects").find({}, { projection: { _id: 1, draftWorkflow: 1 } }).sort({ _id: 1 }).limit(100_001).toArray());
      if (snapshotRows.length > 100_000) throw new Error("migration_project_limit");
      const offset = cursor === undefined ? 0 : Number(cursor); if (!Number.isSafeInteger(offset) || offset < 0) throw new Error("migration_snapshot_invalid");
      const sources = snapshotRows.map(sourceFrom); const rows = sources.slice(offset, offset + 128); const projects = rows.map(source => ({ projectId: source.projectId, sourceProjectRevision: source.sourceProjectRevision }));
      const snapshotToken = migrationDigest("rowboat:plugin-migration-mongo-snapshot:v1", sources.map(source => ({ id: source.projectId, revision: source.sourceProjectRevision })));
      return Object.freeze({ projects, nextCursor: offset + rows.length < snapshotRows.length ? String(offset + rows.length) : null, snapshotToken });
    }, getProjectSource: fetchSource, previewProject: (source, catalog) => migration.preview(source, catalog),
    issueConfirmation: ({ actor, preview: selected, reportDigest }) => {
      const tuple = actorTuple(actor); const idempotencyKey = `migration-${selected.id}`; const issuedAt = new Date(); const expiresAt = new Date(issuedAt.getTime() + 2 * 60_000);
      const claims: MigrationConfirmationClaims = Object.freeze({ version: 1, operation: "apply_plugin_migration", ...tuple, projectId: selected.projectId,
        sourceProjectRevision: selected.sourceProjectRevision, sourceDigest: selected.sourceDigest, sourceInventoryDigest: selected.sourceInventoryDigest,
        recipeDigest: selected.recipeDigest, rollbackSnapshotDigest: selected.rollbackSnapshotDigest, targetCatalogDigest: selected.targetCatalogDigest,
        targetInstallationIds: selected.targetInstallationIds, previewDigest: previewDigest(selected as ReadyPreview), reportDigest,
        issuedAt: issuedAt.toISOString(), expiresAt: expiresAt.toISOString(), nonce: randomUUID(), idempotencyKey });
      return Object.freeze({ token: signMigrationConfirmation(claims, requiredSecret()), idempotencyKey });
    }, now: () => new Date() });

  const apply = new ApplyPluginMigrationUseCase({ authorizeProject,
    verifyConfirmation: token => verifyMigrationConfirmation(token, requiredSecret(), new Date()), previewProject: buildPreview, digestPreview: value => previewDigest(value as ReadyPreview),
    runAtomically: async operation => {
      const session = mongoClient.startSession();
      try { let output!: Awaited<ReturnType<typeof operation>>; let completed = false; await session.withTransaction(async () => { output = await operation(session); completed = true; }); if (!completed) throw new Error("migration_apply_failed"); return output; }
      catch (error) { if (error !== null && typeof error === "object" && "code" in error && error.code === 11000) throw new Error("migration_confirmation_replayed"); throw error instanceof Error && /^[a-z0-9_]+$/.test(error.message) ? error : new Error("migration_apply_failed"); }
      finally { await session.endSession(); }
    },
    writeRollbackSnapshot: async (transaction, selected) => {
      const context = pending.get(selected.projectId); if (context === undefined) throw new Error("migration_preview_stale");
      await db.collection<{ _id: string; [key: string]: unknown }>("plugin_migration_rollbacks").insertOne({ _id: selected.id, projectId: selected.projectId, sourceProjectRevision: context.source.sourceProjectRevision,
        sourceDigest: context.preview.sourceDigest, rollbackSnapshotDigest: context.preview.rollbackSnapshotDigest, draftWorkflow: context.source.sourceConfiguration }, { session: transaction as ClientSession });
    },
    writeInstallationsAndAdmissions: async (transaction, selected) => {
      const context = pending.get(selected.projectId); if (context === undefined) throw new Error("migration_preview_stale"); const session = transaction as ClientSession;
      for (const installation of context.preview.installations) {
        await db.collection(PLUGIN_COLLECTIONS.installations).insertOne({ ...installation }, { session });
        const entry = context.catalog.entries.find(item => item.pluginName === installation.pluginName); if (entry === undefined) throw new Error("migration_preview_stale");
        for (const binding of installation.providerBindings as readonly Readonly<{ componentId: string; binding: Readonly<{ componentDigest: string }> }>[]) {
          const component = entry.components.find(item => item.component.id === binding.componentId && item.component.metadata.bindingDigest === binding.binding.componentDigest);
          if (component === undefined || component.admission.status !== "admitted") throw new Error("migration_preview_stale");
          await db.collection(PLUGIN_COLLECTIONS.componentAdmissions).insertOne({ installationId: installation.id, componentDigest: binding.binding.componentDigest,
            componentKind: component.component.kind, componentName: component.component.name, status: "admitted", policyVersion: context.catalog.policyVersion }, { session });
        }
      }
    },
    writeMigrationRecord: async (transaction, selected, claims, idempotencyKey) => {
      const context = pending.get(selected.projectId); if (context === undefined) throw new Error("migration_preview_stale"); const session = transaction as ClientSession;
      const { installations: _installations, mutationsApplied: _mutationsApplied, ...raw } = context.preview;
      const record = ZPluginMigrationRecord.parse({ ...raw, status: "applied" });
      await db.collection(PLUGIN_COLLECTIONS.migrationRecords).insertOne({ ...record }, { session });
      const nonce = "nonce" in claims ? claims.nonce : migrationDigest("rowboat:migration-test-nonce:v1", claims);
      await db.collection<{ _id: string; [key: string]: unknown }>("plugin_migration_confirmations").insertOne({ _id: nonce, projectId: selected.projectId, migrationRecordId: record.id,
        idempotencyKey, actorKind: claims.actorKind, actorId: claims.actorId, previewDigest: claims.previewDigest }, { session });
      return Object.freeze({ receiptIds: Object.freeze([record.id]) });
    },
    compareAndSetProjectPointer: async (transaction, selected) => {
      const context = pending.get(selected.projectId); if (context === undefined) throw new Error("migration_preview_stale");
      const result = await db.collection<{ _id: string; [key: string]: unknown }>("projects").updateOne({ _id: selected.projectId, "draftWorkflow.lastUpdatedAt": context.source.sourceUpdatedAt, pluginMigrationPointer: { $exists: false } },
        { $set: { pluginMigrationPointer: { migrationRecordId: selected.id, sourceProjectRevision: context.source.sourceProjectRevision, catalogDigest: context.preview.targetCatalogDigest } } }, { session: transaction as ClientSession });
      if (result.modifiedCount !== 1) throw new Error("migration_preview_stale");
    } });
  return Object.freeze({ authorization, preview, apply,
    previewController: new PreviewPluginMigrationController({ authorization, useCase: preview }), applyController: new ApplyPluginMigrationController({ authorization, useCase: apply }) });
}

let composed: ReturnType<typeof createComposition> | undefined;
const composition = () => composed ??= createComposition();
export async function resolveMigrationPreviewController() { return (await composition()).previewController; }
export async function resolveMigrationApplyController() { return (await composition()).applyController; }
export async function previewAllMigrations() {
  const actorId = process.env.PLUGIN_MIGRATION_ACTOR_USER_ID; if (actorId === undefined) throw new Error("migration_all_scope_authority_required");
  return (await composition()).preview.execute({ actor: { kind: "user", userId: actorId }, scope: "all" });
}
export async function applyProjectMigration(input: Readonly<{ projectId: string; confirmationToken: string; idempotencyKey: string }>) {
  const actorId = process.env.PLUGIN_MIGRATION_ACTOR_USER_ID; if (actorId === undefined) throw new Error("migration_apply_authority_required");
  return (await composition()).apply.execute({ actor: { kind: "user", userId: actorId }, ...input });
}
