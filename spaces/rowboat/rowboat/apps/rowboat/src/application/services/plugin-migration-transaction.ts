import type { MigrationDeadline } from "./plugin-migration-keyset-snapshot";
import { canonical, migrationConfirmationTimestamp } from "../use-cases/plugins/plugin-migration.shared";
import { types as utilTypes } from "node:util";
import { finalizeMigrationSession, type MigrationCommitOutcome } from "./plugin-migration-session-finalizer";

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

function captureEvidence(input: unknown, depth = 0, budget: { items: number } = { items: 0 }): unknown {
  if (depth > 16 || ++budget.items > 10_000) throw new Error("migration_recovery_invalid");
  if (input === null || typeof input === "string" || typeof input === "boolean") return input;
  if (typeof input === "number") { if (!Number.isFinite(input)) throw new Error("migration_recovery_invalid"); return input; }
  if (typeof input !== "object" || utilTypes.isProxy(input)) throw new Error("migration_recovery_invalid");
  const prototype = Object.getPrototypeOf(input);
  if (Array.isArray(input)) {
    if (prototype !== Array.prototype) throw new Error("migration_recovery_invalid");
    const output: unknown[] = [];
    for (let index = 0; index < input.length; index += 1) {
      const descriptor = Object.getOwnPropertyDescriptor(input, String(index));
      if (descriptor === undefined || !("value" in descriptor) || !descriptor.enumerable) throw new Error("migration_recovery_invalid");
      output.push(captureEvidence(descriptor.value, depth + 1, budget));
    }
    if (Reflect.ownKeys(input).length !== output.length + 1) throw new Error("migration_recovery_invalid");
    return Object.freeze(output);
  }
  if (prototype !== Object.prototype && prototype !== null) throw new Error("migration_recovery_invalid");
  const output: Record<string, unknown> = Object.create(null) as Record<string, unknown>;
  const keys = Reflect.ownKeys(input);
  if (keys.some(key => typeof key !== "string")) throw new Error("migration_recovery_invalid");
  for (const key of keys as string[]) {
    const descriptor = Object.getOwnPropertyDescriptor(input, key);
    if (descriptor === undefined || !("value" in descriptor) || !descriptor.enumerable) throw new Error("migration_recovery_invalid");
    output[key] = captureEvidence(descriptor.value, depth + 1, budget);
  }
  return Object.freeze(output);
}

export function exactMigrationRecoveryEvidence(expected: unknown, actual: unknown): boolean {
  try { return canonical(captureEvidence(expected)) === canonical(captureEvidence(actual)); }
  catch { return false; }
}

export function migrationRollbackRecoveryRow(recordId: string, rollbackSnapshot: Readonly<Record<string, unknown>>,
  rollbackSnapshotDigest: string): Readonly<Record<string, unknown>> {
  return Object.freeze({ _id: recordId, ...rollbackSnapshot, rollbackSnapshotDigest });
}

export function migrationReportGeneratedAt(issuedAt: string): string {
  return migrationConfirmationTimestamp(issuedAt);
}

export async function runGuardedMigrationOperation<T>(deadline: MigrationDeadline,
  operation: (remainingMs: number, signal: AbortSignal) => Promise<T>): Promise<T> {
  return deadline.run(operation, "migration_preview_timeout");
}

export function migrationMongoDeadlineOptions<T extends object>(base: T, remainingMs: number, signal: AbortSignal): T & Readonly<{
  maxTimeMS: number; signal: AbortSignal;
}> {
  if (!Number.isSafeInteger(remainingMs) || remainingMs < 1 || signal.aborted) throw new Error("migration_preview_timeout");
  return Object.freeze({ ...base, maxTimeMS: remainingMs, signal });
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
  let started = false; let result: T | undefined; let hasResult = false;
  try {
    input.transaction.start(input.deadline.remaining("migration_preview_timeout")); started = true;
    try { result = await input.deadline.run(async () => input.work(), "migration_preview_timeout"); hasResult = true; }
    catch (error) {
      await finalizeMigrationSession({ deadline: input.deadline, session: input.transaction, abortIfActive: true,
        cleanupBudgetMs: input.deadline.signal.aborted ? 25 : 1_000 });
      throw error;
    }
    const commit = Promise.resolve().then(() => input.transaction.commit()); commit.catch(() => undefined);
    const commitOutcome: Promise<MigrationCommitOutcome> = commit.then(() => "committed" as const, () => "failed" as const);
    try {
      await input.deadline.run(async () => commit, "migration_preview_timeout");
      await finalizeMigrationSession({ deadline: input.deadline, session: input.transaction, abortIfActive: false, commitOutcome, cleanupBudgetMs: 1_000 });
    } catch {
      const finalized = await finalizeMigrationSession({ deadline: input.deadline, session: input.transaction, abortIfActive: false,
        commitOutcome, cleanupBudgetMs: input.deadline.signal.aborted ? 25 : 1_000 });
      if (finalized.completed) {
        const recovered = await settle(input.recover, 1_000);
        if (recovered.completed && recovered.value !== null && recovered.value !== undefined) { result = recovered.value; hasResult = true; }
        else throw new Error("migration_commit_uncertain");
      } else throw new Error("migration_commit_uncertain");
    }
  } catch (error) {
    if (!started) await finalizeMigrationSession({ deadline: input.deadline, session: input.transaction, abortIfActive: false, cleanupBudgetMs: 25 });
    throw migrationTransactionFailure(error);
  }
  if (!hasResult) throw new Error("migration_repository_failed");
  return result as T;
}
