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
export {
  PluginSourceSecurityError,
  resolveContainedPath,
  type PluginSourceSecurityCode,
} from "./import/path-guard.js";
export { digestTree } from "./import/digest-service.js";
export {
  assertPinnedSource,
  type GitProbe,
  type GitProbeCommand,
  type PinnedSourceRequest,
} from "./import/source-reader.js";
export {
  ContentStore,
  type ContentStoreConfig,
} from "./store/content-store.js";
