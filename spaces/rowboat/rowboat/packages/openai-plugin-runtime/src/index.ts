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
  type CatalogBoundPluginComponent,
  type CatalogComponentAdmission,
  type CatalogComponentMetadata,
  type CatalogInventory,
  type PluginCatalogEntry,
  type PluginCatalogLock,
} from "./domain/catalog.js";
export {
  importCatalog,
  componentBindingDigest,
  pluginCatalogDigest,
  parseCatalogSyncArgs,
  assertCatalogOutputContained,
  writeCatalogLock,
  type CatalogImportOptions,
  type CatalogLockWriteOptions,
  type CatalogSyncArgs,
  type ComponentBindingProvenance,
  type ComponentBindingAdmissionMaterial,
} from "./import/catalog-importer.js";
export { validatePluginCatalogLock } from "./import/catalog-validator.js";
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
export {
  ProviderRegistry,
  assertBinding,
} from "./providers/provider-registry.js";
export type {
  PluginProvider,
  ProviderBinding,
  ProviderContext,
  ProviderDescriptor,
  ProviderKind,
  ProviderRequest,
  ProviderResolution,
  ProviderResult,
} from "./providers/provider.js";
export {
  MAX_PROCESS_TIMEOUT_MS,
  normalizeMcpServer,
  pairAppAndMcp,
  type NormalizedHttpMcp,
  type NormalizedMcpServer,
  type NormalizedProcessMcp,
} from "./components/mcp-normalizer.js";
export {
  normalizeApp,
  type NormalizedApp,
} from "./components/app-normalizer.js";
export {
  createTemporaryAdapterBindings,
  type TemporaryAdapterBinding,
  type TemporaryAdapterDigests,
} from "./providers/temporary-adapters.js";
export {
  SecretValue,
  createSecretValue,
  revealSecretValue,
  assertCredentialRequest,
  type CredentialReference,
  type CredentialResolver,
} from "./providers/credential-resolver.js";
export {
  HttpMcpProvider,
  McpCompatibilityError,
  type HttpMcpClient,
  type HttpMcpClientFactory,
  type HttpMcpProviderOptions,
  type HttpMcpTransport,
  type HttpMcpTransportFactory,
  type HttpMcpTransportInput,
} from "./providers/mcp-http-provider.js";
export {
  ConnectorBridgeProvider,
  type ConnectorBridgeProviderOptions,
} from "./providers/connector-bridge-provider.js";
export {
  OPENAI_API_KEY_CREDENTIAL_REFERENCE,
  deriveConnectorReference,
  deriveOAuthBearerReference,
} from "./providers/credential-naming.js";
export {
  ProcessMcpProvider,
  type ProcessMcpClient,
  type ProcessMcpClientFactory,
  type ProcessMcpProviderOptions,
  type ProcessMcpSdkClient,
  type ProcessMcpSdkClientFactory,
  type ProcessSpawner,
  type SafeSpawnOptions,
  type SpawnedProcess,
} from "./providers/mcp-process-provider.js";
export {
  DEFAULT_PROCESS_OUTPUT_LIMIT_BYTES,
  NodeProcessSpawner,
  assertSafeSpawnIdentity,
  captureSafeEnvironment,
  collectBoundedOutput,
  executeSafeProcess,
  resolveSafeWorkingDirectory,
  terminateSpawnedProcess,
  type CollectedOutput,
  type SafeProcessResult,
  type SpawnCompletion,
} from "./process/safe-process.js";
export {
  VerifiedProcessExecutionRoot,
  verifyProcessExecutionRoot,
} from "./process/process-execution-root.js";
export {
  normalizeHookEvent,
  type HookEventType,
} from "./hooks/hook-event.js";
export {
  compileHookMatcher,
  type CompiledHookMatcher,
} from "./hooks/hook-matcher.js";
export {
  HookRunner,
  type HookCommand,
  type HookExecutionEvent,
  type HookExecutionReceipt,
  type HookRunnerOptions,
} from "./hooks/hook-runner.js";
export type {
  InstallationProviderBinding,
  PluginInstallation,
} from "./domain/installation.js";
export {
  ZPluginMigrationBlocker,
  ZPluginMigrationRecord,
  ZSha256,
  type PluginMigrationBlocker,
  type PluginMigrationRecord,
} from "./domain/migration.js";
export type {
  PluginReceipt,
  PluginReceiptStatus,
  PluginReceiptType,
  TruncatedReceiptOutput,
} from "./domain/receipt.js";
export {
  PINNED_PLUGIN_CATALOG_DIGEST,
  resolveInstallation,
  type ResolvedComponent,
  type ResolvedInstallation,
} from "./resolution/plugin-resolver.js";
export {
  buildReceipt,
  type ReceiptLimits,
} from "./receipts/receipt-builder.js";
