import { types as utilTypes } from "node:util";
import type { PluginCatalogLock, CatalogInventory, CatalogComponentAdmission } from "../domain/catalog.js";
import {
  PINNED_OPENAI_PLUGIN_COUNT,
  PINNED_OPENAI_PLUGINS_COMMIT,
  PINNED_PLUGIN_CATALOG_DIGEST,
  PLUGIN_SCHEMA_VERSION,
} from "../domain/catalog.js";
import { DEFAULT_POLICY } from "../policy/default-policy.js";
import type { AdmissionDecision } from "../policy/license-policy.js";
import type { NormalizedPluginComponent, PluginReasonCode, SourceProvenance } from "../domain/plugin.js";
import { OPENAI_PLUGINS_SOURCE_URL } from "./source-reader.js";
import { componentBindingDigest, pluginCatalogDigest } from "./catalog-importer.js";

const DIGEST = /^[a-f0-9]{64}$/;
const COMMIT = /^[a-f0-9]{40}$/;
const MAX_DEPTH = 32;
const MAX_ITEMS = 250_000;
const MAX_STRING = 1_048_576;
const REASONS = [
  "source_mismatch", "manifest_invalid", "path_escape", "digest_mismatch",
  "license_review_required", "license_rejected", "provider_unavailable", "credential_missing",
  "http_mcp_not_admitted", "process_not_admitted", "hook_not_admitted", "write_review_required",
  "component_unsupported", "migration_conflict", "parity_failed", "rollback_unavailable",
] as const;
const arrayIsArray = Array.isArray;
const arrayPrototype = Array.prototype;
const objectPrototype = Object.prototype;
const getPrototypeOf = Object.getPrototypeOf;
const getOwnPropertyDescriptor = Object.getOwnPropertyDescriptor;
const ownKeysOf = Reflect.ownKeys;
const stringify = JSON.stringify;
const isProxy = utilTypes.isProxy;
const charCodeAt = Function.call.bind(String.prototype.charCodeAt) as (value: string, index: number) => number;

function hasText(values: readonly string[], candidate: string): boolean {
  for (let index = 0; index < values.length; index += 1) if (values[index] === candidate) return true;
  return false;
}

function sortedStrings(values: readonly string[]): string[] {
  const output: string[] = [];
  for (let index = 0; index < values.length; index += 1) {
    const value = values[index] as string;
    let position = output.length;
    while (position > 0 && (output[position - 1] as string) > value) position -= 1;
    for (let move = output.length; move > position; move -= 1) output[move] = output[move - 1] as string;
    output[position] = value;
  }
  return output;
}

type JsonPrimitive = string | number | boolean | null;
interface JsonObject { readonly [key: string]: Json; }
interface JsonArray extends ReadonlyArray<Json> {}
type Json = JsonPrimitive | JsonArray | JsonObject;

function fail(): never { throw new Error("catalog_lock_invalid"); }

function capture(input: unknown, depth = 0, budget = { items: 0 }): Json {
  if (depth > MAX_DEPTH || ++budget.items > MAX_ITEMS) fail();
  if (input === null || typeof input === "boolean") return input;
  if (typeof input === "number") {
    if (!Number.isFinite(input)) fail();
    return input;
  }
  if (typeof input === "string") {
    if (input.length > MAX_STRING) fail();
    for (let index = 0; index < input.length; index += 1) if (charCodeAt(input, index) === 0) fail();
    return input;
  }
  if (typeof input !== "object" || isProxy(input)) fail();
  const prototype = getPrototypeOf(input);
  const ownKeys = ownKeysOf(input);
  for (let index = 0; index < ownKeys.length; index += 1) if (typeof ownKeys[index] !== "string") fail();
  if (arrayIsArray(input)) {
    if (prototype !== arrayPrototype) fail();
    const length = getOwnPropertyDescriptor(input, "length");
    if (length === undefined || !("value" in length) || ownKeys.length !== length.value + 1) fail();
    const output: Json[] = [];
    for (let index = 0; index < length.value; index += 1) {
      const descriptor = getOwnPropertyDescriptor(input, String(index));
      if (descriptor === undefined || !("value" in descriptor) || !descriptor.enumerable) fail();
      output[index] = capture(descriptor.value, depth + 1, budget);
    }
    return Object.freeze(output);
  }
  if (prototype !== objectPrototype && prototype !== null) fail();
  const output = Object.create(null) as Record<string, Json>;
  const stringKeys: string[] = [];
  for (let index = 0; index < ownKeys.length; index += 1) stringKeys[index] = ownKeys[index] as string;
  const orderedKeys = sortedStrings(stringKeys);
  for (let index = 0; index < orderedKeys.length; index += 1) {
    const key = orderedKeys[index] as string;
    if (key === "__proto__" || key === "prototype" || key === "constructor" || charCodeAt(key, 0) === 36) fail();
    const descriptor = getOwnPropertyDescriptor(input, key);
    if (descriptor === undefined || !("value" in descriptor) || !descriptor.enumerable) fail();
    output[key] = capture(descriptor.value, depth + 1, budget);
  }
  return Object.freeze(output);
}

function record(input: Json, allowed: readonly string[]): Readonly<Record<string, Json>> {
  if (input === null || arrayIsArray(input) || typeof input !== "object") fail();
  const selected = input as Readonly<Record<string, Json>>;
  const keys = ownKeysOf(selected);
  for (let index = 0; index < keys.length; index += 1) {
    const key = keys[index];
    if (typeof key !== "string" || !hasText(allowed, key)) fail();
  }
  return selected;
}

function text(input: Json | undefined, pattern: RegExp): string {
  if (typeof input !== "string" || !pattern.test(input)) fail();
  return input;
}

function decision(input: Json | undefined, expectedPolicyVersion: string): AdmissionDecision {
  const selected = record(input as Json, ["status", "reason", "policyVersion"]);
  const status = text(selected.status, /^(admitted|review_required|rejected)$/) as AdmissionDecision["status"];
  const policyVersion = text(selected.policyVersion, /^.{1,128}$/);
  if (policyVersion !== expectedPolicyVersion) fail();
  if (status === "admitted") {
    if (selected.reason !== undefined) fail();
    return { status, policyVersion };
  }
  const reason = text(selected.reason, /^.{1,128}$/);
  if (!hasText(REASONS, reason)) fail();
  return { status, reason: reason as PluginReasonCode, policyVersion };
}

function inventoryFrom(entries: PluginCatalogLock["entries"]): CatalogInventory {
  const counts = { pluginsWithSkills: 0, pluginsWithApps: 0, pluginsWithAgents: 0, pluginsWithCommands: 0, pluginsWithMcp: 0, pluginsWithCommandHooks: 0 };
  for (let entryIndex = 0; entryIndex < entries.length; entryIndex += 1) {
    const components = (entries[entryIndex] as PluginCatalogLock["entries"][number]).components;
    let skill = false, app = false, agent = false, command = false, mcp = false, hook = false;
    for (let componentIndex = 0; componentIndex < components.length; componentIndex += 1) {
      const kind = (components[componentIndex] as CatalogComponentAdmission).component.kind;
      skill ||= kind === "skill"; app ||= kind === "app"; agent ||= kind === "agent";
      command ||= kind === "command"; mcp ||= kind === "mcp"; hook ||= kind === "hook";
    }
    counts.pluginsWithSkills += Number(skill); counts.pluginsWithApps += Number(app);
    counts.pluginsWithAgents += Number(agent); counts.pluginsWithCommands += Number(command);
    counts.pluginsWithMcp += Number(mcp); counts.pluginsWithCommandHooks += Number(hook);
  }
  return counts;
}

export function validatePluginCatalogLock(input: unknown): PluginCatalogLock {
  const captured = capture(input);
  const root = record(captured, ["sourceUrl", "sourceCommit", "importedAt", "schemaVersion", "policyVersion", "inventory", "licenseDeclarations", "entries", "catalogDigest"]);
  if (root.sourceUrl !== OPENAI_PLUGINS_SOURCE_URL || root.sourceCommit !== PINNED_OPENAI_PLUGINS_COMMIT || root.schemaVersion !== PLUGIN_SCHEMA_VERSION || root.policyVersion !== DEFAULT_POLICY.version || root.catalogDigest !== PINNED_PLUGIN_CATALOG_DIGEST) fail();
  text(root.sourceCommit, COMMIT);
  text(root.importedAt, /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}/);
  const policyVersion = text(root.policyVersion, /^.{1,128}$/);
  text(root.catalogDigest, DIGEST);
  if (!arrayIsArray(root.entries) || root.entries.length !== PINNED_OPENAI_PLUGIN_COUNT) fail();
  const lock = captured as unknown as PluginCatalogLock;
  const names: string[] = [];
  let previous = "";
  const licenseCounts = Object.create(null) as Record<string, number>;
  for (let entryIndex = 0; entryIndex < lock.entries.length; entryIndex += 1) {
    const entry = lock.entries[entryIndex] as PluginCatalogLock["entries"][number];
    const entryRecord = record(entry as unknown as Json, ["name", "licenseDeclaration", "sourceUrl", "sourceCommit", "pluginName", "pluginVersion", "manifestDigest", "treeDigest", "importedAt", "schemaVersion", "policyVersion", "admission", "components", "storedContentDigest"]);
    const name = text(entryRecord.name, /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/);
    if (name !== entryRecord.pluginName || name <= previous || hasText(names, name)) fail();
    previous = name;
    names[names.length] = name;
    if (entryRecord.sourceUrl !== root.sourceUrl || entryRecord.sourceCommit !== root.sourceCommit || entryRecord.importedAt !== root.importedAt || entryRecord.schemaVersion !== root.schemaVersion || entryRecord.policyVersion !== policyVersion) fail();
    text(entryRecord.pluginVersion, /^.{1,128}$/);
    text(entryRecord.manifestDigest, DIGEST);
    text(entryRecord.treeDigest, DIGEST);
    const licenseDeclaration = text(entryRecord.licenseDeclaration, /^.{1,256}$/);
    const licenseAdmission = decision(entryRecord.admission, policyVersion);
    if (!arrayIsArray(entryRecord.components) || entryRecord.components.length > 1024) fail();
    const componentIds: string[] = [];
    const bindings: string[] = [];
    const provenance = entry as unknown as SourceProvenance;
    for (let componentIndex = 0; componentIndex < entryRecord.components.length; componentIndex += 1) {
      const selectedValue = entryRecord.components[componentIndex] as Json;
      const selected = record(selectedValue, ["component", "admission"]);
      const componentRecord = record(selected.component as Json, ["id", "name", "kind", "status", "reason", "metadata"]);
      const id = text(componentRecord.id, /^.{1,512}$/);
      text(componentRecord.name, /^.{1,512}$/);
      text(componentRecord.kind, /^(skill|agent|command|mcp|app|hook|asset)$/);
      text(componentRecord.status, /^(available|review_required|installed|partially_available|unavailable|migration_required|error|invalid|unsupported)$/);
      if (componentRecord.reason !== undefined) text(componentRecord.reason, /^.{1,128}$/);
      const metadataObject = componentRecord.metadata as Readonly<Record<string, Json>>;
      const metadataOwnKeys = ownKeysOf(metadataObject);
      const metadataKeys: string[] = [];
      for (let keyIndex = 0; keyIndex < metadataOwnKeys.length; keyIndex += 1) metadataKeys[keyIndex] = metadataOwnKeys[keyIndex] as string;
      const metadata = record(componentRecord.metadata as Json, metadataKeys);
      text(metadata.digest, DIGEST);
      const bindingDigest = text(metadata.bindingDigest, DIGEST);
      const componentAdmission = decision(selected.admission, policyVersion);
      const component = selected.component as unknown as NormalizedPluginComponent;
      const expected = componentBindingDigest(provenance, component, { componentAdmission, licenseDeclaration, licenseAdmission });
      if (bindingDigest !== expected || hasText(componentIds, id) || hasText(bindings, bindingDigest)) fail();
      componentIds[componentIds.length] = id;
      bindings[bindings.length] = bindingDigest;
    }
    if (licenseAdmission.status === "admitted" && entryRecord.storedContentDigest !== entryRecord.treeDigest) fail();
    if (licenseAdmission.status !== "admitted" && entryRecord.storedContentDigest !== undefined) fail();
    licenseCounts[licenseDeclaration] = (licenseCounts[licenseDeclaration] ?? 0) + 1;
  }
  if (stringify(root.inventory) !== stringify(capture(inventoryFrom(lock.entries)))) fail();
  if (stringify(root.licenseDeclarations) !== stringify(capture(licenseCounts))) fail();
  const { catalogDigest, ...payload } = lock;
  if (catalogDigest !== pluginCatalogDigest(payload)) fail();
  return lock;
}
