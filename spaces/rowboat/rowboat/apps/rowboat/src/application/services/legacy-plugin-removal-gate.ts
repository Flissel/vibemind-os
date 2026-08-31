import { PINNED_PLUGIN_CATALOG_DIGEST, type PluginMigrationRecord, type PluginReceipt } from "@rowboat/openai-plugin-runtime";
import { PluginRuntimeState, type PluginRuntimeStateValue } from "@/src/entities/models/project";

/**
 * How long a cut-over project keeps its rollback path before the legacy public
 * surfaces may be removed. The window is measured from the recorded cutover.
 */
export const ROLLBACK_RETENTION_MS = 14 * 24 * 60 * 60 * 1000;

export interface ProjectRuntimeStateEntry {
  readonly projectId: string;
  readonly state: PluginRuntimeStateValue;
}

export interface LegacyRemovalReport {
  readonly ready: boolean;
  readonly generatedAt: string;
  readonly totalProjects: number;
  readonly counts: Readonly<{ legacy: number; shadow: number; openai: number }>;
  readonly verifiedProjects: number;
  readonly blockedMigrations: number;
  readonly missingReceipts: number;
  readonly activeRollbackWindows: number;
  readonly rollbackRetentionMs: number;
  readonly reasons: readonly string[];
}

export interface LegacyPluginRemovalGateDependencies {
  readonly listProjectRuntimeStates: () => Promise<readonly ProjectRuntimeStateEntry[]>;
  readonly loadMigrationRecord: (id: string) => Promise<PluginMigrationRecord | null>;
  readonly loadReceipt: (id: string) => Promise<PluginReceipt | null>;
  readonly now: () => Date;
  readonly rollbackRetentionMs?: number;
}

function stateInvalid(): never {
  throw new Error("legacy_removal_gate_state_invalid");
}

/**
 * Read-only all-project report. It counts what is actually stored and proves
 * each openai project against its own migration and parity evidence; the
 * existence of plugin code is never treated as a verified project.
 */
export class LegacyPluginRemovalGate {
  readonly #dependencies: LegacyPluginRemovalGateDependencies;
  readonly #retentionMs: number;

  constructor(dependencies: LegacyPluginRemovalGateDependencies) {
    this.#dependencies = dependencies;
    this.#retentionMs = dependencies.rollbackRetentionMs ?? ROLLBACK_RETENTION_MS;
  }

  async report(): Promise<LegacyRemovalReport> {
    const now = this.#dependencies.now();
    const entries = await this.#dependencies.listProjectRuntimeStates();
    if (!Array.isArray(entries)) stateInvalid();
    const counts = { legacy: 0, shadow: 0, openai: 0 };
    let verifiedProjects = 0;
    let blockedMigrations = 0;
    let missingReceipts = 0;
    let activeRollbackWindows = 0;

    for (const entry of entries) {
      if (entry === null || typeof entry !== "object" || typeof entry.projectId !== "string") stateInvalid();
      const parsed = PluginRuntimeState.safeParse(entry.state);
      if (!parsed.success) stateInvalid();
      const state = parsed.data;
      counts[state.mode] += 1;
      if (state.mode !== "openai") continue;

      const record = state.migrationRecordId === undefined ? null : await this.#dependencies.loadMigrationRecord(state.migrationRecordId);
      const migrationVerified = record !== null
        && record.projectId === entry.projectId
        && (record.status === "applied" || record.status === "verified")
        && record.blockers.length === 0
        && record.targetInstallationIds.length !== 0
        && record.targetCatalogDigest === PINNED_PLUGIN_CATALOG_DIGEST;
      if (!migrationVerified) blockedMigrations += 1;

      const receipt = state.parityReceiptId === undefined ? null : await this.#dependencies.loadReceipt(state.parityReceiptId);
      const receiptVerified = receipt !== null && receipt.projectId === entry.projectId && receipt.type === "execution" && receipt.status === "success";
      if (!receiptVerified) missingReceipts += 1;

      // An openai project without a recorded cutover keeps its rollback path
      // open: unknown retention is treated as still active, never as elapsed.
      const cutoverAt = state.cutoverAt === undefined ? null : Date.parse(state.cutoverAt);
      const retentionElapsed = cutoverAt !== null && Number.isFinite(cutoverAt) && now.getTime() - cutoverAt >= this.#retentionMs;
      if (!retentionElapsed) activeRollbackWindows += 1;
      if (migrationVerified && receiptVerified && retentionElapsed) verifiedProjects += 1;
    }

    const reasons: string[] = [];
    if (entries.length === 0) reasons.push("no_projects_observed");
    if (counts.legacy > 0) reasons.push(`legacy_projects_remaining:${counts.legacy}`);
    if (counts.shadow > 0) reasons.push(`shadow_projects_remaining:${counts.shadow}`);
    if (blockedMigrations > 0) reasons.push(`blocked_migrations:${blockedMigrations}`);
    if (missingReceipts > 0) reasons.push(`missing_receipts:${missingReceipts}`);
    if (activeRollbackWindows > 0) reasons.push("rollback_window_active");

    return Object.freeze({
      ready: reasons.length === 0 && verifiedProjects === entries.length,
      generatedAt: now.toISOString(),
      totalProjects: entries.length,
      counts: Object.freeze(counts),
      verifiedProjects,
      blockedMigrations,
      missingReceipts,
      activeRollbackWindows,
      rollbackRetentionMs: this.#retentionMs,
      reasons: Object.freeze(reasons),
    });
  }

  async assertReady(): Promise<void> {
    const report = await this.report();
    if (!report.ready) throw new Error(report.reasons.join(" "));
  }
}
