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
  getVerifiedPluginDigests,
  OPENAI_PLUGINS_SOURCE_URL,
  type PinnedSourceRequest,
  type VerifiedPluginDigests,
  type VerifiedPinnedSource,
} from "./import/source-reader.js";
export {
  ContentStore,
  type ContentStoreEntry,
  type ContentStoreConfig,
  type ContentStorePublicationObserver,
  type ContentStorePublicationState,
  type StoredPluginContent,
} from "./store/content-store.js";
export {
  AppFileSchema,
  HttpMcpSchema,
  McpFileSchema,
  McpServerSchema,
  ProcessMcpSchema,
} from "./schema/component-schemas.js";
export {
  discoverPluginComponents,
  type ComponentDiscoveryOptions,
  type ComponentDiscoveryResult,
} from "./import/component-discovery.js";
export {
  normalizePlugin,
  type NormalizePluginOptions,
} from "./import/normalize-plugin.js";
