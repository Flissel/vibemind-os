import { createHash } from "node:crypto";
import { types as utilTypes } from "node:util";

const SHA = /^[a-f0-9]{64}$/;
const PROJECT_ID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
export interface MigrationProjectManifestEntry { readonly projectId: string; readonly scalarIdentityDigest: string; readonly stateDigest: string }
export interface MigrationProjectManifestCandidate extends MigrationProjectManifestEntry { readonly capturedBytes: number }
interface MaterializedManifest { readonly entries: readonly MigrationProjectManifestEntry[]; readonly snapshotToken: string; readonly pageReads?: number; readonly manifestBytes?: number; readonly capturedBytes?: number }

function entry(input: unknown): MigrationProjectManifestEntry {
  if (input === null || typeof input !== "object" || Array.isArray(input) || utilTypes.isProxy(input)
    || (Object.getPrototypeOf(input) !== Object.prototype && Object.getPrototypeOf(input) !== null)) throw new Error("migration_snapshot_invalid");
  const keys = Object.keys(input).sort(); if (keys.join("\0") !== "projectId\0scalarIdentityDigest\0stateDigest") throw new Error("migration_snapshot_invalid");
  const projectId = Object.getOwnPropertyDescriptor(input, "projectId"); const scalarIdentityDigest = Object.getOwnPropertyDescriptor(input, "scalarIdentityDigest");
  const stateDigest = Object.getOwnPropertyDescriptor(input, "stateDigest");
  if (projectId === undefined || !("value" in projectId) || !projectId.enumerable || typeof projectId.value !== "string" || !PROJECT_ID.test(projectId.value)
    || scalarIdentityDigest === undefined || !("value" in scalarIdentityDigest) || !scalarIdentityDigest.enumerable || typeof scalarIdentityDigest.value !== "string" || !SHA.test(scalarIdentityDigest.value)
    || stateDigest === undefined || !("value" in stateDigest) || !stateDigest.enumerable || typeof stateDigest.value !== "string" || !SHA.test(stateDigest.value)) throw new Error("migration_snapshot_invalid");
  return Object.freeze({ projectId: projectId.value, scalarIdentityDigest: scalarIdentityDigest.value, stateDigest: stateDigest.value });
}

function manifestCandidate(input: unknown): Readonly<{ entry: MigrationProjectManifestEntry; capturedBytes: number }> {
  if (input === null || typeof input !== "object" || Array.isArray(input) || utilTypes.isProxy(input)
    || (Object.getPrototypeOf(input) !== Object.prototype && Object.getPrototypeOf(input) !== null)) throw new Error("migration_snapshot_invalid");
  const keys = Object.keys(input).sort(); if (keys.join("\0") !== "capturedBytes\0projectId\0scalarIdentityDigest\0stateDigest") throw new Error("migration_snapshot_invalid");
  const capturedBytes = Object.getOwnPropertyDescriptor(input, "capturedBytes");
  if (capturedBytes === undefined || !("value" in capturedBytes) || !capturedBytes.enumerable || !Number.isSafeInteger(capturedBytes.value) || capturedBytes.value < 1) throw new Error("migration_snapshot_invalid");
  const projectId = Object.getOwnPropertyDescriptor(input, "projectId"); const scalarIdentityDigest = Object.getOwnPropertyDescriptor(input, "scalarIdentityDigest");
  const stateDigest = Object.getOwnPropertyDescriptor(input, "stateDigest");
  if (projectId === undefined || !("value" in projectId) || !projectId.enumerable || scalarIdentityDigest === undefined || !("value" in scalarIdentityDigest)
    || !scalarIdentityDigest.enumerable || stateDigest === undefined || !("value" in stateDigest) || !stateDigest.enumerable) throw new Error("migration_snapshot_invalid");
  return Object.freeze({ entry: entry({ projectId: projectId.value, scalarIdentityDigest: scalarIdentityDigest.value, stateDigest: stateDigest.value }), capturedBytes: capturedBytes.value as number });
}

export async function materializeMigrationProjectManifest(dependencies: Readonly<{
  readPage: (afterProjectId: string | undefined, limit: number) => Promise<readonly unknown[]>;
  now: () => number; pageSize?: number; maximumProjects?: number; maximumBytes?: number; maximumCapturedBytes?: number; maximumDurationMs?: number;
}>): Promise<Readonly<{ entries: readonly MigrationProjectManifestEntry[]; snapshotToken: string; pageReads: number; manifestBytes: number; capturedBytes: number }>> {
  const pageSize = dependencies.pageSize ?? 32; const maximumProjects = dependencies.maximumProjects ?? 1_000;
  const maximumBytes = dependencies.maximumBytes ?? 256_000; const maximumCapturedBytes = dependencies.maximumCapturedBytes ?? 64 * 1024 * 1024;
  const maximumDurationMs = dependencies.maximumDurationMs ?? 5_000;
  if (!Number.isSafeInteger(pageSize) || pageSize < 1 || pageSize > 64 || !Number.isSafeInteger(maximumProjects) || maximumProjects < 1 || maximumProjects > 1_000
    || !Number.isSafeInteger(maximumBytes) || maximumBytes < 1 || maximumBytes > 256_000
    || !Number.isSafeInteger(maximumCapturedBytes) || maximumCapturedBytes < 1 || maximumCapturedBytes > 64 * 1024 * 1024
    || !Number.isSafeInteger(maximumDurationMs) || maximumDurationMs < 1 || maximumDurationMs > 30_000) throw new Error("migration_snapshot_invalid");
  const startedAt = dependencies.now(); if (!Number.isFinite(startedAt)) throw new Error("migration_snapshot_invalid");
  const entries: MigrationProjectManifestEntry[] = []; const hash = createHash("sha256"); let cursor: string | undefined; let pageReads = 0; let manifestBytes = 2; let capturedBytes = 0;
  while (true) {
    const rawPage = await dependencies.readPage(cursor, pageSize + 1); pageReads += 1;
    if (dependencies.now() - startedAt > maximumDurationMs) throw new Error("migration_manifest_limit");
    if (!Array.isArray(rawPage) || utilTypes.isProxy(rawPage) || rawPage.length > pageSize + 1) throw new Error("migration_snapshot_invalid");
    if (rawPage.length > pageSize && entries.length + pageSize >= maximumProjects) throw new Error("migration_project_limit");
    const page = rawPage.length > pageSize ? rawPage.slice(0, pageSize) : rawPage;
    if (page.length === 0) break;
    for (const rawCandidate of page) {
      const captured = manifestCandidate(rawCandidate); const selected = captured.entry;
      if (cursor !== undefined && selected.projectId.localeCompare(cursor) <= 0) throw new Error("migration_snapshot_invalid");
      if (entries.length >= maximumProjects) throw new Error("migration_project_limit");
      const bytes = Buffer.byteLength(JSON.stringify(selected), "utf8") + (entries.length === 0 ? 0 : 1); manifestBytes += bytes;
      if (manifestBytes > maximumBytes) throw new Error("migration_manifest_limit");
      capturedBytes += captured.capturedBytes;
      if (capturedBytes > maximumCapturedBytes) throw new Error("migration_manifest_limit");
      entries.push(selected); hash.update(selected.projectId).update("\0").update(selected.scalarIdentityDigest).update("\0").update(selected.stateDigest).update("\0"); cursor = selected.projectId;
    }
    if (rawPage.length <= pageSize) break;
  }
  return Object.freeze({ entries: Object.freeze(entries), snapshotToken: hash.digest("hex"), pageReads, manifestBytes, capturedBytes });
}

function blockerCode(error: unknown): "snapshot_changed" | "migration_project_invalid" | "migration_pointer_invalid" | null {
  if (!(error instanceof Error) || Object.getPrototypeOf(error) !== Error.prototype) return null;
  if (error.message === "project_not_found" || error.message === "migration_snapshot_changed") return "snapshot_changed";
  if (["migration_project_invalid", "source_invalid", "legacy_inventory_invalid"].includes(error.message)) return "migration_project_invalid";
  if (error.message === "migration_pointer_invalid") return "migration_pointer_invalid";
  return null;
}

export async function executeMigrationManifest<T, S>(dependencies: Readonly<{
  materialize: () => Promise<MaterializedManifest>;
  loadShared: () => Promise<Readonly<{ value: S; retainedBytes: number }>>;
  prepare: (entry: MigrationProjectManifestEntry, shared: S) => Promise<Readonly<{ value: T; scalarIdentityDigest: string; stateDigest: string }>>;
  blocked: (entry: MigrationProjectManifestEntry, code: "snapshot_changed" | "migration_project_invalid" | "migration_pointer_invalid", shared: S) => T;
  visit: (value: T) => Promise<void>; preparedBytes: (value: T) => number; now: () => number;
  maximumPreparedBytes?: number; maximumDurationMs?: number;
}>): Promise<Readonly<{ snapshotToken: string; projectCount: number; sharedRetainedBytes: number; preparedBytes: number; retainedBytes: number; shared: S }>> {
  const maximumPreparedBytes = dependencies.maximumPreparedBytes ?? 32 * 1024 * 1024; const maximumDurationMs = dependencies.maximumDurationMs ?? 30_000;
  const startedAt = dependencies.now(); const manifest = await dependencies.materialize();
  if (!Array.isArray(manifest.entries) || !SHA.test(manifest.snapshotToken)) throw new Error("migration_snapshot_invalid");
  const loaded = await dependencies.loadShared();
  if (!Number.isSafeInteger(loaded.retainedBytes) || loaded.retainedBytes < 0) throw new Error("migration_snapshot_invalid");
  const shared = loaded.value; let preparedBytes = 0;
  if (loaded.retainedBytes > maximumPreparedBytes) throw new Error("migration_manifest_limit");
  for (const rawEntry of manifest.entries) {
    const selected = entry(rawEntry); let value: T;
    try {
      const prepared = await dependencies.prepare(selected, shared);
      value = prepared.scalarIdentityDigest === selected.scalarIdentityDigest && prepared.stateDigest === selected.stateDigest
        ? prepared.value : dependencies.blocked(selected, "snapshot_changed", shared);
    } catch (error) {
      const code = blockerCode(error); if (code === null) throw error; value = dependencies.blocked(selected, code, shared);
    }
    const bytes = dependencies.preparedBytes(value); if (!Number.isSafeInteger(bytes) || bytes < 0) throw new Error("migration_snapshot_invalid");
    preparedBytes += bytes;
    if (loaded.retainedBytes + preparedBytes > maximumPreparedBytes || dependencies.now() - startedAt > maximumDurationMs) throw new Error("migration_manifest_limit");
    await dependencies.visit(value);
  }
  return Object.freeze({ snapshotToken: manifest.snapshotToken, projectCount: manifest.entries.length, sharedRetainedBytes: loaded.retainedBytes,
    preparedBytes, retainedBytes: loaded.retainedBytes + preparedBytes, shared });
}
