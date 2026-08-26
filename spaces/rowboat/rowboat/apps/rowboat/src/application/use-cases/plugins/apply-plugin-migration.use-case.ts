import type { PluginApiIdentity } from "../../policies/plugin-api-authorization.policy";
import { actorTuple, type MigrationConfirmationClaims } from "./plugin-migration.shared";

export interface MigrationPreparedPreview {
  readonly id: string; readonly projectId: string; readonly status: "previewed" | "blocked" | "applied" | "verified" | "rolled_back";
  readonly blockers: readonly Readonly<{ readonly code: string }>[];
  readonly targetInstallationIds: readonly string[]; readonly sourceProjectRevision: number; readonly sourceDigest: string;
  readonly sourceInventoryDigest: string; readonly recipeDigest: string; readonly rollbackSnapshotDigest: string;
  readonly targetCatalogDigest: string; readonly targetSourceCommit: string; readonly targetPolicyVersion: string; readonly recipeId: string;
}
export interface PreparedMigrationInvocation { readonly preview: MigrationPreparedPreview; readonly context: unknown }
interface ApplyResult { readonly receiptIds: readonly string[]; readonly replayed: boolean; readonly mutationCount: number; readonly generatedAt: string }
interface Dependencies {
  readonly authorizeProject: (actor: PluginApiIdentity, projectId: string) => Promise<void>;
  readonly verifyConfirmation: (token: string) => MigrationConfirmationClaims;
  readonly prepareProject: (projectId: string) => Promise<PreparedMigrationInvocation>;
  readonly digestPreview: (preview: MigrationPreparedPreview) => string;
  readonly applyAtomically: (context: unknown, claims: MigrationConfirmationClaims) => Promise<ApplyResult>;
}
export class ApplyPluginMigrationUseCase {
  constructor(private readonly dependencies: Dependencies) {}
  async execute(input: Readonly<{ actor: PluginApiIdentity; projectId: string; confirmationToken?: string }>) {
    if (input.confirmationToken === undefined) throw new Error("migration_confirmation_required");
    if (typeof input.projectId !== "string" || input.projectId.length < 1 || input.projectId.length > 128) throw new Error("migration_request_invalid");
    const selectedActor = actorTuple(input.actor);
    await this.dependencies.authorizeProject(input.actor, input.projectId);
    const claims = this.dependencies.verifyConfirmation(input.confirmationToken);
    if (claims.actorKind !== selectedActor.actorKind || claims.actorId !== selectedActor.actorId || claims.projectId !== input.projectId) throw new Error("migration_confirmation_invalid");
    const prepared = await this.dependencies.prepareProject(input.projectId);
    const preview = prepared.preview;
    if ((preview.status !== "previewed" && preview.status !== "applied") || preview.blockers.length !== 0 || preview.targetInstallationIds.length === 0) throw new Error("migration_preview_blocked");
    if (this.dependencies.digestPreview(preview) !== claims.previewDigest
      || claims.sourceProjectRevision !== preview.sourceProjectRevision || claims.sourceDigest !== preview.sourceDigest
      || claims.sourceInventoryDigest !== preview.sourceInventoryDigest || claims.recipeDigest !== preview.recipeDigest
      || claims.rollbackSnapshotDigest !== preview.rollbackSnapshotDigest || claims.targetCatalogDigest !== preview.targetCatalogDigest
      || claims.targetInstallationIds.length !== preview.targetInstallationIds.length
      || claims.targetInstallationIds.some((id, index) => id !== preview.targetInstallationIds[index])) throw new Error("migration_preview_stale");
    const result = await this.dependencies.applyAtomically(prepared.context, claims);
    const project = Object.freeze({ projectId: preview.projectId, sourceProjectRevision: preview.sourceProjectRevision, sourceDigest: preview.sourceDigest,
      sourceInventoryDigest: preview.sourceInventoryDigest, recipeId: preview.recipeId, recipeDigest: preview.recipeDigest,
      rollbackSnapshotDigest: preview.rollbackSnapshotDigest, targetCatalogDigest: preview.targetCatalogDigest,
      targetSourceCommit: preview.targetSourceCommit, targetPolicyVersion: preview.targetPolicyVersion,
      targetInstallationIds: preview.targetInstallationIds, status: "applied" as const, blockers: Object.freeze([]), previewDigest: claims.previewDigest });
    return Object.freeze({ version: 1, catalogDigest: preview.targetCatalogDigest, sourceCommit: preview.targetSourceCommit,
      policyVersion: preview.targetPolicyVersion, generatedAt: result.generatedAt,
      snapshotToken: claims.previewDigest, scope: "project" as const,
      projectCount: 1, blockerCount: 0, blockerReasons: Object.freeze({}), mutationCount: result.mutationCount,
      receiptIds: Object.freeze([...result.receiptIds]), mutationsApplied: !result.replayed, reportDigest: claims.reportDigest,
      confirmation: Object.freeze({ actorKind: claims.actorKind, actorId: claims.actorId, issuedAt: claims.issuedAt,
        expiresAt: claims.expiresAt, idempotencyKey: claims.idempotencyKey, replayed: result.replayed }), projects: Object.freeze([project]) });
  }
}
