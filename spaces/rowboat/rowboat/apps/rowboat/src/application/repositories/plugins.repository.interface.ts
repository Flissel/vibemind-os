import type {
  CredentialReference,
  PluginCatalogEntry as RuntimePluginCatalogEntry,
  PluginCatalogLock,
  PluginInstallation as RuntimePluginInstallation,
  PluginReasonCode,
  PluginReceipt as RuntimePluginReceipt,
} from "@rowboat/openai-plugin-runtime";

export type PluginCatalogSnapshot = Readonly<Omit<PluginCatalogLock, "entries">>;

export type PluginCatalogEntry = Readonly<RuntimePluginCatalogEntry & {
  readonly catalogDigest: string;
}>;

export type PluginInstallation = RuntimePluginInstallation;

export interface PluginComponentAdmission {
  readonly installationId: string;
  readonly componentDigest: string;
  readonly componentKind: "skill" | "agent" | "command" | "mcp" | "app" | "hook" | "asset";
  readonly componentName: string;
  readonly status: "admitted" | "review_required" | "rejected";
  readonly reason?: PluginReasonCode;
  readonly policyVersion: string;
}

export interface PluginCredentialMetadata {
  readonly label?: string;
  readonly required?: boolean;
  readonly order?: number;
}

export interface PluginCredentialSlot {
  readonly id: string;
  readonly projectId: string;
  readonly installationId: string;
  readonly name: string;
  readonly reference: CredentialReference;
  readonly metadata?: Readonly<PluginCredentialMetadata>;
}

export interface PluginMigrationRecord {
  readonly id: string;
  readonly projectId: string;
  readonly sourceDigest: string;
  readonly targetDigest: string;
  readonly status: "previewed" | "applied" | "verified" | "rolled_back" | "blocked";
  readonly createdAt: string;
}

export type PluginReceipt = RuntimePluginReceipt;

export interface PluginIdempotentInstall {
  readonly scope: string;
  readonly fingerprint: string;
  readonly installation: PluginInstallation;
  readonly admissions: readonly PluginComponentAdmission[];
  readonly credentialSlots: readonly PluginCredentialSlot[];
  readonly receipt: PluginReceipt;
}

export interface PluginIdempotentInstallResult {
  readonly receipt: PluginReceipt;
  readonly fingerprint: string;
  readonly replayed: boolean;
}

export interface PluginIdempotentEnable {
  readonly scope: string;
  readonly fingerprint: string;
  readonly projectId: string;
  readonly pluginName: string;
  readonly catalogDigest: string;
  readonly installationId: string;
  readonly enabled: boolean;
  readonly expectedRevision: number;
  readonly receipt: PluginReceipt;
}

export interface PluginIdempotentEnableResult extends PluginIdempotentInstallResult {
  readonly installation: PluginInstallation;
}

export type PluginMutationOperation = "install" | "set_enabled";

export interface PluginIdempotencyLookup {
  readonly scope: string;
  readonly fingerprint: string;
  readonly projectId: string;
  readonly pluginName: string;
  readonly operation: PluginMutationOperation;
}

export interface IPluginsRepository {
  putCatalog(lock: PluginCatalogLock): Promise<void>;
  getCatalog(digest: string): Promise<PluginCatalogLock | null>;
  putCatalogSnapshot(snapshot: PluginCatalogSnapshot): Promise<void>;
  getCatalogSnapshot(digest: string): Promise<PluginCatalogSnapshot | null>;
  listCatalogEntries(catalogDigest: string): Promise<readonly PluginCatalogEntry[]>;
  getInstallation(projectId: string, pluginName: string): Promise<PluginInstallation | null>;
  putCatalogEntries(entries: readonly PluginCatalogEntry[]): Promise<void>;
  putInstallation(installation: PluginInstallation): Promise<void>;
  listInstallations(projectId: string): Promise<readonly PluginInstallation[]>;
  setInstallationEnabled(id: string, enabled: boolean, expectedRevision: number): Promise<PluginInstallation>;
  putAdmissions(admissions: readonly PluginComponentAdmission[]): Promise<void>;
  listAdmissions(installationId: string): Promise<readonly PluginComponentAdmission[]>;
  listCredentialSlots(installationId: string): Promise<readonly PluginCredentialSlot[]>;
  putCredentialSlot(slot: PluginCredentialSlot): Promise<void>;
  putMigrationRecord(record: PluginMigrationRecord): Promise<void>;
  putReceipt(receipt: PluginReceipt): Promise<void>;
  getIdempotentReceipt(request: PluginIdempotencyLookup): Promise<PluginReceipt | null>;
  installIdempotently(request: PluginIdempotentInstall): Promise<PluginIdempotentInstallResult>;
  setInstallationEnabledIdempotently(request: PluginIdempotentEnable): Promise<PluginIdempotentEnableResult>;
}
