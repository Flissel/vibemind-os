import { describe, expect, it } from "vitest";
import { MongoServerError, type ClientSession, type Db, type MongoClient } from "mongodb";
import {
  type PluginCatalogEntry,
  type PluginCatalogSnapshot,
  type PluginComponentAdmission,
  type PluginCredentialSlot,
  type PluginInstallation,
  type PluginIdempotentInstall,
  type PluginIdempotentEnable,
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
import { ensureAllIndexes } from "@/src/infrastructure/mongodb/ensure-indexes";
import { componentBindingDigest, type PluginCatalogLock } from "@rowboat/openai-plugin-runtime";
import catalogLockFixture from "../../../../config/openai-plugin-catalog.lock.json";

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
  readonly operationSessions: Array<ClientSession | undefined> = [];
  writes = 0;
  createIndexCalls = 0;
  createIndexesError: Error | undefined;
  beforeInsert?: (document: Document, session: ClientSession | undefined) => void;

  constructor(private readonly collectionName: string, private readonly database: MemoryDatabase) {}

  private documentsFor(session: ClientSession | undefined): Document[] {
    this.operationSessions.push(session);
    this.database.recordOperation(session);
    if (session === undefined) return this.documents;
    const state = this.database.sessionState(session);
    if (state.aborted) throw new MongoServerError({ ok: 0, code: 251, errmsg: "transaction-aborted token-secret" });
    return [...(state.snapshots.get(this) ?? []), ...(state.pending.get(this) ?? [])];
  }

  private project(document: Document, options?: { projection?: Document }): Document {
    const projected = clone(document);
    if (options?.projection?._id === 0) delete projected._id;
    return projected;
  }

  async findOne(filter: Document, options?: { projection?: Document; session?: ClientSession }): Promise<Document | null> {
    const found = this.documentsFor(options?.session).find((document) => matches(document, filter));
    return found === undefined ? null : this.project(found, options);
  }

  find(filter: Document, options?: { projection?: Document; session?: ClientSession }): { sort: (sort: Document) => { toArray: () => Promise<Document[]> } } {
    return {
      sort: (sort) => ({
        toArray: async () => {
          const entries = this.documentsFor(options?.session).filter((document) => matches(document, filter));
          const [[field, direction]] = Object.entries(sort);
          return entries
            .sort((left, right) => String(left[field]).localeCompare(String(right[field])) * Number(direction))
            .map((document) => this.project(document, options));
        },
      }),
    };
  }

  async insertOne(document: Document, options?: { session: ClientSession }): Promise<{ acknowledged: true }> {
    const uniqueKeys: Readonly<Record<string, readonly (readonly string[])[]>> = {
      plugin_catalog_snapshots: [["catalogDigest"]],
      plugin_catalog_entries: [["catalogDigest", "name"]],
      plugin_installations: [["id"], ["projectId", "pluginName"]],
      plugin_component_admissions: [["installationId", "componentDigest"]],
      plugin_credential_slots: [["id"]],
      plugin_migration_records: [["id"]],
      plugin_receipts: [["receiptId"], ["idempotencyScope"]],
    };
    await Promise.resolve();
    const beforeInsert = this.beforeInsert;
    this.beforeInsert = undefined;
    beforeInsert?.(document, options?.session);
    const visible = this.documentsFor(options?.session);
    for (const key of uniqueKeys[this.collectionName] ?? []) {
      if (visible.some((existing) => key.every((field) => existing[field] === document[field]))
        || this.documents.some((existing) => key.every((field) => existing[field] === document[field]))) {
        if (options?.session !== undefined) this.database.sessionState(options.session).aborted = true;
        throw new MongoServerError({ ok: 0, code: 11000, errmsg: "E11000 token-secret" });
      }
    }
    const stored = { _id: `mongo-id-${this.documents.length + visible.length + 1}`, ...clone(document) };
    if (options?.session === undefined) {
      this.documents.push(stored);
      this.writes += 1;
    } else {
      const state = this.database.sessionState(options.session);
      const pending = state.pending.get(this) ?? [];
      pending.push(stored);
      state.pending.set(this, pending);
    }
    return { acknowledged: true };
  }

  insertExternal(document: Document): void {
    this.documents.push({ _id: `external-id-${this.documents.length + 1}`, ...clone(document) });
    this.writes += 1;
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
    this.createIndexCalls += 1;
    if (this.createIndexesError !== undefined) throw this.createIndexesError;
    this.indexes.push(...clone(indexes));
    return indexes.map((index) => String(index.name));
  }
}

class MemoryDatabase {
  readonly collections = new Map<string, MemoryCollection>();
  readonly sessionStates = new Map<ClientSession, MemorySessionState>();
  activeSession: ClientSession | undefined;
  transactionSessionMismatches = 0;

  collection(name: string): MemoryCollection {
    let collection = this.collections.get(name);
    if (!collection) {
      collection = new MemoryCollection(name, this);
      this.collections.set(name, collection);
    }
    return collection;
  }

  startSession(): MemorySessionState {
    const token = Object.freeze({ memorySession: this.sessionStates.size + 1 }) as unknown as ClientSession;
    const state: MemorySessionState = { token, aborted: false, snapshots: new Map(), pending: new Map() };
    for (const collection of this.collections.values()) state.snapshots.set(collection, clone(collection.documents));
    this.sessionStates.set(token, state);
    return state;
  }

  sessionState(token: ClientSession): MemorySessionState {
    const state = this.sessionStates.get(token);
    if (state === undefined) throw new Error("unknown_memory_session");
    return state;
  }

  recordOperation(session: ClientSession | undefined): void {
    if (this.activeSession !== undefined && session !== this.activeSession) this.transactionSessionMismatches += 1;
  }

  commit(state: MemorySessionState): void {
    if (state.aborted) throw new Error("transaction_aborted");
    for (const [collection, documents] of state.pending) {
      collection.documents.push(...clone(documents));
      collection.writes += documents.length;
    }
  }
}

interface MemorySessionState {
  readonly token: ClientSession;
  aborted: boolean;
  readonly snapshots: Map<MemoryCollection, Document[]>;
  readonly pending: Map<MemoryCollection, Document[]>;
}

class MemoryTransactionRunner implements PluginTransactionRunner {
  constructor(private readonly database: MemoryDatabase) {}

  async run<T>(work: (session: ClientSession) => Promise<T>): Promise<T> {
    const state = this.database.startSession();
    this.database.activeSession = state.token;
    try {
      const value = await work(state.token);
      this.database.commit(state);
      return value;
    } finally {
      this.database.activeSession = undefined;
    }
  }
}

const digest = (character: string): string => character.repeat(64);
const PLUGIN_RECORD_MAX_BYTES = 256 * 1024;

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

function bindCatalogEntry(input: PluginCatalogEntry): PluginCatalogEntry {
  return {
    ...input,
    components: input.components.map((selected) => ({
      ...selected,
      component: {
        ...selected.component,
        metadata: { ...selected.component.metadata, bindingDigest: componentBindingDigest(input, selected.component) },
      },
    })),
  };
}

const entry = bindCatalogEntry({
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
  licenseDeclaration: "MIT",
  admission: { status: "admitted", policyVersion: snapshot.policyVersion },
  components: [{
    component: {
      id: "github:mcp",
      name: "GitHub MCP",
      kind: "mcp",
      status: "available",
      metadata: { digest: digest("d"), transport: "process" },
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
  }, {
    component: {
      id: "github:app",
      name: "GitHub App",
      kind: "app",
      status: "available",
      metadata: { digest: digest("f") },
    },
    admission: { status: "admitted", policyVersion: snapshot.policyVersion },
  }],
} as unknown as PluginCatalogEntry);

const MCP_BINDING_DIGEST = entry.components[0]!.component.metadata.bindingDigest;
const COMMAND_BINDING_DIGEST = entry.components[1]!.component.metadata.bindingDigest;
const APP_BINDING_DIGEST = entry.components[2]!.component.metadata.bindingDigest;

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

function catalogEntryAtStoredBytes(targetBytes: number): PluginCatalogEntry {
  const padding = Array.from({ length: 16 }, () => "");
  const padded: PluginCatalogEntry = bindCatalogEntry({
    ...clone(entry),
    name: "budget-entry",
    pluginName: "budget-entry",
    components: entry.components.map((item, index) => index === 0
      ? { ...clone(item), component: { ...clone(item.component), metadata: { ...clone(item.component.metadata), padding } } }
      : clone(item)),
  });
  const storedBytes = (): number => Buffer.byteLength(JSON.stringify({
    catalogDigest: padded.catalogDigest,
    name: padded.name,
    payload: JSON.stringify(padded),
  }), "utf8");
  let remaining = targetBytes - storedBytes();
  if (remaining < 0) throw new Error("test_budget_target_too_small");
  for (let index = 0; index < padding.length && remaining > 0; index += 1) {
    const selected = Math.min(16_384, remaining);
    padding[index] = "a".repeat(selected);
    remaining -= selected;
  }
  if (remaining !== 0 || storedBytes() !== targetBytes) throw new Error("test_budget_target_unreachable");
  return padded;
}

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
  it("persists real components with distinct structural bindings when their raw content digest is identical", async () => {
    const lock = catalogLockFixture as unknown as PluginCatalogLock;
    const { entries, ...catalogSnapshot } = lock;
    const brand24 = entries.find((selected) => selected.name === "brand24");
    if (brand24 === undefined) throw new Error("brand24 fixture missing");
    const collision = brand24.components.filter(({ component }) => component.metadata.digest === "7205418daf4cd98ccec8878ec1a27195f73aa3bd22e4c7480a15c793828991fc");
    expect(collision).toHaveLength(2);
    expect(new Set(collision.map(({ component }) => component.metadata.bindingDigest)).size).toBe(2);
    const { repository } = repositoryFixture();
    await repository.putCatalogSnapshot(catalogSnapshot);
    await expect(repository.putCatalogEntries(entries.map((selected) => ({ ...selected, catalogDigest: lock.catalogDigest })))).resolves.toBeUndefined();
    await expect(repository.listCatalogEntries(lock.catalogDigest)).resolves.toHaveLength(180);
  });

  it("uses structural binding digests for provider and admission persistence", async () => {
    const { repository } = repositoryFixture();
    const boundEntry = entry;
    await repository.putCatalogSnapshot(snapshot);
    await repository.putCatalogEntries([boundEntry]);
    const mcp = boundEntry.components[0]!;
    const componentDigest = String(mcp.component.metadata.bindingDigest);
    await repository.putInstallation({
      ...installation,
      providerBindings: [{ componentId: mcp.component.id, binding: { id: "binding-structural", providerKind: "mcp-process", componentDigest } }],
    });
    await expect(repository.putAdmissions([{
      installationId: installation.id,
      componentDigest,
      componentKind: mcp.component.kind,
      componentName: mcp.component.name,
      status: "admitted",
      policyVersion: snapshot.policyVersion,
    }])).resolves.toBeUndefined();
  });

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
    await repository.putCatalogEntries([bindCatalogEntry({ ...entry, name: "slack", pluginName: "slack" }), entry]);
    const entries = await repository.listCatalogEntries(snapshot.catalogDigest);
    expect(entries.map((item) => item.name)).toEqual(["github", "slack"]);
    expect(Object.isFrozen(entries)).toBe(true);
    expect(Object.isFrozen(entries[0])).toBe(true);
    expect(Object.isFrozen(entries[0]?.components)).toBe(true);
    expect(Object.isFrozen(entries[0]?.components[0]?.component)).toBe(true);
    expect(Object.isFrozen(entries[0]?.components[0]?.component.metadata)).toBe(true);
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

  it.each([
    ["missing digest", entry.components.map((item, index) => index === 0 ? { ...item, component: { ...item.component, metadata: {} } } : item)],
    ["invalid digest", entry.components.map((item, index) => index === 0 ? { ...item, component: { ...item.component, metadata: { digest: "invalid" } } } : item)],
    ["duplicate component id", [entry.components[0]!, { ...entry.components[1]!, component: { ...entry.components[1]!.component, id: entry.components[0]!.component.id } }]],
    ["duplicate component digest", [entry.components[0]!, { ...entry.components[1]!, component: { ...entry.components[1]!.component, metadata: { digest: entry.components[0]!.component.metadata.digest } } }]],
  ])("rejects %s in catalog components before any entry write", async (_case, components) => {
    const { database, repository } = repositoryFixture();
    await repository.putCatalogSnapshot(snapshot);
    await expect(repository.putCatalogEntries([{ ...entry, components } as PluginCatalogEntry]))
      .rejects.toThrow("catalog_entry_invalid");
    expect(database.collection(PLUGIN_COLLECTIONS.catalogEntries).writes).toBe(0);
  });

  it("validates a catalog-entry batch before writing any member", async () => {
    const { database, repository } = repositoryFixture();
    await repository.putCatalogSnapshot(snapshot);
    const missingCatalogEntry = bindCatalogEntry({ ...entry, catalogDigest: digest("8"), name: "slack", pluginName: "slack" });
    await expect(repository.putCatalogEntries([entry, missingCatalogEntry]))
      .rejects.toThrow("catalog_snapshot_not_found");
    expect(database.collection(PLUGIN_COLLECTIONS.catalogEntries).writes).toBe(0);
  });

  it("rolls back an entry batch when a later existing member conflicts", async () => {
    const { database, repository } = repositoryFixture();
    await repository.putCatalogSnapshot(snapshot);
    const slack = bindCatalogEntry({ ...entry, name: "slack", pluginName: "slack" });
    await repository.putCatalogEntries([slack]);
    const collection = database.collection(PLUGIN_COLLECTIONS.catalogEntries);
    const baselineWrites = collection.writes;
    await expect(repository.putCatalogEntries([entry, bindCatalogEntry({ ...slack, pluginVersion: "2.0.0" })]))
      .rejects.toThrow("catalog_entry_conflict");
    expect(collection.writes).toBe(baselineWrites);
    expect(await repository.listCatalogEntries(snapshot.catalogDigest)).toEqual([slack]);
  });

  it("rolls back earlier transactional writes when a unique race appears during the batch", async () => {
    const { database, repository } = repositoryFixture();
    await repository.putCatalogSnapshot(snapshot);
    const slack = bindCatalogEntry({ ...entry, name: "slack", pluginName: "slack" });
    const collection = database.collection(PLUGIN_COLLECTIONS.catalogEntries);
    collection.beforeInsert = (document) => {
      if (document.name === "github") {
        collection.beforeInsert = (racingDocument) => collection.insertExternal(racingDocument);
      }
    };
    await expect(repository.putCatalogEntries([entry, slack])).rejects.toThrow("catalog_entry_conflict");
    expect(collection.documents).toHaveLength(1);
    expect(collection.documents[0]).toMatchObject({ catalogDigest: snapshot.catalogDigest, name: "slack" });
    expect(collection.writes).toBe(1);
  });

  it("propagates one exact session to every transactional catalog read and write", async () => {
    const { database, repository } = repositoryFixture();
    await repository.putCatalogSnapshot(snapshot);
    await repository.putCatalogEntries([entry]);
    const sessions = [
      ...database.collection(PLUGIN_COLLECTIONS.catalogSnapshots).operationSessions,
      ...database.collection(PLUGIN_COLLECTIONS.catalogEntries).operationSessions,
    ].filter((session): session is ClientSession => session !== undefined);
    expect(sessions.length).toBeGreaterThan(0);
    expect(new Set(sessions).size).toBe(1);
    expect(sessions.every((session) => session === sessions[0])).toBe(true);
    expect(database.transactionSessionMismatches).toBe(0);
  });

  it("roundtrips a catalog entry just below the canonical UTF-8 document budget", async () => {
    const { repository } = repositoryFixture();
    await repository.putCatalogSnapshot(snapshot);
    const boundary = catalogEntryAtStoredBytes(PLUGIN_RECORD_MAX_BYTES - 1);
    await repository.putCatalogEntries([boundary]);
    expect(await repository.listCatalogEntries(snapshot.catalogDigest)).toEqual([boundary]);
  });

  it("rejects a record above the canonical document budget before any write", async () => {
    const { database, repository } = repositoryFixture();
    await repository.putCatalogSnapshot(snapshot);
    const oversized = catalogEntryAtStoredBytes(PLUGIN_RECORD_MAX_BYTES + 1);
    await expect(repository.putCatalogEntries([oversized])).rejects.toThrow("plugin_record_too_large");
    expect(database.collection(PLUGIN_COLLECTIONS.catalogEntries).writes).toBe(0);
  });

  it("counts multibyte Unicode as UTF-8 bytes and validates a whole batch before writing", async () => {
    const { database, repository } = repositoryFixture();
    await repository.putCatalogSnapshot(snapshot);
    const boundary = catalogEntryAtStoredBytes(PLUGIN_RECORD_MAX_BYTES - 1);
    const multibyte = {
      ...boundary,
      components: boundary.components.map((item, index) => index === 0
        ? {
            ...item,
            component: {
              ...item.component,
              metadata: {
                ...item.component.metadata,
                padding: (item.component.metadata.padding as readonly string[]).map((value) => value.replaceAll("a", "é")),
              },
            },
          }
        : item),
    } as PluginCatalogEntry;
    await expect(repository.putCatalogEntries([entry, multibyte])).rejects.toThrow("plugin_record_too_large");
    expect(database.collection(PLUGIN_COLLECTIONS.catalogEntries).writes).toBe(0);
  });

  it("applies the same UTF-8 budget before parsing stored payloads", async () => {
    const { database, repository } = repositoryFixture();
    database.collection(PLUGIN_COLLECTIONS.catalogEntries).documents.push({
      catalogDigest: snapshot.catalogDigest,
      name: "oversized-read",
      payload: `"${"é".repeat(PLUGIN_RECORD_MAX_BYTES / 2 + 1)}"`,
    });
    await expect(repository.listCatalogEntries(snapshot.catalogDigest)).rejects.toThrow("plugin_record_too_large");
  });

  it("rejects an over-budget dense array before bulk descriptors, own keys, or accessors", async () => {
    const { database, repository } = repositoryFixture();
    let accessorCalls = 0;
    const dense = Array.from({ length: 5_000 }, (_, index) => index);
    Object.defineProperty(dense, "0", { enumerable: true, get: () => { accessorCalls += 1; return 0; } });
    const originalBulkDescriptors = Object.getOwnPropertyDescriptors;
    const originalOwnKeys = Reflect.ownKeys;
    let denseBulkDescriptorCalls = 0;
    let denseOwnKeysCalls = 0;
    Object.getOwnPropertyDescriptors = ((value: object) => {
      if (value === dense) denseBulkDescriptorCalls += 1;
      return originalBulkDescriptors(value);
    }) as typeof Object.getOwnPropertyDescriptors;
    Reflect.ownKeys = ((value: object) => {
      if (value === dense) denseOwnKeysCalls += 1;
      return originalOwnKeys(value);
    }) as typeof Reflect.ownKeys;
    try {
      await expect(repository.putReceipt({
        type: "execution", receiptId: "receipt-dense-array", projectId: installation.projectId,
        pluginName: installation.pluginName, status: "failed", output: dense, redactions: [],
      })).rejects.toThrow("receipt_invalid");
    } finally {
      Object.getOwnPropertyDescriptors = originalBulkDescriptors;
      Reflect.ownKeys = originalOwnKeys;
    }
    expect(denseBulkDescriptorCalls).toBe(0);
    expect(denseOwnKeysCalls).toBe(0);
    expect(accessorCalls).toBe(0);
    expect(database.collection(PLUGIN_COLLECTIONS.receipts).writes).toBe(0);
  });

  it("rejects an over-budget plain record after one key count and before descriptor fetch", async () => {
    const { database, repository } = repositoryFixture();
    const dense = Object.create(null) as Record<string, unknown>;
    for (let index = 0; index < 5_000; index += 1) dense[`key${index}`] = index;
    let accessorCalls = 0;
    Object.defineProperty(dense, "key0", { enumerable: true, get: () => { accessorCalls += 1; return 0; } });
    const originalBulkDescriptors = Object.getOwnPropertyDescriptors;
    const originalDescriptor = Object.getOwnPropertyDescriptor;
    const originalOwnKeys = Reflect.ownKeys;
    let denseBulkDescriptorCalls = 0;
    let denseDescriptorCalls = 0;
    let denseOwnKeysCalls = 0;
    Object.getOwnPropertyDescriptors = ((value: object) => {
      if (value === dense) denseBulkDescriptorCalls += 1;
      return originalBulkDescriptors(value);
    }) as typeof Object.getOwnPropertyDescriptors;
    Object.getOwnPropertyDescriptor = ((value: object, key: PropertyKey) => {
      if (value === dense) denseDescriptorCalls += 1;
      return originalDescriptor(value, key);
    }) as typeof Object.getOwnPropertyDescriptor;
    Reflect.ownKeys = ((value: object) => {
      if (value === dense) denseOwnKeysCalls += 1;
      return originalOwnKeys(value);
    }) as typeof Reflect.ownKeys;
    try {
      await expect(repository.putReceipt({
        type: "execution", receiptId: "receipt-dense-record", projectId: installation.projectId,
        pluginName: installation.pluginName, status: "failed", output: dense, redactions: [],
      })).rejects.toThrow("receipt_invalid");
    } finally {
      Object.getOwnPropertyDescriptors = originalBulkDescriptors;
      Object.getOwnPropertyDescriptor = originalDescriptor;
      Reflect.ownKeys = originalOwnKeys;
    }
    expect(denseBulkDescriptorCalls).toBe(0);
    expect(denseOwnKeysCalls).toBe(1);
    expect(denseDescriptorCalls).toBe(0);
    expect(accessorCalls).toBe(0);
    expect(database.collection(PLUGIN_COLLECTIONS.receipts).writes).toBe(0);
  });

  it("rejects entry provenance that differs from its catalog snapshot with zero writes", async () => {
    const { database, repository } = repositoryFixture();
    await repository.putCatalogSnapshot(snapshot);
    await expect(repository.putCatalogEntries([{ ...entry, importedAt: "2026-08-25T00:00:00.000Z" }]))
      .rejects.toThrow("catalog_entry_mismatch");
    expect(database.collection(PLUGIN_COLLECTIONS.catalogEntries).writes).toBe(0);
  });

  it("persists one atomic installation receipt per exact idempotency scope and rejects payload reuse", async () => {
    const { database, repository } = repositoryFixture();
    await repository.putCatalogSnapshot(snapshot);
    await repository.putCatalogEntries([entry]);
    const request: PluginIdempotentInstall = {
      scope: digest("7"),
      fingerprint: digest("8"),
      installation: { ...installation, revision: 0 },
      admissions: entry.components.map(({ component, admission }) => ({
        installationId: installation.id,
        componentDigest: component.metadata.bindingDigest,
        componentKind: component.kind,
        componentName: component.name,
        status: admission.status,
        policyVersion: admission.policyVersion,
      })),
      credentialSlots: [],
      receipt: { type: "install", receiptId: "receipt-idempotent-1", projectId: installation.projectId, pluginName: installation.pluginName, status: "success", redactions: [] },
    };
    const first = await repository.installIdempotently(request);
    const second = await repository.installIdempotently(request);
    expect(second.receipt).toEqual(first.receipt);
    expect(second.replayed).toBe(true);
    expect(database.collection(PLUGIN_COLLECTIONS.installations).writes).toBe(1);
    expect(database.collection(PLUGIN_COLLECTIONS.receipts).writes).toBe(1);
    await expect(repository.installIdempotently({ ...request, fingerprint: digest("9") })).rejects.toThrow("idempotency_conflict");
    await expect(repository.installIdempotently({
      ...request, scope: digest("6"), receipt: { ...request.receipt, receiptId: "receipt-other-actor" },
    })).rejects.toThrow("installation_conflict");
    expect(database.collection(PLUGIN_COLLECTIONS.receipts).writes).toBe(1);
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
          componentDigest: MCP_BINDING_DIGEST,
        },
      }],
    };
    await expect(repository.putInstallation(malformed as unknown as PluginInstallation))
      .rejects.toThrow("installation_invalid");
  });

  it.each([
    ["unknown component", { componentId: "github:missing", binding: { id: "binding-1", providerKind: "rowboat-native", componentDigest: MCP_BINDING_DIGEST } }],
    ["wrong component digest", { componentId: "github:mcp", binding: { id: "binding-1", providerKind: "mcp-process", componentDigest: digest("0") } }],
    ["incompatible provider kind", { componentId: "github:mcp", binding: { id: "binding-1", providerKind: "mcp-http", componentDigest: MCP_BINDING_DIGEST } }],
    ["incompatible component kind", { componentId: "github:command", binding: { id: "binding-1", providerKind: "rowboat-native", componentDigest: COMMAND_BINDING_DIGEST } }],
    ["invalid paired component", { componentId: "github:app", binding: { id: "binding-1", providerKind: "mcp-process", componentDigest: APP_BINDING_DIGEST, pairedComponentDigests: [APP_BINDING_DIGEST, digest("0")] } }],
  ])("rejects a provider binding with %s before any installation write", async (_case, selected) => {
    const { database, repository } = repositoryFixture();
    await seedCatalog(repository);
    await expect(repository.putInstallation({ ...installation, providerBindings: [selected] } as PluginInstallation))
      .rejects.toThrow("installation_source_mismatch");
    expect(database.collection(PLUGIN_COLLECTIONS.installations).writes).toBe(0);
  });

  it("does not accept an idempotent re-put of a previously persisted invalid provider binding", async () => {
    const { database, repository } = repositoryFixture();
    await seedCatalog(repository);
    const invalidInstallation: PluginInstallation = {
      ...installation,
      providerBindings: [{
        componentId: "github:mcp",
        binding: { id: "binding-1", providerKind: "mcp-process", componentDigest: digest("0") },
      }],
    };
    database.collection(PLUGIN_COLLECTIONS.installations).documents.push({
      _id: "legacy-invalid",
      ...installation,
      providerBindingsJson: JSON.stringify(invalidInstallation.providerBindings),
    });
    await expect(repository.putInstallation(invalidInstallation)).rejects.toThrow("installation_source_mismatch");
    expect(database.collection(PLUGIN_COLLECTIONS.installations).writes).toBe(0);
  });

  it("stores and deep-freezes an exact app-to-MCP provider binding", async () => {
    const { repository } = repositoryFixture();
    await seedCatalog(repository);
    const bound: PluginInstallation = {
      ...installation,
      providerBindings: [{
        componentId: "github:app",
        binding: {
          id: "binding-1",
          providerKind: "mcp-process",
          componentDigest: APP_BINDING_DIGEST,
          pairedComponentDigests: [APP_BINDING_DIGEST, MCP_BINDING_DIGEST],
        },
      }],
    };
    await repository.putInstallation(bound);
    const stored = (await repository.listInstallations(installation.projectId))[0]!;
    expect(stored).toEqual(bound);
    expect(Object.isFrozen(stored.providerBindings)).toBe(true);
    expect(Object.isFrozen(stored.providerBindings?.[0])).toBe(true);
    expect(Object.isFrozen(stored.providerBindings?.[0]?.binding)).toBe(true);
    expect(Object.isFrozen(stored.providerBindings?.[0]?.binding.pairedComponentDigests)).toBe(true);
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

  it("persists enablement and its idempotency receipt as one replayable optimistic mutation", async () => {
    const { database, repository } = repositoryFixture();
    await seedInstallation(repository);
    const request: PluginIdempotentEnable = {
      scope: digest("5"), fingerprint: digest("4"), projectId: installation.projectId,
      pluginName: installation.pluginName, catalogDigest: snapshot.catalogDigest,
      installationId: installation.id, enabled: false, expectedRevision: installation.revision,
      receipt: { type: "install", receiptId: "receipt-enable-1", projectId: installation.projectId, pluginName: installation.pluginName, status: "success", redactions: [] },
    };
    const first = await repository.setInstallationEnabledIdempotently(request);
    const toggled = await repository.setInstallationEnabledIdempotently({
      ...request, scope: digest("2"), fingerprint: digest("1"), enabled: true,
      expectedRevision: installation.revision + 1,
      receipt: { ...request.receipt, receiptId: "receipt-enable-2" },
    });
    const second = await repository.setInstallationEnabledIdempotently(request);
    expect(first.installation).toMatchObject({ enabled: false, revision: installation.revision + 1 });
    expect(toggled.installation).toMatchObject({ enabled: true, revision: installation.revision + 2 });
    expect(second.installation).toEqual(first.installation);
    expect(second.replayed).toBe(true);
    expect(database.collection(PLUGIN_COLLECTIONS.receipts).writes).toBe(2);
    await expect(repository.setInstallationEnabledIdempotently({ ...request, fingerprint: digest("3") })).rejects.toThrow("idempotency_conflict");
  });

  it("returns a stable not-found error for missing installation enablement", async () => {
    const { repository } = repositoryFixture();
    await expect(repository.setInstallationEnabled("missing", false, 0))
      .rejects.toThrow("installation_not_found");
  });

  it("fails closed when an idempotency receipt has no immutable enable result snapshot", async () => {
    const { database, repository } = repositoryFixture();
    await seedInstallation(repository);
    const request: PluginIdempotentEnable = {
      scope: digest("0"), fingerprint: digest("1"), projectId: installation.projectId,
      pluginName: installation.pluginName, catalogDigest: snapshot.catalogDigest,
      installationId: installation.id, enabled: false, expectedRevision: installation.revision,
      receipt: { type: "install", receiptId: "receipt-enable-malformed", projectId: installation.projectId, pluginName: installation.pluginName, status: "success", redactions: [] },
    };
    await repository.setInstallationEnabledIdempotently(request);
    delete database.collection(PLUGIN_COLLECTIONS.receipts).documents[0]!.resultPayload;
    await expect(repository.setInstallationEnabledIdempotently(request)).rejects.toThrow("idempotency_result_invalid");
  });

  it("stores deterministic admissions bound to installation and component digest", async () => {
    const { repository } = repositoryFixture();
    await seedInstallation(repository);
    const admissions: readonly PluginComponentAdmission[] = [
      { installationId: installation.id, componentDigest: COMMAND_BINDING_DIGEST, componentKind: "command", componentName: "GitHub command", status: "admitted", policyVersion: snapshot.policyVersion },
      { installationId: installation.id, componentDigest: MCP_BINDING_DIGEST, componentKind: "mcp", componentName: "GitHub MCP", status: "admitted", policyVersion: snapshot.policyVersion },
    ];
    await repository.putAdmissions(admissions);
    expect((await repository.listAdmissions(installation.id)).map((item) => item.componentDigest))
      .toEqual([MCP_BINDING_DIGEST, COMMAND_BINDING_DIGEST].sort());
  });

  it("rejects a duplicate admission batch before writing any member", async () => {
    const { database, repository } = repositoryFixture();
    const selected: PluginComponentAdmission = {
      installationId: installation.id,
      componentDigest: MCP_BINDING_DIGEST,
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
      componentDigest: MCP_BINDING_DIGEST,
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
      installationId: installation.id, componentDigest: MCP_BINDING_DIGEST, componentKind: "mcp", componentName: "GitHub MCP",
      status: "admitted", policyVersion: snapshot.policyVersion,
    };
    await repository.putAdmissions([existing]);
    const collection = database.collection(PLUGIN_COLLECTIONS.componentAdmissions);
    const corrupted = collection.documents.find((document) => document.componentDigest === existing.componentDigest)!;
    corrupted.status = "rejected";
    corrupted.reason = "provider_unavailable";
    const baselineWrites = collection.writes;
    await expect(repository.putAdmissions([{
      installationId: installation.id, componentDigest: COMMAND_BINDING_DIGEST, componentKind: "command", componentName: "GitHub command",
      status: "admitted", policyVersion: snapshot.policyVersion,
    }, existing]))
      .rejects.toThrow("admission_conflict");
    expect(collection.writes).toBe(baselineWrites);
    expect(await repository.listAdmissions(installation.id)).toEqual([{ ...existing, status: "rejected", reason: "provider_unavailable" }]);
  });

  it("rolls back only its admission writes and preserves an external race winner", async () => {
    const { database, repository } = repositoryFixture();
    await seedInstallation(repository);
    const collection = database.collection(PLUGIN_COLLECTIONS.componentAdmissions);
    const orderedDigests = [MCP_BINDING_DIGEST, COMMAND_BINDING_DIGEST].sort();
    collection.beforeInsert = (document) => {
      if (document.componentDigest === orderedDigests[0]) {
        collection.beforeInsert = (racingDocument) => collection.insertExternal(racingDocument);
      }
    };
    await expect(repository.putAdmissions([
      { installationId: installation.id, componentDigest: MCP_BINDING_DIGEST, componentKind: "mcp", componentName: "GitHub MCP", status: "admitted", policyVersion: snapshot.policyVersion },
      { installationId: installation.id, componentDigest: COMMAND_BINDING_DIGEST, componentKind: "command", componentName: "GitHub command", status: "admitted", policyVersion: snapshot.policyVersion },
    ])).rejects.toThrow("admission_conflict");
    expect(collection.documents).toHaveLength(1);
    expect(collection.documents[0]).toMatchObject({ installationId: installation.id, componentDigest: orderedDigests[1] });
    expect(collection.writes).toBe(1);
  });

  it("propagates one exact session to every transactional admission read and write", async () => {
    const { database, repository } = repositoryFixture();
    await seedInstallation(repository);
    await repository.putAdmissions([{
      installationId: installation.id,
      componentDigest: MCP_BINDING_DIGEST,
      componentKind: "mcp",
      componentName: "GitHub MCP",
      status: "admitted",
      policyVersion: snapshot.policyVersion,
    }]);
    const sessions = [...database.collections.values()]
      .flatMap((collection) => collection.operationSessions)
      .filter((session): session is ClientSession => session !== undefined);
    const admissionSession = sessions.at(-1);
    expect(admissionSession).toBeDefined();
    const currentTransactionSessions = sessions.filter((session) => session === admissionSession);
    expect(currentTransactionSessions.length).toBeGreaterThanOrEqual(4);
    expect(currentTransactionSessions.every((session) => session === admissionSession)).toBe(true);
    expect(database.transactionSessionMismatches).toBe(0);
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

  it.each([
    "https://user:token-secret@example.invalid/path",
    "https%3A%2F%2Fuser%3Atoken-secret%40example.invalid",
    "Bearer abcdefghijklmnopqrstuvwxyz",
    "key=token-secret",
    "-----BEGIN PRIVATE KEY-----",
    "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1c2VyIn0.c2lnbmF0dXJl",
    "AbCdEfGhIjKlMnOpQrStUvWxYz0123456789AbCdEfGhIjKl",
    "display\u0001control",
  ])("rejects credential-like metadata string without persisting it", async (label) => {
    const { database, repository } = repositoryFixture();
    const error = await repository.putCredentialSlot({
      id: "slot-unsafe",
      projectId: installation.projectId,
      installationId: installation.id,
      name: "GITHUB_PAT_TOKEN",
      reference: { kind: "environment", reference: "github/pat" },
      metadata: { label },
    }).catch((caught: unknown) => caught);
    expect(error).toEqual(new Error("secret_value_rejected"));
    expect(String(error)).not.toContain(label);
    expect(database.collection(PLUGIN_COLLECTIONS.credentialSlots).writes).toBe(0);
  });

  it.each([
    "Basic dXNlcjpwYXNz",
    "  bAsIc\t dXNlcjpwYXNz",
    "Digest username=alice",
    "Bearer abc123",
    "Negotiate abc123",
    "NTLM abc123",
    "ApiKey abc123",
    "API-Key: abc123",
    "Token abc123",
  ])("rejects explicit authentication scheme in metadata and reference", async (credentialText) => {
    for (const location of ["metadata", "reference"] as const) {
      const { database, repository } = repositoryFixture();
      const slot: PluginCredentialSlot = {
        id: `slot-${location}`,
        projectId: installation.projectId,
        installationId: installation.id,
        name: "GITHUB_PAT_TOKEN",
        reference: { kind: "environment", reference: location === "reference" ? credentialText : "github/pat" },
        metadata: { label: location === "metadata" ? credentialText : "GitHub access" },
      };
      const error = await repository.putCredentialSlot(slot).catch((caught: unknown) => caught);
      expect(error).toEqual(new Error("secret_value_rejected"));
      expect(String(error)).not.toContain(credentialText);
      expect(database.collection(PLUGIN_COLLECTIONS.credentialSlots).writes).toBe(0);
    }
  });

  it.each([
    "X-API-Key abc123",
    "X-API-Key:abc123",
    "X_API_KEY=abc123",
    "X-ApiKey abc123",
    "   x-api-key abc123",
    "\tX_ApI_KeY=abc123",
  ])("rejects API key header metadata and references", async (credentialText) => {
    for (const location of ["metadata", "reference"] as const) {
      const { database, repository } = repositoryFixture();
      const error = await repository.putCredentialSlot({
        id: `slot-api-key-${location}`,
        projectId: installation.projectId,
        installationId: installation.id,
        name: "GITHUB_PAT_TOKEN",
        reference: { kind: "environment", reference: location === "reference" ? credentialText : "github/pat" },
        metadata: { label: location === "metadata" ? credentialText : "GitHub access" },
      }).catch((caught: unknown) => caught);
      expect(error).toEqual(new Error("secret_value_rejected"));
      expect(String(error)).not.toContain(credentialText);
      expect(database.collection(PLUGIN_COLLECTIONS.credentialSlots).writes).toBe(0);
    }
  });

  it.each(["X API compatibility", "keynote"])("preserves benign display label %s", async (label) => {
    const { database, repository } = repositoryFixture();
    await seedInstallation(repository);
    await repository.putCredentialSlot({
      id: `slot-${label.replaceAll(" ", "-").toLowerCase()}`,
      projectId: installation.projectId,
      installationId: installation.id,
      name: "GITHUB_PAT_TOKEN",
      reference: { kind: "environment", reference: "github/pat" },
      metadata: { label },
    });
    expect(JSON.stringify(database.collection(PLUGIN_COLLECTIONS.credentialSlots).documents)).toContain(label);
  });

  it("rejects unknown credential metadata keys, accessors, and proxies without executing them", async () => {
    const { database, repository } = repositoryFixture();
    let getterCalls = 0;
    const metadata = { label: "GitHub access" };
    Object.defineProperty(metadata, "description", {
      enumerable: true,
      get: () => { getterCalls += 1; return "token-secret"; },
    });
    await expect(repository.putCredentialSlot({
      id: "slot-accessor", projectId: installation.projectId, installationId: installation.id,
      name: "GITHUB_PAT_TOKEN", reference: { kind: "environment", reference: "github/pat" }, metadata,
    })).rejects.toThrow("secret_value_rejected");
    let trapCalls = 0;
    const proxiedMetadata = new Proxy({ label: "GitHub access" }, {
      getPrototypeOf: (target) => { trapCalls += 1; return Reflect.getPrototypeOf(target); },
      ownKeys: (target) => { trapCalls += 1; return Reflect.ownKeys(target); },
    });
    await expect(repository.putCredentialSlot({
      id: "slot-proxy", projectId: installation.projectId, installationId: installation.id,
      name: "GITHUB_PAT_TOKEN", reference: { kind: "environment", reference: "github/pat" }, metadata: proxiedMetadata,
    })).rejects.toThrow("secret_value_rejected");
    expect(getterCalls).toBe(0);
    expect(trapCalls).toBe(0);
    expect(database.collection(PLUGIN_COLLECTIONS.credentialSlots).writes).toBe(0);
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
      metadata: { label: "GitHub access", required: true, order: 1 },
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
      { collection: "plugin_receipts", keys: [{ receiptId: 1 }, { idempotencyScope: 1 }], unique: [true, true] },
    ]);

    const database = new MemoryDatabase();
    await ensurePluginIndexes(database as unknown as Db);
    await ensurePluginIndexes(database as unknown as Db);
    expect([...database.collections.values()].every((collection) => collection.indexes.length > 0)).toBe(true);
  });

  it("provisions all plugin indexes exactly once through the application bootstrap", async () => {
    const database = new MemoryDatabase();
    await ensureAllIndexes(database as unknown as Db);
    const pluginCollections = new Set<string>(Object.values(PLUGIN_COLLECTIONS));
    const uniquePluginIndexes = PLUGIN_COLLECTION_INDEXES.reduce(
      (count, { indexes }) => count + indexes.filter((index) => index.unique).length,
      0,
    );
    expect(uniquePluginIndexes).toBe(9);
    for (const { collection, indexes } of PLUGIN_COLLECTION_INDEXES) {
      const invoked = database.collection(collection);
      expect(invoked.createIndexCalls).toBe(1);
      expect(invoked.indexes).toEqual(indexes);
    }
    const existingCollections = [...database.collections.entries()]
      .filter(([name]) => !pluginCollections.has(name))
      .map(([, collection]) => collection);
    expect(existingCollections.length).toBeGreaterThan(0);
    expect(existingCollections.every((collection) => collection.createIndexCalls === 1)).toBe(true);
  });

  it("propagates a plugin-index bootstrap failure", async () => {
    const database = new MemoryDatabase();
    const failure = new Error("plugin_index_failure");
    database.collection(PLUGIN_COLLECTIONS.catalogSnapshots).createIndexesError = failure;
    await expect(ensureAllIndexes(database as unknown as Db)).rejects.toBe(failure);
  });
});

describe("Mongo plugin transaction runner", () => {
  it("models duplicate-key aborts without rolling back an external winner", async () => {
    const database = new MemoryDatabase();
    const collection = database.collection(PLUGIN_COLLECTIONS.catalogEntries);
    const runner = new MemoryTransactionRunner(database);
    let followupError: unknown;
    await expect(runner.run(async (session) => {
      collection.insertExternal({ catalogDigest: digest("a"), name: "github", payload: "external" });
      await collection.insertOne({ catalogDigest: digest("a"), name: "github", payload: "own" }, { session })
        .catch((error: unknown) => { followupError = error; });
      await collection.findOne({ catalogDigest: digest("a"), name: "github" }, { session });
    })).rejects.toMatchObject({ code: 251 });
    expect(followupError).toMatchObject({ code: 11000 });
    expect(collection.documents).toEqual([{ _id: "external-id-1", catalogDigest: digest("a"), name: "github", payload: "external" }]);
  });

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

  it("sanitizes a raw Mongo duplicate for outside-transaction classification and still ends its session", async () => {
    let ended = 0;
    const session = {
      withTransaction: async () => { throw new MongoServerError({ ok: 0, code: 11000, errmsg: "E11000 token-secret" }); },
      endSession: async () => { ended += 1; },
    };
    const client = { startSession: () => session } as unknown as MongoClient;
    const runner = new MongoPluginTransactionRunner({ pluginsMongoClient: client });
    const error = await runner.run(async () => undefined).catch((caught: unknown) => caught);
    expect(error).toEqual(new Error("repository_duplicate_key"));
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
