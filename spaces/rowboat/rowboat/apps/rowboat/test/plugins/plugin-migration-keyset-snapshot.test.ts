import { statSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { executeMigrationManifest, materializeMigrationProjectManifest, materializeMigrationProjectManifestInTransaction,
  type MigrationProjectManifestEntry, type MigrationProjectSizeCandidate } from "@/src/application/services/plugin-migration-keyset-snapshot";
import { captureMigrationProjectManifestCandidate, captureMigrationProjectSizeCandidate } from "@/src/application/services/plugin-migration-project-state";
import catalogLock from "../../../../config/openai-plugin-catalog.lock.json";
import customerSupport from "@/app/lib/prebuilt-cards/customer-support.json";

const scalarIdentityDigest = "a".repeat(64); const stateDigest = "b".repeat(64);
const projectId = (index: number) => `11111111-1111-4111-8111-${String(index).padStart(12, "0")}`;
const sizeCandidate = (index: number, projectBsonBytes = 512): MigrationProjectSizeCandidate => Object.freeze({ projectId: projectId(index), scalarIdentityDigest, projectBsonBytes });
const fullCandidate = (index: number, capturedBytes = 512) => Object.freeze({ projectId: projectId(index), scalarIdentityDigest, stateDigest, capturedBytes });

function paged(total: () => number, counters: { reads: number }, bsonBytes = 512) {
  return async (after: string | undefined, limit: number, _remainingMs: number, _signal: AbortSignal) => {
    counters.reads += 1; const start = after === undefined ? 0 : Number(after.slice(-12)) + 1;
    return Array.from({ length: Math.max(0, Math.min(limit, total() - start)) }, (_value, offset) => sizeCandidate(start + offset, bsonBytes));
  };
}
const fullReader = async (id: string) => fullCandidate(Number(id.slice(-12)));

describe("plugin migration manifest snapshots", () => {
  it("materializes the 1k two-stage boundary with bounded metadata pages and one full resident project", async () => {
    const counters = { reads: 0, fullReads: 0, resident: 0, maximumResident: 0 }; let clock = 0;
    const report = await materializeMigrationProjectManifest({ readPage: paged(() => 1_000, counters),
      readProject: async id => { counters.fullReads += 1; counters.resident += 512; counters.maximumResident = Math.max(counters.maximumResident, counters.resident);
        const selected = await fullReader(id); counters.resident -= 512; return selected; }, now: () => clock++, pageSize: 32,
      maximumProjects: 1_000, maximumBytes: 256_000, maximumSourceBytes: 64 * 1024 * 1024, maximumProjectBsonBytes: 1024 * 1024, maximumDurationMs: 20_000 });
    expect(report.entries).toHaveLength(1_000); expect(counters.reads).toBeLessThanOrEqual(33); expect(counters.fullReads).toBe(1_000);
    expect(counters.maximumResident).toBeLessThanOrEqual(1024 * 1024); expect(report.eligibleReportedBytes).toBe(512_000); expect(report.capturedBytes).toBe(512_000);
  });

  it("hashes 1k real-size workflows one at a time and retains digests only", async () => {
    const workflow = { ...customerSupport, lastUpdatedAt: "2026-08-26T09:59:59.000Z" }; const counters = { reads: 0 }; let resident = 0; let maximumResident = 0;
    const readPage = async (after: string | undefined, limit: number) => { counters.reads += 1; const start = after === undefined ? 0 : Number(after.slice(-12)) + 1;
      return Array.from({ length: Math.max(0, Math.min(limit, 1_000 - start)) }, (_value, offset) => captureMigrationProjectSizeCandidate({
        _id: projectId(start + offset), lastUpdatedAt: "2026-08-26T10:00:00.000Z", version: 1, projectBsonBytes: 16_384,
      })); };
    const report = await materializeMigrationProjectManifest({ readPage, readProject: async id => {
      const selected = captureMigrationProjectManifestCandidate({ _id: id, lastUpdatedAt: "2026-08-26T10:00:00.000Z", version: 1, draftWorkflow: workflow, liveWorkflow: workflow });
      resident += selected.capturedBytes; maximumResident = Math.max(maximumResident, resident); resident -= selected.capturedBytes; return selected;
    }, now: () => 0, pageSize: 32, maximumProjectBsonBytes: 1024 * 1024, maximumSourceBytes: 64 * 1024 * 1024 });
    expect(report.entries).toHaveLength(1_000); expect(report.capturedBytes).toBeGreaterThan(1_000_000); expect(maximumResident).toBeLessThan(1024 * 1024);
    expect(Object.keys(report.entries[0]!).sort()).toEqual(["projectId", "scalarIdentityDigest", "stateDigest"]);
  });

  it("retains 33 simulated 16MiB projects as typed blockers without fetching or materializing them", async () => {
    let fullReads = 0; const counters = { reads: 0 };
    const report = await materializeMigrationProjectManifest({ readPage: paged(() => 33, counters, 16 * 1024 * 1024), readProject: async () => { fullReads += 1; throw new Error("unexpected"); },
      now: () => 0, maximumProjectBsonBytes: 1024 * 1024, maximumSourceBytes: 64 * 1024 * 1024 });
    expect(fullReads).toBe(0); expect(report.entries).toHaveLength(33); expect(report.capturedBytes).toBe(0); expect(report.maximumResidentBytes).toBe(0);
    expect(report.entries[0]).toEqual({ projectId: projectId(0), projectBsonBytes: 16 * 1024 * 1024, blockerCode: "project_too_large" });
  });

  it("fails closed above count, retained-byte, eligible-source-byte, and time limits", async () => {
    await expect(materializeMigrationProjectManifest({ readPage: paged(() => 1_001, { reads: 0 }), readProject: fullReader, now: () => 0, maximumProjects: 1_000 })).rejects.toThrow("migration_project_limit");
    await expect(materializeMigrationProjectManifest({ readPage: paged(() => 2, { reads: 0 }), readProject: fullReader, now: () => 0, maximumBytes: 20 })).rejects.toThrow("migration_manifest_limit");
    await expect(materializeMigrationProjectManifest({ readPage: paged(() => 2, { reads: 0 }), readProject: fullReader, now: () => 0, maximumSourceBytes: 20 })).rejects.toThrow("migration_manifest_limit");
    let clock = 0; await expect(materializeMigrationProjectManifest({ readPage: paged(() => 2, { reads: 0 }), readProject: fullReader, now: () => clock += 10, maximumDurationMs: 5 })).rejects.toThrow("migration_manifest_limit");
  });

  it.each(["cooperative", "hung"] as const)("aborts a %s metadata query near the deadline and suppresses late work", async mode => {
    const started = Date.now(); let observedSignal: AbortSignal | undefined;
    const readPage = async (_after: string | undefined, _limit: number, _remainingMs: number, signal: AbortSignal): Promise<readonly MigrationProjectSizeCandidate[]> => {
      observedSignal = signal; return new Promise((_resolve, reject) => { if (mode === "cooperative") signal.addEventListener("abort", () => reject(new Error("driver_abort")), { once: true }); });
    };
    await expect(materializeMigrationProjectManifest({ readPage, readProject: fullReader, now: () => Date.now(), maximumDurationMs: 20 })).rejects.toThrow("migration_manifest_limit");
    expect(Date.now() - started).toBeLessThan(80); expect(observedSignal?.aborted).toBe(true);
  });

  it("passes one signal and decreasing remaining maxTimeMS to both query stages", async () => {
    const signals = new Set<AbortSignal>(); const remaining: number[] = []; let tick = 0;
    const report = await materializeMigrationProjectManifest({ readPage: async (after, _limit, remainingMs, signal) => {
      signals.add(signal); remaining.push(remainingMs); return after === undefined ? [sizeCandidate(0)] : [];
    }, readProject: async (_id, remainingMs, signal) => { signals.add(signal); remaining.push(remainingMs); return fullCandidate(0); }, now: () => tick++, maximumDurationMs: 100 });
    expect(report.entries).toHaveLength(1); expect(signals.size).toBe(1); expect(remaining.length).toBe(2);
    expect(remaining[0]!).toBeGreaterThan(remaining[1]!);
  });

  it("always aborts and ends its snapshot transaction when a query deadline fires", async () => {
    const events: string[] = []; let active = false;
    const transaction = { start: (maxCommitTimeMS: number) => { active = true; events.push(`start:${maxCommitTimeMS}`); }, inTransaction: () => active,
      commit: async () => { active = false; events.push("commit"); }, abort: async () => { active = false; events.push("abort"); }, end: async () => { events.push("end"); } };
    await expect(materializeMigrationProjectManifestInTransaction({ transaction, readPage: async () => new Promise(() => undefined), readProject: fullReader,
      now: () => Date.now(), maximumDurationMs: 20 })).rejects.toThrow("migration_manifest_limit");
    expect(events).toEqual(["start:20", "abort", "end"]); expect(active).toBe(false);
  });

  it("rejects accessor-backed metadata without invoking accessors", async () => {
    let calls = 0; const malicious: Record<string, unknown> = { scalarIdentityDigest, projectBsonBytes: 512 };
    Object.defineProperty(malicious, "projectId", { enumerable: true, get: () => { calls += 1; return projectId(0); } });
    await expect(materializeMigrationProjectManifest({ readPage: async () => [malicious as unknown as MigrationProjectSizeCandidate], readProject: fullReader, now: () => 0 })).rejects.toThrow("migration_snapshot_invalid");
    expect(calls).toBe(0);
  });

  it("loads the real catalog once after materialization and processes 1k public rows", async () => {
    const entries: readonly MigrationProjectManifestEntry[] = Array.from({ length: 1_000 }, (_value, index) => ({ projectId: projectId(index), scalarIdentityDigest, stateDigest }));
    let getCatalogCalls = 0; const references = new Set<unknown>(); let visits = 0;
    const report = await executeMigrationManifest({ materialize: async () => ({ entries, snapshotToken: stateDigest }),
      loadShared: async () => { getCatalogCalls += 1; return { value: catalogLock, retainedBytes: Buffer.byteLength(JSON.stringify(catalogLock), "utf8") }; },
      prepare: async (entry, catalog) => { if (!("stateDigest" in entry)) throw new Error("unexpected"); references.add(catalog); return { value: { projectId: entry.projectId, status: "blocked" }, scalarIdentityDigest: entry.scalarIdentityDigest, stateDigest: entry.stateDigest }; },
      blocked: () => { throw new Error("unexpected"); }, visit: async () => { visits += 1; }, preparedBytes: value => Buffer.byteLength(JSON.stringify(value), "utf8"), now: () => 0 });
    expect(statSync(new URL("../../../../config/openai-plugin-catalog.lock.json", import.meta.url)).size).toBe(908_908); expect(getCatalogCalls).toBe(1); expect(references.size).toBe(1);
    expect(visits).toBe(1_000); expect(report.projectCount).toBe(1_000); expect(report.retainedBytes).toBeLessThan(32 * 1024 * 1024);
  });

  it("turns oversized, deleted, invalid, and changed members into explicit blockers", async () => {
    const entries: readonly MigrationProjectManifestEntry[] = [
      { projectId: projectId(0), projectBsonBytes: 16 * 1024 * 1024, blockerCode: "project_too_large" },
      ...[1, 2, 3].map(index => ({ projectId: projectId(index), scalarIdentityDigest, stateDigest }) as const),
    ]; const visited: string[] = [];
    await executeMigrationManifest({ materialize: async () => ({ entries, snapshotToken: stateDigest }), loadShared: async () => ({ value: null, retainedBytes: 0 }),
      prepare: async entry => { if (!("stateDigest" in entry)) throw new Error("unexpected"); if (entry.projectId === projectId(1)) throw new Error("project_not_found"); if (entry.projectId === projectId(2)) throw new Error("migration_project_invalid");
        return { value: entry.projectId, scalarIdentityDigest: entry.scalarIdentityDigest, stateDigest: "c".repeat(64) }; },
      blocked: (entry, code) => `${entry.projectId}:${code}`, visit: async value => { visited.push(value); }, preparedBytes: value => value.length, now: () => 0 });
    expect(visited).toEqual([`${projectId(0)}:project_too_large`, `${projectId(1)}:snapshot_changed`, `${projectId(2)}:migration_project_invalid`, `${projectId(3)}:snapshot_changed`]);
  });
});
