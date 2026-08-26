import { describe, expect, it } from "vitest";
import { abortMigrationTransaction, migrationTransactionFailure, runGuardedMigrationTransaction } from "@/src/application/services/plugin-migration-transaction";
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
});
