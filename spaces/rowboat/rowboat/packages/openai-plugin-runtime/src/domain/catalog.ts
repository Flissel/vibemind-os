import type {
  NormalizedPluginComponent,
  SourceProvenance,
} from "./plugin.js";
import type { AdmissionDecision } from "../policy/license-policy.js";

export const PLUGIN_SCHEMA_VERSION = "rowboat-plugin-schema-v1" as const;
export const PINNED_OPENAI_PLUGINS_COMMIT =
  "11c74d6ba24d3a6d48f54a194cd00ef3beea18f9" as const;
export const PINNED_OPENAI_PLUGIN_COUNT = 180 as const;

export interface CatalogInventory {
  readonly pluginsWithSkills: number;
  readonly pluginsWithApps: number;
  readonly pluginsWithAgents: number;
  readonly pluginsWithCommands: number;
  readonly pluginsWithMcp: number;
  readonly pluginsWithCommandHooks: number;
}

export interface CatalogComponentAdmission {
  readonly component: NormalizedPluginComponent;
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
