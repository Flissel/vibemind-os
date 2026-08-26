import { describe, expect, it } from "vitest";
import { abortMigrationTransaction, exactMigrationRecoveryEvidence, migrationMongoDeadlineOptions, migrationTransactionFailure, runGuardedMigrationOperation, runGuardedMigrationTransaction } from "@/src/application/services/plugin-migration-transaction";
import { createMigrationDeadline } from "@/src/application/services/plugin-migration-keyset-snapshot";

describe("plugin migration transaction failure boundary", () => {
  it("aborts staged writes and safely maps a concurrent Mongo write conflict", async () => {
    const staged = ["rollback", "installation", "record"]; let active = true; let aborts = 0;
    const session = { inTransaction: () => active, abortTransaction: async () => { aborts += 1; staged.length = 0; active = false; } };
    await abortMigrationTransaction(session);
    expect(staged).toEqual([]); expect(aborts).toBe(1); expect(migrationTransactionFailure(Object.assign(new Error("WriteConflict raw details"), { code: 112 })).message).toBe("migration_repository_failed");
  });

  it("preserves only exact safe migration errors after rollback", () => {
    expect(migrationTransactionFailure(new Error("migration_pointer_conflict")).message).toBe("migration_pointer_conflict");
    expect(migrationTransactionFailure(new Error("migration_pointer_conflict raw")).message).toBe("migration_repository_failed");
  });

  it("rolls back staged writes when the caller aborts before commit", async () => {
    const caller = new AbortController(); const staged: string[] = []; const counters = { aborts: 0, commits: 0, ends: 0 }; let active = false;
    const deadline = createMigrationDeadline(() => Date.now(), 100, caller.signal);
    const transaction = { start: () => { active = true; }, inTransaction: () => active, commit: async () => { counters.commits += 1; },
      abort: async () => { counters.aborts += 1; staged.length = 0; active = false; }, end: async () => { counters.ends += 1; } };
    const pending = runGuardedMigrationTransaction({ deadline, transaction, work: async () => { staged.push("rollback"); caller.abort(); await new Promise(() => undefined); }, recover: async () => null });
    await expect(pending).rejects.toThrow("request_aborted"); expect(staged).toEqual([]); expect(counters).toEqual({ aborts: 1, commits: 0, ends: 1 });
  });

  it("passes one signal and decreasing maxTimeMS to every transactional operation", async () => {
    let tick = 1_000; const deadline = createMigrationDeadline(() => tick++, 100);
    const observed: Array<Readonly<{ maxTimeMS: number; signal: AbortSignal }>> = [];
    const stages = ["idempotency", "nonce", "project", "rollback", "installation", "admission", "record", "pointer"];
    for (const _stage of stages) await runGuardedMigrationOperation(deadline, async (remainingMs, signal) => {
      observed.push(migrationMongoDeadlineOptions({}, remainingMs, signal)); return undefined;
    });
    expect(new Set(observed.map(selected => selected.signal)).size).toBe(1);
    expect(observed.every((selected, index) => index === 0 || selected.maxTimeMS < observed[index - 1]!.maxTimeMS)).toBe(true);
  });

  it.each(["idempotency", "nonce", "project", "rollback", "installation", "admission", "record", "pointer"])(
    "waits for the %s operation to cancel before aborting and ending its session",
    async selectedStage => {
      const caller = new AbortController(); const deadline = createMigrationDeadline(() => Date.now(), 250, caller.signal);
      const events: string[] = []; let active = false;
      const transaction = { start: () => { active = true; }, inTransaction: () => active, commit: async () => { events.push("commit"); },
        abort: async () => { events.push("abort"); active = false; }, end: async () => { events.push("end"); } };
      const stages = ["idempotency", "nonce", "project", "rollback", "installation", "admission", "record", "pointer"];
      const pending = runGuardedMigrationTransaction({ deadline, transaction, work: async () => {
        for (const stage of stages) {
          await runGuardedMigrationOperation(deadline, async (_remainingMs, signal) => {
            if (stage !== selectedStage) return undefined;
            const operation = new Promise<never>((_resolve, reject) => signal.addEventListener("abort", () => {
              setTimeout(() => { events.push(`cancel:${stage}`); reject(new Error("cancelled")); }, 5);
            }, { once: true }));
            queueMicrotask(() => caller.abort()); return operation;
          });
        }
      }, recover: async () => null });
      await expect(pending).rejects.toThrow("request_aborted");
      expect(events).toEqual([`cancel:${selectedStage}`, "abort", "end"]);
    },
  );

  it.each(["missing", "recovered"] as const)("treats a timed-out commit as uncertain and uses exact recovery when %s", async outcome => {
    const counters = { aborts: 0, commits: 0, ends: 0 }; let active = false; let committed = false;
    const deadline = createMigrationDeadline(() => Date.now(), 20);
    const transaction = { start: () => { active = true; }, inTransaction: () => active,
      commit: async () => { counters.commits += 1; await new Promise(resolve => setTimeout(resolve, 35)); committed = true; active = false; },
      abort: async () => { counters.aborts += 1; active = false; }, end: async () => { counters.ends += 1; } };
    const pending = runGuardedMigrationTransaction({ deadline, transaction, work: async () => "receipt",
      recover: async () => { if (outcome === "recovered") await new Promise(resolve => setTimeout(resolve, 25)); return committed ? "replayed" : null; } });
    if (outcome === "recovered") await expect(pending).resolves.toBe("replayed"); else await expect(pending).rejects.toThrow("migration_commit_uncertain");
    expect(counters).toEqual({ aborts: 0, commits: 1, ends: 1 });
  });

  it.each(["sourceProjectRevision", "sourceStateDigest", "catalogDigest", "extra"] as const)("rejects replay evidence with pointer %s drift", field => {
    const expected = recoveryEvidence(); const actual = structuredClone(expected);
    if (field === "extra") (actual.pointer as Record<string, unknown>).extra = "forbidden";
    else (actual.pointer as Record<string, unknown>)[field] = field === "sourceProjectRevision" ? 2 : "f".repeat(64);
    expect(exactMigrationRecoveryEvidence(expected, actual)).toBe(false);
  });

  it("rejects corrupt records and accessor/proxy replay evidence without invoking them", () => {
    const expected = recoveryEvidence(); const corrupt = structuredClone(expected); (corrupt.record as Record<string, unknown>).status = "previewed";
    expect(exactMigrationRecoveryEvidence(expected, corrupt)).toBe(false);
    let calls = 0; const accessor = structuredClone(expected);
    Object.defineProperty(accessor.pointer, "catalogDigest", { enumerable: true, get: () => { calls += 1; return "a".repeat(64); } });
    expect(exactMigrationRecoveryEvidence(expected, accessor)).toBe(false); expect(calls).toBe(0);
    expect(exactMigrationRecoveryEvidence(expected, new Proxy(structuredClone(expected), {}))).toBe(false);
  });

  it.each(["record", "nonce", "idempotency", "installation", "admission"] as const)("rejects missing, extra, or mutated %s recovery truth", section => {
    const expected = recoveryEvidence();
    const missing = structuredClone(expected); const extra = structuredClone(expected); const mutated = structuredClone(expected);
    if (section === "record") {
      delete (missing.record as Record<string, unknown>).status; (extra.record as Record<string, unknown>).extra = true;
      (mutated.record as Record<string, unknown>).sourceProjectRevision = 2;
    } else if (section === "nonce") {
      delete (missing.nonce as Record<string, unknown>).idempotencyKey; (extra.nonce as Record<string, unknown>).extra = true;
      (mutated.nonce as Record<string, unknown>).projectId = "99999999-9999-4999-8999-999999999999";
    } else if (section === "idempotency") {
      delete (missing.idempotency as Record<string, unknown>).payloadDigest; (extra.idempotency as Record<string, unknown>).extra = true;
      (mutated.idempotency as Record<string, unknown>).receiptIds = [];
    } else if (section === "installation") {
      delete (missing.installations[0] as Record<string, unknown>).providerBindingsJson; (extra.installations[0] as Record<string, unknown>).extra = true;
      (mutated.installations[0] as Record<string, unknown>).id = "99999999-9999-4999-8999-999999999999";
    } else {
      delete (missing.admissions[0] as Record<string, unknown>).componentDigest; (extra.admissions[0] as Record<string, unknown>).extra = true;
      (mutated.admissions[0] as Record<string, unknown>).componentDigest = "f".repeat(64);
    }
    expect(exactMigrationRecoveryEvidence(expected, missing)).toBe(false);
    expect(exactMigrationRecoveryEvidence(expected, extra)).toBe(false);
    expect(exactMigrationRecoveryEvidence(expected, mutated)).toBe(false);
  });
});

function recoveryEvidence() {
  const pointer = { migrationRecordId: "22222222-2222-4222-8222-222222222222", sourceProjectRevision: 1, sourceStateDigest: "a".repeat(64), catalogDigest: "b".repeat(64) };
  return { pointer, record: { id: pointer.migrationRecordId, status: "applied", sourceProjectRevision: 1 },
    nonce: { _id: "nonce", projectId: "11111111-1111-4111-8111-111111111111", idempotencyKey: "migration-id" },
    idempotency: { _id: "migration-id", payloadDigest: "c".repeat(64), projectId: "11111111-1111-4111-8111-111111111111",
      migrationRecordId: pointer.migrationRecordId, receiptIds: [pointer.migrationRecordId], generatedAt: "2026-08-26T10:00:00.000Z" },
    installations: [{ id: "33333333-3333-4333-8333-333333333333", providerBindingsJson: "[]" }], admissions: [{ installationId: "33333333-3333-4333-8333-333333333333", componentDigest: "d".repeat(64) }] };
}
