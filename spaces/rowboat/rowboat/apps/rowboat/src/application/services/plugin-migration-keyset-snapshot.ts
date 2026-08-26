import { createHash } from "node:crypto";
import { types as utilTypes } from "node:util";

const SHA = /^[a-f0-9]{64}$/;
const PROJECT_ID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
export interface MigrationProjectManifestEntry { readonly projectId: string; readonly scalarIdentityDigest: string }
interface MaterializedManifest { readonly entries: readonly MigrationProjectManifestEntry[]; readonly snapshotToken: string; readonly pageReads?: number; readonly manifestBytes?: number }

function entry(input: unknown): MigrationProjectManifestEntry {
  if (input === null || typeof input !== "object" || Array.isArray(input) || utilTypes.isProxy(input)
    || (Object.getPrototypeOf(input) !== Object.prototype && Object.getPrototypeOf(input) !== null)) throw new Error("migration_snapshot_invalid");
  const keys = Object.keys(input).sort(); if (keys.join("\0") !== "projectId\0scalarIdentityDigest") throw new Error("migration_snapshot_invalid");
  const projectId = Object.getOwnPropertyDescriptor(input, "projectId"); const scalarIdentityDigest = Object.getOwnPropertyDescriptor(input, "scalarIdentityDigest");
  if (projectId === undefined || !("value" in projectId) || !projectId.enumerable || typeof projectId.value !== "string" || !PROJECT_ID.test(projectId.value)
    || scalarIdentityDigest === undefined || !("value" in scalarIdentityDigest) || !scalarIdentityDigest.enumerable || typeof scalarIdentityDigest.value !== "string" || !SHA.test(scalarIdentityDigest.value)) throw new Error("migration_snapshot_invalid");
  return Object.freeze({ projectId: projectId.value, scalarIdentityDigest: scalarIdentityDigest.value });
}

export async function materializeMigrationProjectManifest(dependencies: Readonly<{
  readPage: (afterProjectId: string | undefined, limit: number) => Promise<readonly unknown[]>;
  now: () => number; pageSize?: number; maximumProjects?: number; maximumBytes?: number; maximumDurationMs?: number;
}>): Promise<Readonly<{ entries: readonly MigrationProjectManifestEntry[]; snapshotToken: string; pageReads: number; manifestBytes: number }>> {
  const pageSize = dependencies.pageSize ?? 128; const maximumProjects = dependencies.maximumProjects ?? 10_000;
  const maximumBytes = dependencies.maximumBytes ?? 2_000_000; const maximumDurationMs = dependencies.maximumDurationMs ?? 5_000;
  if (!Number.isSafeInteger(pageSize) || pageSize < 1 || pageSize > 512 || !Number.isSafeInteger(maximumProjects) || maximumProjects < 1 || maximumProjects > 10_000
    || !Number.isSafeInteger(maximumBytes) || maximumBytes < 1 || maximumBytes > 2_000_000 || !Number.isSafeInteger(maximumDurationMs) || maximumDurationMs < 1 || maximumDurationMs > 30_000) throw new Error("migration_snapshot_invalid");
  const startedAt = dependencies.now(); if (!Number.isFinite(startedAt)) throw new Error("migration_snapshot_invalid");
  const entries: MigrationProjectManifestEntry[] = []; const hash = createHash("sha256"); let cursor: string | undefined; let pageReads = 0; let manifestBytes = 2;
  while (true) {
    const rawPage = await dependencies.readPage(cursor, pageSize + 1); pageReads += 1;
    if (dependencies.now() - startedAt > maximumDurationMs) throw new Error("migration_manifest_limit");
    if (!Array.isArray(rawPage) || utilTypes.isProxy(rawPage) || rawPage.length > pageSize + 1) throw new Error("migration_snapshot_invalid");
    if (rawPage.length > pageSize && entries.length + pageSize >= maximumProjects) throw new Error("migration_project_limit");
    const page = rawPage.length > pageSize ? rawPage.slice(0, pageSize) : rawPage;
    if (page.length === 0) break;
    for (const candidate of page) {
      const selected = entry(candidate);
      if (cursor !== undefined && selected.projectId.localeCompare(cursor) <= 0) throw new Error("migration_snapshot_invalid");
      if (entries.length >= maximumProjects) throw new Error("migration_project_limit");
      const bytes = Buffer.byteLength(JSON.stringify(selected), "utf8") + (entries.length === 0 ? 0 : 1); manifestBytes += bytes;
      if (manifestBytes > maximumBytes) throw new Error("migration_manifest_limit");
      entries.push(selected); hash.update(selected.projectId).update("\0").update(selected.scalarIdentityDigest).update("\0"); cursor = selected.projectId;
    }
    if (rawPage.length <= pageSize) break;
  }
  return Object.freeze({ entries: Object.freeze(entries), snapshotToken: hash.digest("hex"), pageReads, manifestBytes });
}

function blockerCode(error: unknown): "snapshot_changed" | "migration_project_invalid" | "migration_pointer_invalid" | null {
  if (!(error instanceof Error) || Object.getPrototypeOf(error) !== Error.prototype) return null;
  if (error.message === "project_not_found") return "snapshot_changed";
  if (["migration_project_invalid", "source_invalid", "legacy_inventory_invalid"].includes(error.message)) return "migration_project_invalid";
  if (error.message === "migration_pointer_invalid") return "migration_pointer_invalid";
  return null;
}

export async function executeMigrationManifest<T>(dependencies: Readonly<{
  materialize: () => Promise<MaterializedManifest>;
  prepare: (entry: MigrationProjectManifestEntry) => Promise<Readonly<{ value: T; scalarIdentityDigest: string }>>;
  blocked: (entry: MigrationProjectManifestEntry, code: "snapshot_changed" | "migration_project_invalid" | "migration_pointer_invalid") => T;
  visit: (value: T) => Promise<void>; preparedBytes: (value: T) => number; now: () => number;
  maximumPreparedBytes?: number; maximumDurationMs?: number;
}>): Promise<Readonly<{ snapshotToken: string; projectCount: number; preparedBytes: number }>> {
  const maximumPreparedBytes = dependencies.maximumPreparedBytes ?? 32 * 1024 * 1024; const maximumDurationMs = dependencies.maximumDurationMs ?? 30_000;
  const startedAt = dependencies.now(); const manifest = await dependencies.materialize();
  if (!Array.isArray(manifest.entries) || !SHA.test(manifest.snapshotToken)) throw new Error("migration_snapshot_invalid");
  let preparedBytes = 0;
  for (const rawEntry of manifest.entries) {
    const selected = entry(rawEntry); let value: T;
    try {
      const prepared = await dependencies.prepare(selected);
      value = prepared.scalarIdentityDigest === selected.scalarIdentityDigest ? prepared.value : dependencies.blocked(selected, "snapshot_changed");
    } catch (error) {
      const code = blockerCode(error); if (code === null) throw error; value = dependencies.blocked(selected, code);
    }
    const bytes = dependencies.preparedBytes(value); if (!Number.isSafeInteger(bytes) || bytes < 0) throw new Error("migration_snapshot_invalid");
    preparedBytes += bytes;
    if (preparedBytes > maximumPreparedBytes || dependencies.now() - startedAt > maximumDurationMs) throw new Error("migration_manifest_limit");
    await dependencies.visit(value);
  }
  return Object.freeze({ snapshotToken: manifest.snapshotToken, projectCount: manifest.entries.length, preparedBytes });
}
