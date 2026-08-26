interface AbortableMigrationSession { inTransaction(): boolean; abortTransaction(): Promise<unknown> }
const SAFE_TRANSACTION_ERRORS = new Set([
  "migration_rollback_conflict", "migration_installation_conflict", "migration_admission_conflict", "migration_record_conflict",
  "migration_confirmation_replayed", "migration_idempotency_conflict", "migration_pointer_conflict", "migration_preview_stale",
]);

export async function abortMigrationTransaction(session: AbortableMigrationSession): Promise<void> {
  if (session.inTransaction()) await session.abortTransaction();
}

export function migrationTransactionFailure(error: unknown): Error {
  if (error instanceof Error && Object.getPrototypeOf(error) === Error.prototype && SAFE_TRANSACTION_ERRORS.has(error.message)) return new Error(error.message);
  return new Error("migration_repository_failed");
}
