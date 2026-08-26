import { describe, expect, it } from "vitest";
import { executeMigrationManifest, materializeMigrationProjectManifest, type MigrationProjectManifestEntry } from "@/src/application/services/plugin-migration-keyset-snapshot";

const scalarIdentityDigest = "a".repeat(64);
const projectId = (index: number) => `11111111-1111-4111-8111-${String(index).padStart(12, "0")}`;
function source(total: () => number, counters: { reads: number }) {
  return async (after: string | undefined, limit: number) => {
    counters.reads += 1; const start = after === undefined ? 0 : Number(after.slice(-12)) + 1;
    return Array.from({ length: Math.max(0, Math.min(limit, total() - start)) }, (_value, offset) => ({ projectId: projectId(start + offset), scalarIdentityDigest }));
  };
}

describe("plugin migration manifest snapshots", () => {
  it("materializes the defensible 10k boundary with bounded pages, bytes, and O(N) work", async () => {
    const counters = { reads: 0 }; let clock = 0;
    const report = await materializeMigrationProjectManifest({ readPage: source(() => 10_000, counters), now: () => clock++, pageSize: 128,
      maximumProjects: 10_000, maximumBytes: 2_000_000, maximumDurationMs: 20_000 });
    expect(report.entries).toHaveLength(10_000); expect(counters.reads).toBeLessThanOrEqual(80); expect(report.pageReads).toBe(counters.reads); expect(report.manifestBytes).toBeLessThan(2_000_000);
  });

  it("fails closed above project, byte, and time limits", async () => {
    await expect(materializeMigrationProjectManifest({ readPage: source(() => 10_001, { reads: 0 }), now: () => 0, maximumProjects: 10_000 })).rejects.toThrow("migration_project_limit");
    await expect(materializeMigrationProjectManifest({ readPage: source(() => 2, { reads: 0 }), now: () => 0, maximumBytes: 20 })).rejects.toThrow("migration_manifest_limit");
    let clock = 0; await expect(materializeMigrationProjectManifest({ readPage: source(() => 2, { reads: 0 }), now: () => clock += 10, maximumDurationMs: 5 })).rejects.toThrow("migration_manifest_limit");
  });

  it("closes materialization before preparing each manifest project exactly once", async () => {
    const events: string[] = []; const entries: readonly MigrationProjectManifestEntry[] = [0, 1].map(index => ({ projectId: projectId(index), scalarIdentityDigest }));
    const report = await executeMigrationManifest({
      materialize: async () => { events.push("transaction:start", "transaction:closed"); return { entries, snapshotToken: "b".repeat(64) }; },
      prepare: async entry => { events.push(`prepare:${entry.projectId}`); return { value: entry.projectId, scalarIdentityDigest: entry.scalarIdentityDigest }; },
      blocked: (entry, code) => `${entry.projectId}:${code}`, visit: async value => { events.push(`visit:${value}`); }, preparedBytes: value => value.length, now: () => 0,
    });
    expect(events).toEqual(["transaction:start", "transaction:closed", `prepare:${projectId(0)}`, `visit:${projectId(0)}`, `prepare:${projectId(1)}`, `visit:${projectId(1)}`]);
    expect(report).toEqual({ snapshotToken: "b".repeat(64), projectCount: 2, preparedBytes: 72 });
  });

  it("turns deleted, invalid, and changed manifest members into explicit blockers and excludes mid-run creations", async () => {
    const entries: readonly MigrationProjectManifestEntry[] = [0, 1, 2].map(index => ({ projectId: projectId(index), scalarIdentityDigest })); const visited: string[] = [];
    let newProjectCreated = false;
    await executeMigrationManifest({ materialize: async () => ({ entries, snapshotToken: "b".repeat(64) }),
      prepare: async entry => {
        if (entry.projectId === projectId(0)) throw new Error("project_not_found");
        if (entry.projectId === projectId(1)) throw new Error("migration_project_invalid");
        newProjectCreated = true; return { value: entry.projectId, scalarIdentityDigest: "c".repeat(64) };
      }, blocked: (entry, code) => `${entry.projectId}:${code}`, visit: async value => { visited.push(value); }, preparedBytes: value => value.length, now: () => 0 });
    expect(visited).toEqual([`${projectId(0)}:snapshot_changed`, `${projectId(1)}:migration_project_invalid`, `${projectId(2)}:snapshot_changed`]);
    expect(newProjectCreated).toBe(true); expect(visited.some(value => value.includes(projectId(3)))).toBe(false);
  });

  it("is fresh per execution and includes a project created after the first manifest", async () => {
    let total = 2; const first = await materializeMigrationProjectManifest({ readPage: source(() => total, { reads: 0 }), now: () => 0 }); total = 3;
    const second = await materializeMigrationProjectManifest({ readPage: source(() => total, { reads: 0 }), now: () => 0 }); expect(first.entries).toHaveLength(2); expect(second.entries).toHaveLength(3);
  });
});
