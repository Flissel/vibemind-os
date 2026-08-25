import type { Db, IndexDescription } from "mongodb";

export const PLUGIN_COLLECTIONS = Object.freeze({
  catalogSnapshots: "plugin_catalog_snapshots",
  catalogEntries: "plugin_catalog_entries",
  installations: "plugin_installations",
  componentAdmissions: "plugin_component_admissions",
  credentialSlots: "plugin_credential_slots",
  migrationRecords: "plugin_migration_records",
  receipts: "plugin_receipts",
} as const);

type PluginCollectionIndexSet = Readonly<{
  collection: (typeof PLUGIN_COLLECTIONS)[keyof typeof PLUGIN_COLLECTIONS];
  indexes: readonly IndexDescription[];
}>;

export const PLUGIN_COLLECTION_INDEXES = Object.freeze([
  { collection: PLUGIN_COLLECTIONS.catalogSnapshots, indexes: [{ key: { catalogDigest: 1 }, name: "catalogDigest_unique", unique: true }] },
  { collection: PLUGIN_COLLECTIONS.catalogEntries, indexes: [{ key: { catalogDigest: 1, name: 1 }, name: "catalogDigest_name_unique", unique: true }] },
  { collection: PLUGIN_COLLECTIONS.installations, indexes: [
    { key: { id: 1 }, name: "id_unique", unique: true },
    { key: { projectId: 1, pluginName: 1 }, name: "projectId_pluginName_unique", unique: true },
  ] },
  { collection: PLUGIN_COLLECTIONS.componentAdmissions, indexes: [{ key: { installationId: 1, componentDigest: 1 }, name: "installationId_componentDigest_unique", unique: true }] },
  { collection: PLUGIN_COLLECTIONS.credentialSlots, indexes: [{ key: { id: 1 }, name: "id_unique", unique: true }] },
  { collection: PLUGIN_COLLECTIONS.migrationRecords, indexes: [{ key: { id: 1 }, name: "id_unique", unique: true }] },
  { collection: PLUGIN_COLLECTIONS.receipts, indexes: [{ key: { receiptId: 1 }, name: "receiptId_unique", unique: true }] },
] satisfies readonly PluginCollectionIndexSet[]);

export async function ensurePluginIndexes(database: Db): Promise<void> {
  for (const { collection, indexes } of PLUGIN_COLLECTION_INDEXES) {
    await database.collection(collection).createIndexes(indexes.map((index) => index as IndexDescription));
  }
}
