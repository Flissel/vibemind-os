import { createHash } from "node:crypto";

export interface KeysetSnapshotItem { readonly projectId: string; readonly stateDigest: string }

export async function scanMigrationKeysetSnapshot<T>(dependencies: Readonly<{
  readPage: (afterProjectId: string | undefined, limit: number) => Promise<readonly T[]>;
  identity: (item: T) => KeysetSnapshotItem;
  visit: (item: T) => Promise<void>;
  pageSize?: number;
  maximumProjects?: number;
}>): Promise<Readonly<{ projectCount: number; snapshotToken: string; pageReads: number }>> {
  const pageSize = dependencies.pageSize ?? 128; const maximumProjects = dependencies.maximumProjects ?? 100_000;
  if (!Number.isSafeInteger(pageSize) || pageSize < 1 || pageSize > 1024 || !Number.isSafeInteger(maximumProjects) || maximumProjects < 1 || maximumProjects > 100_000) throw new Error("migration_snapshot_invalid");
  const hash = createHash("sha256"); let cursor: string | undefined; let count = 0; let pageReads = 0;
  while (true) {
    const rawPage = await dependencies.readPage(cursor, pageSize + 1); pageReads += 1;
    if (!Array.isArray(rawPage) || rawPage.length > pageSize + 1) throw new Error("migration_snapshot_invalid");
    const page = rawPage.length > pageSize ? rawPage.slice(0, pageSize) : rawPage;
    if (rawPage.length > pageSize && count + pageSize >= maximumProjects) throw new Error("migration_project_limit");
    if (page.length === 0) break;
    for (const item of page) {
      const identity = dependencies.identity(item);
      if (typeof identity.projectId !== "string" || identity.projectId.length === 0 || typeof identity.stateDigest !== "string" || !/^[a-f0-9]{64}$/.test(identity.stateDigest)
        || (cursor !== undefined && identity.projectId.localeCompare(cursor) <= 0)) throw new Error("migration_snapshot_invalid");
      count += 1; if (count > maximumProjects) throw new Error("migration_project_limit");
      hash.update(identity.projectId).update("\0").update(identity.stateDigest).update("\0"); await dependencies.visit(item); cursor = identity.projectId;
    }
    if (rawPage.length <= pageSize) break;
  }
  return Object.freeze({ projectCount: count, snapshotToken: hash.digest("hex"), pageReads });
}
