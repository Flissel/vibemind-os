import { types as utilTypes } from "node:util";
import type { PluginApiIdentity } from "../../policies/plugin-api-authorization.policy";
import { actorTuple, migrationDigest, SHA256 } from "./plugin-migration.shared";

interface ProjectRef { readonly projectId: string; readonly sourceProjectRevision: number }
interface CatalogRef { readonly catalogDigest: string; readonly sourceCommit: string; readonly policyVersion: string }
interface PreviewLike extends ProjectRef {
  readonly id: string; readonly recipeId: string; readonly recipeDigest: string; readonly sourceDigest: string; readonly sourceInventoryDigest: string;
  readonly targetCatalogDigest: string; readonly targetSourceCommit: string; readonly targetPolicyVersion: string;
  readonly targetInstallationIds: readonly string[]; readonly rollbackSnapshotDigest: string; readonly status: "previewed" | "blocked" | "applied" | "verified" | "rolled_back";
  readonly blockers: readonly Readonly<{ readonly code: string }>[]; readonly createdAt: string; readonly mutationsApplied: false; readonly installations: readonly unknown[];
}
interface Dependencies {
  readonly authorizeProject: (actor: PluginApiIdentity, projectId: string) => Promise<void>;
  readonly authorizeAll: (actor: PluginApiIdentity) => Promise<void>;
  readonly getPinnedCatalog: () => Promise<CatalogRef>;
  readonly listProjectsPage: (actor: PluginApiIdentity, cursor?: string) => Promise<Readonly<{ projects: readonly ProjectRef[]; nextCursor: string | null; snapshotToken: string }>>;
  readonly getProjectSource: (projectId: string) => Promise<ProjectRef>;
  readonly previewProject: (source: ProjectRef, catalog: CatalogRef) => PreviewLike;
  readonly issueConfirmation: (input: Readonly<{ actor: PluginApiIdentity; projectId: string; preview: PreviewLike; reportDigest: string }>) => string | Readonly<{ token: string; idempotencyKey: string }>;
  readonly now: () => Date;
}
export type MigrationPreviewScope = "all" | "project";

function projectId(value: unknown): asserts value is string { if (typeof value !== "string" || value.length < 1 || value.length > 128 || !/^[A-Za-z0-9._:-]+$/.test(value)) throw new Error("project_id_invalid"); }
function captureProject(value: unknown): ProjectRef {
  if (value === null || typeof value !== "object" || utilTypes.isProxy(value)) throw new Error("migration_project_invalid");
  const prototype = Object.getPrototypeOf(value); if (prototype !== Object.prototype && prototype !== null) throw new Error("migration_project_invalid");
  const keys = Reflect.ownKeys(value); if (keys.some(key => typeof key !== "string")) throw new Error("migration_project_invalid");
  const projectDescriptor = Object.getOwnPropertyDescriptor(value, "projectId");
  const revisionDescriptor = Object.getOwnPropertyDescriptor(value, "sourceProjectRevision");
  if (projectDescriptor === undefined || !("value" in projectDescriptor) || !projectDescriptor.enumerable
    || revisionDescriptor === undefined || !("value" in revisionDescriptor) || !revisionDescriptor.enumerable) throw new Error("migration_project_invalid");
  projectId(projectDescriptor.value);
  if (!Number.isSafeInteger(revisionDescriptor.value) || revisionDescriptor.value < 0) throw new Error("migration_project_invalid");
  return Object.freeze({ projectId: projectDescriptor.value, sourceProjectRevision: revisionDescriptor.value });
}

export class PreviewPluginMigrationUseCase {
  constructor(private readonly dependencies: Dependencies) {}
  async execute(input: Readonly<{ actor: PluginApiIdentity; scope: MigrationPreviewScope; projectId?: string }>) {
    actorTuple(input.actor);
    let refs: readonly ProjectRef[]; let snapshotToken: string;
    if (input.scope === "project") {
      projectId(input.projectId);
      await this.dependencies.authorizeProject(input.actor, input.projectId);
      const catalog = await this.dependencies.getPinnedCatalog();
      const rawSource = await this.dependencies.getProjectSource(input.projectId);
      const source = captureProject(rawSource);
      refs = [source]; snapshotToken = migrationDigest("rowboat:plugin-migration-project-snapshot:v1", source);
      return this.report(input.actor, input.scope, refs, snapshotToken, catalog, [rawSource]);
    }
    if (input.scope !== "all" || input.projectId !== undefined) throw new Error("migration_request_invalid");
    await this.dependencies.authorizeAll(input.actor);
    const catalog = await this.dependencies.getPinnedCatalog();
    const collected: ProjectRef[] = []; let cursor: string | undefined; let expectedSnapshot: string | undefined;
    for (let page = 0; page < 1000; page += 1) {
      const result = await this.dependencies.listProjectsPage(input.actor, cursor);
      if (typeof result.snapshotToken !== "string" || result.snapshotToken.length < 1 || result.snapshotToken.length > 256 || (expectedSnapshot !== undefined && expectedSnapshot !== result.snapshotToken)) throw new Error("migration_snapshot_invalid");
      expectedSnapshot ??= result.snapshotToken;
      if (!Array.isArray(result.projects) || result.projects.length > 256 || collected.length + result.projects.length > 100_000) throw new Error("migration_project_limit");
      for (const ref of result.projects) collected.push(captureProject(ref));
      if (result.nextCursor === null) break;
      if (typeof result.nextCursor !== "string" || result.nextCursor.length < 1 || result.nextCursor.length > 256 || result.nextCursor === cursor) throw new Error("migration_snapshot_invalid");
      cursor = result.nextCursor;
      if (page === 999) throw new Error("migration_project_limit");
    }
    const unique = new Set(collected.map(item => item.projectId)); if (unique.size !== collected.length) throw new Error("migration_project_invalid");
    refs = collected.sort((a, b) => a.projectId.localeCompare(b.projectId)); snapshotToken = expectedSnapshot ?? migrationDigest("rowboat:plugin-migration-empty-snapshot:v1", catalog);
    return this.report(input.actor, input.scope, refs, snapshotToken, catalog);
  }

  private async report(actor: PluginApiIdentity, scope: MigrationPreviewScope, refs: readonly ProjectRef[], snapshotToken: string, catalog: CatalogRef, suppliedSources?: readonly ProjectRef[]) {
    if (!SHA256.test(catalog.catalogDigest) || !/^[a-f0-9]{40}$/.test(catalog.sourceCommit)) throw new Error("catalog_digest_mismatch");
    const previews: PreviewLike[] = [];
    for (let index = 0; index < refs.length; index += 1) {
      const ref = refs[index]!;
      const rawSource = suppliedSources?.[index] ?? await this.dependencies.getProjectSource(ref.projectId);
      const source = captureProject(rawSource);
      if (source.sourceProjectRevision !== ref.sourceProjectRevision) throw new Error("migration_snapshot_stale");
      previews.push(this.dependencies.previewProject(rawSource, catalog));
    }
    const blockerReasons: Record<string, number> = Object.create(null) as Record<string, number>;
    for (const preview of previews) for (const blocker of preview.blockers) blockerReasons[blocker.code] = (blockerReasons[blocker.code] ?? 0) + 1;
    const actorId = actorTuple(actor);
    const reportCore = Object.freeze({ version: 1, catalogDigest: catalog.catalogDigest, sourceCommit: catalog.sourceCommit, policyVersion: catalog.policyVersion,
      generatedAt: this.dependencies.now().toISOString(), snapshotToken, scope, projectCount: previews.length,
      blockerCount: previews.reduce((sum, item) => sum + item.blockers.length, 0), blockerReasons: Object.freeze(blockerReasons),
      mutationCount: 0, receiptIds: Object.freeze([]), mutationsApplied: false as const,
      projects: previews.map(item => Object.freeze({ projectId: item.projectId, sourceProjectRevision: item.sourceProjectRevision, sourceDigest: item.sourceDigest,
        sourceInventoryDigest: item.sourceInventoryDigest, recipeId: item.recipeId, recipeDigest: item.recipeDigest, rollbackSnapshotDigest: item.rollbackSnapshotDigest,
        targetCatalogDigest: item.targetCatalogDigest, targetInstallationIds: item.targetInstallationIds, status: item.status, blockers: item.blockers })),
    });
    const reportDigest = migrationDigest("rowboat:plugin-migration-preview-report:v1", { ...reportCore, actor: actorId });
    const projects = previews.map((preview, index) => {
      if (preview.status !== "previewed" || preview.blockers.length !== 0) return Object.freeze({ ...reportCore.projects[index] });
      const issued = this.dependencies.issueConfirmation({ actor, projectId: preview.projectId, preview, reportDigest });
      return Object.freeze({ ...reportCore.projects[index], ...(typeof issued === "string" ? { confirmationToken: issued } : { confirmationToken: issued.token, confirmationIdempotencyKey: issued.idempotencyKey }) });
    });
    return Object.freeze({ ...reportCore, reportDigest, projects: Object.freeze(projects) });
  }
}
