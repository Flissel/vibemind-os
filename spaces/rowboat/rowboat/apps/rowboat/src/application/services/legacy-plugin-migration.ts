import { createHash } from "node:crypto";
import { isProxy } from "node:util/types";
import {
  PINNED_OPENAI_PLUGINS_COMMIT,
  PINNED_PLUGIN_CATALOG_DIGEST,
  ZPluginMigrationRecord,
  validatePluginCatalogLock,
  type PluginCatalogEntry,
  type PluginCatalogLock,
  type PluginMigrationBlocker,
  type PluginMigrationRecord,
  type PluginInstallation,
} from "@rowboat/openai-plugin-runtime";
import {
  LEGACY_PLUGIN_RECIPES,
  buildLegacyExecutableInventory,
  resolveLegacyRecipe,
  sourceDriftBlocker,
  unmappedActionBlocker,
  type LegacyExecutableInventory,
  type ResolvedLegacyCapability,
} from "./legacy-plugin-recipes";

const SOURCE_KEYS = Object.freeze(["legacyCardId", "projectId", "sourceConfiguration", "sourceProjectRevision", "sourceUpdatedAt"]);
const DANGEROUS_KEYS = new Set(["__proto__", "constructor", "prototype"]);
const MAX_SOURCE_NODES = 20_000;
const MAX_SOURCE_DEPTH = 32;
const MAX_SOURCE_BYTES = 1024 * 1024;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

export class LegacyPluginMigrationError extends Error {
  readonly code: "source_invalid" | "catalog_drift" | "migration_record_invalid";

  constructor(code: LegacyPluginMigrationError["code"]) {
    super(code);
    this.name = "LegacyPluginMigrationError";
    this.code = code;
  }
}

export interface LegacyPluginMigrationSource {
  readonly projectId: string;
  readonly sourceProjectRevision: number;
  readonly sourceUpdatedAt: string | null;
  readonly legacyCardId: string;
  readonly sourceConfiguration: unknown;
}

export interface PluginMigrationPreview extends PluginMigrationRecord {
  readonly mutationsApplied: false;
  readonly installations: readonly PluginInstallation[];
}

export interface LegacyPluginMigrationOptions {
  readonly sideEffectGuards?: Readonly<{
    readonly repository: () => void;
    readonly provider: () => void;
    readonly credential: () => void;
    readonly network: () => void;
  }>;
}

interface CaptureBudget {
  nodes: number;
  bytes: number;
  readonly seen: Set<object>;
}

function fail(code: LegacyPluginMigrationError["code"]): never {
  throw new LegacyPluginMigrationError(code);
}

function captureJson(input: unknown, depth: number, budget: CaptureBudget): unknown {
  budget.nodes += 1;
  if (budget.nodes > MAX_SOURCE_NODES || depth > MAX_SOURCE_DEPTH) fail("source_invalid");
  if (input === null || typeof input === "boolean") return input;
  if (typeof input === "string") {
    budget.bytes += Buffer.byteLength(input, "utf8");
    if (budget.bytes > MAX_SOURCE_BYTES) fail("source_invalid");
    return input;
  }
  if (typeof input === "number") {
    if (!Number.isFinite(input)) fail("source_invalid");
    return input;
  }
  if (typeof input !== "object" || isProxy(input)) fail("source_invalid");
  if (budget.seen.has(input)) fail("source_invalid");
  budget.seen.add(input);
  // Migration input follows the JSON/Mongo visibility boundary: only own,
  // enumerable string data properties participate. Hidden and symbol metadata
  // is neither enumerated, copied, hashed, nor invoked.
  if (Array.isArray(input)) {
    if (Object.getPrototypeOf(input) !== Array.prototype) fail("source_invalid");
    const lengthDescriptor = Object.getOwnPropertyDescriptor(input, "length");
    if (lengthDescriptor === undefined || !("value" in lengthDescriptor) || !Number.isSafeInteger(lengthDescriptor.value) || lengthDescriptor.value < 0) fail("source_invalid");
    const length = lengthDescriptor.value as number;
    if (length > MAX_SOURCE_NODES - budget.nodes) fail("source_invalid");
    let enumerableKeys = 0;
    for (const key in input) {
      if (!Object.prototype.hasOwnProperty.call(input, key)) continue;
      enumerableKeys += 1;
      if (enumerableKeys > length || !/^(?:0|[1-9]\d*)$/.test(key) || Number(key) >= length) fail("source_invalid");
    }
    if (enumerableKeys !== length) fail("source_invalid");
    const output: unknown[] = [];
    for (let index = 0; index < length; index += 1) {
      const descriptor = Object.getOwnPropertyDescriptor(input, String(index));
      if (descriptor === undefined || !("value" in descriptor) || !descriptor.enumerable) fail("source_invalid");
      output.push(captureJson(descriptor.value, depth + 1, budget));
    }
    budget.seen.delete(input);
    return output;
  }
  const prototype = Object.getPrototypeOf(input);
  if (prototype !== Object.prototype && prototype !== null) fail("source_invalid");
  let keyCount = 0;
  for (const key in input) {
    if (!Object.prototype.hasOwnProperty.call(input, key)) continue;
    keyCount += 1;
    if (keyCount > MAX_SOURCE_NODES - budget.nodes) fail("source_invalid");
  }
  const keys = new Array<string>(keyCount);
  let keyIndex = 0;
  for (const key in input) {
    if (!Object.prototype.hasOwnProperty.call(input, key)) continue;
    keys[keyIndex] = key;
    keyIndex += 1;
  }
  keys.sort();
  const output: Record<string, unknown> = Object.create(null) as Record<string, unknown>;
  for (const key of keys) {
    if (DANGEROUS_KEYS.has(key)) fail("source_invalid");
    budget.bytes += Buffer.byteLength(key, "utf8");
    if (budget.bytes > MAX_SOURCE_BYTES) fail("source_invalid");
    const descriptor = Object.getOwnPropertyDescriptor(input, key);
    if (descriptor === undefined || !("value" in descriptor) || !descriptor.enumerable) fail("source_invalid");
    Object.defineProperty(output, key, {
      configurable: descriptor.configurable,
      enumerable: true,
      value: captureJson(descriptor.value, depth + 1, budget),
      writable: descriptor.writable,
    });
  }
  budget.seen.delete(input);
  return output;
}

function capturedJson(input: unknown): unknown {
  return captureJson(input, 0, { nodes: 0, bytes: 0, seen: new Set<object>() });
}

function captureSource(input: unknown): LegacyPluginMigrationSource {
  const captured = capturedJson(input);
  if (captured === null || typeof captured !== "object" || Array.isArray(captured)) fail("source_invalid");
  const record = captured as Readonly<Record<string, unknown>>;
  if (Object.keys(record).sort().join("\0") !== SOURCE_KEYS.join("\0")) fail("source_invalid");
  if (typeof record.projectId !== "string" || !UUID.test(record.projectId)) fail("source_invalid");
  if (!Number.isSafeInteger(record.sourceProjectRevision) || (record.sourceProjectRevision as number) < 0) fail("source_invalid");
  if (typeof record.legacyCardId !== "string" || record.legacyCardId.length === 0 || record.legacyCardId.length > 128) fail("source_invalid");
  if (record.sourceUpdatedAt !== null && (typeof record.sourceUpdatedAt !== "string" || Number.isNaN(Date.parse(record.sourceUpdatedAt)) || new Date(record.sourceUpdatedAt).toISOString() !== record.sourceUpdatedAt)) fail("source_invalid");
  return Object.freeze({
    projectId: record.projectId,
    sourceProjectRevision: record.sourceProjectRevision as number,
    sourceUpdatedAt: record.sourceUpdatedAt as string | null,
    legacyCardId: record.legacyCardId,
    sourceConfiguration: record.sourceConfiguration,
  });
}

function canonical(value: unknown): string {
  if (value === null || typeof value === "boolean" || typeof value === "string") return JSON.stringify(value);
  if (typeof value === "number") return Object.is(value, -0) ? "0" : JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  const record = value as Readonly<Record<string, unknown>>;
  return `{${Object.keys(record).sort().map(key => `${JSON.stringify(key)}:${canonical(record[key])}`).join(",")}}`;
}

function digest(domain: string, value: unknown): string {
  return createHash("sha256").update(domain).update("\0").update(canonical(value)).digest("hex");
}

function deterministicUuid(domain: string, value: unknown): string {
  const bytes = Buffer.from(digest(domain, value).slice(0, 32), "hex");
  bytes[6] = (bytes[6]! & 0x0f) | 0x50;
  bytes[8] = (bytes[8]! & 0x3f) | 0x80;
  const hex = bytes.toString("hex");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

function deepFreeze<T>(value: T): T {
  if (Array.isArray(value)) {
    for (const item of value) deepFreeze(item);
    return Object.freeze(value) as T;
  }
  if (value !== null && typeof value === "object") {
    for (const item of Object.values(value as object)) deepFreeze(item);
    return Object.freeze(value);
  }
  return value;
}

function compareBlockers(left: PluginMigrationBlocker, right: PluginMigrationBlocker): number {
  return `${left.capabilityId}\0${left.legacyActionId}\0${left.code}\0${left.pluginName}\0${left.componentId}`
    .localeCompare(`${right.capabilityId}\0${right.legacyActionId}\0${right.code}\0${right.pluginName}\0${right.componentId}`);
}

function installationsFrom(
  source: LegacyPluginMigrationSource,
  catalog: PluginCatalogLock,
  recipeId: string,
  recipeDigest: string,
  resolved: readonly ResolvedLegacyCapability[],
): readonly PluginInstallation[] {
  const byPlugin = new Map<string, { entry: PluginCatalogEntry; capabilities: ResolvedLegacyCapability[] }>();
  for (const item of resolved) {
    const group = byPlugin.get(item.entry.pluginName) ?? { entry: item.entry, capabilities: [] };
    group.capabilities.push(item);
    byPlugin.set(item.entry.pluginName, group);
  }
  return [...byPlugin.values()].sort((left, right) => left.entry.pluginName.localeCompare(right.entry.pluginName)).map(({ entry, capabilities }) => {
    const byComponent = new Map<string, ResolvedLegacyCapability[]>();
    for (const item of capabilities) {
      const digestValue = item.component.metadata.bindingDigest;
      const group = byComponent.get(digestValue) ?? [];
      group.push(item);
      byComponent.set(digestValue, group);
    }
    const providerBindings = [...byComponent.entries()].sort(([left], [right]) => left.localeCompare(right)).map(([, items]) => {
      const first = items[0]!;
      return Object.freeze({
        componentId: first.component.id,
        binding: first.providerBinding,
      });
    });
    return Object.freeze({
      id: deterministicUuid("rowboat:legacy-migration:installation-id:v1", {
        projectId: source.projectId,
        recipeId,
        recipeDigest,
        pluginName: entry.pluginName,
        catalogDigest: catalog.catalogDigest,
      }),
      projectId: source.projectId,
      pluginName: entry.pluginName,
      pluginVersion: entry.pluginVersion,
      sourceCommit: entry.sourceCommit,
      manifestDigest: entry.manifestDigest,
      treeDigest: entry.treeDigest,
      policyVersion: entry.policyVersion,
      enabled: true,
      revision: 0,
      providerBindings: Object.freeze(providerBindings),
    });
  });
}

function captureExisting(input: unknown): PluginMigrationRecord {
  try {
    return ZPluginMigrationRecord.parse(capturedJson(input));
  } catch {
    fail("migration_record_invalid");
  }
}

export class LegacyPluginMigration {
  readonly #options: LegacyPluginMigrationOptions;

  constructor(options: LegacyPluginMigrationOptions = {}) {
    this.#options = options;
  }

  preview(input: unknown, catalogInput: unknown, existingRecord?: unknown): PluginMigrationPreview {
    const source = captureSource(input);
    let sourceInventory: LegacyExecutableInventory;
    try {
      sourceInventory = buildLegacyExecutableInventory(source.sourceConfiguration);
    } catch {
      fail("source_invalid");
    }
    let catalog: PluginCatalogLock;
    try {
      catalog = validatePluginCatalogLock(catalogInput);
    } catch {
      fail("catalog_drift");
    }
    if (catalog.catalogDigest !== PINNED_PLUGIN_CATALOG_DIGEST || catalog.sourceCommit !== PINNED_OPENAI_PLUGINS_COMMIT) fail("catalog_drift");
    const createdAt = source.sourceUpdatedAt ?? catalog.importedAt;
    const recipe = Object.prototype.hasOwnProperty.call(LEGACY_PLUGIN_RECIPES, source.legacyCardId)
      ? LEGACY_PLUGIN_RECIPES[source.legacyCardId as keyof typeof LEGACY_PLUGIN_RECIPES]
      : undefined;
    const recipeId = recipe?.recipeId ?? `legacy-custom:${digest("rowboat:legacy-migration:custom-card-id:v1", source.legacyCardId).slice(0, 24)}:v1`;
    const recipeDigest = recipe?.recipeDigest ?? digest("rowboat:legacy-migration:custom-recipe:v1", {
      legacyCardId: source.legacyCardId,
      inventory: sourceInventory,
      mappings: sourceInventory.actions.map(action => ({ action, target: null })),
    });
    const resolved: ResolvedLegacyCapability[] = [];
    const blockers: PluginMigrationBlocker[] = [];
    if (recipe === undefined) {
      if (sourceInventory.actions.length === 0) {
        blockers.push(Object.freeze({ capabilityId: "legacy-card", legacyActionId: `card:${recipeDigest.slice(0, 24)}`, code: "legacy_card_unmapped", pluginName: null, componentId: null }));
      } else {
        blockers.push(...sourceInventory.actions.map(action => unmappedActionBlocker(action)));
      }
    } else if (sourceInventory.digest !== recipe.inventory.digest) {
      blockers.push(sourceDriftBlocker());
      const expectedIdentities = new Set(recipe.inventory.actions.map(action => action.identity));
      for (const action of sourceInventory.actions) if (!expectedIdentities.has(action.identity)) blockers.push(unmappedActionBlocker(action));
    } else {
      for (const capability of recipe.capabilities) {
        const resolution = resolveLegacyRecipe(capability, catalog.entries);
        if ("code" in resolution) blockers.push(resolution);
        else resolved.push(resolution);
      }
    }
    blockers.sort(compareBlockers);
    const installations = blockers.length === 0 ? installationsFrom(source, catalog, recipeId, recipeDigest, resolved) : Object.freeze([]);
    const sourceDigest = digest("rowboat:legacy-migration:source:v1", {
      projectId: source.projectId,
      sourceProjectRevision: source.sourceProjectRevision,
      sourceUpdatedAt: source.sourceUpdatedAt,
      legacyCardId: source.legacyCardId,
      recipeDigest,
      sourceInventoryDigest: sourceInventory.digest,
      sourceConfiguration: source.sourceConfiguration,
    });
    const targetInstallationIds = Object.freeze(installations.map(installation => installation.id));
    const base = {
      id: deterministicUuid("rowboat:legacy-migration:record-id:v1", {
        projectId: source.projectId,
        recipeId,
        recipeDigest,
        sourceProjectRevision: source.sourceProjectRevision,
        sourceDigest,
        targetCatalogDigest: catalog.catalogDigest,
      }),
      projectId: source.projectId,
      recipeId,
      recipeDigest,
      sourceProjectRevision: source.sourceProjectRevision,
      sourceDigest,
      sourceInventoryDigest: sourceInventory.digest,
      targetCatalogDigest: catalog.catalogDigest,
      targetSourceCommit: catalog.sourceCommit,
      targetPolicyVersion: catalog.policyVersion,
      targetInstallationIds,
      rollbackSnapshotDigest: digest("rowboat:legacy-migration:rollback-snapshot:v1", {
        projectId: source.projectId,
        sourceProjectRevision: source.sourceProjectRevision,
        recipeDigest,
        sourceInventoryDigest: sourceInventory.digest,
        sourceDigest,
      }),
      status: blockers.length === 0 ? "previewed" as const : "blocked" as const,
      blockers: Object.freeze(blockers),
      createdAt,
    };
    let record = ZPluginMigrationRecord.parse(base);
    if (existingRecord !== undefined) {
      const existing = captureExisting(existingRecord);
      const expectedProvenance = { ...base, status: existing.status };
      if (blockers.length !== 0 || existing.status !== "applied" || canonical(existing) !== canonical(expectedProvenance)) fail("migration_record_invalid");
      record = existing;
    }
    return deepFreeze({ ...record, mutationsApplied: false as const, installations: [...installations] });
  }
}
