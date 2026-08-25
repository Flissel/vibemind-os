import { describe, expect, it } from "vitest";
import { MongoServerError, type ClientSession, type Db, type MongoClient } from "mongodb";
import {
  type PluginCatalogEntry,
  type PluginCatalogSnapshot,
  type PluginComponentAdmission,
  type PluginCredentialSlot,
  type PluginInstallation,
  type PluginMigrationRecord,
  type PluginReceipt,
} from "@/src/application/repositories/plugins.repository.interface";
import {
  MongodbPluginsRepository,
  MongoPluginTransactionRunner,
  type PluginTransactionRunner,
} from "@/src/infrastructure/repositories/mongodb.plugins.repository";
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
  beforeInsert?: (document: Document) => void;

  constructor(private readonly collectionName: string) {}

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
    const uniqueKeys: Readonly<Record<string, readonly (readonly string[])[]>> = {
      plugin_catalog_snapshots: [["catalogDigest"]],
      plugin_catalog_entries: [["catalogDigest", "name"]],
      plugin_installations: [["id"], ["projectId", "pluginName"]],
      plugin_component_admissions: [["installationId", "componentDigest"]],
      plugin_credential_slots: [["id"]],
      plugin_migration_records: [["id"]],
      plugin_receipts: [["receiptId"]],
    };
    await Promise.resolve();
    const beforeInsert = this.beforeInsert;
    this.beforeInsert = undefined;
    beforeInsert?.(document);
    for (const key of uniqueKeys[this.collectionName] ?? []) {
      if (this.documents.some((existing) => key.every((field) => existing[field] === document[field]))) {
        throw new MongoServerError({ ok: 0, code: 11000, errmsg: "E11000 token-secret" });
      }
    }
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
      collection = new MemoryCollection(name);
      this.collections.set(name, collection);
    }
    return collection;
  }
}

class MemoryTransactionRunner implements PluginTransactionRunner {
  constructor(private readonly database: MemoryDatabase) {}

  async run<T>(work: (session: ClientSession | undefined) => Promise<T>): Promise<T> {
    const snapshots = new Map<string, { documents: Document[]; writes: number }>();
    for (const [name, collection] of this.database.collections) {
      snapshots.set(name, { documents: clone(collection.documents), writes: collection.writes });
    }
    try {
      return await work(undefined);
    } catch (error) {
      for (const [name, collection] of this.database.collections) {
        const snapshot = snapshots.get(name) ?? { documents: [], writes: 0 };
        collection.documents.splice(0, collection.documents.length, ...snapshot.documents);
        collection.writes = snapshot.writes;
      }
      throw error;
    }
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
  components: [{
    component: {
      id: "github:mcp",
      name: "GitHub MCP",
      kind: "mcp",
      status: "available",
      metadata: { digest: digest("d") },
    },
    admission: { status: "admitted", policyVersion: snapshot.policyVersion },
  }, {
    component: {
      id: "github:command",
      name: "GitHub command",
      kind: "command",
      status: "available",
      metadata: { digest: digest("e") },
    },
    admission: { status: "admitted", policyVersion: snapshot.policyVersion },
  }],
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
    repository: new MongodbPluginsRepository({
      pluginsDatabase: database as unknown as Db,
      pluginTransactionRunner: new MemoryTransactionRunner(database),
    }),
  };
}

async function seedCatalog(repository: MongodbPluginsRepository): Promise<void> {
  await repository.putCatalogSnapshot(snapshot);
  await repository.putCatalogEntries([entry]);
}

async function seedInstallation(repository: MongodbPluginsRepository): Promise<void> {
  await seedCatalog(repository);
  await repository.putInstallation(installation);
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

  it("concurrently stores an identical snapshot once and succeeds twice", async () => {
    const { database, repository } = repositoryFixture();
    await expect(Promise.all([
      repository.putCatalogSnapshot(snapshot),
      repository.putCatalogSnapshot(clone(snapshot)),
    ])).resolves.toEqual([undefined, undefined]);
    expect(database.collection(PLUGIN_COLLECTIONS.catalogSnapshots).writes).toBe(1);
  });

  it("concurrently rejects a conflicting digest without leaking Mongo duplicate details", async () => {
    const { database, repository } = repositoryFixture();
    const outcomes = await Promise.allSettled([
      repository.putCatalogSnapshot(snapshot),
      repository.putCatalogSnapshot({ ...snapshot, policyVersion: "rowboat-plugin-policy-v2" }),
    ]);
    expect(outcomes.filter((outcome) => outcome.status === "fulfilled")).toHaveLength(1);
    const rejected = outcomes.find((outcome) => outcome.status === "rejected");
    expect(rejected).toMatchObject({ reason: new Error("catalog_digest_conflict") });
    expect(JSON.stringify(rejected)).not.toContain("token-secret");
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

  it("rolls back an entry batch when a later existing member conflicts", async () => {
    const { database, repository } = repositoryFixture();
    await repository.putCatalogSnapshot(snapshot);
    const slack = { ...entry, name: "slack", pluginName: "slack" };
    await repository.putCatalogEntries([slack]);
    const collection = database.collection(PLUGIN_COLLECTIONS.catalogEntries);
    const baselineWrites = collection.writes;
    await expect(repository.putCatalogEntries([entry, { ...slack, pluginVersion: "2.0.0" }]))
      .rejects.toThrow("catalog_entry_conflict");
    expect(collection.writes).toBe(baselineWrites);
    expect(await repository.listCatalogEntries(snapshot.catalogDigest)).toEqual([slack]);
  });

  it("rolls back earlier transactional writes when a unique race appears during the batch", async () => {
    const { database, repository } = repositoryFixture();
    await repository.putCatalogSnapshot(snapshot);
    const slack = { ...entry, name: "slack", pluginName: "slack" };
    const collection = database.collection(PLUGIN_COLLECTIONS.catalogEntries);
    collection.beforeInsert = (document) => {
      if (document.name === "github") {
        collection.beforeInsert = () => {
          collection.documents.push({
            _id: "racing-mongo-id",
            catalogDigest: snapshot.catalogDigest,
            name: "slack",
            payload: "{}",
          });
        };
      }
    };
    await expect(repository.putCatalogEntries([entry, slack])).rejects.toThrow("catalog_entry_conflict");
    expect(collection.documents).toEqual([]);
    expect(collection.writes).toBe(0);
  });

  it("rejects entry provenance that differs from its catalog snapshot with zero writes", async () => {
    const { database, repository } = repositoryFixture();
    await repository.putCatalogSnapshot(snapshot);
    await expect(repository.putCatalogEntries([{ ...entry, importedAt: "2026-08-25T00:00:00.000Z" }]))
      .rejects.toThrow("catalog_entry_mismatch");
    expect(database.collection(PLUGIN_COLLECTIONS.catalogEntries).writes).toBe(0);
  });

  it("preserves installation revision on idempotent upsert", async () => {
    const { repository } = repositoryFixture();
    await seedCatalog(repository);
    await repository.putInstallation(installation);
    await repository.putInstallation(clone(installation));
    expect(await repository.listInstallations(installation.projectId)).toEqual([installation]);
  });

  it("rejects any material installation mismatch and preserves the stored revision", async () => {
    const { repository } = repositoryFixture();
    await seedCatalog(repository);
    await repository.putInstallation(installation);
    await expect(repository.putInstallation({ ...installation, revision: 0, enabled: false }))
      .rejects.toThrow("installation_conflict");
    expect(await repository.listInstallations(installation.projectId)).toEqual([installation]);
  });

  it("concurrently stores an identical installation once", async () => {
    const { database, repository } = repositoryFixture();
    await seedCatalog(repository);
    await expect(Promise.all([
      repository.putInstallation(installation),
      repository.putInstallation(clone(installation)),
    ])).resolves.toEqual([undefined, undefined]);
    expect(database.collection(PLUGIN_COLLECTIONS.installations).writes).toBe(1);
  });

  it("concurrently rejects a conflicting installation with a stable secret-free error", async () => {
    const { database, repository } = repositoryFixture();
    await seedCatalog(repository);
    const outcomes = await Promise.allSettled([
      repository.putInstallation(installation),
      repository.putInstallation({ ...installation, enabled: false, revision: 0 }),
    ]);
    expect(outcomes.filter((outcome) => outcome.status === "fulfilled")).toHaveLength(1);
    const rejected = outcomes.find((outcome) => outcome.status === "rejected");
    expect((rejected as PromiseRejectedResult).reason).toMatchObject({ message: "installation_conflict" });
    expect(JSON.stringify(rejected)).not.toContain("token-secret");
    expect(database.collection(PLUGIN_COLLECTIONS.installations).writes).toBe(1);
  });

  it("rejects an installation not bound to one exact catalog entry", async () => {
    const { database, repository } = repositoryFixture();
    await seedCatalog(repository);
    await expect(repository.putInstallation({ ...installation, treeDigest: digest("7") }))
      .rejects.toThrow("installation_catalog_mismatch");
    expect(database.collection(PLUGIN_COLLECTIONS.installations).writes).toBe(0);
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
    await seedCatalog(repository);
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
    await seedInstallation(repository);
    const admissions: readonly PluginComponentAdmission[] = [
      { installationId: installation.id, componentDigest: digest("e"), componentKind: "command", componentName: "GitHub command", status: "admitted", policyVersion: snapshot.policyVersion },
      { installationId: installation.id, componentDigest: digest("d"), componentKind: "mcp", componentName: "GitHub MCP", status: "admitted", policyVersion: snapshot.policyVersion },
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
      componentKind: "mcp",
      componentName: "GitHub MCP",
      status: "admitted",
      policyVersion: snapshot.policyVersion,
    };
    await expect(repository.putAdmissions([selected, selected])).rejects.toThrow("admission_conflict");
    expect(database.collection(PLUGIN_COLLECTIONS.componentAdmissions).writes).toBe(0);
  });

  it("rejects admissions without an exact installation and catalog component binding", async () => {
    const { database, repository } = repositoryFixture();
    await expect(repository.putAdmissions([{
      installationId: installation.id,
      componentDigest: digest("d"),
      componentKind: "mcp",
      componentName: "GitHub MCP",
      status: "admitted",
      policyVersion: snapshot.policyVersion,
    }])).rejects.toThrow("admission_parent_not_found");
    expect(database.collection(PLUGIN_COLLECTIONS.componentAdmissions).writes).toBe(0);
  });

  it("rolls back an admission batch when a later existing member conflicts", async () => {
    const { database, repository } = repositoryFixture();
    await seedInstallation(repository);
    const existing: PluginComponentAdmission = {
      installationId: installation.id, componentDigest: digest("d"), componentKind: "mcp", componentName: "GitHub MCP",
      status: "admitted", policyVersion: snapshot.policyVersion,
    };
    await repository.putAdmissions([existing]);
    const collection = database.collection(PLUGIN_COLLECTIONS.componentAdmissions);
    const corrupted = collection.documents.find((document) => document.componentDigest === existing.componentDigest)!;
    corrupted.status = "rejected";
    corrupted.reason = "provider_unavailable";
    const baselineWrites = collection.writes;
    await expect(repository.putAdmissions([{
      installationId: installation.id, componentDigest: digest("e"), componentKind: "command", componentName: "GitHub command",
      status: "admitted", policyVersion: snapshot.policyVersion,
    }, existing]))
      .rejects.toThrow("admission_conflict");
    expect(collection.writes).toBe(baselineWrites);
    expect(await repository.listAdmissions(installation.id)).toEqual([{ ...existing, status: "rejected", reason: "provider_unavailable" }]);
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
    let trapCalls = 0;
    const proxied = new Proxy(slot, {
      getPrototypeOf: (target) => { trapCalls += 1; return Reflect.getPrototypeOf(target); },
      ownKeys: (target) => { trapCalls += 1; return Reflect.ownKeys(target); },
      getOwnPropertyDescriptor: (target, key) => { trapCalls += 1; return Reflect.getOwnPropertyDescriptor(target, key); },
    });
    await expect(repository.putCredentialSlot(proxied as unknown as PluginCredentialSlot))
      .rejects.toThrow("secret_value_rejected");
    expect(getterCalls).toBe(0);
    expect(trapCalls).toBe(0);
  });

  it.each(["accessToken", "client_secret", "private-data", "apiKey", "auth", "cert", "sessionId"])(
    "rejects normalized credential key %s",
    async (field) => {
      const { repository } = repositoryFixture();
      await expect(repository.putCredentialSlot({
        id: "slot-1", projectId: installation.projectId, installationId: installation.id,
        name: "GITHUB_PAT_TOKEN", reference: { kind: "environment", reference: "github/pat" },
        metadata: { [field]: "secret-value" },
      } as unknown as PluginCredentialSlot)).rejects.toThrow("secret_value_rejected");
    },
  );

  it("rejects nested non-secret metadata and credential-bearing references", async () => {
    const { repository } = repositoryFixture();
    await expect(repository.putCredentialSlot({
      id: "slot-1", projectId: installation.projectId, installationId: installation.id,
      name: "GITHUB_PAT_TOKEN", reference: { kind: "environment", reference: "github/pat" },
      metadata: { details: { label: "GitHub" } },
    } as unknown as PluginCredentialSlot)).rejects.toThrow("credential_slot_invalid");
    const secretReference = "https://user:token-secret@example.invalid/key";
    const error = await repository.putCredentialSlot({
      id: "slot-1", projectId: installation.projectId, installationId: installation.id,
      name: "GITHUB_PAT_TOKEN", reference: { kind: "oauth", reference: secretReference },
    }).catch((caught: unknown) => caught);
    expect(error).toEqual(new Error("secret_value_rejected"));
    expect(String(error)).not.toContain("token-secret");
  });

  it("stores only allowlisted credential references and immutable bounded records", async () => {
    const { database, repository } = repositoryFixture();
    await seedInstallation(repository);
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

  it("rejects a credential slot whose project differs from its installation", async () => {
    const { database, repository } = repositoryFixture();
    await seedInstallation(repository);
    await expect(repository.putCredentialSlot({
      id: "slot-1", projectId: "project-2", installationId: installation.id,
      name: "GITHUB_PAT_TOKEN", reference: { kind: "environment", reference: "github/pat" },
    })).rejects.toThrow("credential_slot_parent_mismatch");
    expect(database.collection(PLUGIN_COLLECTIONS.credentialSlots).writes).toBe(0);
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

describe("Mongo plugin transaction runner", () => {
  it("supports a successful void transaction and always ends its session", async () => {
    let ended = 0;
    const session = {
      withTransaction: async (work: () => Promise<void>) => work(),
      endSession: async () => { ended += 1; },
    };
    const client = { startSession: () => session } as unknown as MongoClient;
    const runner = new MongoPluginTransactionRunner({ pluginsMongoClient: client });
    await expect(runner.run(async () => undefined)).resolves.toBeUndefined();
    expect(ended).toBe(1);
  });

  it("sanitizes a raw Mongo transaction error and still ends its session", async () => {
    let ended = 0;
    const session = {
      withTransaction: async () => { throw new MongoServerError({ ok: 0, code: 11000, errmsg: "E11000 token-secret" }); },
      endSession: async () => { ended += 1; },
    };
    const client = { startSession: () => session } as unknown as MongoClient;
    const runner = new MongoPluginTransactionRunner({ pluginsMongoClient: client });
    const error = await runner.run(async () => undefined).catch((caught: unknown) => caught);
    expect(error).toEqual(new Error("repository_transaction_failed"));
    expect(String(error)).not.toContain("token-secret");
    expect(ended).toBe(1);
  });

  it("sanitizes a session creation failure", async () => {
    const client = {
      startSession: () => { throw new Error("mongodb://user:token-secret@host"); },
    } as unknown as MongoClient;
    const runner = new MongoPluginTransactionRunner({ pluginsMongoClient: client });
    const error = await runner.run(async () => undefined).catch((caught: unknown) => caught);
    expect((error as Error).message).toBe("repository_transaction_failed");
    expect(String(error)).not.toContain("token-secret");
  });
});
