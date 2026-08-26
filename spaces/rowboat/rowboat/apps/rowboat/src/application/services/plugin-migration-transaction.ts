import type { MigrationDeadline } from "./plugin-migration-keyset-snapshot";

interface AbortableMigrationSession { inTransaction(): boolean; abortTransaction(): Promise<unknown> }
const SAFE_TRANSACTION_ERRORS = new Set([
  "migration_rollback_conflict", "migration_installation_conflict", "migration_admission_conflict", "migration_record_conflict",
  "migration_confirmation_replayed", "migration_idempotency_conflict", "migration_pointer_conflict", "migration_preview_stale",
  "request_aborted", "migration_preview_timeout", "migration_commit_uncertain",
]);

export async function abortMigrationTransaction(session: AbortableMigrationSession): Promise<void> {
  if (session.inTransaction()) await session.abortTransaction();
}

export function migrationTransactionFailure(error: unknown): Error {
  if (error instanceof Error && Object.getPrototypeOf(error) === Error.prototype && SAFE_TRANSACTION_ERRORS.has(error.message)) return new Error(error.message);
  return new Error("migration_repository_failed");
}

interface GuardedMigrationTransaction {
  readonly start: (maxCommitTimeMS: number) => void; readonly inTransaction: () => boolean;
  readonly commit: () => Promise<void>; readonly abort: () => Promise<void>; readonly end: () => Promise<void>;
}

async function settle<T>(operation: () => Promise<T>, timeoutMs: number): Promise<Readonly<{ completed: boolean; value?: T }>> {
  const pending = Promise.resolve().then(operation); pending.catch(() => undefined);
  let timeout: ReturnType<typeof setTimeout> | undefined;
  const expired = new Promise<Readonly<{ completed: false }>>(resolve => { timeout = setTimeout(() => resolve({ completed: false }), timeoutMs); });
  try { return await Promise.race([pending.then(value => ({ completed: true as const, value }), () => ({ completed: false as const })), expired]); }
  finally { if (timeout !== undefined) clearTimeout(timeout); }
}

export async function runGuardedMigrationTransaction<T>(input: Readonly<{
  deadline: MigrationDeadline; transaction: GuardedMigrationTransaction; work: () => Promise<T>; recover: () => Promise<T | null>;
}>): Promise<T> {
  let started = false; let commitStarted = false; let committed = false; let hasResult = false; let result: T | undefined; let primary: unknown;
  try {
    input.transaction.start(input.deadline.remaining("migration_preview_timeout")); started = true;
    try { result = await input.deadline.run(async () => input.work(), "migration_preview_timeout"); hasResult = true; }
    catch (error) { primary = error; }
    if (primary === undefined) {
      commitStarted = true;
      try { await input.deadline.run(async () => input.transaction.commit(), "migration_preview_timeout"); committed = true; }
      catch {
        const recovered = await settle(input.recover, 1_000);
        if (recovered.completed && recovered.value !== null && recovered.value !== undefined) { result = recovered.value; hasResult = true; committed = true; }
        else primary = new Error("migration_commit_uncertain");
      }
    }
    if (primary !== undefined && !commitStarted && started && input.transaction.inTransaction()) {
      await settle(() => input.transaction.abort(), input.deadline.signal.aborted ? 10 : 1_000);
    }
  } catch (error) { primary = migrationTransactionFailure(error); }
  finally {
    const ended = await settle(() => input.transaction.end(), input.deadline.signal.aborted ? 10 : 1_000);
    if (!ended.completed && primary === undefined && !committed) primary = new Error("migration_repository_failed");
  }
  if (primary !== undefined) throw primary;
  if (!hasResult) throw new Error("migration_repository_failed");
  return result as T;
}
