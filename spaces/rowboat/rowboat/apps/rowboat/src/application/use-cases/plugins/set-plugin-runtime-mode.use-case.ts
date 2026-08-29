import { PINNED_PLUGIN_CATALOG_DIGEST, type PluginMigrationRecord, type PluginReceipt } from "@rowboat/openai-plugin-runtime";
import type { PluginApiIdentity } from "../../policies/plugin-api-authorization.policy";
import { PluginRuntimeState, type PluginRuntimeStateValue } from "@/src/entities/models/project";
import type { ProjectWorkflowPair } from "../../repositories/projects.repository.interface";
import { fingerprint, serviceError } from "./plugin-service.shared";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const DIGEST = /^[a-f0-9]{64}$/;
const RECEIPT_ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/;

/**
 * The runtime owner of every project-level cutover receipt. A cutover is a
 * project transition, not a single plugin action, so it carries this reserved
 * scope name instead of an installed plugin name.
 */
const RUNTIME_SCOPE = "rowboat.plugin-runtime";

export type PluginRuntimeMode = PluginRuntimeStateValue["mode"];

/**
 * The complete set of admitted transitions. Everything else, including the
 * direct legacy to openai jump and any same-mode write, is rejected.
 */
const TRANSITIONS: readonly Readonly<{ from: PluginRuntimeMode; to: PluginRuntimeMode }>[] = Object.freeze([
  Object.freeze({ from: "legacy" as const, to: "shadow" as const }),
  Object.freeze({ from: "shadow" as const, to: "openai" as const }),
  Object.freeze({ from: "openai" as const, to: "legacy" as const }),
]);

export interface SetPluginRuntimeModeRequest {
  readonly identity: PluginApiIdentity;
  readonly projectId: string;
  readonly mode: PluginRuntimeMode;
  readonly expectedRevision: number;
  readonly catalogDigest?: string;
  readonly migrationRecordId?: string;
  readonly parityReceiptId?: string;
}

export interface SetPluginRuntimeModeResult {
  readonly projectId: string;
  readonly mode: PluginRuntimeMode;
  readonly revision: number;
  readonly rolledBack: boolean;
  readonly receiptId: string;
  /** Tools bound to a plugin on cutover, or restored to legacy on rollback. */
  readonly workflowsWritten: boolean;
}

export interface SetPluginRuntimeModeDependencies {
  readonly authorizeProject: (identity: PluginApiIdentity, projectId: string) => Promise<void>;
  readonly loadRuntimeState: (projectId: string) => Promise<PluginRuntimeStateValue | null>;
  readonly loadMigrationRecord: (id: string) => Promise<PluginMigrationRecord | null>;
  readonly loadReceipt: (id: string) => Promise<PluginReceipt | null>;
  readonly saveRuntimeState: (projectId: string, expectedRevision: number, state: PluginRuntimeStateValue, workflows?: ProjectWorkflowPair) => Promise<PluginRuntimeStateValue>;
  /**
   * Produces the workflows that make the openai runtime actually authoritative:
   * the legacy tools an applied migration mapped, carrying their plugin
   * binding. Without it a cutover would change the mode while the agent kept
   * building legacy tools.
   */
  readonly materializeWorkflows?: (input: Readonly<{ projectId: string; migrationRecordId: string }>) => Promise<ProjectWorkflowPair>;
  /**
   * Produces the workflows a rollback restores, from the retained snapshot the
   * migration wrote. Fails closed when that snapshot is unavailable.
   */
  readonly restoreWorkflows?: (input: Readonly<{ projectId: string; migrationRecordId: string; rollbackSnapshotDigest: string }>) => Promise<ProjectWorkflowPair>;
  readonly putReceipt: (receipt: PluginReceipt) => Promise<void>;
  readonly now: () => Date;
}

function requestInvalid(): never {
  serviceError("plugin_runtime_request_invalid");
}

function assertRequest(request: SetPluginRuntimeModeRequest): void {
  if (request === null || typeof request !== "object") requestInvalid();
  if (typeof request.projectId !== "string" || !UUID.test(request.projectId)) requestInvalid();
  if (request.mode !== "legacy" && request.mode !== "shadow" && request.mode !== "openai") requestInvalid();
  if (!Number.isSafeInteger(request.expectedRevision) || request.expectedRevision < 0) requestInvalid();
  if (request.catalogDigest !== undefined && (typeof request.catalogDigest !== "string" || !DIGEST.test(request.catalogDigest))) requestInvalid();
  if (request.migrationRecordId !== undefined && (typeof request.migrationRecordId !== "string" || !UUID.test(request.migrationRecordId))) requestInvalid();
  if (request.parityReceiptId !== undefined && (typeof request.parityReceiptId !== "string" || !RECEIPT_ID.test(request.parityReceiptId))) requestInvalid();
}

/**
 * Cutover requires direct, current evidence: an applied migration for this
 * project against the pinned catalog with no unresolved blockers, at least one
 * target installation, a retained rollback snapshot, and an accepted parity
 * receipt for the same project. Anything missing fails closed.
 */
function assertCutoverEvidence(
  projectId: string,
  record: PluginMigrationRecord | null,
  receipt: PluginReceipt | null,
): asserts record is PluginMigrationRecord {
  if (record === null || receipt === null) serviceError("cutover_evidence_required");
  if (record.projectId !== projectId || receipt.projectId !== projectId) serviceError("cutover_evidence_required");
  if (record.status !== "applied" && record.status !== "verified") serviceError("cutover_evidence_required");
  if (record.blockers.length !== 0 || record.targetInstallationIds.length === 0) serviceError("cutover_evidence_required");
  if (record.targetCatalogDigest !== PINNED_PLUGIN_CATALOG_DIGEST) serviceError("cutover_evidence_required");
  if (typeof record.rollbackSnapshotDigest !== "string" || !DIGEST.test(record.rollbackSnapshotDigest)) serviceError("cutover_evidence_required");
  if (receipt.type !== "execution" || receipt.status !== "success") serviceError("cutover_evidence_required");
}

export class SetPluginRuntimeModeUseCase {
  constructor(private readonly dependencies: SetPluginRuntimeModeDependencies) {}

  async execute(request: SetPluginRuntimeModeRequest): Promise<SetPluginRuntimeModeResult> {
    assertRequest(request);
    await this.dependencies.authorizeProject(request.identity, request.projectId);
    const current = await this.dependencies.loadRuntimeState(request.projectId);
    if (current === null) serviceError("project_not_found");
    if (!TRANSITIONS.some((transition) => transition.from === current.mode && transition.to === request.mode)) {
      serviceError("runtime_mode_transition_rejected");
    }
    if (current.revision !== request.expectedRevision) serviceError("plugin_runtime_state_conflict");

    const timestamp = this.dependencies.now().toISOString();
    let next: PluginRuntimeStateValue;
    if (request.mode === "openai") {
      if (request.catalogDigest !== PINNED_PLUGIN_CATALOG_DIGEST) serviceError("catalog_digest_mismatch");
      if (request.migrationRecordId === undefined || request.parityReceiptId === undefined) serviceError("cutover_evidence_required");
      const record = await this.dependencies.loadMigrationRecord(request.migrationRecordId);
      const receipt = await this.dependencies.loadReceipt(request.parityReceiptId);
      assertCutoverEvidence(request.projectId, record, receipt);
      next = {
        mode: "openai", revision: current.revision + 1,
        migrationRecordId: record.id, parityReceiptId: request.parityReceiptId,
        rollbackSnapshotDigest: record.rollbackSnapshotDigest, cutoverAt: timestamp,
      };
    } else if (request.mode === "legacy") {
      // Rollback returns authority to the legacy tools. Where a cutover
      // materialized plugin bindings into the workflow, the retained snapshot
      // is restored in the same conditional write; where it did not, the
      // untouched legacy fields already are the legacy tools.
      if (typeof current.rollbackSnapshotDigest !== "string") serviceError("rollback_snapshot_required");
      next = {
        mode: "legacy", revision: current.revision + 1,
        ...(current.migrationRecordId === undefined ? {} : { migrationRecordId: current.migrationRecordId }),
        rollbackSnapshotDigest: current.rollbackSnapshotDigest, rolledBackAt: timestamp,
      };
    } else {
      next = {
        mode: "shadow", revision: current.revision + 1,
        ...(current.migrationRecordId === undefined ? {} : { migrationRecordId: current.migrationRecordId }),
        ...(current.rollbackSnapshotDigest === undefined ? {} : { rollbackSnapshotDigest: current.rollbackSnapshotDigest }),
      };
    }

    const state = PluginRuntimeState.parse(next);
    // Materializing before the swap keeps a failure inert: nothing is written,
    // and the project stays in its current mode with its current tools.
    let workflows: ProjectWorkflowPair | undefined;
    if (state.mode === "openai" && this.dependencies.materializeWorkflows !== undefined) {
      workflows = await this.dependencies.materializeWorkflows({ projectId: request.projectId, migrationRecordId: state.migrationRecordId! });
    }
    if (state.mode === "legacy" && current.mode === "openai" && this.dependencies.restoreWorkflows !== undefined) {
      if (current.migrationRecordId === undefined || current.rollbackSnapshotDigest === undefined) serviceError("rollback_snapshot_required");
      workflows = await this.dependencies.restoreWorkflows({
        projectId: request.projectId,
        migrationRecordId: current.migrationRecordId,
        rollbackSnapshotDigest: current.rollbackSnapshotDigest,
      });
    }
    const saved = await this.dependencies.saveRuntimeState(request.projectId, request.expectedRevision, state, workflows);
    const receiptId = `runtime-mode:${fingerprint({ projectId: request.projectId, from: current.mode, to: saved.mode, revision: saved.revision })}`;
    const receipt: PluginReceipt = Object.freeze({
      type: "migration", receiptId, projectId: request.projectId, pluginName: RUNTIME_SCOPE,
      status: "success", redactions: Object.freeze([]),
      output: Object.freeze({
        version: 1, from: current.mode, to: saved.mode, revision: saved.revision, at: timestamp,
        migrationRecordId: saved.migrationRecordId ?? null,
        parityReceiptId: saved.parityReceiptId ?? null,
        rollbackSnapshotDigest: saved.rollbackSnapshotDigest ?? null,
      }),
    });
    await this.dependencies.putReceipt(receipt);
    return Object.freeze({ projectId: request.projectId, mode: saved.mode, revision: saved.revision, rolledBack: saved.mode === "legacy", receiptId, workflowsWritten: workflows !== undefined });
  }
}
