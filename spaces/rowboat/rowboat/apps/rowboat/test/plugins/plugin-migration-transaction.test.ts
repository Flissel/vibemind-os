import { describe, expect, it } from "vitest";
import { abortMigrationTransaction, migrationTransactionFailure } from "@/src/application/services/plugin-migration-transaction";

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
});
