import { describe, expect, it } from "vitest";
import { statSync } from "node:fs";
import { executeMigrationManifest, materializeMigrationProjectManifest, type MigrationProjectManifestEntry } from "@/src/application/services/plugin-migration-keyset-snapshot";
import { captureMigrationProjectManifestCandidate } from "@/src/application/services/plugin-migration-project-state";
import catalogLock from "../../../../config/openai-plugin-catalog.lock.json";
import customerSupport from "@/app/lib/prebuilt-cards/customer-support.json";

const scalarIdentityDigest = "a".repeat(64);
const stateDigest = "b".repeat(64);
const projectId = (index: number) => `11111111-1111-4111-8111-${String(index).padStart(12, "0")}`;
function source(total: () => number, counters: { reads: number }) {
  return async (after: string | undefined, limit: number) => {
    counters.reads += 1; const start = after === undefined ? 0 : Number(after.slice(-12)) + 1;
    return Array.from({ length: Math.max(0, Math.min(limit, total() - start)) }, (_value, offset) => ({ projectId: projectId(start + offset), scalarIdentityDigest, stateDigest, capturedBytes: 512 }));
  };
}

describe("plugin migration manifest snapshots", () => {
  it("materializes the defensible 1k full-state boundary with bounded pages, captured bytes, and O(N) work", async () => {
    const counters = { reads: 0 }; let clock = 0;
    const report = await materializeMigrationProjectManifest({ readPage: source(() => 1_000, counters), now: () => clock++, pageSize: 32,
      maximumProjects: 1_000, maximumBytes: 256_000, maximumCapturedBytes: 64 * 1024 * 1024, maximumDurationMs: 20_000 });
    expect(report.entries).toHaveLength(1_000); expect(counters.reads).toBeLessThanOrEqual(33); expect(report.pageReads).toBe(counters.reads);
    expect(report.manifestBytes).toBeLessThan(256_000); expect(report.capturedBytes).toBe(512_000);
  });

  it("hashes 1k real-size draft/live workflow projections inside the bounded snapshot and retains digests only", async () => {
    const workflow = { ...customerSupport, lastUpdatedAt: "2026-08-26T09:59:59.000Z" }; const counters = { reads: 0 };
    const readPage = async (after: string | undefined, limit: number) => {
      counters.reads += 1; const start = after === undefined ? 0 : Number(after.slice(-12)) + 1;
      return Array.from({ length: Math.max(0, Math.min(limit, 1_000 - start)) }, (_value, offset) => captureMigrationProjectManifestCandidate({
        _id: projectId(start + offset), lastUpdatedAt: "2026-08-26T10:00:00.000Z", version: 1, draftWorkflow: workflow, liveWorkflow: workflow,
      }));
    };
    const report = await materializeMigrationProjectManifest({ readPage, now: () => 0, pageSize: 32, maximumProjects: 1_000,
      maximumBytes: 256_000, maximumCapturedBytes: 64 * 1024 * 1024, maximumDurationMs: 5_000 });
    expect(report.entries).toHaveLength(1_000); expect(counters.reads).toBeLessThanOrEqual(33); expect(report.capturedBytes).toBeGreaterThan(1_000_000);
    expect(Object.keys(report.entries[0]!).sort()).toEqual(["projectId", "scalarIdentityDigest", "stateDigest"]);
  });

  it("fails closed above project, byte, and time limits", async () => {
    await expect(materializeMigrationProjectManifest({ readPage: source(() => 1_001, { reads: 0 }), now: () => 0, maximumProjects: 1_000 })).rejects.toThrow("migration_project_limit");
    await expect(materializeMigrationProjectManifest({ readPage: source(() => 2, { reads: 0 }), now: () => 0, maximumBytes: 20 })).rejects.toThrow("migration_manifest_limit");
    await expect(materializeMigrationProjectManifest({ readPage: source(() => 2, { reads: 0 }), now: () => 0, maximumCapturedBytes: 20 })).rejects.toThrow("migration_manifest_limit");
    let clock = 0; await expect(materializeMigrationProjectManifest({ readPage: source(() => 2, { reads: 0 }), now: () => clock += 10, maximumDurationMs: 5 })).rejects.toThrow("migration_manifest_limit");
  });

  it("rejects accessor-backed full-state candidates without invoking accessors", async () => {
    let calls = 0; const malicious: Record<string, unknown> = { scalarIdentityDigest, stateDigest, capturedBytes: 512 };
    Object.defineProperty(malicious, "projectId", { enumerable: true, get: () => { calls += 1; return projectId(0); } });
    await expect(materializeMigrationProjectManifest({ readPage: async () => [malicious], now: () => 0 })).rejects.toThrow("migration_snapshot_invalid");
    expect(calls).toBe(0);
  });

  it("closes materialization before preparing each manifest project exactly once", async () => {
    const events: string[] = []; const entries: readonly MigrationProjectManifestEntry[] = [0, 1].map(index => ({ projectId: projectId(index), scalarIdentityDigest, stateDigest }));
    const report = await executeMigrationManifest({
      materialize: async () => { events.push("transaction:start", "transaction:closed"); return { entries, snapshotToken: "b".repeat(64) }; },
      loadShared: async () => { events.push("catalog:loaded"); return { value: Object.freeze({ digest: "catalog" }), retainedBytes: 10 }; },
      prepare: async (entry, shared) => { expect(shared.digest).toBe("catalog"); events.push(`prepare:${entry.projectId}`); return { value: entry.projectId, scalarIdentityDigest: entry.scalarIdentityDigest, stateDigest: entry.stateDigest }; },
      blocked: (entry, code) => `${entry.projectId}:${code}`, visit: async value => { events.push(`visit:${value}`); }, preparedBytes: value => value.length, now: () => 0,
    });
    expect(events).toEqual(["transaction:start", "transaction:closed", "catalog:loaded", `prepare:${projectId(0)}`, `visit:${projectId(0)}`, `prepare:${projectId(1)}`, `visit:${projectId(1)}`]);
    expect(report).toEqual({ snapshotToken: "b".repeat(64), projectCount: 2, sharedRetainedBytes: 10, preparedBytes: 72, retainedBytes: 82, shared: { digest: "catalog" } });
  });

  it("loads and retains the real 908,908-byte catalog once while processing 1k realistic public rows", async () => {
    const entries: readonly MigrationProjectManifestEntry[] = Array.from({ length: 1_000 }, (_value, index) => ({ projectId: projectId(index), scalarIdentityDigest, stateDigest }));
    let getCatalogCalls = 0; const references = new Set<unknown>(); let visits = 0;
    const report = await executeMigrationManifest({ materialize: async () => ({ entries, snapshotToken: stateDigest }),
      loadShared: async () => { getCatalogCalls += 1; return { value: catalogLock, retainedBytes: Buffer.byteLength(JSON.stringify(catalogLock), "utf8") }; },
      prepare: async (entry, catalog) => { references.add(catalog); return { value: { projectId: entry.projectId, sourceProjectRevision: 1787738400000,
        sourceDigest: stateDigest, sourceInventoryDigest: scalarIdentityDigest, recipeId: "legacy-card:real:v1", recipeDigest: stateDigest,
        rollbackSnapshotDigest: stateDigest, targetCatalogDigest: catalog.catalogDigest, targetInstallationIds: [], status: "blocked", blockers: [{ code: "source_drift" }] },
        scalarIdentityDigest: entry.scalarIdentityDigest, stateDigest: entry.stateDigest }; },
      blocked: () => { throw new Error("unexpected"); }, visit: async () => { visits += 1; }, preparedBytes: value => Buffer.byteLength(JSON.stringify(value), "utf8"), now: () => 0,
      maximumPreparedBytes: 32 * 1024 * 1024,
    });
    expect(statSync(new URL("../../../../config/openai-plugin-catalog.lock.json", import.meta.url)).size).toBe(908_908);
    expect(getCatalogCalls).toBe(1); expect(references.size).toBe(1);
    expect(visits).toBe(1_000); expect(report.projectCount).toBe(1_000);
    expect(report.sharedRetainedBytes).toBe(Buffer.byteLength(JSON.stringify(catalogLock), "utf8")); expect(report.retainedBytes).toBeLessThan(32 * 1024 * 1024);
  });

  it("turns deleted, invalid, and changed manifest members into explicit blockers and excludes mid-run creations", async () => {
    const entries: readonly MigrationProjectManifestEntry[] = [0, 1, 2].map(index => ({ projectId: projectId(index), scalarIdentityDigest, stateDigest })); const visited: string[] = [];
    let newProjectCreated = false;
    await executeMigrationManifest({ materialize: async () => ({ entries, snapshotToken: "b".repeat(64) }),
      loadShared: async () => ({ value: null, retainedBytes: 0 }), prepare: async entry => {
        if (entry.projectId === projectId(0)) throw new Error("project_not_found");
        if (entry.projectId === projectId(1)) throw new Error("migration_project_invalid");
        newProjectCreated = true; return { value: entry.projectId, scalarIdentityDigest: entry.scalarIdentityDigest, stateDigest: "c".repeat(64) };
      }, blocked: (entry, code) => `${entry.projectId}:${code}`, visit: async value => { visited.push(value); }, preparedBytes: value => value.length, now: () => 0 });
    expect(visited).toEqual([`${projectId(0)}:snapshot_changed`, `${projectId(1)}:migration_project_invalid`, `${projectId(2)}:snapshot_changed`]);
    expect(newProjectCreated).toBe(true); expect(visited.some(value => value.includes(projectId(3)))).toBe(false);
  });

  it("is fresh per execution and includes a project created after the first manifest", async () => {
    let total = 2; const first = await materializeMigrationProjectManifest({ readPage: source(() => total, { reads: 0 }), now: () => 0 }); total = 3;
    const second = await materializeMigrationProjectManifest({ readPage: source(() => total, { reads: 0 }), now: () => 0 }); expect(first.entries).toHaveLength(2); expect(second.entries).toHaveLength(3);
  });
});
