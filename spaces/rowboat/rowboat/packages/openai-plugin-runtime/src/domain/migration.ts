import { z } from "zod";

export const ZSha256 = z.string().regex(/^[a-f0-9]{64}$/);

export const ZPluginMigrationBlocker = z.object({
  capabilityId: z.string().min(1).max(128),
  code: z.enum([
    "provider_unavailable",
    "component_missing",
    "component_ambiguous",
    "license_review_required",
    "license_rejected",
    "admission_review_required",
    "admission_rejected",
    "component_unavailable",
  ]),
  pluginName: z.string().min(1).max(128),
  componentId: z.string().min(1).max(512),
}).strict().readonly();

export const ZPluginMigrationRecord = z.object({
  id: z.string().min(1),
  projectId: z.string().min(1),
  recipeId: z.string().min(1),
  sourceProjectRevision: z.number().int().nonnegative(),
  sourceDigest: ZSha256,
  targetCatalogDigest: ZSha256,
  targetSourceCommit: z.string().regex(/^[a-f0-9]{40}$/),
  targetPolicyVersion: z.string().min(1),
  targetInstallationIds: z.array(z.string().min(1)).readonly(),
  rollbackSnapshotDigest: ZSha256,
  status: z.enum(["previewed", "applied", "verified", "rolled_back", "blocked"]),
  blockers: z.array(ZPluginMigrationBlocker).readonly(),
  createdAt: z.string().datetime(),
}).strict().readonly();

export type PluginMigrationBlocker = z.infer<typeof ZPluginMigrationBlocker>;
export type PluginMigrationRecord = z.infer<typeof ZPluginMigrationRecord>;
