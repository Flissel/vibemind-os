import type { PluginApiIdentity } from "../../policies/plugin-api-authorization.policy";
import { actorTuple, SAFE_TOKEN, type MigrationConfirmationClaims } from "./plugin-migration.shared";

interface PreviewLike {
  readonly id: string; readonly projectId: string; readonly status: string; readonly blockers: readonly unknown[]; readonly targetInstallationIds: readonly string[];
  readonly installations?: readonly unknown[];
  readonly sourceProjectRevision?: number; readonly sourceDigest?: string; readonly sourceInventoryDigest?: string; readonly recipeDigest?: string;
  readonly rollbackSnapshotDigest?: string; readonly targetCatalogDigest?: string;
}
type Claims = MigrationConfirmationClaims | Readonly<{ actorKind: string; actorId: string; projectId: string; previewDigest: string; idempotencyKey?: string }>;
interface Dependencies {
  readonly authorizeProject: (actor: PluginApiIdentity, projectId: string) => Promise<void>;
  readonly verifyConfirmation: (token: string) => Claims;
  readonly previewProject: (projectId: string) => Promise<PreviewLike>;
  readonly digestPreview?: (preview: PreviewLike) => string;
  readonly runAtomically: <T>(operation: (transaction: unknown) => Promise<T>) => Promise<T>;
  readonly writeRollbackSnapshot: (transaction: unknown, preview: PreviewLike, claims: Claims) => Promise<void>;
  readonly writeInstallationsAndAdmissions: (transaction: unknown, preview: PreviewLike, claims: Claims) => Promise<void>;
  readonly writeMigrationRecord: (transaction: unknown, preview: PreviewLike, claims: Claims, idempotencyKey: string) => Promise<Readonly<{ receiptIds: readonly string[] }>>;
  readonly compareAndSetProjectPointer: (transaction: unknown, preview: PreviewLike, claims: Claims) => Promise<void>;
}
export class ApplyPluginMigrationUseCase {
  constructor(private readonly dependencies: Dependencies) {}
  async execute(input: Readonly<{ actor: PluginApiIdentity; projectId: string; confirmationToken?: string; idempotencyKey: string }>) {
    if (input.confirmationToken === undefined) throw new Error("migration_confirmation_required");
    if (!SAFE_TOKEN.test(input.idempotencyKey) || typeof input.projectId !== "string" || input.projectId.length < 1 || input.projectId.length > 128) throw new Error("migration_request_invalid");
    const selectedActor = actorTuple(input.actor);
    await this.dependencies.authorizeProject(input.actor, input.projectId);
    const claims = this.dependencies.verifyConfirmation(input.confirmationToken);
    if (claims.actorKind !== selectedActor.actorKind || claims.actorId !== selectedActor.actorId || claims.projectId !== input.projectId) throw new Error("migration_confirmation_invalid");
    if (claims.idempotencyKey !== undefined && claims.idempotencyKey !== input.idempotencyKey) throw new Error("migration_confirmation_invalid");
    const preview = await this.dependencies.previewProject(input.projectId);
    if (preview.status !== "previewed" || preview.blockers.length !== 0 || preview.targetInstallationIds.length === 0) throw new Error("migration_preview_blocked");
    const digest = this.dependencies.digestPreview?.(preview);
    if (digest !== undefined && digest !== claims.previewDigest) throw new Error("migration_preview_stale");
    if ("sourceProjectRevision" in claims && (
      claims.sourceProjectRevision !== preview.sourceProjectRevision || claims.sourceDigest !== preview.sourceDigest
      || claims.sourceInventoryDigest !== preview.sourceInventoryDigest || claims.recipeDigest !== preview.recipeDigest
      || claims.rollbackSnapshotDigest !== preview.rollbackSnapshotDigest || claims.targetCatalogDigest !== preview.targetCatalogDigest
      || claims.targetInstallationIds.length !== preview.targetInstallationIds.length
      || claims.targetInstallationIds.some((id, index) => id !== preview.targetInstallationIds[index])
    )) throw new Error("migration_preview_stale");
    const result = await this.dependencies.runAtomically(async transaction => {
      await this.dependencies.writeRollbackSnapshot(transaction, preview, claims);
      await this.dependencies.writeInstallationsAndAdmissions(transaction, preview, claims);
      const receipt = await this.dependencies.writeMigrationRecord(transaction, preview, claims, input.idempotencyKey);
      await this.dependencies.compareAndSetProjectPointer(transaction, preview, claims);
      return receipt;
    });
    return Object.freeze({ projectId: input.projectId, mutationsApplied: true as const, mutationCount: 4, receiptIds: Object.freeze([...result.receiptIds]) });
  }
}
