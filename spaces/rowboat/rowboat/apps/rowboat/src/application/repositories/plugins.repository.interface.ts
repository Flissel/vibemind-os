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
  readonly status: "admitted" | "review_required" | "rejected";
  readonly reason?: PluginReasonCode;
  readonly policyVersion: string;
}

export type PluginCredentialMetadataValue = string | number | boolean | null;

export interface PluginCredentialSlot {
  readonly id: string;
  readonly projectId: string;
  readonly installationId: string;
  readonly name: string;
  readonly reference: CredentialReference;
  readonly metadata?: Readonly<Record<string, PluginCredentialMetadataValue>>;
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

export interface IPluginsRepository {
  putCatalogSnapshot(snapshot: PluginCatalogSnapshot): Promise<void>;
  getCatalogSnapshot(digest: string): Promise<PluginCatalogSnapshot | null>;
  listCatalogEntries(catalogDigest: string): Promise<readonly PluginCatalogEntry[]>;
  putCatalogEntries(entries: readonly PluginCatalogEntry[]): Promise<void>;
  putInstallation(installation: PluginInstallation): Promise<void>;
  listInstallations(projectId: string): Promise<readonly PluginInstallation[]>;
  setInstallationEnabled(id: string, enabled: boolean, expectedRevision: number): Promise<PluginInstallation>;
  putAdmissions(admissions: readonly PluginComponentAdmission[]): Promise<void>;
  listAdmissions(installationId: string): Promise<readonly PluginComponentAdmission[]>;
  putCredentialSlot(slot: PluginCredentialSlot): Promise<void>;
  putMigrationRecord(record: PluginMigrationRecord): Promise<void>;
  putReceipt(receipt: PluginReceipt): Promise<void>;
}
