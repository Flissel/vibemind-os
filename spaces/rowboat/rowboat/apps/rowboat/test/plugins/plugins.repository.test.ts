import { describe, expect, it } from "vitest";
import type { Db } from "mongodb";
import {
  type PluginCatalogEntry,
  type PluginCatalogSnapshot,
  type PluginComponentAdmission,
  type PluginCredentialSlot,
  type PluginInstallation,
  type PluginMigrationRecord,
  type PluginReceipt,
} from "@/src/application/repositories/plugins.repository.interface";
import { MongodbPluginsRepository } from "@/src/infrastructure/repositories/mongodb.plugins.repository";
import {
  PLUGIN_COLLECTION_INDEXES,
  PLUGIN_COLLECTIONS,
  ensurePluginIndexes,
} from "@/src/infrastructure/repositories/mongodb.plugins.indexes";

type Document = Record<string, unknown>;

function clone<T>(value: T): T {
  return structuredClone(value);
}

function matches(document: Document, filter: Document): boolean {
  return Object.entries(filter).every(([key, value]) => document[key] === value);
}

class MemoryCollection {
  readonly documents: Document[] = [];
  readonly indexes: Document[] = [];
  writes = 0;

  private project(document: Document, options?: { projection?: Document }): Document {
    const projected = clone(document);
    if (options?.projection?._id === 0) delete projected._id;
    return projected;
  }

  async findOne(filter: Document, options?: { projection?: Document }): Promise<Document | null> {
    const found = this.documents.find((document) => matches(document, filter));
    return found === undefined ? null : this.project(found, options);
  }

  find(filter: Document, options?: { projection?: Document }): { sort: (sort: Document) => { toArray: () => Promise<Document[]> } } {
    return {
      sort: (sort) => ({
        toArray: async () => {
          const entries = this.documents.filter((document) => matches(document, filter));
          const [[field, direction]] = Object.entries(sort);
          return entries
            .sort((left, right) => String(left[field]).localeCompare(String(right[field])) * Number(direction))
            .map((document) => this.project(document, options));
        },
      }),
    };
  }

  async insertOne(document: Document): Promise<{ acknowledged: true }> {
    this.documents.push({ _id: `mongo-id-${this.documents.length + 1}`, ...clone(document) });
    this.writes += 1;
    return { acknowledged: true };
  }

  async findOneAndUpdate(
    filter: Document,
    update: { $set: Document; $inc: Document },
    options?: { projection?: Document },
  ): Promise<Document | null> {
    const index = this.documents.findIndex((document) => matches(document, filter));
    if (index < 0) return null;
    const original = this.documents[index]!;
    const updated: Document = { ...original, ...clone(update.$set) };
    for (const [key, increment] of Object.entries(update.$inc)) {
      updated[key] = Number(updated[key]) + Number(increment);
    }
    this.documents[index] = updated;
    this.writes += 1;
    return this.project(updated, options);
  }

  async createIndexes(indexes: readonly Document[]): Promise<string[]> {
    this.indexes.push(...clone(indexes));
    return indexes.map((index) => String(index.name));
  }
}

class MemoryDatabase {
  readonly collections = new Map<string, MemoryCollection>();

  collection(name: string): MemoryCollection {
    let collection = this.collections.get(name);
    if (!collection) {
      collection = new MemoryCollection();
      this.collections.set(name, collection);
    }
    return collection;
  }
}

const digest = (character: string): string => character.repeat(64);

const snapshot: PluginCatalogSnapshot = {
  sourceUrl: "https://github.com/openai/plugins.git",
  sourceCommit: "11c74d6ba24d3a6d48f54a194cd00ef3beea18f9",
  importedAt: "2026-08-24T00:00:00.000Z",
  schemaVersion: "rowboat-plugin-schema-v1",
  policyVersion: "rowboat-plugin-policy-v1",
  inventory: {
    pluginsWithSkills: 72,
    pluginsWithApps: 154,
    pluginsWithAgents: 14,
    pluginsWithCommands: 6,
    pluginsWithMcp: 8,
    pluginsWithCommandHooks: 2,
  },
  licenseDeclarations: { MIT: 164 },
  catalogDigest: digest("a"),
};

const entry: PluginCatalogEntry = {
  catalogDigest: snapshot.catalogDigest,
  sourceUrl: snapshot.sourceUrl,
  sourceCommit: snapshot.sourceCommit,
  pluginName: "github",
  pluginVersion: "1.0.0",
  manifestDigest: digest("b"),
  treeDigest: digest("c"),
  importedAt: snapshot.importedAt,
  schemaVersion: snapshot.schemaVersion,
  policyVersion: snapshot.policyVersion,
  name: "github",
  admission: { status: "admitted", policyVersion: snapshot.policyVersion },
  components: [],
};

const installation: PluginInstallation = {
  id: "installation-1",
  projectId: "project-1",
  pluginName: "github",
  pluginVersion: "1.0.0",
  sourceCommit: snapshot.sourceCommit,
  manifestDigest: entry.manifestDigest,
  treeDigest: entry.treeDigest,
  policyVersion: snapshot.policyVersion,
  enabled: true,
  revision: 1,
};

function repositoryFixture(): { database: MemoryDatabase; repository: MongodbPluginsRepository } {
  const database = new MemoryDatabase();
  return {
    database,
    repository: new MongodbPluginsRepository({ pluginsDatabase: database as unknown as Db }),
  };
}

describe("plugin repository contract", () => {
  it("stores an immutable catalog snapshot once by digest", async () => {
    const { database, repository } = repositoryFixture();
    const mutable = { ...clone(snapshot) };
    await repository.putCatalogSnapshot(mutable);
    mutable.sourceUrl = "https://attacker.invalid/replaced";
    await repository.putCatalogSnapshot(snapshot);

    const stored = await repository.getCatalogSnapshot(snapshot.catalogDigest);
    expect(stored).toEqual(snapshot);
    expect(Object.isFrozen(stored)).toBe(true);
    expect(database.collection(PLUGIN_COLLECTIONS.catalogSnapshots).writes).toBe(1);
  });

  it("rejects a conflicting snapshot with the same digest without overwriting", async () => {
    const { repository } = repositoryFixture();
    await repository.putCatalogSnapshot(snapshot);
    await expect(repository.putCatalogSnapshot({ ...snapshot, policyVersion: "rowboat-plugin-policy-v2" }))
      .rejects.toThrow("catalog_digest_conflict");
    expect((await repository.getCatalogSnapshot(snapshot.catalogDigest))?.sourceUrl).toBe(snapshot.sourceUrl);
  });

  it("rejects a credential-bearing catalog source URL without reflecting it", async () => {
    const { repository } = repositoryFixture();
    const sourceUrl = "https://user:token-secret@github.com/openai/plugins.git";
    const error = await repository.putCatalogSnapshot({ ...snapshot, sourceUrl }).catch((caught: unknown) => caught);
    expect(error).toEqual(new Error("catalog_snapshot_invalid"));
    expect(String(error)).not.toContain("token-secret");
  });

  it("binds entries to a catalog and lists them deterministically", async () => {
    const { repository } = repositoryFixture();
    await repository.putCatalogSnapshot(snapshot);
    await repository.putCatalogEntries([{ ...entry, name: "slack", pluginName: "slack" }, entry]);
    const entries = await repository.listCatalogEntries(snapshot.catalogDigest);
    expect(entries.map((item) => item.name)).toEqual(["github", "slack"]);
    expect(Object.isFrozen(entries)).toBe(true);
    expect(Object.isFrozen(entries[0])).toBe(true);
  });

  it("rejects malformed nested catalog admission records", async () => {
    const { repository } = repositoryFixture();
    await repository.putCatalogSnapshot(snapshot);
    const malformed = {
      ...entry,
      admission: { status: "admitted", policyVersion: snapshot.policyVersion, bypass: true },
    };
    await expect(repository.putCatalogEntries([malformed as unknown as PluginCatalogEntry]))
      .rejects.toThrow("catalog_entry_invalid");
  });

  it("validates a catalog-entry batch before writing any member", async () => {
    const { database, repository } = repositoryFixture();
    await repository.putCatalogSnapshot(snapshot);
    const missingCatalogEntry = { ...entry, catalogDigest: digest("8"), name: "slack", pluginName: "slack" };
    await expect(repository.putCatalogEntries([entry, missingCatalogEntry]))
      .rejects.toThrow("catalog_snapshot_not_found");
    expect(database.collection(PLUGIN_COLLECTIONS.catalogEntries).writes).toBe(0);
  });

  it("preserves installation revision on idempotent upsert", async () => {
    const { repository } = repositoryFixture();
    await repository.putInstallation(installation);
    await repository.putInstallation({ ...installation, revision: 0, enabled: false });
    expect(await repository.listInstallations(installation.projectId)).toEqual([installation]);
  });

  it("rejects malformed nested provider bindings", async () => {
    const { repository } = repositoryFixture();
    const malformed = {
      ...installation,
      providerBindings: [{
        componentId: "github:mcp",
        binding: {
          id: "binding-1",
          providerKind: "invented-provider",
          componentDigest: digest("d"),
        },
      }],
    };
    await expect(repository.putInstallation(malformed as unknown as PluginInstallation))
      .rejects.toThrow("installation_invalid");
  });

  it("uses one atomic optimistic update for installation enablement", async () => {
    const { database, repository } = repositoryFixture();
    await repository.putInstallation(installation);
    await expect(repository.setInstallationEnabled(installation.id, false, 0))
      .rejects.toThrow("installation_conflict");
    const updated = await repository.setInstallationEnabled(installation.id, false, 1);
    expect(updated).toMatchObject({ enabled: false, revision: 2 });
    expect(database.collection(PLUGIN_COLLECTIONS.installations).writes).toBe(2);
  });

  it("returns a stable not-found error for missing installation enablement", async () => {
    const { repository } = repositoryFixture();
    await expect(repository.setInstallationEnabled("missing", false, 0))
      .rejects.toThrow("installation_not_found");
  });

  it("stores deterministic admissions bound to installation and component digest", async () => {
    const { repository } = repositoryFixture();
    const admissions: readonly PluginComponentAdmission[] = [
      { installationId: installation.id, componentDigest: digest("e"), status: "admitted", policyVersion: snapshot.policyVersion },
      { installationId: installation.id, componentDigest: digest("d"), status: "rejected", reason: "provider_unavailable", policyVersion: snapshot.policyVersion },
    ];
    await repository.putAdmissions(admissions);
    expect((await repository.listAdmissions(installation.id)).map((item) => item.componentDigest))
      .toEqual([digest("d"), digest("e")]);
  });

  it("rejects a duplicate admission batch before writing any member", async () => {
    const { database, repository } = repositoryFixture();
    const selected: PluginComponentAdmission = {
      installationId: installation.id,
      componentDigest: digest("d"),
      status: "admitted",
      policyVersion: snapshot.policyVersion,
    };
    await expect(repository.putAdmissions([selected, selected])).rejects.toThrow("admission_conflict");
    expect(database.collection(PLUGIN_COLLECTIONS.componentAdmissions).writes).toBe(0);
  });

  it.each(["value", "secret", "token", "password", "privateKey"])(
    "rejects credential secret material in %s",
    async (field) => {
      const { repository } = repositoryFixture();
      const slot = {
        id: "slot-1",
        projectId: installation.projectId,
        installationId: installation.id,
        name: "GITHUB_PAT_TOKEN",
        reference: { kind: "environment", reference: "github/pat" },
        metadata: { label: "GitHub token" },
        [field]: "secret-value",
      };
      await expect(repository.putCredentialSlot(slot as unknown as PluginCredentialSlot))
        .rejects.toThrow("secret_value_rejected");
    },
  );

  it("rejects nested credential secrets and accessors without executing getters", async () => {
    const { repository } = repositoryFixture();
    let getterCalls = 0;
    const slot = {
      id: "slot-1",
      projectId: installation.projectId,
      installationId: installation.id,
      name: "GITHUB_PAT_TOKEN",
      reference: { kind: "environment", reference: "github/pat" },
      metadata: { nested: { password: "secret-value" } },
    };
    Object.defineProperty(slot, "token", { enumerable: true, get: () => { getterCalls += 1; return "secret"; } });
    await expect(repository.putCredentialSlot(slot as unknown as PluginCredentialSlot)).rejects.toThrow("secret_value_rejected");
    expect(getterCalls).toBe(0);
  });

  it("rejects a proxied credential accessor without executing its getter", async () => {
    const { repository } = repositoryFixture();
    let getterCalls = 0;
    const slot = {
      id: "slot-1",
      projectId: installation.projectId,
      installationId: installation.id,
      name: "GITHUB_PAT_TOKEN",
      reference: { kind: "environment", reference: "github/pat" },
    };
    Object.defineProperty(slot, "password", { enumerable: true, get: () => { getterCalls += 1; return "secret"; } });
    await expect(repository.putCredentialSlot(new Proxy(slot, {}) as unknown as PluginCredentialSlot))
      .rejects.toThrow("secret_value_rejected");
    expect(getterCalls).toBe(0);
  });

  it("stores only allowlisted credential references and immutable bounded records", async () => {
    const { database, repository } = repositoryFixture();
    const slot: PluginCredentialSlot = {
      id: "slot-1",
      projectId: installation.projectId,
      installationId: installation.id,
      name: "GITHUB_PAT_TOKEN",
      reference: { kind: "environment", reference: "github/pat" },
      metadata: { label: "GitHub token" },
    };
    const migration: PluginMigrationRecord = {
      id: "migration-1",
      projectId: installation.projectId,
      sourceDigest: digest("f"),
      targetDigest: digest("0"),
      status: "previewed",
      createdAt: snapshot.importedAt,
    };
    const receipt: PluginReceipt = {
      type: "install",
      receiptId: "receipt-1",
      projectId: installation.projectId,
      pluginName: installation.pluginName,
      status: "success",
      redactions: [],
    };
    await repository.putCredentialSlot(slot);
    await repository.putMigrationRecord(migration);
    await repository.putReceipt(receipt);
    const serialized = JSON.stringify([...database.collections.values()].flatMap((collection) => collection.documents));
    expect(serialized).not.toContain("secret-value");
  });

  it("accepts real dotted license identifiers without creating dotted Mongo fields", async () => {
    const { database, repository } = repositoryFixture();
    await repository.putCatalogSnapshot({
      ...snapshot,
      catalogDigest: digest("9"),
      licenseDeclarations: { "Apache-2.0": 6, MIT: 164 },
    });
    const stored = database.collection(PLUGIN_COLLECTIONS.catalogSnapshots).documents[0]!;
    expect(Object.keys(stored).filter((key) => key !== "_id")).toEqual(["catalogDigest", "payload"]);
    expect((await repository.getCatalogSnapshot(digest("9")))?.licenseDeclarations)
      .toEqual({ "Apache-2.0": 6, MIT: 164 });
  });
});

describe("plugin Mongo index contract", () => {
  it("declares the exact seven collections and unique indexes", async () => {
    expect(PLUGIN_COLLECTIONS).toEqual({
      catalogSnapshots: "plugin_catalog_snapshots",
      catalogEntries: "plugin_catalog_entries",
      installations: "plugin_installations",
      componentAdmissions: "plugin_component_admissions",
      credentialSlots: "plugin_credential_slots",
      migrationRecords: "plugin_migration_records",
      receipts: "plugin_receipts",
    });
    expect(PLUGIN_COLLECTION_INDEXES.map(({ collection, indexes }) => ({
      collection,
      keys: indexes.map((index) => index.key),
      unique: indexes.map((index) => index.unique),
    }))).toEqual([
      { collection: "plugin_catalog_snapshots", keys: [{ catalogDigest: 1 }], unique: [true] },
      { collection: "plugin_catalog_entries", keys: [{ catalogDigest: 1, name: 1 }], unique: [true] },
      { collection: "plugin_installations", keys: [{ id: 1 }, { projectId: 1, pluginName: 1 }], unique: [true, true] },
      { collection: "plugin_component_admissions", keys: [{ installationId: 1, componentDigest: 1 }], unique: [true] },
      { collection: "plugin_credential_slots", keys: [{ id: 1 }], unique: [true] },
      { collection: "plugin_migration_records", keys: [{ id: 1 }], unique: [true] },
      { collection: "plugin_receipts", keys: [{ receiptId: 1 }], unique: [true] },
    ]);

    const database = new MemoryDatabase();
    await ensurePluginIndexes(database as unknown as Db);
    await ensurePluginIndexes(database as unknown as Db);
    expect([...database.collections.values()].every((collection) => collection.indexes.length > 0)).toBe(true);
  });
});
