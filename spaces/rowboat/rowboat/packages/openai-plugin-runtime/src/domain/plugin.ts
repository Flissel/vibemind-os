import type { PluginManifest } from "../schema/plugin-manifest.js";

export type PluginComponentKind =
  | "skill"
  | "agent"
  | "command"
  | "mcp"
  | "app"
  | "hook"
  | "asset";

export type PluginComponentStatus =
  | "available"
  | "review_required"
  | "installed"
  | "partially_available"
  | "unavailable"
  | "migration_required"
  | "error"
  | "invalid"
  | "unsupported";

export type PluginReasonCode =
  | "source_mismatch"
  | "manifest_invalid"
  | "path_escape"
  | "digest_mismatch"
  | "license_review_required"
  | "license_rejected"
  | "provider_unavailable"
  | "credential_missing"
  | "http_mcp_not_admitted"
  | "process_not_admitted"
  | "hook_not_admitted"
  | "write_review_required"
  | "component_unsupported"
  | "migration_conflict"
  | "parity_failed"
  | "rollback_unavailable";

export type PluginMetadataValue =
  | string
  | number
  | boolean
  | null
  | PluginMetadata
  | readonly PluginMetadataValue[];

export type PluginMetadata = {
  readonly [key: string]: PluginMetadataValue;
};

export interface SourceProvenance {
  readonly sourceUrl: string;
  readonly sourceCommit: string;
  readonly pluginName: string;
  readonly pluginVersion: string;
  readonly manifestDigest: string;
  readonly treeDigest: string;
  readonly importedAt: string;
  readonly schemaVersion: string;
  readonly policyVersion: string;
}

export interface NormalizedPluginComponent {
  readonly id: string;
  readonly name: string;
  readonly kind: PluginComponentKind;
  readonly status: PluginComponentStatus;
  readonly reason?: PluginReasonCode;
  readonly metadata: PluginMetadata;
}

export interface NormalizedPlugin {
  readonly manifest: PluginManifest;
  readonly provenance: SourceProvenance;
  readonly status: PluginComponentStatus;
  readonly components: readonly NormalizedPluginComponent[];
}
