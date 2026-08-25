import { types as utilTypes } from "node:util";
import type { PluginCatalogLock, CatalogInventory } from "../domain/catalog.js";
import {
  PINNED_OPENAI_PLUGIN_COUNT,
  PINNED_OPENAI_PLUGINS_COMMIT,
  PLUGIN_SCHEMA_VERSION,
} from "../domain/catalog.js";
import type { AdmissionDecision } from "../policy/license-policy.js";
import type { NormalizedPluginComponent, PluginReasonCode, SourceProvenance } from "../domain/plugin.js";
import { OPENAI_PLUGINS_SOURCE_URL } from "./source-reader.js";
import { componentBindingDigest, pluginCatalogDigest } from "./catalog-importer.js";

const DIGEST = /^[a-f0-9]{64}$/;
const COMMIT = /^[a-f0-9]{40}$/;
const MAX_DEPTH = 32;
const MAX_ITEMS = 250_000;
const MAX_STRING = 1_048_576;
const REASONS = new Set([
  "source_mismatch", "manifest_invalid", "path_escape", "digest_mismatch",
  "license_review_required", "license_rejected", "provider_unavailable", "credential_missing",
  "http_mcp_not_admitted", "process_not_admitted", "hook_not_admitted", "write_review_required",
  "component_unsupported", "migration_conflict", "parity_failed", "rollback_unavailable",
]);

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
    if (input.length > MAX_STRING || input.includes("\0")) fail();
    return input;
  }
  if (typeof input !== "object" || utilTypes.isProxy(input)) fail();
  const prototype = Object.getPrototypeOf(input);
  const ownKeys = Reflect.ownKeys(input);
  if (ownKeys.some((key) => typeof key !== "string")) fail();
  if (Array.isArray(input)) {
    if (prototype !== Array.prototype) fail();
    const length = Object.getOwnPropertyDescriptor(input, "length");
    if (length === undefined || !("value" in length) || ownKeys.length !== length.value + 1) fail();
    const output: Json[] = [];
    for (let index = 0; index < length.value; index += 1) {
      const descriptor = Object.getOwnPropertyDescriptor(input, String(index));
      if (descriptor === undefined || !("value" in descriptor) || !descriptor.enumerable) fail();
      output.push(capture(descriptor.value, depth + 1, budget));
    }
    return Object.freeze(output);
  }
  if (prototype !== Object.prototype && prototype !== null) fail();
  const output = Object.create(null) as Record<string, Json>;
  for (const key of (ownKeys as string[]).sort()) {
    if (key === "__proto__" || key === "prototype" || key === "constructor" || key.startsWith("$")) fail();
    const descriptor = Object.getOwnPropertyDescriptor(input, key);
    if (descriptor === undefined || !("value" in descriptor) || !descriptor.enumerable) fail();
    output[key] = capture(descriptor.value, depth + 1, budget);
  }
  return Object.freeze(output);
}

function record(input: Json, allowed: readonly string[]): Readonly<Record<string, Json>> {
  if (input === null || Array.isArray(input) || typeof input !== "object") fail();
  const selected = input as Readonly<Record<string, Json>>;
  const keys = Object.keys(selected);
  if (keys.some((key) => !allowed.includes(key))) fail();
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
  if (!REASONS.has(reason)) fail();
  return { status, reason: reason as PluginReasonCode, policyVersion };
}

function inventoryFrom(entries: PluginCatalogLock["entries"]): CatalogInventory {
  const counts = { pluginsWithSkills: 0, pluginsWithApps: 0, pluginsWithAgents: 0, pluginsWithCommands: 0, pluginsWithMcp: 0, pluginsWithCommandHooks: 0 };
  for (const entry of entries) {
    const kinds = new Set(entry.components.map(({ component }) => component.kind));
    counts.pluginsWithSkills += Number(kinds.has("skill"));
    counts.pluginsWithApps += Number(kinds.has("app"));
    counts.pluginsWithAgents += Number(kinds.has("agent"));
    counts.pluginsWithCommands += Number(kinds.has("command"));
    counts.pluginsWithMcp += Number(kinds.has("mcp"));
    counts.pluginsWithCommandHooks += Number(kinds.has("hook"));
  }
  return counts;
}

export function validatePluginCatalogLock(input: unknown): PluginCatalogLock {
  const captured = capture(input);
  const root = record(captured, ["sourceUrl", "sourceCommit", "importedAt", "schemaVersion", "policyVersion", "inventory", "licenseDeclarations", "entries", "catalogDigest"]);
  if (root.sourceUrl !== OPENAI_PLUGINS_SOURCE_URL || root.sourceCommit !== PINNED_OPENAI_PLUGINS_COMMIT || root.schemaVersion !== PLUGIN_SCHEMA_VERSION) fail();
  text(root.sourceCommit, COMMIT);
  text(root.importedAt, /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}/);
  const policyVersion = text(root.policyVersion, /^.{1,128}$/);
  text(root.catalogDigest, DIGEST);
  if (!Array.isArray(root.entries) || root.entries.length !== PINNED_OPENAI_PLUGIN_COUNT) fail();
  const lock = captured as unknown as PluginCatalogLock;
  const names = new Set<string>();
  let previous = "";
  const licenseCounts = new Map<string, number>();
  for (const entry of lock.entries) {
    const entryRecord = record(entry as unknown as Json, ["name", "licenseDeclaration", "sourceUrl", "sourceCommit", "pluginName", "pluginVersion", "manifestDigest", "treeDigest", "importedAt", "schemaVersion", "policyVersion", "admission", "components", "storedContentDigest"]);
    const name = text(entryRecord.name, /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/);
    if (name !== entryRecord.pluginName || name <= previous || names.has(name)) fail();
    previous = name;
    names.add(name);
    if (entryRecord.sourceUrl !== root.sourceUrl || entryRecord.sourceCommit !== root.sourceCommit || entryRecord.importedAt !== root.importedAt || entryRecord.schemaVersion !== root.schemaVersion || entryRecord.policyVersion !== policyVersion) fail();
    text(entryRecord.pluginVersion, /^.{1,128}$/);
    text(entryRecord.manifestDigest, DIGEST);
    text(entryRecord.treeDigest, DIGEST);
    const licenseDeclaration = text(entryRecord.licenseDeclaration, /^.{1,256}$/);
    const licenseAdmission = decision(entryRecord.admission, policyVersion);
    if (!Array.isArray(entryRecord.components) || entryRecord.components.length > 1024) fail();
    const componentIds = new Set<string>();
    const bindings = new Set<string>();
    const provenance = entry as unknown as SourceProvenance;
    for (const selectedValue of entryRecord.components) {
      const selected = record(selectedValue, ["component", "admission"]);
      const componentRecord = record(selected.component as Json, ["id", "name", "kind", "status", "reason", "metadata"]);
      const id = text(componentRecord.id, /^.{1,512}$/);
      text(componentRecord.name, /^.{1,512}$/);
      text(componentRecord.kind, /^(skill|agent|command|mcp|app|hook|asset)$/);
      text(componentRecord.status, /^(available|review_required|installed|partially_available|unavailable|migration_required|error|invalid|unsupported)$/);
      if (componentRecord.reason !== undefined) text(componentRecord.reason, /^.{1,128}$/);
      const metadata = record(componentRecord.metadata as Json, Object.keys(componentRecord.metadata as Readonly<Record<string, Json>>));
      text(metadata.digest, DIGEST);
      const bindingDigest = text(metadata.bindingDigest, DIGEST);
      const componentAdmission = decision(selected.admission, policyVersion);
      const component = selected.component as unknown as NormalizedPluginComponent;
      const expected = componentBindingDigest(provenance, component, { componentAdmission, licenseDeclaration, licenseAdmission });
      if (bindingDigest !== expected || componentIds.has(id) || bindings.has(bindingDigest)) fail();
      componentIds.add(id);
      bindings.add(bindingDigest);
    }
    if (licenseAdmission.status === "admitted" && entryRecord.storedContentDigest !== entryRecord.treeDigest) fail();
    if (licenseAdmission.status !== "admitted" && entryRecord.storedContentDigest !== undefined) fail();
    licenseCounts.set(licenseDeclaration, (licenseCounts.get(licenseDeclaration) ?? 0) + 1);
  }
  if (JSON.stringify(root.inventory) !== JSON.stringify(capture(inventoryFrom(lock.entries)))) fail();
  if (JSON.stringify(root.licenseDeclarations) !== JSON.stringify(capture(Object.fromEntries(licenseCounts)))) fail();
  const { catalogDigest, ...payload } = lock;
  if (catalogDigest !== pluginCatalogDigest(payload)) fail();
  return lock;
}
