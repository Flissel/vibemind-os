import { createHash } from "node:crypto";
import { types as utilTypes } from "node:util";

const SHA = /^[a-f0-9]{64}$/;
const PROJECT_ID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
type ManifestBlocker = "project_too_large" | "source_invalid";
type PreparationBlocker = "snapshot_changed" | "migration_project_invalid" | "migration_pointer_invalid" | ManifestBlocker;

export interface MigrationProjectReadyManifestEntry { readonly projectId: string; readonly scalarIdentityDigest: string; readonly stateDigest: string }
export interface MigrationProjectBlockedManifestEntry { readonly projectId: string; readonly projectBsonBytes: number; readonly blockerCode: ManifestBlocker }
export type MigrationProjectManifestEntry = MigrationProjectReadyManifestEntry | MigrationProjectBlockedManifestEntry;
export interface MigrationProjectManifestCandidate extends MigrationProjectReadyManifestEntry { readonly capturedBytes: number }
export interface MigrationProjectSizeCandidate { readonly projectId: string; readonly scalarIdentityDigest: string; readonly projectBsonBytes: number }
interface MaterializedManifest { readonly entries: readonly MigrationProjectManifestEntry[]; readonly snapshotToken: string }
interface SnapshotTransaction {
  readonly start: (maxCommitTimeMS: number) => void; readonly inTransaction: () => boolean;
  readonly commit: () => Promise<void>; readonly abort: () => Promise<void>; readonly end: () => Promise<void>;
}
interface ManifestDependencies {
  readonly readPage: (afterProjectId: string | undefined, limit: number, remainingMs: number, signal: AbortSignal) => Promise<readonly unknown[]>;
  readonly readProject: (projectId: string, remainingMs: number, signal: AbortSignal) => Promise<unknown>;
  readonly now: () => number; readonly pageSize?: number; readonly maximumProjects?: number; readonly maximumBytes?: number;
  readonly maximumSourceBytes?: number; readonly maximumProjectBsonBytes?: number; readonly maximumDurationMs?: number;
}

function plain(input: unknown): input is object {
  return input !== null && typeof input === "object" && !Array.isArray(input) && !utilTypes.isProxy(input)
    && (Object.getPrototypeOf(input) === Object.prototype || Object.getPrototypeOf(input) === null);
}
function data(input: object, name: string): PropertyDescriptor {
  const selected = Object.getOwnPropertyDescriptor(input, name);
  if (selected === undefined || !("value" in selected) || !selected.enumerable) throw new Error("migration_snapshot_invalid");
  return selected;
}
function readyEntry(input: unknown): MigrationProjectReadyManifestEntry {
  if (!plain(input) || Object.keys(input).sort().join("\0") !== "projectId\0scalarIdentityDigest\0stateDigest") throw new Error("migration_snapshot_invalid");
  const projectId = data(input, "projectId").value; const scalarIdentityDigest = data(input, "scalarIdentityDigest").value; const stateDigest = data(input, "stateDigest").value;
  if (typeof projectId !== "string" || !PROJECT_ID.test(projectId) || typeof scalarIdentityDigest !== "string" || !SHA.test(scalarIdentityDigest)
    || typeof stateDigest !== "string" || !SHA.test(stateDigest)) throw new Error("migration_snapshot_invalid");
  return Object.freeze({ projectId, scalarIdentityDigest, stateDigest });
}
function blockedEntry(input: unknown): MigrationProjectBlockedManifestEntry {
  if (!plain(input) || Object.keys(input).sort().join("\0") !== "blockerCode\0projectBsonBytes\0projectId") throw new Error("migration_snapshot_invalid");
  const projectId = data(input, "projectId").value; const projectBsonBytes = data(input, "projectBsonBytes").value; const blockerCode = data(input, "blockerCode").value;
  if (typeof projectId !== "string" || !PROJECT_ID.test(projectId) || !Number.isSafeInteger(projectBsonBytes) || projectBsonBytes < 1
    || !["project_too_large", "source_invalid"].includes(blockerCode as string)) throw new Error("migration_snapshot_invalid");
  return Object.freeze({ projectId, projectBsonBytes, blockerCode: blockerCode as ManifestBlocker });
}
function entry(input: unknown): MigrationProjectManifestEntry {
  if (!plain(input)) throw new Error("migration_snapshot_invalid");
  return Object.prototype.hasOwnProperty.call(input, "blockerCode") ? blockedEntry(input) : readyEntry(input);
}
function sizeCandidate(input: unknown): MigrationProjectSizeCandidate {
  if (!plain(input) || Object.keys(input).sort().join("\0") !== "projectBsonBytes\0projectId\0scalarIdentityDigest") throw new Error("migration_snapshot_invalid");
  const projectId = data(input, "projectId").value; const scalarIdentityDigest = data(input, "scalarIdentityDigest").value; const projectBsonBytes = data(input, "projectBsonBytes").value;
  if (typeof projectId !== "string" || !PROJECT_ID.test(projectId) || typeof scalarIdentityDigest !== "string" || !SHA.test(scalarIdentityDigest)
    || !Number.isSafeInteger(projectBsonBytes) || projectBsonBytes < 1) throw new Error("migration_snapshot_invalid");
  return Object.freeze({ projectId, scalarIdentityDigest, projectBsonBytes });
}
function manifestCandidate(input: unknown): Readonly<{ entry: MigrationProjectReadyManifestEntry; capturedBytes: number }> {
  if (!plain(input) || Object.keys(input).sort().join("\0") !== "capturedBytes\0projectId\0scalarIdentityDigest\0stateDigest") throw new Error("migration_snapshot_invalid");
  const capturedBytes = data(input, "capturedBytes").value;
  if (!Number.isSafeInteger(capturedBytes) || capturedBytes < 1) throw new Error("migration_snapshot_invalid");
  return Object.freeze({ entry: readyEntry({ projectId: data(input, "projectId").value, scalarIdentityDigest: data(input, "scalarIdentityDigest").value,
    stateDigest: data(input, "stateDigest").value }), capturedBytes });
}
function validatedLimits(dependencies: ManifestDependencies) {
  const pageSize = dependencies.pageSize ?? 32; const maximumProjects = dependencies.maximumProjects ?? 1_000;
  const maximumBytes = dependencies.maximumBytes ?? 256_000; const maximumSourceBytes = dependencies.maximumSourceBytes ?? 64 * 1024 * 1024;
  const maximumProjectBsonBytes = dependencies.maximumProjectBsonBytes ?? 1024 * 1024; const maximumDurationMs = dependencies.maximumDurationMs ?? 5_000;
  if (!Number.isSafeInteger(pageSize) || pageSize < 1 || pageSize > 64 || !Number.isSafeInteger(maximumProjects) || maximumProjects < 1 || maximumProjects > 1_000
    || !Number.isSafeInteger(maximumBytes) || maximumBytes < 1 || maximumBytes > 256_000
    || !Number.isSafeInteger(maximumSourceBytes) || maximumSourceBytes < 1 || maximumSourceBytes > 64 * 1024 * 1024
    || !Number.isSafeInteger(maximumProjectBsonBytes) || maximumProjectBsonBytes < 1 || maximumProjectBsonBytes > 1024 * 1024
    || !Number.isSafeInteger(maximumDurationMs) || maximumDurationMs < 1 || maximumDurationMs > 30_000) throw new Error("migration_snapshot_invalid");
  return Object.freeze({ pageSize, maximumProjects, maximumBytes, maximumSourceBytes, maximumProjectBsonBytes, maximumDurationMs });
}

export async function materializeMigrationProjectManifest(dependencies: ManifestDependencies) {
  const limits = validatedLimits(dependencies); const startedAt = dependencies.now();
  if (!Number.isFinite(startedAt)) throw new Error("migration_snapshot_invalid");
  const deadline = startedAt + limits.maximumDurationMs; const controller = new AbortController();
  const remaining = () => { const current = dependencies.now(); if (!Number.isFinite(current)) throw new Error("migration_snapshot_invalid");
    const selected = Math.ceil(deadline - current); if (selected <= 0) { controller.abort(); throw new Error("migration_manifest_limit"); } return selected; };
  const bounded = async <T>(operation: (remainingMs: number, signal: AbortSignal) => Promise<T>): Promise<T> => {
    const remainingMs = remaining(); let timeout: ReturnType<typeof setTimeout> | undefined;
    const pending = Promise.resolve().then(() => operation(remainingMs, controller.signal)); pending.catch(() => undefined);
    const expired = new Promise<never>((_resolve, reject) => { timeout = setTimeout(() => { reject(new Error("migration_manifest_limit")); controller.abort(); }, remainingMs); });
    try { const selected = await Promise.race([pending, expired]); remaining(); return selected; }
    catch (error) { if (controller.signal.aborted) throw new Error("migration_manifest_limit"); throw error; }
    finally { if (timeout !== undefined) clearTimeout(timeout); }
  };
  const entries: MigrationProjectManifestEntry[] = []; const hash = createHash("sha256"); let cursor: string | undefined; let pageReads = 0;
  let manifestBytes = 2; let eligibleReportedBytes = 0; let capturedBytes = 0; let maximumResidentBytes = 0;
  while (true) {
    const rawPage = await bounded((remainingMs, signal) => dependencies.readPage(cursor, limits.pageSize + 1, remainingMs, signal)); pageReads += 1;
    if (!Array.isArray(rawPage) || utilTypes.isProxy(rawPage) || rawPage.length > limits.pageSize + 1) throw new Error("migration_snapshot_invalid");
    if (rawPage.length > limits.pageSize && entries.length + limits.pageSize >= limits.maximumProjects) throw new Error("migration_project_limit");
    const page = rawPage.length > limits.pageSize ? rawPage.slice(0, limits.pageSize) : rawPage;
    if (page.length === 0) break;
    for (const rawMetadata of page) {
      const metadata = sizeCandidate(rawMetadata);
      if (cursor !== undefined && metadata.projectId.localeCompare(cursor) <= 0) throw new Error("migration_snapshot_invalid");
      if (entries.length >= limits.maximumProjects) throw new Error("migration_project_limit");
      let selected: MigrationProjectManifestEntry;
      if (metadata.projectBsonBytes > limits.maximumProjectBsonBytes) {
        selected = Object.freeze({ projectId: metadata.projectId, projectBsonBytes: metadata.projectBsonBytes, blockerCode: "project_too_large" as const });
      } else {
        eligibleReportedBytes += metadata.projectBsonBytes;
        if (eligibleReportedBytes > limits.maximumSourceBytes) throw new Error("migration_manifest_limit");
        const captured = manifestCandidate(await bounded((remainingMs, signal) => dependencies.readProject(metadata.projectId, remainingMs, signal)));
        if (captured.entry.projectId !== metadata.projectId || captured.entry.scalarIdentityDigest !== metadata.scalarIdentityDigest) {
          selected = Object.freeze({ projectId: metadata.projectId, projectBsonBytes: metadata.projectBsonBytes, blockerCode: "source_invalid" as const });
        } else if (captured.capturedBytes > limits.maximumProjectBsonBytes) {
          selected = Object.freeze({ projectId: metadata.projectId, projectBsonBytes: metadata.projectBsonBytes, blockerCode: "source_invalid" as const });
        } else {
          capturedBytes += captured.capturedBytes; maximumResidentBytes = Math.max(maximumResidentBytes, captured.capturedBytes);
          if (capturedBytes > limits.maximumSourceBytes) throw new Error("migration_manifest_limit");
          selected = captured.entry;
        }
      }
      const retained = Buffer.byteLength(JSON.stringify(selected), "utf8") + (entries.length === 0 ? 0 : 1); manifestBytes += retained;
      if (manifestBytes > limits.maximumBytes) throw new Error("migration_manifest_limit");
      entries.push(selected); hash.update(JSON.stringify(selected)).update("\0"); cursor = selected.projectId;
    }
    if (rawPage.length <= limits.pageSize) break;
  }
  return Object.freeze({ entries: Object.freeze(entries), snapshotToken: hash.digest("hex"), pageReads, manifestBytes,
    eligibleReportedBytes, capturedBytes, maximumResidentBytes });
}

export async function materializeMigrationProjectManifestInTransaction(dependencies: ManifestDependencies & Readonly<{ transaction: SnapshotTransaction }>) {
  const maximumDurationMs = dependencies.maximumDurationMs ?? 5_000; dependencies.transaction.start(maximumDurationMs);
  try { const manifest = await materializeMigrationProjectManifest(dependencies); await dependencies.transaction.commit(); return manifest; }
  catch (error) { if (dependencies.transaction.inTransaction()) await dependencies.transaction.abort(); throw error; }
  finally { await dependencies.transaction.end(); }
}

function blockerCode(error: unknown): PreparationBlocker | null {
  if (!(error instanceof Error) || Object.getPrototypeOf(error) !== Error.prototype) return null;
  if (error.message === "project_not_found" || error.message === "migration_snapshot_changed") return "snapshot_changed";
  if (["migration_project_invalid", "source_invalid", "legacy_inventory_invalid"].includes(error.message)) return "migration_project_invalid";
  if (error.message === "migration_pointer_invalid") return "migration_pointer_invalid";
  return null;
}

export async function executeMigrationManifest<T, S>(dependencies: Readonly<{
  materialize: () => Promise<MaterializedManifest>; loadShared: () => Promise<Readonly<{ value: S; retainedBytes: number }>>;
  prepare: (entry: MigrationProjectReadyManifestEntry, shared: S) => Promise<Readonly<{ value: T; scalarIdentityDigest: string; stateDigest: string }>>;
  blocked: (entry: MigrationProjectManifestEntry, code: PreparationBlocker, shared: S) => T;
  visit: (value: T) => Promise<void>; preparedBytes: (value: T) => number; now: () => number; maximumPreparedBytes?: number; maximumDurationMs?: number;
}>) {
  const maximumPreparedBytes = dependencies.maximumPreparedBytes ?? 32 * 1024 * 1024; const maximumDurationMs = dependencies.maximumDurationMs ?? 30_000;
  const startedAt = dependencies.now(); const manifest = await dependencies.materialize();
  if (!Array.isArray(manifest.entries) || !SHA.test(manifest.snapshotToken)) throw new Error("migration_snapshot_invalid");
  const loaded = await dependencies.loadShared(); if (!Number.isSafeInteger(loaded.retainedBytes) || loaded.retainedBytes < 0) throw new Error("migration_snapshot_invalid");
  const shared = loaded.value; let preparedBytes = 0; if (loaded.retainedBytes > maximumPreparedBytes) throw new Error("migration_manifest_limit");
  for (const rawEntry of manifest.entries) {
    const selected = entry(rawEntry); let value: T;
    if ("blockerCode" in selected) value = dependencies.blocked(selected, selected.blockerCode, shared);
    else {
      try { const prepared = await dependencies.prepare(selected, shared); value = prepared.scalarIdentityDigest === selected.scalarIdentityDigest && prepared.stateDigest === selected.stateDigest
        ? prepared.value : dependencies.blocked(selected, "snapshot_changed", shared); }
      catch (error) { const code = blockerCode(error); if (code === null) throw error; value = dependencies.blocked(selected, code, shared); }
    }
    const bytes = dependencies.preparedBytes(value); if (!Number.isSafeInteger(bytes) || bytes < 0) throw new Error("migration_snapshot_invalid");
    preparedBytes += bytes;
    if (loaded.retainedBytes + preparedBytes > maximumPreparedBytes || dependencies.now() - startedAt > maximumDurationMs) throw new Error("migration_manifest_limit");
    await dependencies.visit(value);
  }
  return Object.freeze({ snapshotToken: manifest.snapshotToken, projectCount: manifest.entries.length, sharedRetainedBytes: loaded.retainedBytes,
    preparedBytes, retainedBytes: loaded.retainedBytes + preparedBytes, shared });
}
