import { MongoServerError, type ClientSession, type Db, type MongoClient } from "mongodb";
import { Buffer } from "node:buffer";
import { createHash } from "node:crypto";
import { types as utilTypes } from "node:util";
import type {
  IPluginsRepository,
  PluginCatalogEntry,
  PluginCatalogSnapshot,
  PluginComponentAdmission,
  PluginCredentialSlot,
  PluginExecutionDispatchClaim,
  PluginInstallation,
  PluginIdempotentInstall,
  PluginIdempotentInstallResult,
  PluginIdempotentEnable,
  PluginIdempotentEnableResult,
  PluginIdempotencyLookup,
  PluginMigrationRecord,
  PluginReceipt,
} from "@/src/application/repositories/plugins.repository.interface";
import { PLUGIN_COLLECTIONS } from "./mongodb.plugins.indexes";
import { ZPluginMigrationRecord, componentBindingDigest, validatePluginCatalogLock, type PluginCatalogLock } from "@rowboat/openai-plugin-runtime";

const ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/;
const DIGEST = /^[a-f0-9]{64}$/;
const COMMIT = /^[a-f0-9]{40}$/;
const REFERENCE = /^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$/;
const NAME = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/;
const COMPONENT_ID = /^[A-Za-z0-9][A-Za-z0-9._:/#-]{0,255}$/;
const COMPONENT_NAME = /^[^\0]{1,256}$/u;
const OPENAI_PLUGINS_SOURCE_URL = "https://github.com/openai/plugins.git";
const MAX_DEPTH = 16;
const MAX_ITEMS = 4096;
const MAX_STRING = 16_384;
const MAX_RECORD_BYTES = 256 * 1024;
const FORBIDDEN_CREDENTIAL_FRAGMENTS = [
  "value", "secret", "token", "password", "passphrase", "private", "apikey", "auth", "cert", "session", "credential",
] as const;

type JsonPrimitive = string | number | boolean | null;
type Captured = JsonPrimitive | readonly Captured[] | Readonly<{ [key: string]: Captured }>;
type StoredDocument = Record<string, unknown>;

const repositoryErrors = new WeakSet<Error>();

/**
 * The MongoDB driver assigns the generated `_id` onto the document object it is
 * handed. Every document these writes produce is validated and frozen, so the
 * driver would throw `TypeError: Cannot add property _id` and the write would
 * surface as `repository_write_failed`. Each insert therefore passes an
 * extensible shallow copy; the frozen original stays the authority.
 */
function insertable<T>(document: T): T {
  return { ...(document as object) } as T;
}

function invalid(reason: string): never {
  const error = new Error(reason);
  repositoryErrors.add(error);
  throw error;
}

interface CaptureBudget {
  items: number;
  bytes: number;
}

function addBytes(budget: CaptureBudget, bytes: number): void {
  budget.bytes += bytes;
  if (budget.bytes > MAX_RECORD_BYTES) invalid("plugin_record_too_large");
}

function encodedJsonBytes(value: string): number {
  return Buffer.byteLength(JSON.stringify(value), "utf8");
}

function capture(input: unknown, reason: string, secretKeys: boolean, depth = 0, budget: CaptureBudget = { items: 0, bytes: 0 }): Captured {
  if (depth > MAX_DEPTH || ++budget.items > MAX_ITEMS) invalid(reason);
  if (input === null) {
    addBytes(budget, 4);
    return input;
  }
  if (typeof input === "boolean") {
    addBytes(budget, input ? 4 : 5);
    return input;
  }
  if (typeof input === "number") {
    if (!Number.isFinite(input)) invalid(reason);
    addBytes(budget, Buffer.byteLength(JSON.stringify(input), "utf8"));
    return input;
  }
  if (typeof input === "string") {
    if (input.length > MAX_STRING || input.includes("\0")) invalid(reason);
    addBytes(budget, encodedJsonBytes(input));
    return input;
  }
  if (typeof input !== "object") invalid(reason);
  if (utilTypes.isProxy(input)) invalid(reason);

  if (Array.isArray(input)) {
    let lengthDescriptor: PropertyDescriptor | undefined;
    try {
      lengthDescriptor = Object.getOwnPropertyDescriptor(input, "length");
    } catch {
      invalid(reason);
    }
    if (
      lengthDescriptor === undefined
      || !("value" in lengthDescriptor)
      || typeof lengthDescriptor.value !== "number"
      || !Number.isSafeInteger(lengthDescriptor.value)
      || lengthDescriptor.value < 0
    ) invalid(reason);
    const length = lengthDescriptor.value;
    if (length > MAX_ITEMS - budget.items) invalid(reason);
    let prototype: object | null;
    let ownKeys: readonly PropertyKey[];
    try {
      prototype = Object.getPrototypeOf(input);
      ownKeys = Reflect.ownKeys(input);
    } catch {
      invalid(reason);
    }
    if (prototype !== Array.prototype || ownKeys.length !== length + 1 || ownKeys.some((key) => typeof key !== "string")) invalid(reason);
    const keySet = new Set(ownKeys as readonly string[]);
    if (!keySet.has("length")) invalid(reason);
    const values: Captured[] = [];
    addBytes(budget, 2);
    for (let index = 0; index < length; index += 1) {
      if (index > 0) addBytes(budget, 1);
      const key = String(index);
      if (!keySet.has(key)) invalid(reason);
      let descriptor: PropertyDescriptor | undefined;
      try {
        descriptor = Object.getOwnPropertyDescriptor(input, key);
      } catch {
        invalid(reason);
      }
      if (descriptor === undefined || !("value" in descriptor) || !descriptor.enumerable) invalid(reason);
      values.push(capture(descriptor.value, reason, secretKeys, depth + 1, budget));
    }
    return Object.freeze(values);
  }
  let prototype: object | null;
  let ownKeys: readonly PropertyKey[];
  try {
    prototype = Object.getPrototypeOf(input);
    ownKeys = Reflect.ownKeys(input);
  } catch {
    invalid(reason);
  }
  if (prototype !== Object.prototype && prototype !== null) invalid(reason);
  if (ownKeys.length > MAX_ITEMS - budget.items || ownKeys.some((key) => typeof key !== "string")) invalid(reason);
  const output = Object.create(null) as Record<string, Captured>;
  const descriptorKeys = [...ownKeys as readonly string[]].sort();
  addBytes(budget, 2);
  for (const [index, key] of descriptorKeys.entries()) {
    let descriptor: PropertyDescriptor | undefined;
    try {
      descriptor = Object.getOwnPropertyDescriptor(input, key);
    } catch {
      invalid(reason);
    }
    if (descriptor === undefined || !("value" in descriptor) || !descriptor.enumerable) invalid(reason);
    if (key.length > MAX_STRING || key.includes("\0") || key === "__proto__" || key === "prototype" || key === "constructor" || key.startsWith("$")) invalid(reason);
    addBytes(budget, (index > 0 ? 1 : 0) + encodedJsonBytes(key) + 1);
    const normalizedKey = key.replace(/[^A-Za-z0-9]/g, "").toLowerCase();
    if (secretKeys && FORBIDDEN_CREDENTIAL_FRAGMENTS.some((fragment) => normalizedKey.includes(fragment))) invalid("secret_value_rejected");
    output[key] = capture(descriptor.value, reason, secretKeys, depth + 1, budget);
  }
  return Object.freeze(output);
}

function object(input: unknown, reason: string, secretKeys = false): Readonly<Record<string, Captured>> {
  const captured = capture(input, reason, secretKeys);
  if (captured === null || Array.isArray(captured) || typeof captured !== "object") invalid(reason);
  return captured as Readonly<Record<string, Captured>>;
}

function keys(record: Readonly<Record<string, Captured>>, allowed: readonly string[], reason: string): void {
  const allowedSet = new Set(allowed);
  if (Object.keys(record).some((key) => !allowedSet.has(key))) invalid(reason);
}

function string(record: Readonly<Record<string, Captured>>, key: string, pattern: RegExp, reason: string): string {
  const value = record[key];
  if (typeof value !== "string" || !pattern.test(value)) invalid(reason);
  return value;
}

function integer(record: Readonly<Record<string, Captured>>, key: string, reason: string): number {
  const value = record[key];
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 0) invalid(reason);
  return value;
}

function boolean(record: Readonly<Record<string, Captured>>, key: string, reason: string): boolean {
  const value = record[key];
  if (typeof value !== "boolean") invalid(reason);
  return value;
}

function optionalString(record: Readonly<Record<string, Captured>>, key: string, pattern: RegExp, reason: string): string | undefined {
  const value = record[key];
  if (value === undefined) return undefined;
  if (typeof value !== "string" || !pattern.test(value)) invalid(reason);
  return value;
}

function canonical(value: Captured): string {
  return JSON.stringify(value);
}

function parsePayload(payload: unknown, reason: string): unknown {
  if (typeof payload !== "string") invalid(reason);
  if (Buffer.byteLength(payload, "utf8") > MAX_RECORD_BYTES) invalid("plugin_record_too_large");
  try {
    return JSON.parse(payload) as unknown;
  } catch {
    invalid(reason);
  }
}

function assertStoredDocumentBudget(document: Captured): void {
  if (Buffer.byteLength(JSON.stringify(document), "utf8") > MAX_RECORD_BYTES) invalid("plugin_record_too_large");
}

function freezeRead<T>(document: unknown, reason: string): T {
  return capture(document, reason, false) as T;
}

function catalogSnapshot(input: unknown): PluginCatalogSnapshot {
  const record = object(input, "catalog_snapshot_invalid");
  keys(record, ["sourceUrl", "sourceCommit", "importedAt", "schemaVersion", "policyVersion", "inventory", "licenseDeclarations", "catalogDigest"], "catalog_snapshot_invalid");
  if (record.sourceUrl !== OPENAI_PLUGINS_SOURCE_URL) invalid("catalog_snapshot_invalid");
  string(record, "sourceCommit", COMMIT, "catalog_snapshot_invalid");
  string(record, "importedAt", /^\d{4}-\d{2}-\d{2}T/, "catalog_snapshot_invalid");
  string(record, "schemaVersion", NAME, "catalog_snapshot_invalid");
  string(record, "policyVersion", NAME, "catalog_snapshot_invalid");
  string(record, "catalogDigest", DIGEST, "catalog_snapshot_invalid");
  if (typeof record.inventory !== "object" || record.inventory === null || Array.isArray(record.inventory)) invalid("catalog_snapshot_invalid");
  if (typeof record.licenseDeclarations !== "object" || record.licenseDeclarations === null || Array.isArray(record.licenseDeclarations)) invalid("catalog_snapshot_invalid");
  const inventory = record.inventory as Readonly<Record<string, Captured>>;
  keys(inventory, ["pluginsWithSkills", "pluginsWithApps", "pluginsWithAgents", "pluginsWithCommands", "pluginsWithMcp", "pluginsWithCommandHooks"], "catalog_snapshot_invalid");
  for (const key of Object.keys(inventory)) integer(inventory, key, "catalog_snapshot_invalid");
  const licenses = record.licenseDeclarations as Readonly<Record<string, Captured>>;
  if (Object.keys(licenses).length > 256) invalid("catalog_snapshot_invalid");
  for (const [license, count] of Object.entries(licenses)) {
    if (license.length === 0 || license.length > 256 || typeof count !== "number" || !Number.isSafeInteger(count) || count < 0) invalid("catalog_snapshot_invalid");
  }
  return record as unknown as PluginCatalogSnapshot;
}

function catalogAdmissionDecision(input: Captured, reason: string): void {
  if (typeof input !== "object" || input === null || Array.isArray(input)) invalid(reason);
  const decision = input as Readonly<Record<string, Captured>>;
  keys(decision, ["status", "reason", "policyVersion"], reason);
  const status = string(decision, "status", /^(admitted|review_required|rejected)$/, reason);
  string(decision, "policyVersion", NAME, reason);
  if (status === "admitted") {
    if (decision.reason !== undefined) invalid(reason);
  } else if (optionalString(decision, "reason", NAME, reason) === undefined) {
    invalid(reason);
  }
}

function catalogComponentAdmission(input: Captured, reason: string): Readonly<{ id: string; bindingDigest: string }> {
  if (typeof input !== "object" || input === null || Array.isArray(input)) invalid(reason);
  const selected = input as Readonly<Record<string, Captured>>;
  keys(selected, ["component", "admission"], reason);
  if (typeof selected.component !== "object" || selected.component === null || Array.isArray(selected.component)) invalid(reason);
  const component = selected.component as Readonly<Record<string, Captured>>;
  keys(component, ["id", "name", "kind", "status", "reason", "metadata"], reason);
  const id = string(component, "id", COMPONENT_ID, reason);
  string(component, "name", /^.{1,256}$/, reason);
  string(component, "kind", /^(skill|agent|command|mcp|app|hook|asset)$/, reason);
  string(component, "status", /^(available|review_required|installed|partially_available|unavailable|migration_required|error|invalid|unsupported)$/, reason);
  optionalString(component, "reason", NAME, reason);
  if (typeof component.metadata !== "object" || component.metadata === null || Array.isArray(component.metadata)) invalid(reason);
  string(component.metadata as Readonly<Record<string, Captured>>, "digest", DIGEST, reason);
  const bindingDigest = string(component.metadata as Readonly<Record<string, Captured>>, "bindingDigest", DIGEST, reason);
  catalogAdmissionDecision(selected.admission, reason);
  return Object.freeze({ id, bindingDigest });
}

function catalogEntry(input: unknown): PluginCatalogEntry {
  const record = object(input, "catalog_entry_invalid");
  keys(record, ["catalogDigest", "sourceUrl", "sourceCommit", "pluginName", "pluginVersion", "manifestDigest", "treeDigest", "importedAt", "schemaVersion", "policyVersion", "name", "licenseDeclaration", "admission", "components", "storedContentDigest"], "catalog_entry_invalid");
  string(record, "catalogDigest", DIGEST, "catalog_entry_invalid");
  if (record.sourceUrl !== OPENAI_PLUGINS_SOURCE_URL) invalid("catalog_entry_invalid");
  string(record, "sourceCommit", COMMIT, "catalog_entry_invalid");
  const pluginName = string(record, "pluginName", NAME, "catalog_entry_invalid");
  if (string(record, "name", NAME, "catalog_entry_invalid") !== pluginName) invalid("catalog_entry_invalid");
  string(record, "pluginVersion", /^.{1,128}$/, "catalog_entry_invalid");
  string(record, "manifestDigest", DIGEST, "catalog_entry_invalid");
  string(record, "treeDigest", DIGEST, "catalog_entry_invalid");
  string(record, "importedAt", /^\d{4}-\d{2}-\d{2}T/, "catalog_entry_invalid");
  string(record, "schemaVersion", NAME, "catalog_entry_invalid");
  string(record, "policyVersion", NAME, "catalog_entry_invalid");
  string(record, "licenseDeclaration", /^(?:<missing>|[A-Za-z0-9][A-Za-z0-9.+() -]{0,255})$/, "catalog_entry_invalid");
  optionalString(record, "storedContentDigest", DIGEST, "catalog_entry_invalid");
  if (!Array.isArray(record.components) || record.components.length > 512) invalid("catalog_entry_invalid");
  catalogAdmissionDecision(record.admission, "catalog_entry_invalid");
  const componentIds = new Set<string>();
  const componentDigests = new Set<string>();
  const typed = record as unknown as PluginCatalogEntry;
  for (const component of record.components) {
    const selected = catalogComponentAdmission(component, "catalog_entry_invalid");
    const bound = typed.components.find(({ component: candidate }) => candidate.id === selected.id)?.component;
    if (
      bound === undefined
      || componentBindingDigest(typed, bound, {
        componentAdmission: typed.components.find(({ component: candidate }) => candidate.id === selected.id)?.admission ?? invalid("catalog_entry_invalid"),
        licenseDeclaration: typed.licenseDeclaration ?? "<missing>",
        licenseAdmission: typed.admission,
      }) !== selected.bindingDigest
      || componentIds.has(selected.id)
      || componentDigests.has(selected.bindingDigest)
    ) invalid("catalog_entry_invalid");
    componentIds.add(selected.id);
    componentDigests.add(selected.bindingDigest);
  }
  return record as unknown as PluginCatalogEntry;
}

function installation(input: unknown): PluginInstallation {
  const record = object(input, "installation_invalid");
  keys(record, ["id", "projectId", "pluginName", "pluginVersion", "sourceCommit", "manifestDigest", "treeDigest", "policyVersion", "enabled", "revision", "providerBindings"], "installation_invalid");
  string(record, "id", ID, "installation_invalid");
  string(record, "projectId", ID, "installation_invalid");
  string(record, "pluginName", ID, "installation_invalid");
  string(record, "pluginVersion", /^.{1,128}$/, "installation_invalid");
  string(record, "sourceCommit", COMMIT, "installation_invalid");
  string(record, "manifestDigest", DIGEST, "installation_invalid");
  string(record, "treeDigest", DIGEST, "installation_invalid");
  string(record, "policyVersion", NAME, "installation_invalid");
  boolean(record, "enabled", "installation_invalid");
  integer(record, "revision", "installation_invalid");
  if (record.providerBindings !== undefined) {
    if (!Array.isArray(record.providerBindings) || record.providerBindings.length > 256) invalid("installation_invalid");
    const componentIds = new Set<string>();
    for (const selectedValue of record.providerBindings) {
      if (typeof selectedValue !== "object" || selectedValue === null || Array.isArray(selectedValue)) invalid("installation_invalid");
      const selected = selectedValue as Readonly<Record<string, Captured>>;
      keys(selected, ["componentId", "binding"], "installation_invalid");
      const componentId = string(selected, "componentId", COMPONENT_ID, "installation_invalid");
      if (componentIds.has(componentId)) invalid("installation_invalid");
      componentIds.add(componentId);
      if (typeof selected.binding !== "object" || selected.binding === null || Array.isArray(selected.binding)) invalid("installation_invalid");
      const binding = selected.binding as Readonly<Record<string, Captured>>;
      keys(binding, ["id", "providerKind", "componentDigest", "pairedComponentDigests", "temporaryAdapter"], "installation_invalid");
      string(binding, "id", ID, "installation_invalid");
      string(binding, "providerKind", /^(mcp-http|mcp-process|rowboat-native|legacy-composio-adapter|openai-connector-bridge)$/, "installation_invalid");
      string(binding, "componentDigest", DIGEST, "installation_invalid");
      if (binding.pairedComponentDigests !== undefined) {
        if (!Array.isArray(binding.pairedComponentDigests) || binding.pairedComponentDigests.length !== 2) invalid("installation_invalid");
        for (const pairedDigest of binding.pairedComponentDigests) {
          if (typeof pairedDigest !== "string" || !DIGEST.test(pairedDigest)) invalid("installation_invalid");
        }
      }
      if (binding.temporaryAdapter !== undefined && binding.temporaryAdapter !== true) invalid("installation_invalid");
    }
  }
  return record as unknown as PluginInstallation;
}

function admission(input: unknown): PluginComponentAdmission {
  const record = object(input, "admission_invalid");
  keys(record, ["installationId", "componentDigest", "componentKind", "componentName", "status", "reason", "policyVersion"], "admission_invalid");
  string(record, "installationId", ID, "admission_invalid");
  string(record, "componentDigest", DIGEST, "admission_invalid");
  string(record, "componentKind", /^(skill|agent|command|mcp|app|hook|asset)$/, "admission_invalid");
  string(record, "componentName", /^.{1,256}$/, "admission_invalid");
  const status = string(record, "status", /^(admitted|review_required|rejected)$/, "admission_invalid");
  string(record, "policyVersion", NAME, "admission_invalid");
  if (status === "admitted" && record.reason !== undefined) invalid("admission_invalid");
  if (status !== "admitted" && optionalString(record, "reason", NAME, "admission_invalid") === undefined) invalid("admission_invalid");
  return record as unknown as PluginComponentAdmission;
}

export function serializePluginInstallationDocument(input: unknown): Readonly<Record<string, unknown>> {
  const document = installation(input);
  const stored: StoredDocument = { id: document.id, projectId: document.projectId, pluginName: document.pluginName,
    pluginVersion: document.pluginVersion, sourceCommit: document.sourceCommit, manifestDigest: document.manifestDigest,
    treeDigest: document.treeDigest, policyVersion: document.policyVersion, enabled: document.enabled, revision: document.revision };
  if (document.providerBindings !== undefined) stored.providerBindingsJson = canonical(document.providerBindings as unknown as Captured);
  assertStoredDocumentBudget(stored as unknown as Captured);
  return Object.freeze(stored);
}

export function deserializePluginInstallationDocument(input: unknown): PluginInstallation {
  const stored = object(input, "installation_invalid");
  const selected: StoredDocument = { id: stored.id, projectId: stored.projectId, pluginName: stored.pluginName,
    pluginVersion: stored.pluginVersion, sourceCommit: stored.sourceCommit, manifestDigest: stored.manifestDigest,
    treeDigest: stored.treeDigest, policyVersion: stored.policyVersion, enabled: stored.enabled, revision: stored.revision };
  if (stored.providerBindingsJson !== undefined) selected.providerBindings = parsePayload(stored.providerBindingsJson, "installation_invalid");
  return installation(selected);
}

export function serializePluginAdmissionDocument(input: unknown): PluginComponentAdmission { return admission(input); }

function credentialLikeText(value: string): boolean {
  return /(?:^|[^A-Za-z0-9])(?:x[-_])?api(?:[-_ ]?key)(?=\s|:|=)/iu.test(value)
    || /^\s*(?:basic|digest|bearer|negotiate|ntlm|api(?:[-_ ]?key)|token)(?=\s|:|=)/iu.test(value)
    || /[\u0000-\u001f\u007f]/u.test(value)
    || /%[0-9a-f]{2}/iu.test(value)
    || value.includes("://")
    || /[?@#]/u.test(value)
    || /\bbearer\s+/iu.test(value)
    || /\b[a-z][a-z0-9_.-]*\s*=/iu.test(value)
    || /-----BEGIN/iu.test(value)
    || /(?:[A-Za-z0-9_-]{4,}\.){2}[A-Za-z0-9_-]{4,}/u.test(value)
    || /[A-Za-z0-9+/_=-]{32,}/u.test(value)
    || /\b(?:secret|token|password|passphrase|private|api\s*key|credential)\b/iu.test(value);
}

function credentialSlot(input: unknown): PluginCredentialSlot {
  const record = object(input, "secret_value_rejected", true);
  keys(record, ["id", "projectId", "installationId", "name", "reference", "metadata"], "secret_value_rejected");
  string(record, "id", ID, "credential_slot_invalid");
  string(record, "projectId", ID, "credential_slot_invalid");
  string(record, "installationId", ID, "credential_slot_invalid");
  string(record, "name", /^[A-Z][A-Z0-9_]{0,127}$/, "credential_slot_invalid");
  if (typeof record.reference !== "object" || record.reference === null || Array.isArray(record.reference)) invalid("credential_slot_invalid");
  const reference = record.reference as Readonly<Record<string, Captured>>;
  keys(reference, ["kind", "reference"], "credential_slot_invalid");
  string(reference, "kind", /^(bearer|oauth|environment)$/, "credential_slot_invalid");
  const rawReference = reference.reference;
  if (typeof rawReference === "string" && credentialLikeText(rawReference)) invalid("secret_value_rejected");
  string(reference, "reference", REFERENCE, "credential_slot_invalid");
  if (record.metadata !== undefined) {
    if (typeof record.metadata !== "object" || record.metadata === null || Array.isArray(record.metadata)) invalid("credential_slot_invalid");
    const metadata = record.metadata as Readonly<Record<string, Captured>>;
    keys(metadata, ["label", "required", "order"], "credential_slot_invalid");
    if (metadata.label !== undefined) {
      if (typeof metadata.label !== "string" || !/^[A-Za-z0-9][A-Za-z0-9 ._()/-]{0,127}$/u.test(metadata.label)) invalid("secret_value_rejected");
      if (credentialLikeText(metadata.label)) invalid("secret_value_rejected");
    }
    if (metadata.required !== undefined && typeof metadata.required !== "boolean") invalid("credential_slot_invalid");
    if (metadata.order !== undefined && (typeof metadata.order !== "number" || !Number.isSafeInteger(metadata.order) || metadata.order < 0 || metadata.order > 10_000)) invalid("credential_slot_invalid");
  }
  return record as unknown as PluginCredentialSlot;
}

function migrationRecord(input: unknown): PluginMigrationRecord {
  const record = object(input, "migration_record_invalid");
  const parsed = ZPluginMigrationRecord.safeParse(record);
  if (!parsed.success) invalid("migration_record_invalid");
  return parsed.data;
}

function receipt(input: unknown): PluginReceipt {
  const record = object(input, "receipt_invalid", true);
  keys(record, ["type", "receiptId", "projectId", "pluginName", "status", "componentKind", "componentName", "reason", "temporaryAdapter", "output", "redactions"], "receipt_invalid");
  string(record, "type", /^(import|install|migration|execution)$/, "receipt_invalid");
  string(record, "receiptId", ID, "receipt_invalid");
  string(record, "projectId", ID, "receipt_invalid");
  string(record, "pluginName", ID, "receipt_invalid");
  string(record, "status", /^(success|failed|denied|timed_out)$/, "receipt_invalid");
  if (!Array.isArray(record.redactions) || record.redactions.length > 128) invalid("receipt_invalid");
  return record as unknown as PluginReceipt;
}

function mutationReceipt(input: unknown): PluginReceipt {
  const selected = receipt(input);
  if (
    selected.type !== "install" || selected.status !== "success" || selected.redactions.length !== 0
    || Object.keys(selected).some((key) => !["type", "receiptId", "projectId", "pluginName", "status", "redactions"].includes(key))
  ) invalid("idempotency_record_invalid");
  return selected;
}

interface MutationEnvelopeContext {
  readonly scope: string;
  readonly fingerprint: string;
  readonly projectId: string;
  readonly pluginName: string;
  readonly catalogDigest: string;
  readonly operation: "install" | "set_enabled";
}

interface ParsedMutationEnvelope {
  readonly receipt: PluginReceipt;
  readonly installation: PluginInstallation;
  readonly catalogDigest: string;
}

function mutationSource(installationValue: PluginInstallation): Readonly<Record<string, unknown>> {
  return Object.freeze({
    id: installationValue.id,
    projectId: installationValue.projectId,
    pluginName: installationValue.pluginName,
    pluginVersion: installationValue.pluginVersion,
    sourceCommit: installationValue.sourceCommit,
    manifestDigest: installationValue.manifestDigest,
    treeDigest: installationValue.treeDigest,
    policyVersion: installationValue.policyVersion,
    providerBindings: installationValue.providerBindings ?? Object.freeze([]),
  });
}

function mutationEnvelopePayload(
  context: MutationEnvelopeContext,
  receiptValue: PluginReceipt,
  installationValue: PluginInstallation,
): string {
  const envelope = Object.freeze({
    type: "plugin_mutation_v1",
    receiptId: receiptValue.receiptId,
    projectId: context.projectId,
    pluginName: context.pluginName,
    catalogDigest: context.catalogDigest,
    installationId: installationValue.id,
    operation: context.operation,
    scopeHash: context.scope,
    requestFingerprint: context.fingerprint,
    status: "success",
    source: mutationSource(installationValue),
    result: Object.freeze({ kind: context.operation, installation: installationValue }),
  });
  const captured = capture(envelope, "idempotency_record_invalid", true);
  assertStoredDocumentBudget(captured);
  return canonical(captured);
}

function parseMutationEnvelope(documentInput: unknown, expected: MutationEnvelopeContext): ParsedMutationEnvelope {
  const document = object(documentInput, "idempotency_record_invalid", true);
  keys(document, ["receiptId", "idempotencyScope", "requestFingerprint", "envelopePayload"], "idempotency_record_invalid");
  const receiptId = string(document, "receiptId", ID, "idempotency_record_invalid");
  if (document.idempotencyScope !== expected.scope || document.requestFingerprint !== expected.fingerprint) invalid("idempotency_record_invalid");
  const envelope = object(parsePayload(document.envelopePayload, "idempotency_record_invalid"), "idempotency_record_invalid", true);
  keys(envelope, ["type", "receiptId", "projectId", "pluginName", "catalogDigest", "installationId", "operation", "scopeHash", "requestFingerprint", "status", "source", "result"], "idempotency_record_invalid");
  if (
    envelope.type !== "plugin_mutation_v1" || envelope.receiptId !== receiptId
    || envelope.projectId !== expected.projectId || envelope.pluginName !== expected.pluginName
    || envelope.catalogDigest !== expected.catalogDigest
    || envelope.operation !== expected.operation || envelope.scopeHash !== expected.scope
    || envelope.requestFingerprint !== expected.fingerprint || envelope.status !== "success"
  ) invalid("idempotency_record_invalid");
  string(envelope, "installationId", ID, "idempotency_record_invalid");
  const result = object(envelope.result, "idempotency_record_invalid", true);
  keys(result, ["kind", "installation"], "idempotency_record_invalid");
  if (result.kind !== expected.operation) invalid("idempotency_record_invalid");
  const installed = installation(result.installation);
  if (installed.id !== envelope.installationId || installed.projectId !== expected.projectId || installed.pluginName !== expected.pluginName) invalid("idempotency_record_invalid");
  const source = object(envelope.source, "idempotency_record_invalid", true);
  keys(source, ["id", "projectId", "pluginName", "pluginVersion", "sourceCommit", "manifestDigest", "treeDigest", "policyVersion", "providerBindings"], "idempotency_record_invalid");
  if (
    source.id !== installed.id || source.projectId !== installed.projectId || source.pluginName !== installed.pluginName
    || source.pluginVersion !== installed.pluginVersion || source.sourceCommit !== installed.sourceCommit
    || source.manifestDigest !== installed.manifestDigest || source.treeDigest !== installed.treeDigest
    || source.policyVersion !== installed.policyVersion
    || canonical(source.providerBindings as Captured) !== canonical((installed.providerBindings ?? []) as unknown as Captured)
  ) invalid("idempotency_record_invalid");
  return Object.freeze({
    receipt: Object.freeze({
      type: "install", receiptId, projectId: expected.projectId, pluginName: expected.pluginName,
      status: "success", redactions: Object.freeze([]),
    }),
    installation: installed,
    catalogDigest: expected.catalogDigest,
  });
}

function isDuplicateKey(error: unknown): boolean {
  return (error instanceof MongoServerError && error.code === 11000)
    || (error instanceof Error && repositoryErrors.has(error) && error.message === "repository_duplicate_key");
}

async function immutableInsert(
  collection: {
    findOne(filter: StoredDocument, options: { projection: { _id: 0 }; session?: ClientSession }): Promise<StoredDocument | null>;
    insertOne(document: StoredDocument, options?: { session: ClientSession }): Promise<unknown>;
  },
  filter: StoredDocument,
  document: Captured,
  conflict: string,
  session?: ClientSession,
): Promise<void> {
  assertStoredDocumentBudget(document);
  const readOptions = session === undefined ? { projection: { _id: 0 } as const } : { projection: { _id: 0 } as const, session };
  const existing = await collection.findOne(filter, readOptions);
  if (existing !== null) {
    if (canonical(capture(existing, conflict, false)) !== canonical(document)) invalid(conflict);
    return;
  }
  if (session !== undefined) {
    await collection.insertOne(insertable(document as StoredDocument), { session });
    return;
  }
  try {
    await collection.insertOne(insertable(document as StoredDocument));
  } catch (error) {
    if (!(error instanceof MongoServerError) || error.code !== 11000) invalid("repository_write_failed");
    const raced = await collection.findOne(filter, { projection: { _id: 0 } });
    if (raced !== null && canonical(capture(raced, conflict, false)) === canonical(document)) return;
    invalid(conflict);
  }
}

export interface PluginTransactionRunner {
  run<T>(work: (session: ClientSession) => Promise<T>): Promise<T>;
}

export class MongoPluginTransactionRunner implements PluginTransactionRunner {
  constructor({ pluginsMongoClient }: { readonly pluginsMongoClient: MongoClient }) {
    this.client = pluginsMongoClient;
  }

  private readonly client: MongoClient;

  async run<T>(work: (session: ClientSession) => Promise<T>): Promise<T> {
    let session: ClientSession;
    try {
      session = this.client.startSession();
    } catch {
      invalid("repository_transaction_failed");
    }
    let operationFailed = false;
    try {
      let completed: Readonly<{ value: T }> | undefined;
      await session.withTransaction(async () => {
        completed = { value: await work(session) };
      });
      if (completed === undefined) invalid("repository_transaction_failed");
      return completed.value;
    } catch (error) {
      operationFailed = true;
      if (error instanceof Error && repositoryErrors.has(error)) throw error;
      if (error instanceof MongoServerError && error.code === 11000) invalid("repository_duplicate_key");
      invalid("repository_transaction_failed");
    } finally {
      try {
        await session.endSession();
      } catch {
        if (!operationFailed) invalid("repository_transaction_failed");
      }
    }
    invalid("repository_transaction_failed");
  }
}

export class MongodbPluginsRepository implements IPluginsRepository {
  private readonly database: Db;
  private readonly transactions: PluginTransactionRunner;

  constructor({ pluginsDatabase, pluginTransactionRunner }: { readonly pluginsDatabase: Db; readonly pluginTransactionRunner: PluginTransactionRunner }) {
    this.database = pluginsDatabase;
    this.transactions = pluginTransactionRunner;
  }

  async putCatalog(input: PluginCatalogLock): Promise<void> {
    const lock = validatePluginCatalogLock(input);
    const { entries, ...snapshot } = lock;
    const catalogEntries = entries.map((entry) => catalogEntry({ ...entry, catalogDigest: lock.catalogDigest }));
    try {
      await this.transactions.run(async (session) => {
        const snapshotDocument = Object.freeze({
          catalogDigest: lock.catalogDigest,
          complete: true,
          payload: canonical(snapshot as unknown as Captured),
        }) as unknown as Captured;
        await immutableInsert(
          this.database.collection(PLUGIN_COLLECTIONS.catalogSnapshots),
          { catalogDigest: lock.catalogDigest },
          snapshotDocument,
          "catalog_digest_conflict",
          session,
        );
        for (const entry of catalogEntries) {
          const stored = Object.freeze({
            catalogDigest: lock.catalogDigest,
            name: entry.name,
            payload: canonical(entry as unknown as Captured),
          }) as unknown as Captured;
          await immutableInsert(
            this.database.collection(PLUGIN_COLLECTIONS.catalogEntries),
            { catalogDigest: lock.catalogDigest, name: entry.name },
            stored,
            "catalog_entry_conflict",
            session,
          );
        }
      });
    } catch (error) {
      if (!isDuplicateKey(error)) throw error;
      const existing = await this.getCatalog(lock.catalogDigest);
      if (existing !== null && canonical(existing as unknown as Captured) === canonical(lock as unknown as Captured)) return;
      invalid("catalog_digest_conflict");
    }
  }

  async getCatalog(digest: string, options?: Readonly<{ maxTimeMS: number; signal: AbortSignal; remainingMs?: () => number }>): Promise<PluginCatalogLock | null> {
    if (!DIGEST.test(digest)) invalid("catalog_digest_invalid");
    const snapshotOptions = options === undefined ? undefined : { maxTimeMS: options.remainingMs?.() ?? options.maxTimeMS, signal: options.signal };
    const snapshotDocument = await this.database.collection(PLUGIN_COLLECTIONS.catalogSnapshots)
      .findOne({ catalogDigest: digest }, { projection: { _id: 0 }, ...snapshotOptions });
    if (snapshotDocument === null) return null;
    if (snapshotDocument.complete !== true) invalid("catalog_incomplete");
    const snapshot = catalogSnapshot(parsePayload(snapshotDocument.payload, "catalog_snapshot_invalid"));
    const entries = await this.listCatalogEntries(digest, options);
    const lock = {
      ...snapshot,
      entries: entries.map(({ catalogDigest: ignored, ...entry }) => entry),
    };
    return validatePluginCatalogLock(lock);
  }

  async putCatalogSnapshot(input: PluginCatalogSnapshot): Promise<void> {
    const snapshot = catalogSnapshot(input);
    const captured = snapshot as unknown as Captured;
    const document = Object.freeze({ catalogDigest: snapshot.catalogDigest, payload: canonical(captured) }) as unknown as Captured;
    await immutableInsert(this.database.collection(PLUGIN_COLLECTIONS.catalogSnapshots), { catalogDigest: snapshot.catalogDigest }, document, "catalog_digest_conflict");
  }

  async getCatalogSnapshot(digest: string): Promise<PluginCatalogSnapshot | null> {
    if (!DIGEST.test(digest)) invalid("catalog_digest_invalid");
    const document = await this.database.collection(PLUGIN_COLLECTIONS.catalogSnapshots).findOne({ catalogDigest: digest }, { projection: { _id: 0 } });
    return document === null ? null : catalogSnapshot(parsePayload(document.payload, "catalog_snapshot_invalid"));
  }

  async listCatalogEntries(catalogDigest: string, options?: Readonly<{ maxTimeMS: number; signal: AbortSignal; remainingMs?: () => number }>): Promise<readonly PluginCatalogEntry[]> {
    if (!DIGEST.test(catalogDigest)) invalid("catalog_digest_invalid");
    const boundedOptions = options === undefined ? undefined : { maxTimeMS: options.remainingMs?.() ?? options.maxTimeMS, signal: options.signal };
    const documents = await this.database.collection(PLUGIN_COLLECTIONS.catalogEntries).find({ catalogDigest }, { projection: { _id: 0 }, ...boundedOptions }).sort({ name: 1 }).toArray();
    return Object.freeze(documents.map((document) => catalogEntry(parsePayload(document.payload, "catalog_entry_invalid"))));
  }

  async getInstallation(projectId: string, pluginName: string): Promise<PluginInstallation | null> {
    if (!ID.test(projectId) || !NAME.test(pluginName)) invalid("installation_lookup_invalid");
    const document = await this.database.collection(PLUGIN_COLLECTIONS.installations)
      .findOne({ projectId, pluginName }, { projection: { _id: 0 } });
    return document === null ? null : this.readInstallation(document);
  }

  async putCatalogEntries(inputs: readonly PluginCatalogEntry[]): Promise<void> {
    if (!Array.isArray(inputs) || inputs.length > MAX_ITEMS) invalid("catalog_entry_invalid");
    const documents = inputs.map(catalogEntry);
    const seen = new Set<string>();
    for (const document of documents) {
      const key = `${document.catalogDigest}:${document.name}`;
      if (seen.has(key)) invalid("catalog_entry_conflict");
      seen.add(key);
    }
    const writes = documents.map((document) => {
      const stored = Object.freeze({ catalogDigest: document.catalogDigest, name: document.name, payload: canonical(document as unknown as Captured) }) as unknown as Captured;
      assertStoredDocumentBudget(stored);
      return Object.freeze({ document, stored });
    });
    await this.validateCatalogEntryBatch(documents, undefined);
    try {
      await this.transactions.run(async (session) => {
        await this.validateCatalogEntryBatch(documents, session);
        for (const { document, stored } of writes) {
          await immutableInsert(this.database.collection(PLUGIN_COLLECTIONS.catalogEntries), { catalogDigest: document.catalogDigest, name: document.name }, stored, "catalog_entry_conflict", session);
        }
      });
    } catch (error) {
      if (!isDuplicateKey(error)) throw error;
      await this.classifyCatalogEntryRace(documents);
    }
  }

  async putInstallation(input: PluginInstallation): Promise<void> {
    const document = installation(input);
    const storedDocument = this.installationDocument(document);
    assertStoredDocumentBudget(storedDocument as unknown as Captured);
    const selectedEntry = await this.requireCatalogEntryForInstallation(document, undefined);
    this.validateProviderBindings(document, selectedEntry);
    const collection = this.database.collection(PLUGIN_COLLECTIONS.installations);
    const existingById = await collection.findOne({ id: document.id }, { projection: { _id: 0 } });
    const existingByProject = await collection.findOne({ projectId: document.projectId, pluginName: document.pluginName }, { projection: { _id: 0 } });
    const existing = existingById ?? existingByProject;
    if (existing !== null) {
      const captured = this.readInstallation(existing);
      if (canonical(captured as unknown as Captured) !== canonical(document as unknown as Captured)) invalid("installation_conflict");
      return;
    }
    try {
      await collection.insertOne(insertable(storedDocument));
    } catch (error) {
      if (!(error instanceof MongoServerError) || error.code !== 11000) invalid("repository_write_failed");
      const racedById = await collection.findOne({ id: document.id }, { projection: { _id: 0 } });
      const racedByProject = await collection.findOne({ projectId: document.projectId, pluginName: document.pluginName }, { projection: { _id: 0 } });
      const raced = racedById ?? racedByProject;
      if (raced !== null && canonical(this.readInstallation(raced) as unknown as Captured) === canonical(document as unknown as Captured)) return;
      invalid("installation_conflict");
    }
  }

  async listInstallations(projectId: string): Promise<readonly PluginInstallation[]> {
    if (!ID.test(projectId)) invalid("project_id_invalid");
    const documents = await this.database.collection(PLUGIN_COLLECTIONS.installations).find({ projectId }, { projection: { _id: 0 } }).sort({ pluginName: 1 }).toArray();
    return Object.freeze(documents.map((document) => this.readInstallation(document)));
  }

  async setInstallationEnabled(id: string, enabled: boolean, expectedRevision: number): Promise<PluginInstallation> {
    if (!ID.test(id) || typeof enabled !== "boolean" || !Number.isSafeInteger(expectedRevision) || expectedRevision < 0) invalid("installation_update_invalid");
    const collection = this.database.collection(PLUGIN_COLLECTIONS.installations);
    const updated = await collection.findOneAndUpdate(
      { id, revision: expectedRevision },
      { $set: { enabled }, $inc: { revision: 1 } },
      { returnDocument: "after", projection: { _id: 0 } },
    );
    if (updated !== null) return this.readInstallation(updated);
    if (await collection.findOne({ id }, { projection: { _id: 1 } }) === null) invalid("installation_not_found");
    invalid("installation_conflict");
  }

  async putAdmissions(inputs: readonly PluginComponentAdmission[]): Promise<void> {
    if (!Array.isArray(inputs) || inputs.length > MAX_ITEMS) invalid("admission_invalid");
    const documents = inputs.map(admission).sort((left, right) => left.componentDigest.localeCompare(right.componentDigest));
    const seen = new Set<string>();
    for (const document of documents) {
      const key = `${document.installationId}:${document.componentDigest}`;
      if (seen.has(key)) invalid("admission_conflict");
      seen.add(key);
    }
    for (const document of documents) assertStoredDocumentBudget(document as unknown as Captured);
    await this.validateAdmissionBatch(documents, undefined);
    try {
      await this.transactions.run(async (session) => {
        await this.validateAdmissionBatch(documents, session);
        for (const document of documents) {
          await immutableInsert(this.database.collection(PLUGIN_COLLECTIONS.componentAdmissions), { installationId: document.installationId, componentDigest: document.componentDigest }, document as unknown as Captured, "admission_conflict", session);
        }
      });
    } catch (error) {
      if (!isDuplicateKey(error)) throw error;
      await this.classifyAdmissionRace(documents);
    }
  }

  async listAdmissions(installationId: string): Promise<readonly PluginComponentAdmission[]> {
    if (!ID.test(installationId)) invalid("installation_id_invalid");
    const documents = await this.database.collection(PLUGIN_COLLECTIONS.componentAdmissions).find({ installationId }, { projection: { _id: 0 } }).sort({ componentDigest: 1 }).toArray();
    return Object.freeze(documents.map((document) => freezeRead<PluginComponentAdmission>(document, "admission_invalid")));
  }

  async listCredentialSlots(installationId: string): Promise<readonly PluginCredentialSlot[]> {
    if (!ID.test(installationId)) invalid("installation_id_invalid");
    const documents = await this.database.collection(PLUGIN_COLLECTIONS.credentialSlots)
      .find({ installationId }, { projection: { _id: 0 } }).sort({ name: 1 }).toArray();
    return Object.freeze(documents.map((document) => credentialSlot(parsePayload(document.payload, "credential_slot_invalid"))));
  }

  async putCredentialSlot(input: PluginCredentialSlot): Promise<void> {
    const document = credentialSlot(input);
    const stored = Object.freeze({ id: document.id, payload: canonical(document as unknown as Captured) }) as unknown as Captured;
    await this.transactions.run(async (session) => {
      const installations = this.database.collection(PLUGIN_COLLECTIONS.installations);
      const parent = await installations.findOne({ id: document.installationId }, { projection: { _id: 0 }, session });
      if (parent === null) invalid("credential_slot_parent_not_found");
      if (this.readInstallation(parent).projectId !== document.projectId) invalid("credential_slot_parent_mismatch");
      await immutableInsert(
        this.database.collection(PLUGIN_COLLECTIONS.credentialSlots),
        { id: document.id },
        stored,
        "credential_slot_conflict",
        session,
      );
      const coordinated = await installations.findOneAndUpdate(
        { id: document.installationId, projectId: document.projectId },
        { $inc: { credentialStateRevision: 1 } },
        { returnDocument: "after", projection: { _id: 0 }, session },
      );
      if (coordinated === null) invalid("credential_slot_parent_mismatch");
    });
  }

  async putMigrationRecord(input: PluginMigrationRecord): Promise<void> {
    const document = migrationRecord(input);
    await immutableInsert(this.database.collection(PLUGIN_COLLECTIONS.migrationRecords), { id: document.id }, document as unknown as Captured, "migration_record_conflict");
  }

  async getMigrationRecord(id: string): Promise<PluginMigrationRecord | null> {
    if (!ID.test(id)) invalid("migration_record_invalid");
    const document = await this.database.collection(PLUGIN_COLLECTIONS.migrationRecords).findOne({ id }, { projection: { _id: 0 } });
    return document === null ? null : migrationRecord(document);
  }

  async putReceipt(input: PluginReceipt): Promise<void> {
    const document = receipt(input);
    const stored = Object.freeze({ receiptId: document.receiptId, payload: canonical(document as unknown as Captured) }) as unknown as Captured;
    await immutableInsert(this.database.collection(PLUGIN_COLLECTIONS.receipts), { receiptId: document.receiptId }, stored, "receipt_conflict");
  }

  async getReceipt(receiptId: string): Promise<PluginReceipt | null> {
    if (typeof receiptId !== "string" || !ID.test(receiptId)) invalid("receipt_invalid");
    const document = await this.database.collection(PLUGIN_COLLECTIONS.receipts).findOne({ receiptId }, { projection: { _id: 0 } });
    // Idempotency-scoped records carry a mutation envelope instead of a
    // standalone payload; they are replay state, not readable evidence.
    // A stored receipt keeps its canonical JSON string, exactly as putReceipt
    // wrote it, so it is parsed before it is validated.
    return document === null || document.payload === undefined ? null : receipt(parsePayload(document.payload, "receipt_invalid"));
  }

  async claimExecutionDispatch(input: PluginExecutionDispatchClaim): Promise<void> {
    const request = object(input, "execution_claim_invalid");
    keys(request, [
      "requestId", "catalogDigest", "projectId", "pluginName", "installationId", "installationRevision",
      "componentId", "componentDigest", "componentKind", "componentName", "providerBindingId", "providerKind",
      "admissionPolicyVersion", "credentialSlots",
    ], "execution_claim_invalid");
    const requestId = string(request, "requestId", ID, "execution_claim_invalid");
    const catalogDigest = string(request, "catalogDigest", DIGEST, "execution_claim_invalid");
    const projectId = string(request, "projectId", ID, "execution_claim_invalid");
    const pluginName = string(request, "pluginName", NAME, "execution_claim_invalid");
    const installationId = string(request, "installationId", ID, "execution_claim_invalid");
    const installationRevision = integer(request, "installationRevision", "execution_claim_invalid");
    const componentId = string(request, "componentId", COMPONENT_ID, "execution_claim_invalid");
    const componentDigest = string(request, "componentDigest", DIGEST, "execution_claim_invalid");
    const componentKind = string(request, "componentKind", /^(skill|agent|command|mcp|app|hook|asset)$/, "execution_claim_invalid");
    const componentName = string(request, "componentName", COMPONENT_NAME, "execution_claim_invalid");
    const providerBindingId = string(request, "providerBindingId", ID, "execution_claim_invalid");
    const providerKind = string(request, "providerKind", /^(mcp-http|mcp-process|rowboat-native|legacy-composio-adapter|openai-connector-bridge)$/, "execution_claim_invalid");
    const admissionPolicyVersion = string(request, "admissionPolicyVersion", ID, "execution_claim_invalid");
    if (!Array.isArray(request.credentialSlots) || request.credentialSlots.length > MAX_ITEMS) invalid("execution_claim_invalid");
    const credentialSlots = request.credentialSlots.map(credentialSlot).sort((left, right) => left.name.localeCompare(right.name));
    if (credentialSlots.some((slot) => slot.projectId !== projectId || slot.installationId !== installationId)) invalid("execution_claim_invalid");
    const credentialReferencesDigest = createHash("sha256")
      .update(canonical(credentialSlots as unknown as Captured))
      .digest("hex");
    const claimDocument = Object.freeze({
      requestId, catalogDigest, projectId, pluginName, installationId, installationRevision,
      componentId, componentDigest, componentKind, componentName, providerBindingId, providerKind,
      admissionPolicyVersion, credentialReferencesDigest,
    });
    assertStoredDocumentBudget(claimDocument as unknown as Captured);
    try {
      await this.transactions.run(async (session) => {
        const options = { projection: { _id: 0 }, session };
        const storedInstallation = await this.database.collection(PLUGIN_COLLECTIONS.installations).findOneAndUpdate(
          { id: installationId, projectId, pluginName, revision: installationRevision, enabled: true },
          { $set: { lastDispatchClaimId: requestId } },
          { returnDocument: "after", projection: { _id: 0 }, session },
        );
        if (storedInstallation === null) invalid("execution_claim_conflict");
        const currentInstallation = this.readInstallation(storedInstallation);
        const currentEntry = await this.requireCatalogEntryForInstallation(currentInstallation, session);
        if (currentEntry.catalogDigest !== catalogDigest) invalid("execution_claim_conflict");
        const selectedBindings = currentInstallation.providerBindings?.filter((selected) => selected.componentId === componentId) ?? [];
        if (
          selectedBindings.length !== 1
          || selectedBindings[0]?.binding.id !== providerBindingId
          || selectedBindings[0]?.binding.providerKind !== providerKind
          || selectedBindings[0]?.binding.componentDigest !== componentDigest
        ) invalid("execution_claim_conflict");
        const storedAdmission = await this.database.collection(PLUGIN_COLLECTIONS.componentAdmissions).findOne({
          installationId, componentDigest,
        }, options);
        if (storedAdmission === null) invalid("execution_claim_conflict");
        const currentAdmission = freezeRead<PluginComponentAdmission>(storedAdmission, "execution_claim_conflict");
        if (
          currentAdmission.status !== "admitted" || currentAdmission.componentKind !== componentKind
          || currentAdmission.componentName !== componentName || currentAdmission.policyVersion !== admissionPolicyVersion
          || currentAdmission.reason !== undefined
        ) invalid("execution_claim_conflict");
        const currentCredentialDocuments = await this.database.collection(PLUGIN_COLLECTIONS.credentialSlots)
          .find({ installationId }, options).sort({ name: 1 }).toArray();
        const currentCredentialSlots = currentCredentialDocuments.map((document) => credentialSlot(parsePayload(document.payload, "execution_claim_conflict")));
        if (canonical(currentCredentialSlots as unknown as Captured) !== canonical(credentialSlots as unknown as Captured)) invalid("execution_claim_conflict");
        await this.database.collection(PLUGIN_COLLECTIONS.executionClaims).insertOne(insertable(claimDocument), { session });
      });
    } catch (error) {
      if (isDuplicateKey(error)) invalid("execution_claim_conflict");
      throw error;
    }
  }

  async getIdempotentReceipt(input: PluginIdempotencyLookup): Promise<PluginReceipt | null> {
    const request = object(input, "idempotency_invalid");
    keys(request, ["scope", "fingerprint", "projectId", "pluginName", "catalogDigest", "operation"], "idempotency_invalid");
    const scope = string(request, "scope", DIGEST, "idempotency_invalid");
    const fingerprintValue = string(request, "fingerprint", DIGEST, "idempotency_invalid");
    const projectId = string(request, "projectId", ID, "idempotency_invalid");
    const pluginName = string(request, "pluginName", NAME, "idempotency_invalid");
    const catalogDigest = string(request, "catalogDigest", DIGEST, "idempotency_invalid");
    const operation = string(request, "operation", /^(install|set_enabled)$/, "idempotency_invalid") as "install" | "set_enabled";
    const existing = await this.database.collection(PLUGIN_COLLECTIONS.receipts)
      .findOne({ idempotencyScope: scope }, { projection: { _id: 0 } });
    if (existing === null) return null;
    if (existing.requestFingerprint !== fingerprintValue) invalid("idempotency_conflict");
    const parsed = parseMutationEnvelope(existing, { scope, fingerprint: fingerprintValue, projectId, pluginName, catalogDigest, operation });
    await this.validateReplayProvenance(parsed);
    return parsed.receipt;
  }

  async installIdempotently(input: PluginIdempotentInstall): Promise<PluginIdempotentInstallResult> {
    const request = object(input, "idempotent_install_invalid");
    keys(request, ["scope", "fingerprint", "catalogDigest", "installation", "admissions", "credentialSlots", "receipt"], "idempotent_install_invalid");
    const scope = string(request, "scope", DIGEST, "idempotent_install_invalid");
    const fingerprintValue = string(request, "fingerprint", DIGEST, "idempotent_install_invalid");
    const catalogDigest = string(request, "catalogDigest", DIGEST, "idempotent_install_invalid");
    const install = installation(request.installation);
    const admissionList = request.admissions;
    const slotList = request.credentialSlots;
    if (!Array.isArray(admissionList) || !Array.isArray(slotList)) invalid("idempotent_install_invalid");
    const admissions = admissionList.map(admission);
    const slots = slotList.map(credentialSlot);
    const storedReceipt = mutationReceipt(request.receipt);
    if (storedReceipt.projectId !== install.projectId || storedReceipt.pluginName !== install.pluginName) invalid("idempotent_install_invalid");
    if (admissions.some((item) => item.installationId !== install.id)) invalid("idempotent_install_invalid");
    if (slots.some((item) => item.installationId !== install.id || item.projectId !== install.projectId)) invalid("idempotent_install_invalid");
    const receiptCollection = this.database.collection(PLUGIN_COLLECTIONS.receipts);
    const replay = async (): Promise<PluginIdempotentInstallResult | null> => {
      const existing = await receiptCollection.findOne({ idempotencyScope: scope }, { projection: { _id: 0 } });
      if (existing === null) return null;
      if (existing.requestFingerprint !== fingerprintValue) invalid("idempotency_conflict");
      const parsed = parseMutationEnvelope(existing, {
        scope, fingerprint: fingerprintValue, projectId: install.projectId, pluginName: install.pluginName, catalogDigest, operation: "install",
      });
      await this.validateReplayProvenance(parsed);
      return Object.freeze({ receipt: parsed.receipt, fingerprint: fingerprintValue, replayed: true });
    };
    const existing = await replay();
    if (existing !== null) return existing;
    try {
      await this.transactions.run(async (session) => {
        const raced = await receiptCollection.findOne({ idempotencyScope: scope }, { projection: { _id: 0 }, session });
        if (raced !== null) {
          if (raced.requestFingerprint !== fingerprintValue) invalid("idempotency_conflict");
          return;
        }
        const selectedEntry = await this.requireCatalogEntryForInstallation(install, session);
        this.validateProviderBindings(install, selectedEntry);
        await this.database.collection(PLUGIN_COLLECTIONS.installations).insertOne(insertable(this.installationDocument(install)), { session });
        await this.validateAdmissionBatch(admissions, session);
        for (const item of admissions) {
          await this.database.collection(PLUGIN_COLLECTIONS.componentAdmissions).insertOne(insertable(item as unknown as StoredDocument), { session });
        }
        for (const slot of slots) {
          const stored = Object.freeze({ id: slot.id, installationId: slot.installationId, name: slot.name, payload: canonical(slot as unknown as Captured) });
          await this.database.collection(PLUGIN_COLLECTIONS.credentialSlots).insertOne(insertable(stored as unknown as StoredDocument), { session });
        }
        await receiptCollection.insertOne({
          receiptId: storedReceipt.receiptId, idempotencyScope: scope, requestFingerprint: fingerprintValue,
          envelopePayload: mutationEnvelopePayload({
            scope, fingerprint: fingerprintValue, projectId: install.projectId, pluginName: install.pluginName, catalogDigest, operation: "install",
          }, storedReceipt, install),
        }, { session });
      });
    } catch (error) {
      if (!isDuplicateKey(error)) throw error;
      const winner = await replay();
      if (winner !== null) return winner;
      invalid("installation_conflict");
    }
    const winner = await replay();
    if (winner !== null) return winner;
    invalid("repository_write_failed");
  }

  async setInstallationEnabledIdempotently(input: PluginIdempotentEnable): Promise<PluginIdempotentEnableResult> {
    const request = object(input, "idempotent_enable_invalid");
    keys(request, ["scope", "fingerprint", "projectId", "pluginName", "catalogDigest", "installationId", "enabled", "expectedRevision", "receipt"], "idempotent_enable_invalid");
    const scope = string(request, "scope", DIGEST, "idempotent_enable_invalid");
    const fingerprintValue = string(request, "fingerprint", DIGEST, "idempotent_enable_invalid");
    const projectId = string(request, "projectId", ID, "idempotent_enable_invalid");
    const pluginName = string(request, "pluginName", NAME, "idempotent_enable_invalid");
    const catalogDigest = string(request, "catalogDigest", DIGEST, "idempotent_enable_invalid");
    const installationId = string(request, "installationId", ID, "idempotent_enable_invalid");
    const enabled = boolean(request, "enabled", "idempotent_enable_invalid");
    const expectedRevision = integer(request, "expectedRevision", "idempotent_enable_invalid");
    const storedReceipt = mutationReceipt(request.receipt);
    if (storedReceipt.projectId !== projectId || storedReceipt.pluginName !== pluginName) invalid("idempotent_enable_invalid");
    const receiptCollection = this.database.collection(PLUGIN_COLLECTIONS.receipts);
    const installationCollection = this.database.collection(PLUGIN_COLLECTIONS.installations);
    const readResult = (document: StoredDocument): ParsedMutationEnvelope => {
      const parsed = parseMutationEnvelope(document, {
        scope, fingerprint: fingerprintValue, projectId, pluginName, catalogDigest, operation: "set_enabled",
      });
      if (
        parsed.installation.id !== installationId || parsed.installation.enabled !== enabled
        || parsed.installation.revision !== expectedRevision + 1
      ) invalid("idempotency_record_invalid");
      return parsed;
    };
    const replay = async (): Promise<PluginIdempotentEnableResult | null> => {
      const existingReceipt = await receiptCollection.findOne({ idempotencyScope: scope }, { projection: { _id: 0 } });
      if (existingReceipt === null) return null;
      if (existingReceipt.requestFingerprint !== fingerprintValue) invalid("idempotency_conflict");
      const parsed = readResult(existingReceipt);
      await this.validateReplayProvenance(parsed);
      return Object.freeze({ receipt: parsed.receipt, fingerprint: fingerprintValue, replayed: true, installation: parsed.installation });
    };
    const existing = await replay();
    if (existing !== null) return existing;
    let updatedInstallation: PluginInstallation | null = null;
    try {
      await this.transactions.run(async (session) => {
        const raced = await receiptCollection.findOne({ idempotencyScope: scope }, { projection: { _id: 0 }, session });
        if (raced !== null) {
          if (raced.requestFingerprint !== fingerprintValue) invalid("idempotency_conflict");
          return;
        }
        const current = await installationCollection.findOne({ id: installationId, projectId, pluginName }, { projection: { _id: 0 }, session });
        if (current === null) invalid("installation_not_found");
        const currentInstallation = this.readInstallation(current);
        await this.requireCatalogEntryForInstallation(currentInstallation, session);
        const updated = await installationCollection.findOneAndUpdate(
          { id: installationId, projectId, pluginName, revision: expectedRevision },
          { $set: { enabled }, $inc: { revision: 1 } },
          { returnDocument: "after", projection: { _id: 0 }, session },
        );
        if (updated === null) invalid("installation_conflict");
        updatedInstallation = this.readInstallation(updated);
        const receiptDocument = {
          receiptId: storedReceipt.receiptId, idempotencyScope: scope, requestFingerprint: fingerprintValue,
          envelopePayload: mutationEnvelopePayload({
            scope, fingerprint: fingerprintValue, projectId, pluginName, catalogDigest, operation: "set_enabled",
          }, storedReceipt, updatedInstallation),
        };
        assertStoredDocumentBudget(receiptDocument as unknown as Captured);
        await receiptCollection.insertOne(insertable(receiptDocument), { session });
      });
    } catch (error) {
      if (!isDuplicateKey(error)) throw error;
      const winner = await replay();
      if (winner !== null) return winner;
      invalid("installation_conflict");
    }
    if (updatedInstallation !== null) return Object.freeze({ receipt: storedReceipt, fingerprint: fingerprintValue, replayed: false, installation: updatedInstallation });
    const winner = await replay();
    if (winner !== null) return winner;
    invalid("repository_write_failed");
  }

  private async validateReplayProvenance(parsed: ParsedMutationEnvelope): Promise<void> {
    try {
      const catalog = await this.getCatalog(parsed.catalogDigest);
      if (catalog === null) invalid("idempotency_record_invalid");
      let entry: (typeof catalog.entries)[number] | undefined;
      for (let index = 0; index < catalog.entries.length; index += 1) {
        const candidate = catalog.entries[index];
        if (candidate !== undefined && candidate.pluginName === parsed.installation.pluginName) { entry = candidate; break; }
      }
      if (
        entry === undefined
        || entry.pluginVersion !== parsed.installation.pluginVersion
        || entry.sourceCommit !== parsed.installation.sourceCommit
        || entry.manifestDigest !== parsed.installation.manifestDigest
        || entry.treeDigest !== parsed.installation.treeDigest
        || entry.policyVersion !== parsed.installation.policyVersion
      ) invalid("idempotency_record_invalid");
      const currentDocument = await this.database.collection(PLUGIN_COLLECTIONS.installations)
        .findOne({ id: parsed.installation.id, projectId: parsed.installation.projectId, pluginName: parsed.installation.pluginName }, { projection: { _id: 0 } });
      if (currentDocument === null) invalid("idempotency_record_invalid");
      const current = this.readInstallation(currentDocument);
      if (canonical(mutationSource(current) as Captured) !== canonical(mutationSource(parsed.installation) as Captured)) invalid("idempotency_record_invalid");
    } catch {
      invalid("idempotency_record_invalid");
    }
  }

  private installationDocument(document: PluginInstallation): StoredDocument {
    return { ...serializePluginInstallationDocument(document) };
  }

  private readInstallation(stored: StoredDocument): PluginInstallation {
    return deserializePluginInstallationDocument(stored);
  }

  private async classifyCatalogEntryRace(documents: readonly PluginCatalogEntry[]): Promise<void> {
    const collection = this.database.collection(PLUGIN_COLLECTIONS.catalogEntries);
    for (const document of documents) {
      const existing = await collection.findOne(
        { catalogDigest: document.catalogDigest, name: document.name },
        { projection: { _id: 0 } },
      );
      if (existing === null) invalid("catalog_entry_conflict");
      let matchesExpected = false;
      try {
        const decoded = catalogEntry(parsePayload(existing.payload, "catalog_entry_invalid"));
        matchesExpected = canonical(decoded as unknown as Captured) === canonical(document as unknown as Captured);
      } catch {
        matchesExpected = false;
      }
      if (!matchesExpected) invalid("catalog_entry_conflict");
    }
  }

  private async classifyAdmissionRace(documents: readonly PluginComponentAdmission[]): Promise<void> {
    const collection = this.database.collection(PLUGIN_COLLECTIONS.componentAdmissions);
    for (const document of documents) {
      const existing = await collection.findOne(
        { installationId: document.installationId, componentDigest: document.componentDigest },
        { projection: { _id: 0 } },
      );
      if (existing === null) invalid("admission_conflict");
      let matchesExpected = false;
      try {
        matchesExpected = canonical(freezeRead<PluginComponentAdmission>(existing, "admission_invalid") as unknown as Captured)
          === canonical(document as unknown as Captured);
      } catch {
        matchesExpected = false;
      }
      if (!matchesExpected) invalid("admission_conflict");
    }
  }

  private validateProviderBindings(document: PluginInstallation, entry: PluginCatalogEntry): void {
    for (const selectedBinding of document.providerBindings ?? []) {
      const selected = entry.components.find(({ component }) => component.id === selectedBinding.componentId)?.component;
      const binding = selectedBinding.binding;
      if (selected === undefined || selected.metadata.bindingDigest !== binding.componentDigest) invalid("installation_source_mismatch");
      const mcpTransport = binding.providerKind === "mcp-http"
        ? "http"
        : binding.providerKind === "mcp-process" ? "process" : undefined;
      if (binding.pairedComponentDigests !== undefined) {
        const paired = entry.components.find(({ component }) => component.metadata.bindingDigest === binding.pairedComponentDigests?.[1])?.component;
        if (
          mcpTransport === undefined
          || selected.kind !== "app"
          || binding.pairedComponentDigests[0] !== binding.componentDigest
          || paired?.kind !== "mcp"
          || paired.metadata.transport !== mcpTransport
        ) invalid("installation_source_mismatch");
      } else if (mcpTransport !== undefined) {
        if (selected.kind !== "mcp" || selected.metadata.transport !== mcpTransport) invalid("installation_source_mismatch");
      } else if (selected.kind !== "app" && selected.kind !== "mcp") {
        invalid("installation_source_mismatch");
      }
    }
  }

  private async getCatalogSnapshotWithSession(digest: string, session: ClientSession | undefined): Promise<PluginCatalogSnapshot | null> {
    const options = session === undefined ? { projection: { _id: 0 } } : { projection: { _id: 0 }, session };
    const stored = await this.database.collection(PLUGIN_COLLECTIONS.catalogSnapshots).findOne({ catalogDigest: digest }, options);
    return stored === null ? null : catalogSnapshot(parsePayload(stored.payload, "catalog_snapshot_invalid"));
  }

  private async validateCatalogEntryBatch(documents: readonly PluginCatalogEntry[], session: ClientSession | undefined): Promise<void> {
    const collection = this.database.collection(PLUGIN_COLLECTIONS.catalogEntries);
    for (const document of documents) {
      const snapshot = await this.getCatalogSnapshotWithSession(document.catalogDigest, session);
      if (snapshot === null) invalid("catalog_snapshot_not_found");
      if (
        document.sourceUrl !== snapshot.sourceUrl
        || document.sourceCommit !== snapshot.sourceCommit
        || document.importedAt !== snapshot.importedAt
        || document.schemaVersion !== snapshot.schemaVersion
        || document.policyVersion !== snapshot.policyVersion
      ) invalid("catalog_entry_mismatch");
      const options = session === undefined ? { projection: { _id: 0 } } : { projection: { _id: 0 }, session };
      const existing = await collection.findOne({ catalogDigest: document.catalogDigest, name: document.name }, options);
      if (existing !== null) {
        const decoded = catalogEntry(parsePayload(existing.payload, "catalog_entry_invalid"));
        if (canonical(decoded as unknown as Captured) !== canonical(document as unknown as Captured)) invalid("catalog_entry_conflict");
      }
    }
  }

  private async catalogEntriesByName(name: string, session: ClientSession | undefined): Promise<readonly PluginCatalogEntry[]> {
    const options = session === undefined ? { projection: { _id: 0 } } : { projection: { _id: 0 }, session };
    const documents = await this.database.collection(PLUGIN_COLLECTIONS.catalogEntries).find({ name }, options).sort({ catalogDigest: 1 }).toArray();
    return documents.map((document) => catalogEntry(parsePayload(document.payload, "catalog_entry_invalid")));
  }

  private async requireCatalogEntryForInstallation(document: PluginInstallation, session: ClientSession | undefined): Promise<PluginCatalogEntry> {
    const entries = await this.catalogEntriesByName(document.pluginName, session);
    const matches = entries.filter((entry) =>
      entry.pluginName === document.pluginName
      && entry.pluginVersion === document.pluginVersion
      && entry.sourceCommit === document.sourceCommit
      && entry.manifestDigest === document.manifestDigest
      && entry.treeDigest === document.treeDigest
      && entry.policyVersion === document.policyVersion
    );
    if (matches.length !== 1) invalid("installation_catalog_mismatch");
    return matches[0]!;
  }

  private async validateAdmissionBatch(documents: readonly PluginComponentAdmission[], session: ClientSession | undefined): Promise<void> {
    const installations = this.database.collection(PLUGIN_COLLECTIONS.installations);
    const admissions = this.database.collection(PLUGIN_COLLECTIONS.componentAdmissions);
    for (const document of documents) {
      const options = session === undefined ? { projection: { _id: 0 } } : { projection: { _id: 0 }, session };
      const parent = await installations.findOne({ id: document.installationId }, options);
      if (parent === null) invalid("admission_parent_not_found");
      const entry = await this.requireCatalogEntryForInstallation(this.readInstallation(parent), session);
      const selected = entry.components.find(({ component }) => component.metadata.bindingDigest === document.componentDigest);
      if (
        selected === undefined
        || selected.component.kind !== document.componentKind
        || selected.component.name !== document.componentName
        || selected.admission.status !== document.status
        || selected.admission.policyVersion !== document.policyVersion
        || (selected.admission.status === "admitted" ? document.reason !== undefined : selected.admission.reason !== document.reason)
      ) invalid("admission_binding_mismatch");
      const existing = await admissions.findOne({ installationId: document.installationId, componentDigest: document.componentDigest }, options);
      if (existing !== null && canonical(freezeRead<PluginComponentAdmission>(existing, "admission_invalid") as unknown as Captured) !== canonical(document as unknown as Captured)) {
        invalid("admission_conflict");
      }
    }
  }
}
