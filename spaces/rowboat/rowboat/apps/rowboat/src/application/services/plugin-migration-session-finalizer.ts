interface FinalizerDeadline {
  readonly signal: AbortSignal;
  readonly cancel: () => void;
  readonly remaining: (errorCode?: "migration_manifest_limit" | "migration_preview_timeout") => number;
  readonly run: <T>(operation: (remainingMs: number, signal: AbortSignal) => Promise<T>,
    errorCode?: "migration_manifest_limit" | "migration_preview_timeout") => Promise<T>;
  readonly settlePending: (maximumWaitMs: number) => Promise<boolean>;
  readonly awaitPendingSettlement: () => Promise<void>;
}

interface FinalizerSession {
  readonly inTransaction: () => boolean;
  readonly abort: () => Promise<void>;
  readonly end: () => Promise<void>;
}

export type MigrationCommitOutcome = "committed" | "failed";

interface ReadTransactionSession extends FinalizerSession {
  readonly start: (maxCommitTimeMS: number) => void;
  readonly commit: () => Promise<void>;
}

export async function finalizeMigrationSession(input: Readonly<{
  deadline: FinalizerDeadline;
  session: FinalizerSession;
  abortIfActive: boolean;
  commitOutcome?: Promise<MigrationCommitOutcome>;
  cleanupBudgetMs: number;
}>): Promise<Readonly<{ completed: boolean; cleanupFailed: boolean }>> {
  input.deadline.cancel();
  let cleanupFailed = false;
  const owned = (async () => {
    await input.deadline.awaitPendingSettlement();
    const commitOutcome = input.commitOutcome === undefined ? undefined : await input.commitOutcome;
    if (input.session.inTransaction() && (input.abortIfActive || commitOutcome === "failed")) {
      try { await input.session.abort(); } catch { cleanupFailed = true; }
    }
    try { await input.session.end(); } catch { cleanupFailed = true; }
  })();
  owned.catch(() => undefined);
  if (await input.deadline.settlePending(input.cleanupBudgetMs)) {
    let timeout: ReturnType<typeof setTimeout> | undefined;
    const expired = new Promise<false>(resolve => { timeout = setTimeout(() => resolve(false), input.cleanupBudgetMs); });
    try {
      const completed = await Promise.race([owned.then(() => true as const, () => true as const), expired]);
      return Object.freeze({ completed, cleanupFailed: completed && cleanupFailed });
    } finally { if (timeout !== undefined) clearTimeout(timeout); }
  }
  return Object.freeze({ completed: false, cleanupFailed: false });
}

export async function runMigrationReadTransaction<T>(input: Readonly<{
  deadline: FinalizerDeadline; session: ReadTransactionSession; work: () => Promise<T>;
}>): Promise<T> {
  let started = false; let result: T | undefined; let hasResult = false; let primary: unknown;
  let commitOutcome: Promise<MigrationCommitOutcome> | undefined;
  try {
    input.session.start(input.deadline.remaining("migration_preview_timeout")); started = true;
    try { result = await input.work(); hasResult = true; } catch (error) { primary = error; }
    if (primary === undefined) {
      const commit = Promise.resolve().then(() => input.session.commit()); commit.catch(() => undefined);
      commitOutcome = commit.then(() => "committed" as const, () => "failed" as const);
      try { await input.deadline.run(async () => commit, "migration_preview_timeout"); } catch (error) { primary = error; }
    }
  } catch (error) { primary = error; }
  const finalized = await finalizeMigrationSession({ deadline: input.deadline, session: input.session,
    abortIfActive: started && commitOutcome === undefined, commitOutcome, cleanupBudgetMs: input.deadline.signal.aborted ? 25 : 1_000 });
  if (primary !== undefined) throw primary;
  if (!hasResult || (finalized.completed && finalized.cleanupFailed)) throw new Error("migration_repository_failed");
  return result as T;
}
