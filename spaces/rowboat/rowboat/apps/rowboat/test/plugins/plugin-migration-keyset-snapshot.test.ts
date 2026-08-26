import { describe, expect, it } from "vitest";
import { scanMigrationKeysetSnapshot } from "@/src/application/services/plugin-migration-keyset-snapshot";

const stateDigest = "a".repeat(64);
const projectId = (index: number) => `project-${String(index).padStart(6, "0")}`;
function source(total: () => number, counters: { reads: number; visits: number }) {
  return {
    readPage: async (after: string | undefined, limit: number) => {
      counters.reads += 1; const start = after === undefined ? 0 : Number(after.slice("project-".length)) + 1;
      return Array.from({ length: Math.max(0, Math.min(limit, total() - start)) }, (_value, offset) => ({ projectId: projectId(start + offset), stateDigest }));
    },
    identity: (item: Readonly<{ projectId: string; stateDigest: string }>) => item,
    visit: async () => { counters.visits += 1; },
  };
}

describe("plugin migration keyset snapshots", () => {
  it("scans the 100k boundary with bounded pages and O(N) work", async () => {
    const counters = { reads: 0, visits: 0 }; const report = await scanMigrationKeysetSnapshot({ ...source(() => 100_000, counters), pageSize: 128, maximumProjects: 100_000 });
    expect(report.projectCount).toBe(100_000); expect(counters.visits).toBe(100_000); expect(counters.reads).toBeLessThanOrEqual(783); expect(report.pageReads).toBe(counters.reads);
  });

  it("fails closed above the hard boundary without reading or retaining the whole collection", async () => {
    const counters = { reads: 0, visits: 0 }; await expect(scanMigrationKeysetSnapshot({ ...source(() => 100_001, counters), pageSize: 128, maximumProjects: 100_000 })).rejects.toThrow("migration_project_limit");
    expect(counters.visits).toBeLessThanOrEqual(100_000); expect(counters.reads).toBeLessThanOrEqual(783);
  });

  it("is fresh per execution and includes a project created after the first snapshot", async () => {
    let total = 2; const first = { reads: 0, visits: 0 }; const second = { reads: 0, visits: 0 };
    expect((await scanMigrationKeysetSnapshot(source(() => total, first))).projectCount).toBe(2); total = 3;
    expect((await scanMigrationKeysetSnapshot(source(() => total, second))).projectCount).toBe(3); expect(second.visits).toBe(3);
  });

  it("rejects duplicate or out-of-order page identities", async () => {
    await expect(scanMigrationKeysetSnapshot({ readPage: async () => [{ projectId: "p2", stateDigest }, { projectId: "p1", stateDigest }], identity: item => item, visit: async () => undefined })).rejects.toThrow("migration_snapshot_invalid");
  });
});
