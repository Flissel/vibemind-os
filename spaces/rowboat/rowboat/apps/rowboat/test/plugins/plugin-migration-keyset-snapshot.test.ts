import { statSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { createMigrationChildDeadline, createMigrationDeadline, executeMigrationManifest, materializeMigrationProjectManifest, materializeMigrationProjectManifestInTransaction,
  type MigrationProjectManifestEntry, type MigrationProjectSizeCandidate } from "@/src/application/services/plugin-migration-keyset-snapshot";
import { captureMigrationProjectManifestCandidate, captureMigrationProjectSizeCandidate } from "@/src/application/services/plugin-migration-project-state";
import catalogLock from "../../../../config/openai-plugin-catalog.lock.json";
import customerSupport from "@/app/lib/prebuilt-cards/customer-support.json";
import { runMigrationReadTransaction } from "@/src/application/services/plugin-migration-session-finalizer";

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
    await expect(materializeMigrationProjectManifestInTransaction({ transaction, readPage: async (_after, _limit, _remaining, signal) => new Promise((_resolve, reject) =>
      signal.addEventListener("abort", () => reject(new Error("cancelled")), { once: true })), readProject: fullReader,
      now: () => Date.now(), maximumDurationMs: 20 })).rejects.toThrow("migration_manifest_limit");
    expect(events).toEqual(["start:20", "abort", "end"]); expect(active).toBe(false);
  });

  it("does not abort or end a snapshot session before an uncooperative read settles", async () => {
    const events: string[] = []; let active = false; const caller = new AbortController(); const root = createMigrationDeadline(() => Date.now(), 1_000, caller.signal);
    const read = deferred<readonly MigrationProjectSizeCandidate[]>(); const started = deferred<void>(); const ended = deferred<void>();
    const transaction = { start: () => { active = true; }, inTransaction: () => active, commit: async () => undefined,
      abort: async () => { events.push("abort"); active = false; }, end: async () => { events.push("end"); ended.resolve(); } };
    const pending = materializeMigrationProjectManifestInTransaction({ transaction, readPage: async () => { started.resolve(); return read.promise; },
      readProject: fullReader, now: () => Date.now(), maximumDurationMs: 1_000, deadline: root });
    await started.promise; caller.abort(); await expect(pending).rejects.toThrow("request_aborted"); expect(events).toEqual([]);
    events.push("read:settled"); read.resolve([]); await ended.promise; expect(events).toEqual(["read:settled", "abort", "end"]);
  });

  it.each([
    ["start", true, false], ["end", false, true], ["start+end", true, true],
  ] as const)("ends exactly once and reports a safe error when %s throws", async (_name, startThrows, endThrows) => {
    const counters = { starts: 0, commits: 0, aborts: 0, ends: 0 }; let active = false;
    const transaction = { start: () => { counters.starts += 1; if (startThrows) throw new Error("raw_start_secret"); active = true; }, inTransaction: () => active,
      commit: async () => { counters.commits += 1; active = false; }, abort: async () => { counters.aborts += 1; active = false; },
      end: async () => { counters.ends += 1; if (endThrows) throw new Error("raw_end_secret"); } };
    await expect(materializeMigrationProjectManifestInTransaction({ transaction, readPage: async () => [], readProject: fullReader, now: () => 0 })).rejects.toThrow("migration_repository_failed");
    expect(counters).toEqual({ starts: 1, commits: startThrows ? 0 : 1, aborts: 0, ends: 1 });
  });

  it("bounds hung abort and end cleanup after a snapshot timeout", async () => {
    const counters = { aborts: 0, ends: 0 }; const startedAt = Date.now();
    const transaction = { start: () => undefined, inTransaction: () => true, commit: async () => undefined,
      abort: async () => { counters.aborts += 1; return new Promise<void>(() => undefined); },
      end: async () => { counters.ends += 1; return new Promise<void>(() => undefined); } };
    await expect(materializeMigrationProjectManifestInTransaction({ transaction, readPage: async (_after, _limit, _remaining, signal) => new Promise((_resolve, reject) =>
      signal.addEventListener("abort", () => reject(new Error("cancelled")), { once: true })), readProject: fullReader,
      now: () => Date.now(), maximumDurationMs: 20 })).rejects.toThrow("migration_manifest_limit");
    expect(Date.now() - startedAt).toBeLessThan(80); expect(counters).toEqual({ aborts: 1, ends: 0 });
  });

  it.each(["hang", "late-reject", "late-success"] as const)("bounds a %s commit, aborts when still active, and ends exactly once", async mode => {
    const counters = { commits: 0, aborts: 0, ends: 0 }; let active = false; const caller = new AbortController();
    const root = createMigrationDeadline(() => Date.now(), 1_000, caller.signal); const commit = deferred<void>(); const started = deferred<void>(); const ended = deferred<void>();
    const transaction = { start: () => { active = true; }, inTransaction: () => active,
      commit: async () => { counters.commits += 1; started.resolve(); return commit.promise; },
      abort: async () => { counters.aborts += 1; active = false; }, end: async () => { counters.ends += 1; ended.resolve(); } };
    const pending = materializeMigrationProjectManifestInTransaction({ transaction, readPage: async () => [], readProject: fullReader,
      now: () => Date.now(), maximumDurationMs: 1_000, deadline: root });
    await started.promise; caller.abort(); await expect(pending).rejects.toThrow("request_aborted");
    if (mode === "hang") expect(counters).toEqual({ commits: 1, aborts: 0, ends: 0 });
    else { if (mode === "late-success") { active = false; commit.resolve(); } else commit.reject(new Error("late_commit_secret"));
      await ended.promise; expect(counters).toEqual(mode === "late-success" ? { commits: 1, aborts: 0, ends: 1 } : { commits: 1, aborts: 1, ends: 1 }); }
  });

  it("preserves a safe commit failure when abort and end also throw", async () => {
    const counters = { aborts: 0, ends: 0 }; let active = false;
    const transaction = { start: () => { active = true; }, inTransaction: () => active, commit: async () => { throw new Error("raw_commit_secret"); },
      abort: async () => { counters.aborts += 1; throw new Error("raw_abort_secret"); }, end: async () => { counters.ends += 1; throw new Error("raw_end_secret"); } };
    await expect(materializeMigrationProjectManifestInTransaction({ transaction, readPage: async () => [], readProject: fullReader, now: () => Date.now() }))
      .rejects.toThrow("migration_repository_failed");
    expect(counters).toEqual({ aborts: 1, ends: 1 });
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
    expect(statSync(new URL("../../../../config/openai-plugin-catalog.lock.json", import.meta.url)).size).toBe(952_036); expect(getCatalogCalls).toBe(1); expect(references.size).toBe(1);
    expect(visits).toBe(1_000); expect(report.projectCount).toBe(1_000); expect(report.retainedBytes).toBeLessThan(32 * 1024 * 1024);
  });

  it("keeps the overall manifest deadline alive after closing its child snapshot session", async () => {
    let active = false; let sharedLoads = 0;
    const report = await executeMigrationManifest({ materialize: async (_remaining, _signal, deadline) =>
      materializeMigrationProjectManifestInTransaction({ transaction: { start: () => { active = true; }, inTransaction: () => active,
        commit: async () => { active = false; }, abort: async () => { active = false; }, end: async () => undefined },
      readPage: async () => [], readProject: fullReader, now: () => Date.now(), deadline }),
    loadShared: async () => { sharedLoads += 1; return { value: "catalog", retainedBytes: 7 }; }, prepare: async () => { throw new Error("unexpected"); },
    blocked: () => { throw new Error("unexpected"); }, visit: async () => undefined, preparedBytes: () => 0, now: () => Date.now() });
    expect(sharedLoads).toBe(1); expect(report.projectCount).toBe(0);
  });

  it("uses owned child deadlines for every prepared project without cancelling the root", async () => {
    const entries: readonly MigrationProjectManifestEntry[] = Array.from({ length: 5 }, (_value, index) => ({ projectId: projectId(index), scalarIdentityDigest, stateDigest }));
    let prepares = 0; let visits = 0;
    const report = await executeMigrationManifest({ materialize: async () => ({ entries, snapshotToken: stateDigest }),
      loadShared: async () => ({ value: "catalog", retainedBytes: 7 }), prepare: async (entry, _shared, _remaining, _signal, root) => {
        const child = createMigrationChildDeadline(root, () => Date.now(), 5_000); let active = false;
        await runMigrationReadTransaction({ deadline: child, session: { start: () => { active = true; }, inTransaction: () => active,
          commit: async () => { active = false; }, abort: async () => { active = false; }, end: async () => undefined }, work: async () => { prepares += 1; } });
        expect(root.signal.aborted).toBe(false); return { value: entry, scalarIdentityDigest: entry.scalarIdentityDigest, stateDigest: entry.stateDigest };
      }, blocked: () => { throw new Error("unexpected"); }, visit: async () => { visits += 1; }, preparedBytes: () => 1, now: () => Date.now() });
    expect(prepares).toBe(5); expect(visits).toBe(5); expect(report.projectCount).toBe(5);
  });

  it.each(["catalog", "prepare"] as const)("bounds a hung %s step with the overall preview deadline", async step => {
    const started = Date.now(); let observedSignal: AbortSignal | undefined;
    const entries: readonly MigrationProjectManifestEntry[] = [{ projectId: projectId(0), scalarIdentityDigest, stateDigest }];
    await expect(executeMigrationManifest({ materialize: async () => ({ entries, snapshotToken: stateDigest }),
      loadShared: async (_remainingMs, signal) => { observedSignal = signal; return step === "catalog" ? new Promise(() => undefined) : { value: null, retainedBytes: 0 }; },
      prepare: async (_entry, _shared, _remainingMs, signal) => { observedSignal = signal; return new Promise(() => undefined); },
      blocked: () => { throw new Error("unexpected"); }, visit: async () => undefined, preparedBytes: () => 0, now: () => Date.now(), maximumDurationMs: 20,
    })).rejects.toThrow("migration_preview_timeout");
    expect(Date.now() - started).toBeLessThan(80); expect(observedSignal?.aborted).toBe(true);
  });

  it("uses one decreasing deadline and signal for materialize, catalog, and every prepare", async () => {
    const remaining: number[] = []; const signals = new Set<AbortSignal>(); let tick = 0;
    const entries: readonly MigrationProjectManifestEntry[] = [0, 1].map(index => ({ projectId: projectId(index), scalarIdentityDigest, stateDigest }));
    const report = await executeMigrationManifest({ materialize: async (remainingMs, signal) => { remaining.push(remainingMs); signals.add(signal); return { entries, snapshotToken: stateDigest }; },
      loadShared: async (remainingMs, signal) => { remaining.push(remainingMs); signals.add(signal); return { value: null, retainedBytes: 0 }; },
      prepare: async (selected, _shared, remainingMs, signal) => { remaining.push(remainingMs); signals.add(signal); return { value: selected.projectId, scalarIdentityDigest, stateDigest }; },
      blocked: () => { throw new Error("unexpected"); }, visit: async () => undefined, preparedBytes: value => value.length, now: () => tick++, maximumDurationMs: 100 });
    expect(report.projectCount).toBe(2); expect(signals.size).toBe(1); expect(remaining).toHaveLength(4);
    expect(remaining.every((value, index) => index === 0 || remaining[index - 1]! > value)).toBe(true);
  });

  it("maps caller abort and a late catalog rejection to one prompt request abort without unhandled rejection", async () => {
    const caller = new AbortController(); let lateRejected = false; const started = Date.now(); setTimeout(() => caller.abort(), 10);
    const pending = executeMigrationManifest({ materialize: async () => ({ entries: [], snapshotToken: stateDigest }), callerSignal: caller.signal,
      loadShared: async () => new Promise((_resolve, reject) => setTimeout(() => { lateRejected = true; reject(new Error("late_secret")); }, 50)),
      prepare: async () => { throw new Error("unexpected"); }, blocked: () => { throw new Error("unexpected"); }, visit: async () => undefined,
      preparedBytes: () => 0, now: () => Date.now(), maximumDurationMs: 100 });
    await expect(pending).rejects.toThrow("request_aborted"); expect(Date.now() - started).toBeLessThan(80);
    await new Promise(resolve => setTimeout(resolve, 60)); expect(lateRejected).toBe(true);
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

function deferred<T>() {
  let resolve!: (value?: T) => void; let reject!: (error?: unknown) => void;
  const promise = new Promise<T>((selectedResolve, selectedReject) => { resolve = value => selectedResolve(value as T); reject = selectedReject; });
  promise.catch(() => undefined); return { promise, resolve, reject };
}
