import { z } from "zod";

export const ZSha256 = z.string().regex(/^[a-f0-9]{64}$/);

export const ZPluginMigrationBlocker = z.object({
  capabilityId: z.string().min(1).max(128),
  legacyActionId: z.string().min(1).max(160),
  code: z.enum([
    "source_configuration_drift",
    "legacy_action_unmapped",
    "legacy_card_unmapped",
    "provider_unavailable",
    "component_missing",
    "component_ambiguous",
    "license_review_required",
    "license_rejected",
    "admission_review_required",
    "admission_rejected",
    "component_unavailable",
  ]),
  pluginName: z.string().min(1).max(128).nullable(),
  componentId: z.string().min(1).max(512).nullable(),
}).strict().superRefine((value, context) => {
  const sourceOnly = value.code === "source_configuration_drift" || value.code === "legacy_action_unmapped" || value.code === "legacy_card_unmapped";
  if (sourceOnly && (value.pluginName !== null || value.componentId !== null)) {
    context.addIssue({ code: z.ZodIssueCode.custom, message: "source_blocker_target_forbidden" });
  }
  if (!sourceOnly && (value.pluginName === null || value.componentId === null)) {
    context.addIssue({ code: z.ZodIssueCode.custom, message: "catalog_blocker_target_required" });
  }
}).readonly();

export const ZPluginMigrationRecord = z.object({
  id: z.string().min(1),
  projectId: z.string().min(1),
  recipeId: z.string().min(1),
  recipeDigest: ZSha256,
  sourceProjectRevision: z.number().int().nonnegative(),
  sourceDigest: ZSha256,
  sourceInventoryDigest: ZSha256,
  targetCatalogDigest: ZSha256,
  targetSourceCommit: z.string().regex(/^[a-f0-9]{40}$/),
  targetPolicyVersion: z.string().min(1),
  targetInstallationIds: z.array(z.string().uuid()).readonly(),
  rollbackSnapshotDigest: ZSha256,
  status: z.enum(["previewed", "applied", "verified", "rolled_back", "blocked"]),
  blockers: z.array(ZPluginMigrationBlocker).readonly(),
  createdAt: z.string().datetime(),
}).strict().superRefine((value, context) => {
  const hasBlockers = value.blockers.length !== 0;
  const hasTargets = value.targetInstallationIds.length !== 0;
  const duplicateTarget = new Set(value.targetInstallationIds).size !== value.targetInstallationIds.length;
  if (duplicateTarget) context.addIssue({ code: z.ZodIssueCode.custom, message: "duplicate_target_installation" });
  if (value.status === "blocked") {
    if (!hasBlockers) context.addIssue({ code: z.ZodIssueCode.custom, message: "blocked_requires_blocker" });
    if (hasTargets) context.addIssue({ code: z.ZodIssueCode.custom, message: "blocked_forbids_targets" });
  } else {
    if (hasBlockers) context.addIssue({ code: z.ZodIssueCode.custom, message: "status_forbids_blockers" });
    if (!hasTargets) context.addIssue({ code: z.ZodIssueCode.custom, message: "status_requires_target" });
  }
}).readonly();

export type PluginMigrationBlocker = z.infer<typeof ZPluginMigrationBlocker>;
export type PluginMigrationRecord = z.infer<typeof ZPluginMigrationRecord>;
