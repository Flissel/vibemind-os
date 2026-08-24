export const RUNTIME_SCHEMA_VERSION = "rowboat-openai-plugin-runtime-v1" as const;

export {
  PluginManifestSchema,
  PluginManifestValidationError,
  parsePluginManifest,
  type PluginManifest,
} from "./schema/plugin-manifest.js";
export type {
  NormalizedPlugin,
  NormalizedPluginComponent,
  PluginComponentKind,
  PluginComponentStatus,
  PluginMetadata,
  PluginMetadataValue,
  PluginReasonCode,
  SourceProvenance,
} from "./domain/plugin.js";
