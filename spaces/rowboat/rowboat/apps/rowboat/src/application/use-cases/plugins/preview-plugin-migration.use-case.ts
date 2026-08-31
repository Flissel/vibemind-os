import type { PluginApiIdentity } from "../../policies/plugin-api-authorization.policy";
import { actorTuple, migrationDigest, SHA256 } from "./plugin-migration.shared";

interface CatalogRef { readonly catalogDigest: string; readonly sourceCommit: string; readonly policyVersion: string }
export interface MigrationPreviewLike {
  readonly id: string; readonly projectId: string; readonly sourceProjectRevision: number; readonly sourceDigest: string;
  readonly sourceInventoryDigest: string; readonly recipeId: string; readonly recipeDigest: string; readonly rollbackSnapshotDigest: string;
  readonly targetCatalogDigest: string; readonly targetInstallationIds: readonly string[];
  readonly status: "previewed" | "blocked" | "applied" | "verified" | "rolled_back"; readonly blockers: readonly Readonly<{ readonly code: string }>[];
}
export interface PreparedMigrationPreview { readonly preview: MigrationPreviewLike }
interface PreparedMigrationProject { readonly prepared: PreparedMigrationPreview; readonly catalog: CatalogRef }
interface Dependencies {
  readonly authorizeProject: (actor: PluginApiIdentity, projectId: string) => Promise<void>;
  readonly authorizeAll: (actor: PluginApiIdentity) => Promise<void>;
  readonly prepareProject: (projectId: string, callerSignal?: AbortSignal) => Promise<PreparedMigrationProject>;
  readonly scanAllProjects: (actor: PluginApiIdentity, visit: (prepared: PreparedMigrationPreview) => Promise<void>, callerSignal?: AbortSignal) => Promise<Readonly<{ catalog: CatalogRef; snapshotToken: string }>>;
  readonly issueConfirmation: (input: Readonly<{ actor: PluginApiIdentity; preview: MigrationPreviewLike; reportDigest: string }>) => Readonly<{ token: string; idempotencyKey: string }>;
  readonly now: () => Date;
}
export type MigrationPreviewScope = "all" | "project";
function assertProjectId(value: unknown): asserts value is string { if (typeof value !== "string" || value.length < 1 || value.length > 128 || !/^[A-Za-z0-9._:-]+$/.test(value)) throw new Error("project_id_invalid"); }
export function migrationPreviewProjectProjection(preview: MigrationPreviewLike) {
  return Object.freeze({ projectId: preview.projectId, sourceProjectRevision: preview.sourceProjectRevision,
    sourceDigest: preview.sourceDigest, sourceInventoryDigest: preview.sourceInventoryDigest, recipeId: preview.recipeId, recipeDigest: preview.recipeDigest,
    rollbackSnapshotDigest: preview.rollbackSnapshotDigest, targetCatalogDigest: preview.targetCatalogDigest,
    targetInstallationIds: preview.targetInstallationIds, status: preview.status, blockers: preview.blockers });
}

export class PreviewPluginMigrationUseCase {
  constructor(private readonly dependencies: Dependencies) {}
  async execute(input: Readonly<{ actor: PluginApiIdentity; scope: MigrationPreviewScope; projectId?: string; callerSignal?: AbortSignal }>) {
    actorTuple(input.actor);
    const prepared: PreparedMigrationPreview[] = [];
    let catalog: CatalogRef; let snapshotToken: string;
    if (input.scope === "project") {
      assertProjectId(input.projectId); await this.dependencies.authorizeProject(input.actor, input.projectId);
      const selected = await this.dependencies.prepareProject(input.projectId, input.callerSignal); prepared.push(selected.prepared); catalog = selected.catalog;
      snapshotToken = migrationDigest("rowboat:plugin-migration-project-snapshot:v2", { projectId: selected.prepared.preview.projectId, sourceDigest: selected.prepared.preview.sourceDigest });
    } else {
      if (input.scope !== "all" || input.projectId !== undefined) throw new Error("migration_request_invalid");
      await this.dependencies.authorizeAll(input.actor);
      const scan = await this.dependencies.scanAllProjects(input.actor, async item => { prepared.push(item); }, input.callerSignal); catalog = scan.catalog; snapshotToken = scan.snapshotToken;
    }
    if (!SHA256.test(catalog.catalogDigest) || !/^[a-f0-9]{40}$/.test(catalog.sourceCommit)) throw new Error("catalog_digest_mismatch");
    prepared.sort((left, right) => left.preview.projectId.localeCompare(right.preview.projectId));
    const blockerReasons: Record<string, number> = Object.create(null) as Record<string, number>;
    for (const item of prepared) for (const blocker of item.preview.blockers) blockerReasons[blocker.code] = (blockerReasons[blocker.code] ?? 0) + 1;
    const actorIdentity = actorTuple(input.actor); const generatedAt = this.dependencies.now().toISOString();
    const projectsCore = prepared.map(({ preview }) => migrationPreviewProjectProjection(preview));
    const core = Object.freeze({ version: 1, catalogDigest: catalog.catalogDigest, sourceCommit: catalog.sourceCommit, policyVersion: catalog.policyVersion,
      generatedAt, snapshotToken, scope: input.scope, projectCount: prepared.length,
      blockerCount: prepared.reduce((sum, item) => sum + item.preview.blockers.length, 0), blockerReasons: Object.freeze(blockerReasons),
      mutationCount: 0, receiptIds: Object.freeze([]), mutationsApplied: false as const, projects: projectsCore });
    const reportDigest = migrationDigest("rowboat:plugin-migration-preview-report:v2", { ...core, actor: actorIdentity });
    const projects = prepared.map(({ preview }, index) => {
      if (input.scope !== "project" || preview.status !== "previewed" || preview.blockers.length !== 0) return projectsCore[index]!;
      const issued = this.dependencies.issueConfirmation({ actor: input.actor, preview, reportDigest });
      return Object.freeze({ ...projectsCore[index], confirmationToken: issued.token, confirmationIdempotencyKey: issued.idempotencyKey });
    });
    return Object.freeze({ ...core, reportDigest, projects: Object.freeze(projects) });
  }
}
