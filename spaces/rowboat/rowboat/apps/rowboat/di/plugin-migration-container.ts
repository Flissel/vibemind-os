import { randomUUID } from "node:crypto";
import { types as utilTypes } from "node:util";
import { PINNED_PLUGIN_CATALOG_DIGEST, ZPluginMigrationRecord, type PluginCatalogLock, type PluginMigrationRecord } from "@rowboat/openai-plugin-runtime";
import { db, mongoClient } from "@/app/lib/mongodb";
import { migrationPreviewProjectProjection, PreviewPluginMigrationUseCase, type PreparedMigrationPreview } from "@/src/application/use-cases/plugins/preview-plugin-migration.use-case";
import { ApplyPluginMigrationUseCase, type PreparedMigrationInvocation } from "@/src/application/use-cases/plugins/apply-plugin-migration.use-case";
import { actorTuple, canonical, migrationDigest, signMigrationConfirmation, verifyMigrationConfirmation, type MigrationConfirmationClaims } from "@/src/application/use-cases/plugins/plugin-migration.shared";
import { LegacyPluginMigration, type PluginMigrationPreview } from "@/src/application/services/legacy-plugin-migration";
import { LEGACY_PLUGIN_RECIPES, sourceDriftBlocker } from "@/src/application/services/legacy-plugin-recipes";
import { assertMigrationProjectStateUnchanged, captureMigrationProjectManifestCandidate, captureMigrationProjectManifestEntry, captureMigrationProjectState, migrationProjectPointerCasFilter, parseMigrationPointerRecord, type MigrationProjectState } from "@/src/application/services/plugin-migration-project-state";
import { executeMigrationManifest, materializeMigrationProjectManifest, type MigrationProjectManifestEntry } from "@/src/application/services/plugin-migration-keyset-snapshot";
import { abortMigrationTransaction, migrationTransactionFailure } from "@/src/application/services/plugin-migration-transaction";
import { Auth0PluginApiAuthorizationPolicy, Auth0PluginUserSessionProvider, ExistingProjectApiKeyVerifier, JoseAuth0UserTokenVerifier } from "@/src/infrastructure/policies/auth0.plugin-api-authorization.policy";
import { MongoDBUsersRepository } from "@/src/infrastructure/repositories/mongodb.users.repository";
import { MongoDBApiKeysRepository } from "@/src/infrastructure/repositories/mongodb.api-keys.repository";
import { MongoDBProjectMembersRepository } from "@/src/infrastructure/repositories/mongodb.project-members.repository";
import { MongodbPluginsRepository, MongoPluginTransactionRunner, deserializePluginInstallationDocument, serializePluginAdmissionDocument, serializePluginInstallationDocument } from "@/src/infrastructure/repositories/mongodb.plugins.repository";
import { PLUGIN_COLLECTIONS } from "@/src/infrastructure/repositories/mongodb.plugins.indexes";
import { PreviewPluginMigrationController } from "@/src/interface-adapters/controllers/plugins/preview-plugin-migration.controller";
import { ApplyPluginMigrationController } from "@/src/interface-adapters/controllers/plugins/apply-plugin-migration.controller";
import type { PluginApiIdentity } from "@/src/application/policies/plugin-api-authorization.policy";
import type { PluginComponentAdmission } from "@/src/application/repositories/plugins.repository.interface";
import type { ClientSession, Collection, Document } from "mongodb";

type RawProject = { _id: string; [key: string]: unknown };
interface PreparedContext {
  readonly kind: "plugin_migration_context_v3"; readonly state: MigrationProjectState; readonly manifestEntry: MigrationProjectManifestEntry; readonly preview: PluginMigrationPreview;
  readonly catalog: PluginCatalogLock; readonly admissions: readonly PluginComponentAdmission[];
}
const migration = new LegacyPluginMigration();
const PROJECT_PROJECTION = Object.freeze({ _id: 1, createdAt: 1, lastUpdatedAt: 1, version: 1, draftWorkflow: 1, liveWorkflow: 1, pluginMigrationPointer: 1 });
const MANIFEST_PROJECTION = PROJECT_PROJECTION;

function deepFreeze<T>(value: T): T {
  if (Array.isArray(value)) { for (const item of value) deepFreeze(item); return Object.freeze(value) as T; }
  if (value !== null && typeof value === "object") { for (const item of Object.values(value as object)) deepFreeze(item); return Object.freeze(value); }
  return value;
}
function requiredSecret(): string { const value = process.env.PLUGIN_MIGRATION_CONFIRMATION_SECRET; if (value === undefined) throw new Error("migration_confirmation_secret_invalid"); return value; }
function adminIds(): ReadonlySet<string> {
  const raw = process.env.PLUGIN_MIGRATION_ADMIN_USER_IDS;
  if (raw === undefined || raw.length === 0 || /[\u0000-\u001f\u007f-\u009f]/u.test(raw)) return new Set();
  return new Set(raw.split(",").filter(value => /^[A-Za-z0-9._|:-]{1,256}$/.test(value)));
}
function previewDigest(preview: Readonly<{ id: string; projectId: string; recipeId: string; recipeDigest: string; sourceProjectRevision: number; sourceDigest: string;
  sourceInventoryDigest: string; targetCatalogDigest: string; targetInstallationIds: readonly string[]; rollbackSnapshotDigest: string }>): string {
  return migrationDigest("rowboat:plugin-migration-preview:v2", { id: preview.id, projectId: preview.projectId, recipeId: preview.recipeId,
    recipeDigest: preview.recipeDigest, sourceProjectRevision: preview.sourceProjectRevision, sourceDigest: preview.sourceDigest,
    sourceInventoryDigest: preview.sourceInventoryDigest, targetCatalogDigest: preview.targetCatalogDigest,
    targetInstallationIds: preview.targetInstallationIds, rollbackSnapshotDigest: preview.rollbackSnapshotDigest });
}
function admissionsFrom(preview: PluginMigrationPreview, catalog: PluginCatalogLock): readonly PluginComponentAdmission[] {
  const admissions: PluginComponentAdmission[] = [];
  for (const installation of preview.installations) {
    const entry = catalog.entries.find(candidate => candidate.pluginName === installation.pluginName);
    if (entry === undefined) throw new Error("migration_preview_stale");
    for (const binding of installation.providerBindings ?? []) {
      const selected = entry.components.find(item => item.component.id === binding.componentId && item.component.metadata.bindingDigest === binding.binding.componentDigest);
      if (selected === undefined || selected.admission.status !== "admitted") throw new Error("migration_preview_stale");
      admissions.push(serializePluginAdmissionDocument({ installationId: installation.id, componentDigest: binding.binding.componentDigest,
        componentKind: selected.component.kind, componentName: selected.component.name, status: "admitted", policyVersion: catalog.policyVersion }));
    }
  }
  admissions.sort((left, right) => `${left.installationId}\0${left.componentDigest}`.localeCompare(`${right.installationId}\0${right.componentDigest}`));
  return Object.freeze(admissions);
}
function blockDiverged(preview: PluginMigrationPreview): PluginMigrationPreview {
  const blocker = sourceDriftBlocker();
  return deepFreeze({ ...preview, status: "blocked" as const, blockers: [blocker], targetInstallationIds: [], installations: [] });
}
async function prepareDocument(document: RawProject, manifestEntry: MigrationProjectManifestEntry, catalog: PluginCatalogLock, loadRecord: (id: string) => Promise<PluginMigrationRecord | null>): Promise<PreparedContext> {
  const state = captureMigrationProjectState(document);
  const match = Object.values(LEGACY_PLUGIN_RECIPES).find(recipe => recipe.inventory.digest === state.liveInventoryDigest);
  let existing: PluginMigrationRecord | undefined;
  if (state.pointer !== null) {
    const record = await loadRecord(state.pointer.migrationRecordId);
    if (record === null || record.id !== state.pointer.migrationRecordId || record.projectId !== state.projectId || record.status !== "applied"
      || record.targetCatalogDigest !== state.pointer.catalogDigest || record.sourceProjectRevision !== state.pointer.sourceProjectRevision
      || state.pointer.sourceProjectRevision !== state.sourceProjectRevision || state.pointer.sourceStateDigest !== state.stateDigest) throw new Error("migration_pointer_invalid");
    existing = record;
  }
  const source = Object.freeze({ projectId: state.projectId, sourceProjectRevision: state.sourceProjectRevision,
    sourceUpdatedAt: state.sourceTimestamp, sourceStateDigest: state.stateDigest, legacyCardId: match?.cardId ?? "custom-project", sourceConfiguration: state.liveWorkflow });
  let preview = migration.preview(source, catalog, existing);
  if (state.draftInventoryDigest !== state.liveInventoryDigest) {
    if (existing !== undefined) throw new Error("migration_pointer_invalid");
    preview = blockDiverged(preview);
  }
  const context = { kind: "plugin_migration_context_v3" as const, state, manifestEntry, preview, catalog, admissions: admissionsFrom(preview, catalog) };
  return deepFreeze(context);
}
function context(input: unknown): PreparedContext {
  if (input === null || typeof input !== "object" || utilTypes.isProxy(input) || Object.getPrototypeOf(input) !== Object.prototype || !Object.isFrozen(input)) throw new Error("migration_context_invalid");
  const keys = Reflect.ownKeys(input); if (keys.length !== 6 || keys.some(key => typeof key !== "string" || !["kind","state","manifestEntry","preview","catalog","admissions"].includes(key))) throw new Error("migration_context_invalid");
  const selected = input as PreparedContext; if (selected.kind !== "plugin_migration_context_v3" || !Object.isFrozen(selected.state) || !Object.isFrozen(selected.manifestEntry)
    || !Object.isFrozen(selected.preview) || !Object.isFrozen(selected.catalog) || !Object.isFrozen(selected.admissions)) throw new Error("migration_context_invalid");
  return selected;
}
function asPublic(prepared: PreparedContext): PreparedMigrationPreview { return Object.freeze({ preview: prepared.preview }); }
function blockedPublic(entry: MigrationProjectManifestEntry, catalog: PluginCatalogLock, code: "snapshot_changed" | "migration_project_invalid" | "migration_pointer_invalid"): PreparedMigrationPreview {
  const sourceDigest = migrationDigest("rowboat:plugin-migration-invalid-project:v1", { projectId: entry.projectId, scalarIdentityDigest: entry.scalarIdentityDigest, code });
  const preview = Object.freeze({ id: sourceDigest, projectId: entry.projectId, sourceProjectRevision: 0, sourceDigest,
    sourceInventoryDigest: migrationDigest("rowboat:plugin-migration-invalid-inventory:v1", { projectId: entry.projectId, code }),
    recipeId: "unresolved:v1", recipeDigest: migrationDigest("rowboat:plugin-migration-unresolved-recipe:v1", { code }),
    rollbackSnapshotDigest: migrationDigest("rowboat:plugin-migration-unavailable-rollback:v1", { projectId: entry.projectId, code }),
    targetCatalogDigest: catalog.catalogDigest, targetInstallationIds: Object.freeze([]), status: "blocked" as const,
    blockers: Object.freeze([Object.freeze({ code })]) });
  return Object.freeze({ preview });
}
function mongoCode(error: unknown): number | null {
  if (error === null || typeof error !== "object" || utilTypes.isProxy(error)) return null;
  const descriptor = Object.getOwnPropertyDescriptor(error, "code"); return descriptor !== undefined && "value" in descriptor && descriptor.value === 11000 ? 11000 : null;
}
async function insertExact(collection: Collection, document: Document, session: ClientSession, conflict: string): Promise<void> {
  try { await collection.insertOne(document, { session }); } catch (error) { if (mongoCode(error) === 11000) throw new Error(conflict); throw new Error("migration_repository_failed"); }
}
function exact(left: unknown, right: unknown): boolean { return canonical(left) === canonical(right); }

async function createComposition() {
  const usersRepository = new MongoDBUsersRepository(); const apiKeysRepository = new MongoDBApiKeysRepository(); const projectMembersRepository = new MongoDBProjectMembersRepository();
  const authorization = new Auth0PluginApiAuthorizationPolicy({ pluginUserSessionProvider: new Auth0PluginUserSessionProvider({ usersRepository }),
    pluginProjectApiKeyVerifier: new ExistingProjectApiKeyVerifier({ apiKeysRepository }), pluginUserTokenVerifier: new JoseAuth0UserTokenVerifier({ usersRepository }),
    projectMembersRepository, pluginAuthEnabled: process.env.USE_AUTH === "true" });
  const pluginsRepository = new MongodbPluginsRepository({ pluginsDatabase: db, pluginTransactionRunner: new MongoPluginTransactionRunner({ pluginsMongoClient: mongoClient }) });
  const loadCatalog = async () => { const selected = await pluginsRepository.getCatalog(PINNED_PLUGIN_CATALOG_DIGEST); if (selected === null) throw new Error("catalog_digest_mismatch"); return deepFreeze(selected); };
  const prepareOne = async (projectId: string, catalog: PluginCatalogLock, expected?: MigrationProjectManifestEntry): Promise<PreparedContext> => {
    const session = mongoClient.startSession();
    try {
      session.startTransaction({ readConcern: { level: "snapshot" } });
      const document = await db.collection<RawProject>("projects").findOne({ _id: projectId }, { projection: PROJECT_PROJECTION, session });
      if (document === null) throw new Error("project_not_found");
      const manifestEntry = captureMigrationProjectManifestEntry(document);
      if (expected !== undefined && (manifestEntry.projectId !== expected.projectId || manifestEntry.scalarIdentityDigest !== expected.scalarIdentityDigest
        || manifestEntry.stateDigest !== expected.stateDigest)) throw new Error("migration_snapshot_changed");
      const prepared = await prepareDocument(document, manifestEntry, catalog, async id => {
        const raw = await db.collection(PLUGIN_COLLECTIONS.migrationRecords).findOne({ id }, { projection: { _id: 0 }, session });
        return raw === null ? null : parseMigrationPointerRecord(raw);
      });
      await session.commitTransaction(); return prepared;
    } catch (error) { if (session.inTransaction()) await session.abortTransaction(); throw error; } finally { await session.endSession(); }
  };
  const authorizeProject = (actor: PluginApiIdentity, projectId: string) => authorization.authorizeProject(actor, projectId);
  const authorizeAll = async (actor: PluginApiIdentity) => { const selected = actorTuple(actor); if (selected.actorKind !== "user" || !adminIds().has(selected.actorId)) throw new Error("forbidden"); };
  const scanAllProjects = async (actor: PluginApiIdentity, visit: (prepared: PreparedMigrationPreview) => Promise<void>) => {
    const scan = await executeMigrationManifest({
      materialize: async () => {
        const session = mongoClient.startSession();
        try {
          session.startTransaction({ readConcern: { level: "snapshot" } });
          const manifest = await materializeMigrationProjectManifest({ readPage: async (cursor, limit) => {
            const filter = cursor === undefined ? {} : { _id: { $gt: cursor } };
            const documents = await db.collection<RawProject>("projects").find(filter, { projection: MANIFEST_PROJECTION, session }).sort({ _id: 1 }).limit(limit).toArray();
            return documents.map(captureMigrationProjectManifestCandidate);
          }, now: () => Date.now(), pageSize: 32, maximumProjects: 1_000, maximumBytes: 256_000,
          maximumCapturedBytes: 64 * 1024 * 1024, maximumDurationMs: 5_000 });
          await session.commitTransaction(); return manifest;
        } catch (error) { if (session.inTransaction()) await session.abortTransaction(); throw error; } finally { await session.endSession(); }
      },
      loadShared: async () => { const catalog = await loadCatalog(); return Object.freeze({ value: catalog, retainedBytes: Buffer.byteLength(JSON.stringify(catalog), "utf8") }); },
      prepare: async (entry, catalog) => { await authorizeAll(actor); const prepared = await prepareOne(entry.projectId, catalog, entry);
        return Object.freeze({ value: asPublic(prepared), scalarIdentityDigest: prepared.manifestEntry.scalarIdentityDigest, stateDigest: prepared.manifestEntry.stateDigest }); },
      blocked: (entry, code, catalog) => blockedPublic(entry, catalog, code), visit,
      preparedBytes: value => Buffer.byteLength(JSON.stringify(migrationPreviewProjectProjection(value.preview)), "utf8"),
      now: () => Date.now(), maximumPreparedBytes: 32 * 1024 * 1024, maximumDurationMs: 30_000,
    });
    return Object.freeze({ catalog: scan.shared, snapshotToken: scan.snapshotToken });
  };
  const preview = new PreviewPluginMigrationUseCase({ authorizeProject, authorizeAll, prepareProject: async projectId => { const catalog = await loadCatalog();
    return Object.freeze({ prepared: asPublic(await prepareOne(projectId, catalog)), catalog }); }, scanAllProjects,
    issueConfirmation: ({ actor, preview: selected, reportDigest }) => {
      const tuple = actorTuple(actor); const idempotencyKey = `migration-${selected.id}`; const issuedAt = new Date(); const expiresAt = new Date(issuedAt.getTime() + 120_000);
      const claims: MigrationConfirmationClaims = Object.freeze({ version: 1, operation: "apply_plugin_migration", ...tuple, projectId: selected.projectId,
        sourceProjectRevision: selected.sourceProjectRevision, sourceDigest: selected.sourceDigest, sourceInventoryDigest: selected.sourceInventoryDigest,
        recipeDigest: selected.recipeDigest, rollbackSnapshotDigest: selected.rollbackSnapshotDigest, targetCatalogDigest: selected.targetCatalogDigest,
        targetInstallationIds: selected.targetInstallationIds, previewDigest: previewDigest(selected), reportDigest,
        issuedAt: issuedAt.toISOString(), expiresAt: expiresAt.toISOString(), nonce: randomUUID(), idempotencyKey });
      return Object.freeze({ token: signMigrationConfirmation(claims, requiredSecret()), idempotencyKey });
    }, now: () => new Date() });
  const apply = new ApplyPluginMigrationUseCase({ authorizeProject, verifyConfirmation: token => verifyMigrationConfirmation(token, requiredSecret(), new Date()),
    prepareProject: async projectId => { const catalog = await loadCatalog(); const prepared = await prepareOne(projectId, catalog); return Object.freeze({ preview: prepared.preview, context: prepared }); },
    digestPreview: previewDigest,
    applyAtomically: async (rawContext, claims) => {
      const prepared = context(rawContext); const session = mongoClient.startSession(); let result: { receiptIds: readonly string[]; replayed: boolean; mutationCount: number; generatedAt: string } | undefined;
      const payloadDigest = migrationDigest("rowboat:plugin-migration-apply-payload:v2", { claims, previewDigest: previewDigest(prepared.preview), stateDigest: prepared.state.stateDigest });
      try {
        session.startTransaction();
        const idempotencyCollection = db.collection("plugin_migration_idempotency");
        const prior = await idempotencyCollection.findOne({ _id: claims.idempotencyKey } as unknown as Document, { session });
        if (prior !== null) {
          if (prior.payloadDigest !== payloadDigest || prior.projectId !== prepared.state.projectId || prior.migrationRecordId !== prepared.preview.id
            || !Array.isArray(prior.receiptIds) || prior.receiptIds.length !== 1 || prior.receiptIds[0] !== prepared.preview.id || typeof prior.generatedAt !== "string") throw new Error("migration_idempotency_conflict");
          if (prepared.state.pointer?.migrationRecordId !== prepared.preview.id || prepared.preview.status !== "applied") throw new Error("migration_idempotency_conflict");
          result = { receiptIds: Object.freeze([prepared.preview.id]), replayed: true, mutationCount: 0, generatedAt: prior.generatedAt };
          await session.commitTransaction(); return result;
        }
        if (prepared.state.pointer !== null) throw new Error("migration_idempotency_conflict");
        if (await db.collection<{ _id: string }>("plugin_migration_nonces").findOne({ _id: claims.nonce }, { session }) !== null) throw new Error("migration_confirmation_replayed");
        const currentDocument = await db.collection<RawProject>("projects").findOne({ _id: prepared.state.projectId }, { projection: PROJECT_PROJECTION, session });
        if (currentDocument === null) throw new Error("migration_pointer_conflict");
        const currentState = captureMigrationProjectState(currentDocument);
        assertMigrationProjectStateUnchanged(prepared.state, currentState);
        if (currentState.pointer !== null) throw new Error("migration_pointer_conflict");
        await insertExact(db.collection("plugin_migration_rollbacks"), { _id: prepared.preview.id, ...prepared.state.rollbackSnapshot,
          rollbackSnapshotDigest: prepared.preview.rollbackSnapshotDigest }, session, "migration_rollback_conflict");
        for (const installation of prepared.preview.installations) {
          const installations = db.collection(PLUGIN_COLLECTIONS.installations);
          if (await installations.findOne({ $or: [{ id: installation.id }, { projectId: installation.projectId, pluginName: installation.pluginName }] }, { session }) !== null) throw new Error("migration_installation_conflict");
          const stored = serializePluginInstallationDocument(installation); await insertExact(installations, { ...stored }, session, "migration_installation_conflict");
          const readBack = await installations.findOne({ id: installation.id }, { projection: { _id: 0 }, session });
          if (readBack === null || !exact(deserializePluginInstallationDocument(readBack), installation)) throw new Error("migration_installation_conflict");
        }
        for (const admission of prepared.admissions) {
          const admissions = db.collection(PLUGIN_COLLECTIONS.componentAdmissions);
          if (await admissions.findOne({ installationId: admission.installationId, componentDigest: admission.componentDigest }, { session }) !== null) throw new Error("migration_admission_conflict");
          await insertExact(admissions, { ...serializePluginAdmissionDocument(admission) }, session, "migration_admission_conflict");
          const readBack = await admissions.findOne({ installationId: admission.installationId, componentDigest: admission.componentDigest }, { projection: { _id: 0 }, session });
          if (readBack === null || !exact(serializePluginAdmissionDocument(readBack), admission)) throw new Error("migration_admission_conflict");
        }
        const { installations: _installations, mutationsApplied: _mutationsApplied, ...rawRecord } = prepared.preview;
        const record = ZPluginMigrationRecord.parse({ ...rawRecord, status: "applied" });
        if (await db.collection(PLUGIN_COLLECTIONS.migrationRecords).findOne({ id: record.id }, { session }) !== null) throw new Error("migration_record_conflict");
        await insertExact(db.collection(PLUGIN_COLLECTIONS.migrationRecords), { ...record }, session, "migration_record_conflict");
        await insertExact(db.collection("plugin_migration_nonces"), { _id: claims.nonce, projectId: claims.projectId, idempotencyKey: claims.idempotencyKey }, session, "migration_confirmation_replayed");
        const generatedAt = new Date().toISOString();
        await insertExact(idempotencyCollection, { _id: claims.idempotencyKey, payloadDigest, projectId: claims.projectId,
          migrationRecordId: record.id, receiptIds: [record.id], generatedAt }, session, "migration_idempotency_conflict");
        const updated = await db.collection<RawProject>("projects").updateOne(migrationProjectPointerCasFilter(prepared.state), { $set: { pluginMigrationPointer: { migrationRecordId: record.id,
            sourceProjectRevision: prepared.state.sourceProjectRevision, sourceStateDigest: prepared.state.stateDigest, catalogDigest: record.targetCatalogDigest } } }, { session });
        if (updated.modifiedCount !== 1) throw new Error("migration_pointer_conflict");
        result = { receiptIds: Object.freeze([record.id]), replayed: false, mutationCount: 4, generatedAt };
        await session.commitTransaction(); return result;
      } catch (error) {
        await abortMigrationTransaction(session);
        const retryableExactConflict = error instanceof Error && ["migration_rollback_conflict", "migration_installation_conflict", "migration_admission_conflict",
          "migration_record_conflict", "migration_confirmation_replayed", "migration_idempotency_conflict"].includes(error.message);
        if (retryableExactConflict) {
          const prior = await db.collection("plugin_migration_idempotency").findOne({ _id: claims.idempotencyKey } as unknown as Document);
          const project = await db.collection<RawProject>("projects").findOne({ _id: prepared.state.projectId }, { projection: PROJECT_PROJECTION });
          const selectedPointer = project === null ? null : captureMigrationProjectState(project).pointer;
          if (prior !== null && prior.payloadDigest === payloadDigest && prior.projectId === prepared.state.projectId
            && prior.migrationRecordId === prepared.preview.id && Array.isArray(prior.receiptIds) && prior.receiptIds.length === 1
            && prior.receiptIds[0] === prepared.preview.id && typeof prior.generatedAt === "string" && selectedPointer?.migrationRecordId === prepared.preview.id) {
            return { receiptIds: Object.freeze([prepared.preview.id]), replayed: true, mutationCount: 0, generatedAt: prior.generatedAt };
          }
        }
        throw migrationTransactionFailure(error);
      } finally { await session.endSession(); }
    } });
  return Object.freeze({ preview, apply, previewController: new PreviewPluginMigrationController({ authorization, useCase: preview }),
    applyController: new ApplyPluginMigrationController({ authorization, useCase: apply }) });
}

export async function resolveMigrationPreviewController() { return (await createComposition()).previewController; }
export async function resolveMigrationApplyController() { return (await createComposition()).applyController; }
export async function previewAllMigrations() {
  const actorId = process.env.PLUGIN_MIGRATION_ACTOR_USER_ID; if (actorId === undefined) throw new Error("migration_all_scope_authority_required");
  return (await createComposition()).preview.execute({ actor: { kind: "user", userId: actorId }, scope: "all" });
}
export async function applyProjectMigration(input: Readonly<{ projectId: string; confirmationToken: string }>) {
  const actorId = process.env.PLUGIN_MIGRATION_ACTOR_USER_ID; if (actorId === undefined) throw new Error("migration_apply_authority_required");
  return (await createComposition()).apply.execute({ actor: { kind: "user", userId: actorId }, ...input });
}
