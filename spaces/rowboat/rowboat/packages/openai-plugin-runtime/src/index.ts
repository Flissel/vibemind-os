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
export {
  DEFAULT_POLICY,
  DEFAULT_POLICY_VERSION,
  type ImmutableStringSet,
  type PluginPolicy,
} from "./policy/default-policy.js";
export {
  evaluateLicense,
  type AdmissionDecision,
  type AdmissionStatus,
} from "./policy/license-policy.js";
export {
  evaluateCapability,
  evaluateComponentAdmission,
  type CapabilityKind,
  type CapabilityReference,
} from "./policy/capability-policy.js";
export {
  PINNED_OPENAI_PLUGIN_COUNT,
  PINNED_OPENAI_PLUGINS_COMMIT,
  PLUGIN_SCHEMA_VERSION,
  type CatalogComponentAdmission,
  type CatalogInventory,
  type PluginCatalogEntry,
  type PluginCatalogLock,
} from "./domain/catalog.js";
export {
  importCatalog,
  parseCatalogSyncArgs,
  assertCatalogOutputContained,
  writeCatalogLock,
  type CatalogImportOptions,
  type CatalogLockWriteOptions,
  type CatalogSyncArgs,
} from "./import/catalog-importer.js";
export {
  normalizeSkill,
  type NormalizedSkill,
  type NormalizedSkillResource,
} from "./components/skill-normalizer.js";
export {
  normalizeAgents,
  type NormalizedAgent,
} from "./components/agent-normalizer.js";
export {
  normalizeCommands,
  type NormalizedCommand,
  type NormalizedCommandResource,
} from "./components/command-normalizer.js";
export {
  normalizeAsset,
  type AssetNormalizationOptions,
  type NormalizedAsset,
} from "./components/asset-normalizer.js";
