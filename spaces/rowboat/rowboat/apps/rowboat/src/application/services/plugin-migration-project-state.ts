import { migrationDigest } from "../use-cases/plugins/plugin-migration.shared";
import { buildLegacyExecutableInventory } from "./legacy-plugin-recipes";
import { captureMigrationJson } from "./legacy-plugin-migration";

const PROJECT_KEYS = Object.freeze(["_id", "draftWorkflow", "lastUpdatedAt", "liveWorkflow", "pluginMigrationPointer", "version"]);
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const SHA = /^[a-f0-9]{64}$/;
export interface MigrationPointer {
  readonly migrationRecordId: string; readonly catalogDigest: string; readonly sourceProjectRevision: number; readonly sourceStateDigest: string;
}
export interface MigrationProjectState {
  readonly projectId: string; readonly topLevelUpdatedAt: string; readonly version: number | null;
  readonly draftWorkflow: unknown; readonly liveWorkflow: unknown; readonly draftUpdatedAt: string; readonly liveUpdatedAt: string;
  readonly draftInventoryDigest: string; readonly liveInventoryDigest: string; readonly sourceProjectRevision: number;
  readonly pointer: MigrationPointer | null; readonly stateDigest: string; readonly rollbackSnapshot: Readonly<Record<string, unknown>>;
}
function iso(value: unknown, optional = false): string | null {
  if (value === undefined && optional) return null;
  if (typeof value !== "string" || Number.isNaN(Date.parse(value)) || new Date(value).toISOString() !== value) throw new Error("migration_project_invalid");
  return value;
}
function workflowUpdatedAt(value: unknown): string {
  if (value === null || typeof value !== "object" || Array.isArray(value)) throw new Error("migration_project_invalid");
  const descriptor = Object.getOwnPropertyDescriptor(value, "lastUpdatedAt");
  if (descriptor === undefined || !("value" in descriptor) || !descriptor.enumerable) throw new Error("migration_project_invalid");
  return iso(descriptor.value) as string;
}
function pointer(value: unknown): MigrationPointer | null {
  if (value === undefined || value === null) return null;
  if (typeof value !== "object" || Array.isArray(value) || (Object.getPrototypeOf(value) !== Object.prototype && Object.getPrototypeOf(value) !== null)) throw new Error("migration_pointer_invalid");
  const record = value as Readonly<Record<string, unknown>>;
  if (Object.keys(record).sort().join("\0") !== ["catalogDigest", "migrationRecordId", "sourceProjectRevision", "sourceStateDigest"].join("\0")
    || typeof record.migrationRecordId !== "string" || !UUID.test(record.migrationRecordId)
    || typeof record.catalogDigest !== "string" || !SHA.test(record.catalogDigest)
    || !Number.isSafeInteger(record.sourceProjectRevision) || (record.sourceProjectRevision as number) < 0
    || typeof record.sourceStateDigest !== "string" || !SHA.test(record.sourceStateDigest)) throw new Error("migration_pointer_invalid");
  return Object.freeze(record as unknown as MigrationPointer);
}
function deepFreeze<T>(value: T): T {
  if (Array.isArray(value)) { for (const item of value) deepFreeze(item); return Object.freeze(value) as T; }
  if (value !== null && typeof value === "object") { for (const item of Object.values(value as object)) deepFreeze(item); return Object.freeze(value); }
  return value;
}
export function captureMigrationProjectState(input: unknown): MigrationProjectState {
  const captured = captureMigrationJson(input);
  if (captured === null || typeof captured !== "object" || Array.isArray(captured)) throw new Error("migration_project_invalid");
  const record = captured as Readonly<Record<string, unknown>>;
  if (Object.keys(record).some(key => !PROJECT_KEYS.includes(key))) throw new Error("migration_project_invalid");
  if (typeof record._id !== "string" || !UUID.test(record._id) || record.draftWorkflow === undefined || record.liveWorkflow === undefined) throw new Error("migration_project_invalid");
  const topLevelUpdatedAt = iso(record.lastUpdatedAt, false) as string;
  const version = record.version === undefined ? null : record.version;
  if (version !== null && (!Number.isSafeInteger(version) || (version as number) < 0)) throw new Error("migration_project_invalid");
  const selectedPointer = pointer(record.pluginMigrationPointer);
  const draftInventoryDigest = buildLegacyExecutableInventory(record.draftWorkflow).digest;
  const liveInventoryDigest = buildLegacyExecutableInventory(record.liveWorkflow).digest;
  // The project contract has no independent revision counter. Its authoritative
  // numeric source revision is the validated top-level timestamp in epoch ms;
  // stateDigest supplies collision resistance for edits within the same ms.
  const base = Object.freeze({ projectId: record._id, topLevelUpdatedAt, version: version as number | null,
    draftWorkflow: record.draftWorkflow, liveWorkflow: record.liveWorkflow, draftUpdatedAt: workflowUpdatedAt(record.draftWorkflow),
    liveUpdatedAt: workflowUpdatedAt(record.liveWorkflow), draftInventoryDigest, liveInventoryDigest,
    sourceProjectRevision: Date.parse(topLevelUpdatedAt as string), pointer: selectedPointer });
  const stateDigest = migrationDigest("rowboat:plugin-migration-project-state:v1", { projectId: base.projectId, topLevelUpdatedAt,
    version: base.version, draftWorkflow: base.draftWorkflow, liveWorkflow: base.liveWorkflow,
    draftUpdatedAt: base.draftUpdatedAt, liveUpdatedAt: base.liveUpdatedAt,
    draftInventoryDigest, liveInventoryDigest, sourceProjectRevision: base.sourceProjectRevision });
  const rollbackSnapshot = Object.freeze({ projectId: base.projectId, topLevelUpdatedAt, version: base.version,
    draftWorkflow: base.draftWorkflow, liveWorkflow: base.liveWorkflow, pointer: base.pointer, stateDigest });
  return deepFreeze({ ...base, stateDigest, rollbackSnapshot });
}
