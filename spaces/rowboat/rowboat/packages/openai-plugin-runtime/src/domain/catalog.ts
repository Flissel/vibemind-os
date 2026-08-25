import type {
  NormalizedPluginComponent,
  PluginMetadata,
  SourceProvenance,
} from "./plugin.js";
import type { AdmissionDecision } from "../policy/license-policy.js";

export const PLUGIN_SCHEMA_VERSION = "rowboat-plugin-schema-v1" as const;
export const PINNED_OPENAI_PLUGINS_COMMIT =
  "11c74d6ba24d3a6d48f54a194cd00ef3beea18f9" as const;
export const PINNED_OPENAI_PLUGIN_COUNT = 180 as const;
export const PINNED_PLUGIN_CATALOG_DIGEST =
  "2e436d02b025a14960d5ef813c603bd7aec35a6d173c42d8c58274163da89a92" as const;

export interface CatalogInventory {
  readonly pluginsWithSkills: number;
  readonly pluginsWithApps: number;
  readonly pluginsWithAgents: number;
  readonly pluginsWithCommands: number;
  readonly pluginsWithMcp: number;
  readonly pluginsWithCommandHooks: number;
}

export interface CatalogComponentMetadata extends PluginMetadata {
  readonly digest: string;
  readonly bindingDigest: string;
}

export interface CatalogBoundPluginComponent extends Omit<NormalizedPluginComponent, "metadata"> {
  readonly metadata: CatalogComponentMetadata;
}

export interface CatalogComponentAdmission {
  readonly component: CatalogBoundPluginComponent;
  readonly admission: AdmissionDecision;
}

export interface PluginCatalogEntry extends SourceProvenance {
  readonly name: string;
  readonly licenseDeclaration?: string;
  readonly admission: AdmissionDecision;
  readonly components: readonly CatalogComponentAdmission[];
  readonly storedContentDigest?: string;
}

export interface PluginCatalogLock {
  readonly sourceUrl: string;
  readonly sourceCommit: string;
  readonly importedAt: string;
  readonly schemaVersion: string;
  readonly policyVersion: string;
  readonly inventory: CatalogInventory;
  readonly licenseDeclarations: Readonly<Record<string, number>>;
  readonly entries: readonly PluginCatalogEntry[];
  readonly catalogDigest: string;
}
