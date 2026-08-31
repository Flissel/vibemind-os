import { createHash, randomUUID } from "node:crypto";
import { isProxy } from "node:util/types";
import {
  buildReceipt,
  DEFAULT_POLICY,
  evaluateCapability,
  PINNED_PLUGIN_CATALOG_DIGEST,
  validatePluginCatalogLock,
  type CatalogBoundPluginComponent,
  type PluginCatalogEntry,
  type PluginPolicy,
  type PluginProvider,
  type ProviderBinding,
  type ProviderResolution,
  type ProviderResult,
} from "@rowboat/openai-plugin-runtime";
import type {
  IPluginsRepository,
  PluginComponentAdmission,
  PluginCredentialSlot,
  PluginInstallation,
} from "@/src/application/repositories/plugins.repository.interface";
import type { IPluginWriteReleasePolicy, PluginWriteReleaseDecision } from "@/src/application/policies/plugin-write-release.policy";

const BINDING_KEYS = Object.freeze([
  "capability", "componentDigest", "installationId", "pluginName", "providerBindingId",
]);
const CONTEXT_KEYS = Object.freeze(["operationName", "projectId", "signal"]);
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const IDENTIFIER = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/;
const OPERATION = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/;
const DIGEST = /^[a-f0-9]{64}$/;
const MAX_ARGUMENT_NODES = 1024;
const MAX_ARGUMENT_DEPTH = 16;
const MAX_ARGUMENT_BYTES = 64 * 1024;
const DEFAULT_TIMEOUT_MILLISECONDS = 30_000;
const DEFAULT_RECEIPT_TIMEOUT_MILLISECONDS = 100;
const DANGEROUS_KEYS = new Set(["__proto__", "constructor", "prototype"]);
const ARGUMENTS_DIGEST_DOMAIN = "rowboat:plugin-tool-runtime:arguments:v1";
/**
 * The exact, closed set of kernel-reported failure reasons this runtime
 * trusts enough to surface as themselves, each mapped explicitly to its own
 * app error code -- a member never silently relabels itself as some other
 * fixed code the way a bare Set would. Adding a reason here whose value is
 * not a real `PluginToolRuntimeErrorCode` (a typo, or a code that does not
 * exist) is a typecheck error, not a silent relabel: the map's value type is
 * pinned to that union. A provider is untrusted input: any reason string
 * that is not a byte-exact match for a key here -- whatever its shape --
 * normalises to "provider_failed" instead. This is a fixed constant, never
 * derived from what the provider sent. An over-long reason never reaches
 * this lookup at all: captureProviderResult's own 4096-byte guard on
 * `reason` runs first and throws "provider_result_invalid" before
 * mapProviderFailureReason is ever called.
 */
const KNOWN_PROVIDER_FAILURE_REASONS: ReadonlyMap<string, PluginToolRuntimeErrorCode> = new Map([
  ["credential_missing", "credential_missing"],
]);

export interface PluginToolBindingValue {
  readonly installationId: string;
  readonly pluginName: string;
  readonly componentDigest: string;
  readonly providerBindingId: string;
  readonly capability: "read" | "write";
}

export interface PluginToolInvocationContext {
  readonly projectId: string;
  readonly operationName: string;
  readonly signal?: AbortSignal;
}

export type PluginToolAuthorizationContext = Readonly<
  | { readonly caller: "user"; readonly userId: string }
  | { readonly caller: "api"; readonly apiKey: string }
>;

export interface PluginProviderResolutionInput {
  readonly catalog: ReturnType<typeof validatePluginCatalogLock>;
  readonly entry: PluginCatalogEntry;
  readonly component: CatalogBoundPluginComponent;
  readonly installation: PluginInstallation;
  readonly binding: ProviderBinding;
  readonly credentialSlots: readonly PluginCredentialSlot[];
  readonly signal: AbortSignal;
  readonly policy: PluginPolicy;
}

export interface PluginToolRuntimeDependencies {
  readonly pluginsRepository: IPluginsRepository;
  readonly authorizationContext?: PluginToolAuthorizationContext;
  readonly authorizeProject: (authorization: PluginToolAuthorizationContext, projectId: string) => Promise<void>;
  readonly classifyOperation: (input: Readonly<{
    readonly pluginName: string;
    readonly component: CatalogBoundPluginComponent;
    readonly operationName: string;
  }>) => "read" | "write";
  readonly resolveProvider: (input: PluginProviderResolutionInput) => Promise<ProviderResolution>;
  /**
   * Releases one write. A write stays under review unless a decision for this
   * exact call approves it; the decision is never cached.
   */
  readonly releaseWrite?: IPluginWriteReleasePolicy["release"];
  readonly timeoutMilliseconds?: number;
  readonly receiptTimeoutMilliseconds?: number;
  readonly createRequestId?: () => string;
  readonly onCancel?: () => void;
}

export type PluginToolRuntimeErrorCode =
  | "binding_invalid"
  | "request_invalid"
  | "authorization_context_missing"
  | "authorization_denied"
  | "execution_state_invalid"
  | "execution_state_changed"
  | "catalog_unavailable"
  | "catalog_invalid"
  | "installation_unavailable"
  | "installation_mismatch"
  | "component_unavailable"
  | "admission_denied"
  | "capability_mismatch"
  | "write_review_required"
  | "credential_invalid"
  | "credential_missing"
  | "provider_unavailable"
  | "provider_failed"
  | "provider_result_invalid"
  | "provider_timed_out"
  | "request_aborted"
  | "receipt_unavailable";

export class PluginToolRuntimeError extends Error {
  readonly code: PluginToolRuntimeErrorCode;

  constructor(code: PluginToolRuntimeErrorCode) {
    super(code);
    this.name = "PluginToolRuntimeError";
    this.code = code;
  }
}

interface CaptureBudget {
  nodes: number;
  bytes: number;
  readonly seen: Set<object>;
}

function ownDataRecord(input: unknown, keys: readonly string[], code: PluginToolRuntimeErrorCode): Readonly<Record<string, unknown>> {
  if (input === null || typeof input !== "object" || Array.isArray(input) || isProxy(input)) throw new PluginToolRuntimeError(code);
  const prototype = Object.getPrototypeOf(input);
  if (prototype !== Object.prototype && prototype !== null) throw new PluginToolRuntimeError(code);
  if (Object.getOwnPropertySymbols(input).length !== 0) throw new PluginToolRuntimeError(code);
  const descriptors = Object.getOwnPropertyDescriptors(input);
  if (Object.keys(descriptors).sort().join("\0") !== [...keys].sort().join("\0")) throw new PluginToolRuntimeError(code);
  const result: Record<string, unknown> = Object.create(null) as Record<string, unknown>;
  for (const key of keys) {
    const descriptor = descriptors[key];
    if (descriptor === undefined || !("value" in descriptor) || !descriptor.enumerable) throw new PluginToolRuntimeError(code);
    result[key] = descriptor.value;
  }
  return Object.freeze(result);
}

function captureBinding(input: unknown): PluginToolBindingValue {
  const record = ownDataRecord(input, BINDING_KEYS, "binding_invalid");
  if (
    typeof record.installationId !== "string" || !UUID.test(record.installationId)
    || typeof record.pluginName !== "string" || !IDENTIFIER.test(record.pluginName)
    || typeof record.componentDigest !== "string" || !DIGEST.test(record.componentDigest)
    || typeof record.providerBindingId !== "string" || !IDENTIFIER.test(record.providerBindingId)
    || (record.capability !== "read" && record.capability !== "write")
  ) throw new PluginToolRuntimeError("binding_invalid");
  return Object.freeze({
    installationId: record.installationId,
    pluginName: record.pluginName,
    componentDigest: record.componentDigest,
    providerBindingId: record.providerBindingId,
    capability: record.capability,
  });
}

function captureContext(input: unknown): PluginToolInvocationContext {
  if (input === null || typeof input !== "object" || Array.isArray(input) || isProxy(input)) throw new PluginToolRuntimeError("request_invalid");
  const descriptors = Object.getOwnPropertyDescriptors(input);
  if (Object.getPrototypeOf(input) !== Object.prototype || Object.getOwnPropertySymbols(input).length !== 0) throw new PluginToolRuntimeError("request_invalid");
  const present = Object.keys(descriptors).sort();
  if (present.some((key) => !CONTEXT_KEYS.includes(key)) || !present.includes("projectId") || !present.includes("operationName")) {
    throw new PluginToolRuntimeError("request_invalid");
  }
  for (const descriptor of Object.values(descriptors)) {
    if (!("value" in descriptor) || !descriptor.enumerable) throw new PluginToolRuntimeError("request_invalid");
  }
  const projectId = descriptors.projectId!.value as unknown;
  const operationName = descriptors.operationName!.value as unknown;
  const signal = descriptors.signal?.value as unknown;
  if (
    typeof projectId !== "string" || !IDENTIFIER.test(projectId)
    || typeof operationName !== "string" || !OPERATION.test(operationName)
    || (signal !== undefined && !(signal instanceof AbortSignal))
  ) throw new PluginToolRuntimeError("request_invalid");
  return Object.freeze({ projectId, operationName, ...(signal === undefined ? {} : { signal }) });
}

function captureJson(input: unknown, depth: number, budget: CaptureBudget, code: PluginToolRuntimeErrorCode): unknown {
  budget.nodes += 1;
  if (budget.nodes > MAX_ARGUMENT_NODES || depth > MAX_ARGUMENT_DEPTH) throw new PluginToolRuntimeError(code);
  if (typeof input === "string") {
    budget.bytes += Buffer.byteLength(input, "utf8") + 2;
    if (budget.bytes > MAX_ARGUMENT_BYTES) throw new PluginToolRuntimeError(code);
    return input;
  }
  if (input === null || typeof input === "boolean") return input;
  if (typeof input === "number" && Number.isFinite(input)) return input;
  if (typeof input !== "object" || isProxy(input) || budget.seen.has(input)) throw new PluginToolRuntimeError(code);
  budget.seen.add(input);
  try {
    if (Array.isArray(input)) {
      if (Object.getPrototypeOf(input) !== Array.prototype || input.length > MAX_ARGUMENT_NODES) throw new PluginToolRuntimeError(code);
      const descriptors = Object.getOwnPropertyDescriptors(input);
      const values: unknown[] = [];
      for (let index = 0; index < input.length; index += 1) {
        const descriptor = descriptors[String(index)];
        if (descriptor === undefined || !("value" in descriptor) || !descriptor.enumerable) throw new PluginToolRuntimeError(code);
        values.push(captureJson(descriptor.value, depth + 1, budget, code));
      }
      if (Reflect.ownKeys(input).length !== input.length + 1) throw new PluginToolRuntimeError(code);
      return Object.freeze(values);
    }
    const prototype = Object.getPrototypeOf(input);
    if (prototype !== Object.prototype && prototype !== null) throw new PluginToolRuntimeError(code);
    const descriptors = Object.getOwnPropertyDescriptors(input);
    if (Object.getOwnPropertySymbols(input).length !== 0) throw new PluginToolRuntimeError(code);
    const captured: Record<string, unknown> = Object.create(null) as Record<string, unknown>;
    for (const [key, descriptor] of Object.entries(descriptors)) {
      if (DANGEROUS_KEYS.has(key) || !("value" in descriptor) || !descriptor.enumerable) throw new PluginToolRuntimeError(code);
      budget.bytes += Buffer.byteLength(key, "utf8") + 3;
      if (budget.bytes > MAX_ARGUMENT_BYTES) throw new PluginToolRuntimeError(code);
      captured[key] = captureJson(descriptor.value, depth + 1, budget, code);
    }
    return Object.freeze(captured);
  } finally {
    budget.seen.delete(input);
  }
}

function captureArguments(input: unknown): Readonly<Record<string, unknown>> {
  const captured = captureJson(input, 0, { nodes: 0, bytes: 0, seen: new Set() }, "request_invalid");
  if (captured === null || typeof captured !== "object" || Array.isArray(captured)) throw new PluginToolRuntimeError("request_invalid");
  return captured as Readonly<Record<string, unknown>>;
}

function captureAuthorization(input: unknown): PluginToolAuthorizationContext {
  if (input === undefined) throw new PluginToolRuntimeError("authorization_context_missing");
  if (input === null || typeof input !== "object" || Array.isArray(input) || isProxy(input)) throw new PluginToolRuntimeError("authorization_context_missing");
  const descriptors = Object.getOwnPropertyDescriptors(input);
  if (Object.getPrototypeOf(input) !== Object.prototype || Object.getOwnPropertySymbols(input).length !== 0) throw new PluginToolRuntimeError("authorization_context_missing");
  const callerDescriptor = descriptors.caller;
  if (callerDescriptor === undefined || !("value" in callerDescriptor) || !callerDescriptor.enumerable) throw new PluginToolRuntimeError("authorization_context_missing");
  const caller = callerDescriptor.value as unknown;
  const expected = caller === "user" ? ["caller", "userId"] : caller === "api" ? ["apiKey", "caller"] : [];
  if (Object.keys(descriptors).sort().join("\0") !== expected.join("\0")) throw new PluginToolRuntimeError("authorization_context_missing");
  const valueDescriptor = caller === "user" ? descriptors.userId : descriptors.apiKey;
  if (valueDescriptor === undefined || !("value" in valueDescriptor) || !valueDescriptor.enumerable) throw new PluginToolRuntimeError("authorization_context_missing");
  const value = valueDescriptor.value as unknown;
  if (typeof value !== "string" || value.length < 1 || value.length > 4096 || value.includes("\0")) throw new PluginToolRuntimeError("authorization_context_missing");
  return caller === "user"
    ? Object.freeze({ caller, userId: value })
    : Object.freeze({ caller: "api" as const, apiKey: value });
}

function exactKeys(record: Readonly<Record<string, unknown>>, required: readonly string[], optional: readonly string[] = []): void {
  const keys = Object.keys(record);
  if (required.some((key) => !keys.includes(key)) || keys.some((key) => !required.includes(key) && !optional.includes(key))) {
    throw new PluginToolRuntimeError("execution_state_invalid");
  }
}

function captureInstallation(input: unknown): PluginInstallation {
  const captured = captureJson(input, 0, { nodes: 0, bytes: 0, seen: new Set() }, "execution_state_invalid") as Readonly<Record<string, unknown>>;
  if (captured === null || typeof captured !== "object" || Array.isArray(captured)) throw new PluginToolRuntimeError("execution_state_invalid");
  exactKeys(captured, ["id", "projectId", "pluginName", "pluginVersion", "sourceCommit", "manifestDigest", "treeDigest", "policyVersion", "enabled", "revision"], ["providerBindings"]);
  for (const key of ["id", "projectId", "pluginName", "pluginVersion", "sourceCommit", "manifestDigest", "treeDigest", "policyVersion"] as const) {
    if (typeof captured[key] !== "string") throw new PluginToolRuntimeError("execution_state_invalid");
  }
  if (
    !IDENTIFIER.test(captured.id as string) || !IDENTIFIER.test(captured.projectId as string) || !IDENTIFIER.test(captured.pluginName as string)
    || !DIGEST.test(captured.manifestDigest as string) || !DIGEST.test(captured.treeDigest as string)
    || !/^[a-f0-9]{40}$/.test(captured.sourceCommit as string)
    || typeof captured.enabled !== "boolean" || !Number.isSafeInteger(captured.revision) || (captured.revision as number) < 0
  ) throw new PluginToolRuntimeError("execution_state_invalid");
  const bindings = captured.providerBindings;
  if (bindings !== undefined) {
    if (!Array.isArray(bindings) || bindings.length > 256) throw new PluginToolRuntimeError("execution_state_invalid");
    for (const selected of bindings as readonly unknown[]) {
      if (selected === null || typeof selected !== "object" || Array.isArray(selected)) throw new PluginToolRuntimeError("execution_state_invalid");
      const selectedRecord = selected as Readonly<Record<string, unknown>>;
      exactKeys(selectedRecord, ["binding", "componentId"]);
      if (typeof selectedRecord.componentId !== "string") throw new PluginToolRuntimeError("execution_state_invalid");
      const provider = selectedRecord.binding;
      if (provider === null || typeof provider !== "object" || Array.isArray(provider)) throw new PluginToolRuntimeError("execution_state_invalid");
      const providerRecord = provider as Readonly<Record<string, unknown>>;
      exactKeys(providerRecord, ["componentDigest", "id", "providerKind"], ["pairedComponentDigests", "temporaryAdapter"]);
      if (
        typeof providerRecord.id !== "string" || !IDENTIFIER.test(providerRecord.id)
        || typeof providerRecord.componentDigest !== "string" || !DIGEST.test(providerRecord.componentDigest)
        || typeof providerRecord.providerKind !== "string"
        || !["mcp-http", "mcp-process", "rowboat-native", "legacy-composio-adapter", "openai-connector-bridge"].includes(providerRecord.providerKind)
        || (providerRecord.temporaryAdapter !== undefined && providerRecord.temporaryAdapter !== true)
      ) throw new PluginToolRuntimeError("execution_state_invalid");
      if (providerRecord.pairedComponentDigests !== undefined && (
        !Array.isArray(providerRecord.pairedComponentDigests) || providerRecord.pairedComponentDigests.length !== 2
        || !providerRecord.pairedComponentDigests.every((digest) => typeof digest === "string" && DIGEST.test(digest))
        || providerRecord.pairedComponentDigests[0] !== providerRecord.componentDigest
      )) throw new PluginToolRuntimeError("execution_state_invalid");
    }
  }
  return captured as unknown as PluginInstallation;
}

function captureAdmissions(input: unknown): readonly PluginComponentAdmission[] {
  const captured = captureJson(input, 0, { nodes: 0, bytes: 0, seen: new Set() }, "execution_state_invalid");
  if (!Array.isArray(captured) || captured.length > 256) throw new PluginToolRuntimeError("execution_state_invalid");
  for (const admission of captured) {
    if (admission === null || typeof admission !== "object" || Array.isArray(admission)) throw new PluginToolRuntimeError("execution_state_invalid");
    const record = admission as Readonly<Record<string, unknown>>;
    exactKeys(record, ["componentDigest", "componentKind", "componentName", "installationId", "policyVersion", "status"], ["reason"]);
    if (
      typeof record.installationId !== "string" || !IDENTIFIER.test(record.installationId)
      || typeof record.componentDigest !== "string" || !DIGEST.test(record.componentDigest)
      || typeof record.componentKind !== "string" || !["skill", "agent", "command", "mcp", "app", "hook", "asset"].includes(record.componentKind)
      || typeof record.componentName !== "string" || typeof record.policyVersion !== "string"
      || typeof record.status !== "string" || !["admitted", "review_required", "rejected"].includes(record.status)
      || (record.status === "admitted" ? record.reason !== undefined : typeof record.reason !== "string")
    ) throw new PluginToolRuntimeError("execution_state_invalid");
  }
  return captured as unknown as readonly PluginComponentAdmission[];
}

function captureCredentialSlots(input: unknown): readonly PluginCredentialSlot[] {
  const captured = captureJson(input, 0, { nodes: 0, bytes: 0, seen: new Set() }, "execution_state_invalid");
  if (!Array.isArray(captured) || captured.length > 256) throw new PluginToolRuntimeError("execution_state_invalid");
  for (const slot of captured) {
    if (slot === null || typeof slot !== "object" || Array.isArray(slot)) throw new PluginToolRuntimeError("execution_state_invalid");
    const record = slot as Readonly<Record<string, unknown>>;
    exactKeys(record, ["id", "installationId", "name", "projectId", "reference"], ["metadata"]);
    if (
      typeof record.id !== "string" || !IDENTIFIER.test(record.id)
      || typeof record.installationId !== "string" || !IDENTIFIER.test(record.installationId)
      || typeof record.projectId !== "string" || !IDENTIFIER.test(record.projectId)
      || typeof record.name !== "string" || !/^[A-Z][A-Z0-9_]{0,127}$/.test(record.name)
      || record.reference === null || typeof record.reference !== "object" || Array.isArray(record.reference)
    ) throw new PluginToolRuntimeError("execution_state_invalid");
    const reference = record.reference as Readonly<Record<string, unknown>>;
    exactKeys(reference, ["kind", "reference"]);
    if (
      typeof reference.kind !== "string" || !["bearer", "oauth", "environment"].includes(reference.kind)
      || typeof reference.reference !== "string" || reference.reference.length < 1 || reference.reference.length > 4096 || reference.reference.includes("\0")
    ) throw new PluginToolRuntimeError("execution_state_invalid");
    if (record.metadata !== undefined) {
      if (record.metadata === null || typeof record.metadata !== "object" || Array.isArray(record.metadata)) throw new PluginToolRuntimeError("execution_state_invalid");
      const metadata = record.metadata as Readonly<Record<string, unknown>>;
      exactKeys(metadata, [], ["label", "order", "required"]);
      if (
        (metadata.label !== undefined && (typeof metadata.label !== "string" || metadata.label.length > 256 || metadata.label.includes("\0")))
        || (metadata.required !== undefined && typeof metadata.required !== "boolean")
        || (metadata.order !== undefined && (!Number.isSafeInteger(metadata.order) || (metadata.order as number) < 0))
      ) throw new PluginToolRuntimeError("execution_state_invalid");
    }
  }
  return captured as unknown as readonly PluginCredentialSlot[];
}

// A reason that exactly matches a key in KNOWN_PROVIDER_FAILURE_REASONS
// surfaces as *that entry's own mapped code* -- not a fixed relabel shared by
// every member -- so a future second entry cannot be silently reported as
// "credential_missing". Anything that is not an exact match -- unrecognised,
// empty, or otherwise -- normalises to "provider_failed". Never derived from
// the input beyond the lookup itself. An over-long reason is not among these:
// it is refused earlier, by captureProviderResult's own byte-length guard on
// `reason`, which raises "provider_result_invalid" before this function is
// ever reached -- see the test at the 4097-byte boundary.
function mapProviderFailureReason(reason: string): PluginToolRuntimeErrorCode {
  return KNOWN_PROVIDER_FAILURE_REASONS.get(reason) ?? "provider_failed";
}

function captureProviderResult(input: unknown): ProviderResult {
  if (input === null || typeof input !== "object" || Array.isArray(input) || isProxy(input)) throw new PluginToolRuntimeError("provider_result_invalid");
  const descriptors = Object.getOwnPropertyDescriptors(input);
  if (Object.getPrototypeOf(input) !== Object.prototype || Object.getOwnPropertySymbols(input).length !== 0) throw new PluginToolRuntimeError("provider_result_invalid");
  const statusDescriptor = descriptors.status;
  if (statusDescriptor === undefined || !("value" in statusDescriptor) || !statusDescriptor.enumerable) throw new PluginToolRuntimeError("provider_result_invalid");
  if (statusDescriptor.value === "success" && Object.keys(descriptors).sort().join("\0") === ["output", "status"].join("\0")) {
    const outputDescriptor = descriptors.output;
    if (outputDescriptor === undefined || !("value" in outputDescriptor) || !outputDescriptor.enumerable) throw new PluginToolRuntimeError("provider_result_invalid");
    const output = captureJson(outputDescriptor.value, 0, { nodes: 0, bytes: 0, seen: new Set() }, "provider_result_invalid");
    return Object.freeze({ status: "success", output });
  }
  if (statusDescriptor.value === "failed" && Object.keys(descriptors).sort().join("\0") === ["reason", "status"].join("\0")) {
    const reason = descriptors.reason;
    if (reason === undefined || !("value" in reason) || !reason.enumerable || typeof reason.value !== "string" || Buffer.byteLength(reason.value, "utf8") > 4096) {
      throw new PluginToolRuntimeError("provider_result_invalid");
    }
    return Object.freeze({ status: "failed", reason: mapProviderFailureReason(reason.value) });
  }
  throw new PluginToolRuntimeError("provider_result_invalid");
}

function signature(value: unknown): string {
  return JSON.stringify(value);
}

// Local copy of the domain-separated digest idiom used elsewhere in this
// codebase (legacy-plugin-migration.ts, legacy-plugin-recipes.ts,
// plugin-migration.shared.ts). Kept local rather than imported from
// use-cases/ because a service must not depend on a use-case module.
function canonical(value: unknown): string {
  if (value === null || typeof value === "boolean" || typeof value === "string" || typeof value === "number") return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  const record = value as Readonly<Record<string, unknown>>;
  return `{${Object.keys(record).sort().map(key => `${JSON.stringify(key)}:${canonical(record[key])}`).join(",")}}`;
}

function domainDigest(domain: string, value: unknown): string {
  return createHash("sha256").update(domain).update("\0").update(canonical(value)).digest("hex");
}

function exactCatalog(input: unknown): ReturnType<typeof validatePluginCatalogLock> {
  try {
    const value = validatePluginCatalogLock(input);
    if (value.catalogDigest !== PINNED_PLUGIN_CATALOG_DIGEST || value.policyVersion !== DEFAULT_POLICY.version) {
      throw new Error("catalog_invalid");
    }
    return value;
  } catch {
    throw new PluginToolRuntimeError("catalog_invalid");
  }
}

function exactEntry(catalog: ReturnType<typeof validatePluginCatalogLock>, installation: PluginInstallation): PluginCatalogEntry {
  const matches = catalog.entries.filter((candidate) => candidate.name === installation.pluginName);
  const entry = matches.length === 1 ? matches[0] : undefined;
  if (
    entry === undefined || entry.admission.status !== "admitted"
    || installation.sourceCommit !== entry.sourceCommit
    || installation.pluginVersion !== entry.pluginVersion
    || installation.manifestDigest !== entry.manifestDigest
    || installation.treeDigest !== entry.treeDigest
    || installation.policyVersion !== entry.policyVersion
  ) throw new PluginToolRuntimeError("installation_mismatch");
  return entry;
}

function exactProvider(providerInput: ProviderResolution, binding: ProviderBinding): PluginProvider {
  if (
    providerInput === null || typeof providerInput !== "object" || isProxy(providerInput)
    || (Object.getPrototypeOf(providerInput) !== Object.prototype && Object.getPrototypeOf(providerInput) !== null)
    || !Object.isFrozen(providerInput) || Object.getOwnPropertySymbols(providerInput).length !== 0
  ) throw new PluginToolRuntimeError("provider_unavailable");
  const resolutionDescriptors = Object.getOwnPropertyDescriptors(providerInput);
  const resolutionKeys = Object.keys(resolutionDescriptors).sort().join("\0");
  let provider: PluginProvider | undefined;
  if (resolutionKeys === ["provider", "status"].join("\0")) {
    const status = resolutionDescriptors.status;
    const selected = resolutionDescriptors.provider;
    if (
      status === undefined || selected === undefined || !("value" in status) || !("value" in selected)
      || !status.enumerable || !selected.enumerable || status.value !== "available"
    ) throw new PluginToolRuntimeError("provider_unavailable");
    provider = selected.value as PluginProvider;
  } else if (resolutionKeys === ["reason", "status"].join("\0")) {
    throw new PluginToolRuntimeError("provider_unavailable");
  } else throw new PluginToolRuntimeError("provider_unavailable");
  if (provider === undefined || isProxy(provider) || Object.getPrototypeOf(provider) !== Object.prototype || !Object.isFrozen(provider)) {
    throw new PluginToolRuntimeError("provider_unavailable");
  }
  const descriptors = Object.getOwnPropertyDescriptors(provider);
  if (Object.keys(descriptors).sort().join("\0") !== ["describe", "id", "invoke"].join("\0")) throw new PluginToolRuntimeError("provider_unavailable");
  for (const key of ["describe", "id", "invoke"] as const) {
    const descriptor = descriptors[key];
    if (descriptor === undefined || !("value" in descriptor) || !descriptor.enumerable) throw new PluginToolRuntimeError("provider_unavailable");
  }
  const id = descriptors.id!.value as unknown;
  const describe = descriptors.describe!.value as unknown;
  const invoke = descriptors.invoke!.value as unknown;
  if (id !== binding.id || typeof describe !== "function" || typeof invoke !== "function") throw new PluginToolRuntimeError("provider_unavailable");
  let descriptor: ReturnType<PluginProvider["describe"]>;
  try { descriptor = Reflect.apply(describe, provider, []) as ReturnType<PluginProvider["describe"]>; }
  catch { throw new PluginToolRuntimeError("provider_unavailable"); }
  if (
    descriptor === null || typeof descriptor !== "object" || isProxy(descriptor)
    || (Object.getPrototypeOf(descriptor) !== Object.prototype && Object.getPrototypeOf(descriptor) !== null)
    || !Object.isFrozen(descriptor) || Object.getOwnPropertySymbols(descriptor).length !== 0
  ) throw new PluginToolRuntimeError("provider_unavailable");
  const descriptorProperties = Object.getOwnPropertyDescriptors(descriptor);
  if (Object.keys(descriptorProperties).sort().join("\0") !== ["id", "kind", "temporaryAdapter"].join("\0")) {
    throw new PluginToolRuntimeError("provider_unavailable");
  }
  for (const key of ["id", "kind", "temporaryAdapter"] as const) {
    const property = descriptorProperties[key];
    if (property === undefined || !("value" in property) || !property.enumerable) throw new PluginToolRuntimeError("provider_unavailable");
  }
  if (
    descriptorProperties.id!.value !== binding.id
    || descriptorProperties.kind!.value !== binding.providerKind
    || descriptorProperties.temporaryAdapter!.value !== (binding.temporaryAdapter === true)
  ) {
    throw new PluginToolRuntimeError("provider_unavailable");
  }
  return provider;
}

function isAborted(signal: AbortSignal | undefined): boolean {
  return signal?.aborted === true;
}

function awaitDeadline<T>(operation: Promise<T>, signal: AbortSignal, callerSignal: AbortSignal | undefined): Promise<T> {
  void operation.catch(() => undefined);
  if (signal.aborted) return Promise.reject(new PluginToolRuntimeError(isAborted(callerSignal) ? "request_aborted" : "provider_timed_out"));
  return new Promise<T>((resolve, reject) => {
    const onAbort = (): void => reject(new PluginToolRuntimeError(isAborted(callerSignal) ? "request_aborted" : "provider_timed_out"));
    signal.addEventListener("abort", onAbort, { once: true });
    operation.then(
      (value) => { signal.removeEventListener("abort", onAbort); resolve(value); },
      (error: unknown) => { signal.removeEventListener("abort", onAbort); reject(error); },
    );
  });
}

function classifyFailure(error: unknown): PluginToolRuntimeError {
  if (error instanceof PluginToolRuntimeError) return error;
  if (error instanceof Error && !isProxy(error)) {
    const descriptor = Object.getOwnPropertyDescriptor(error, "message");
    if (descriptor !== undefined && "value" in descriptor && descriptor.value === "credential_missing") {
      return new PluginToolRuntimeError("credential_missing");
    }
  }
  return new PluginToolRuntimeError("provider_unavailable");
}

export class PluginToolRuntime {
  readonly #dependencies: PluginToolRuntimeDependencies;
  readonly #timeoutMilliseconds: number;
  readonly #receiptTimeoutMilliseconds: number;

  constructor(dependencies: PluginToolRuntimeDependencies) {
    const timeout = dependencies.timeoutMilliseconds ?? DEFAULT_TIMEOUT_MILLISECONDS;
    const receiptTimeout = dependencies.receiptTimeoutMilliseconds ?? DEFAULT_RECEIPT_TIMEOUT_MILLISECONDS;
    if (
      !Number.isSafeInteger(timeout) || timeout < 1 || timeout > 300_000
      || !Number.isSafeInteger(receiptTimeout) || receiptTimeout < 1 || receiptTimeout > 5_000
    ) throw new PluginToolRuntimeError("request_invalid");
    this.#dependencies = dependencies;
    this.#timeoutMilliseconds = timeout;
    this.#receiptTimeoutMilliseconds = receiptTimeout;
    Object.freeze(this);
  }

  async invoke(bindingInput: unknown, argumentsInput: unknown, contextInput: unknown): Promise<ProviderResult> {
    const binding = captureBinding(bindingInput);
    const args = captureArguments(argumentsInput);
    const argumentsDigest = domainDigest(ARGUMENTS_DIGEST_DOMAIN, args);
    const context = captureContext(contextInput);
    const authorization = captureAuthorization(this.#dependencies.authorizationContext);
    if (context.signal?.aborted === true) throw new PluginToolRuntimeError("request_aborted");
    const controller = new AbortController();
    let cancelled = false;
    const cancel = (): void => {
      if (cancelled) return;
      cancelled = true;
      controller.abort();
      this.#dependencies.onCancel?.();
    };
    const externalAbort = (): void => cancel();
    context.signal?.addEventListener("abort", externalAbort, { once: true });
    const timer = setTimeout(cancel, this.#timeoutMilliseconds);
    try {
    try {
      await awaitDeadline(this.#dependencies.authorizeProject(authorization, context.projectId), controller.signal, context.signal);
    } catch (error: unknown) {
      if (error instanceof PluginToolRuntimeError) throw error;
      throw new PluginToolRuntimeError("authorization_denied");
    }

    const catalogValue = await awaitDeadline(this.#dependencies.pluginsRepository.getCatalog(PINNED_PLUGIN_CATALOG_DIGEST), controller.signal, context.signal);
    if (catalogValue === null) throw new PluginToolRuntimeError("catalog_unavailable");
    const catalog = exactCatalog(catalogValue);
    const installationValue = await awaitDeadline(this.#dependencies.pluginsRepository.getInstallation(context.projectId, binding.pluginName), controller.signal, context.signal);
    if (installationValue === null) throw new PluginToolRuntimeError("installation_unavailable");
    const installation = captureInstallation(installationValue);
    if (!installation.enabled) throw new PluginToolRuntimeError("installation_unavailable");
    if (
      installation.id !== binding.installationId || installation.projectId !== context.projectId
      || installation.pluginName !== binding.pluginName || !Number.isSafeInteger(installation.revision) || installation.revision < 0
    ) throw new PluginToolRuntimeError("installation_mismatch");
    const entry = exactEntry(catalog, installation);
    const selected = entry.components.filter(({ component }) => component.metadata.bindingDigest === binding.componentDigest);
    if (selected.length !== 1) throw new PluginToolRuntimeError("component_unavailable");
    const component = selected[0]!;
    if (
      component.admission.status !== "admitted" || component.component.status !== "available"
      || (component.component.kind !== "app" && component.component.kind !== "mcp")
    ) throw new PluginToolRuntimeError("admission_denied");
    const installedBindings = installation.providerBindings?.filter(({ componentId }) => componentId === component.component.id) ?? [];
    if (installedBindings.length !== 1) throw new PluginToolRuntimeError("provider_unavailable");
    const installedBinding = installedBindings[0]!.binding;
    if (
      installedBinding.id !== binding.providerBindingId
      || installedBinding.componentDigest !== binding.componentDigest
    ) throw new PluginToolRuntimeError("provider_unavailable");

    // Admission is re-checked here, before anything that can raise a human
    // OpenFang approval below: if it was revoked (or its policy version
    // moved on) since install, that must stop the call *before* a human is
    // ever asked to decide, not after. Raising an approval, having it
    // approved, and only then discovering the component was never callable
    // wastes a human decision that Rowboat cannot even record (see the
    // release-gate receipt-writing below) and leaves an approval sitting in
    // OpenFang with nothing here to reconcile it against.
    const admissions = captureAdmissions(await awaitDeadline(this.#dependencies.pluginsRepository.listAdmissions(installation.id), controller.signal, context.signal));
    const currentAdmissions = admissions.filter((candidate) => candidate.componentDigest === binding.componentDigest);
    if (
      currentAdmissions.length !== 1 || currentAdmissions[0]!.installationId !== installation.id
      || currentAdmissions[0]!.status !== "admitted" || currentAdmissions[0]!.policyVersion !== catalog.policyVersion
      || currentAdmissions[0]!.componentKind !== component.component.kind
      || currentAdmissions[0]!.componentName !== component.component.name
    ) throw new PluginToolRuntimeError("admission_denied");

    const trustedCapability = this.#dependencies.classifyOperation(Object.freeze({
      pluginName: entry.name, component: component.component, operationName: context.operationName,
    }));
    if (trustedCapability !== "read" && trustedCapability !== "write") throw new PluginToolRuntimeError("admission_denied");
    if (binding.capability !== trustedCapability) throw new PluginToolRuntimeError("capability_mismatch");

    // Generated here, before the release gate, so a refusal there (or the
    // release call itself aborting or timing out) has a requestId to write
    // a receipt against -- the whole point of the receipt-writing below.
    const requestId = this.#dependencies.createRequestId?.() ?? randomUUID();
    let policy = DEFAULT_POLICY;
    let approvalId: string | undefined;
    if (trustedCapability === "write" && this.#dependencies.releaseWrite !== undefined) {
      let decision: PluginWriteReleaseDecision;
      try {
        decision = await awaitDeadline(
          this.#dependencies.releaseWrite(Object.freeze({
            projectId: context.projectId,
            pluginName: binding.pluginName,
            toolName: context.operationName,
            componentDigest: binding.componentDigest,
            argumentsDigest,
          }), controller.signal),
          controller.signal,
          context.signal,
        );
      } catch (error: unknown) {
        // Fail closed: a broken release call is never treated as approval.
        // Only the shared deadline's own two outcomes propagate as
        // themselves, through the same convention every other await in this
        // method already uses; any other failure -- a malformed request, a
        // network error, or even a PluginToolRuntimeError raised by some
        // other seam entirely -- is indistinguishable from "no decision" and
        // keeps the write under review rather than escaping as that error.
        if (error instanceof PluginToolRuntimeError && (error.code === "request_aborted" || error.code === "provider_timed_out")) {
          // The release call itself never produced a decision, so there is
          // no approvalId to carry -- but the attempt, and that it stopped
          // here rather than being refused, must still leave a trace.
          await this.#settleReceipt(this.#putReceipt(
            requestId, context.projectId, binding, installation, component.component, installedBinding,
            "timed_out", "write_review_required", undefined,
          ));
          throw error;
        }
        decision = Object.freeze({ status: "unavailable" as const });
      }
      if (decision.status === "approved") {
        // Read .approvalId exactly once into a local, typed unknown rather
        // than trusting the declared string: a hostile releaseWrite could
        // otherwise answer a validating first read and a different value on
        // a second (a getter/Proxy), and an empty string must not qualify.
        const decidedApprovalId: unknown = decision.approvalId;
        if (typeof decidedApprovalId === "string" && UUID.test(decidedApprovalId)) {
          // Released for this call only: the policy copy never leaves this scope.
          policy = Object.freeze({ ...DEFAULT_POLICY, allowWriteCapabilities: true });
          approvalId = decidedApprovalId;
        }
      }
      if (policy === DEFAULT_POLICY) {
        // Not approved, for any reason a decision can fail to elevate the
        // policy: denied, expired, unavailable, or an "approved" decision
        // whose approvalId the UUID guard above refused. Every one of these
        // otherwise vanishes with no local trace before the
        // write_review_required throw a few lines below -- an operator
        // cannot tell which of them happened, nor that a release was ever
        // attempted, and a human's own denial (or a since-expired approval)
        // in OpenFang can never be reconciled against anything Rowboat
        // recorded. Same defensive read as the approved branch above: a
        // "denied"/"expired" decision's approvalId is validated the same
        // way before it is trusted into a receipt, even though the static
        // type already claims it is a string.
        const decidedApprovalId: unknown = decision.status === "denied" || decision.status === "expired" ? decision.approvalId : undefined;
        const releaseApprovalId = typeof decidedApprovalId === "string" && UUID.test(decidedApprovalId) ? decidedApprovalId : undefined;
        await this.#settleReceipt(this.#putReceipt(
          requestId, context.projectId, binding, installation, component.component, installedBinding,
          "denied", "write_review_required", releaseApprovalId,
        ));
      }
    }
    const capabilityDecision = evaluateCapability({ kind: trustedCapability }, policy);
    if (capabilityDecision.status !== "admitted") throw new PluginToolRuntimeError(capabilityDecision.reason === "write_review_required" ? "write_review_required" : "admission_denied");

    // Moving the admission re-check earlier (above) widened this staleness
    // window: it now spans the whole release gate, including a human's
    // decision time. A *sibling* component's admission changing while that
    // decision is pending -- nothing to do with this call -- now also trips
    // this check and aborts an already-approved write with
    // execution_state_changed. That is the correct, fail-closed direction to
    // err in (this comparison intentionally stays broad, not narrowed to
    // just this component -- an unrelated-looking change can still be a
    // real revocation this runtime has no business second-guessing), but if
    // a release was consumed to get here, discarding it with zero local
    // trace reproduces the exact "human decided, Rowboat recorded nothing"
    // gap the release-gate receipts above exist to close. So: record it,
    // the same way, before the throw -- never by re-snapshotting or
    // narrowing what counts as a change.
    const recordConsumedApprovalOnStaleness = async (): Promise<void> => {
      if (approvalId === undefined) return;
      await this.#settleReceipt(this.#putReceipt(
        requestId, context.projectId, binding, installation, component.component, installedBinding,
        "failed", "execution_state_changed", approvalId,
      ));
    };
    captureCredentialSlots(await awaitDeadline(this.#dependencies.pluginsRepository.listCredentialSlots(installation.id), controller.signal, context.signal));
    const preProviderInstallationValue = await awaitDeadline(this.#dependencies.pluginsRepository.getInstallation(context.projectId, binding.pluginName), controller.signal, context.signal);
    if (preProviderInstallationValue === null) {
      await recordConsumedApprovalOnStaleness();
      throw new PluginToolRuntimeError("execution_state_changed");
    }
    const preProviderInstallation = captureInstallation(preProviderInstallationValue);
    const preProviderAdmissions = captureAdmissions(await awaitDeadline(this.#dependencies.pluginsRepository.listAdmissions(installation.id), controller.signal, context.signal));
    if (signature(preProviderInstallation) !== signature(installation) || signature(preProviderAdmissions) !== signature(admissions)) {
      await recordConsumedApprovalOnStaleness();
      throw new PluginToolRuntimeError("execution_state_changed");
    }
    const credentialSlots = captureCredentialSlots(await awaitDeadline(this.#dependencies.pluginsRepository.listCredentialSlots(installation.id), controller.signal, context.signal));
    if (credentialSlots.some((slot) => slot.projectId !== context.projectId || slot.installationId !== installation.id)) {
      throw new PluginToolRuntimeError("credential_invalid");
    }
    let operation: Promise<ProviderResult> | undefined;
    let dispatched = false;
    try {
      const resolutionOperation = this.#dependencies.resolveProvider(Object.freeze({
        catalog, entry, component: component.component, installation, binding: installedBinding,
        credentialSlots: Object.freeze([...credentialSlots]), signal: controller.signal, policy,
      }));
      const resolution = await awaitDeadline(resolutionOperation, controller.signal, context.signal);
      const provider = exactProvider(resolution, installedBinding);
      if (controller.signal.aborted) throw new PluginToolRuntimeError(isAborted(context.signal) ? "request_aborted" : "provider_timed_out");
      try {
        await awaitDeadline(this.#dependencies.pluginsRepository.claimExecutionDispatch(Object.freeze({
          requestId,
          catalogDigest: catalog.catalogDigest,
          projectId: context.projectId,
          pluginName: binding.pluginName,
          installationId: installation.id,
          installationRevision: installation.revision,
          componentId: component.component.id,
          componentDigest: binding.componentDigest,
          componentKind: component.component.kind,
          componentName: component.component.name,
          providerBindingId: installedBinding.id,
          providerKind: installedBinding.providerKind,
          admissionPolicyVersion: currentAdmissions[0]!.policyVersion,
          credentialSlots,
        })), controller.signal, context.signal);
      } catch (error: unknown) {
        if (error instanceof PluginToolRuntimeError) throw error;
        throw new PluginToolRuntimeError("execution_state_changed");
      }
      operation = Promise.resolve().then(() => provider.invoke(Object.freeze({
        projectId: context.projectId,
        pluginName: binding.pluginName,
        componentName: component.component.name,
        operationName: context.operationName,
        capability: trustedCapability,
        arguments: args,
      }), Object.freeze({ requestId, signal: controller.signal })));
      dispatched = true;
      const result = captureProviderResult(await awaitDeadline(operation, controller.signal, context.signal));
      // result.reason is already an allowlisted output of
      // captureProviderResult (see mapProviderFailureReason); this only
      // picks the matching error code, it does not re-derive anything from
      // the provider.
      if (result.status !== "success") throw new PluginToolRuntimeError(result.reason === "credential_missing" ? "credential_missing" : "provider_failed");
      const finalInstallationValue = await awaitDeadline(this.#dependencies.pluginsRepository.getInstallation(context.projectId, binding.pluginName), controller.signal, context.signal);
      if (finalInstallationValue === null) throw new PluginToolRuntimeError("execution_state_changed");
      const finalInstallation = captureInstallation(finalInstallationValue);
      const finalAdmissions = captureAdmissions(await awaitDeadline(this.#dependencies.pluginsRepository.listAdmissions(installation.id), controller.signal, context.signal));
      const finalCredentialSlots = captureCredentialSlots(await awaitDeadline(this.#dependencies.pluginsRepository.listCredentialSlots(installation.id), controller.signal, context.signal));
      if (
        signature(finalInstallation) !== signature(installation)
        || signature(finalAdmissions) !== signature(admissions)
        || signature(finalCredentialSlots) !== signature(credentialSlots)
      ) throw new PluginToolRuntimeError("execution_state_changed");
      const receiptStored = await this.#settleReceipt(this.#putReceipt(requestId, context.projectId, binding, installation, component.component, installedBinding, "success", undefined, approvalId));
      if (!receiptStored) throw new PluginToolRuntimeError("receipt_unavailable");
      return result;
    } catch (error: unknown) {
      if (operation !== undefined) void operation.catch(() => undefined);
      const classified = classifyFailure(error);
      if (operation !== undefined && (classified.code === "provider_timed_out" || classified.code === "request_aborted")) {
        await this.#settleProvider(operation);
      }
      if (
        ["provider_failed", "provider_result_invalid", "provider_timed_out", "request_aborted", "provider_unavailable", "credential_invalid", "credential_missing"].includes(classified.code)
        || (dispatched && classified.code === "execution_state_changed")
      ) {
        const receiptOperation = this.#putReceipt(
          requestId, context.projectId, binding, installation, component.component, installedBinding,
          classified.code === "provider_timed_out" || classified.code === "request_aborted" ? "timed_out" : "failed",
          classified.code === "credential_missing"
            ? "credential_missing"
            : classified.code === "execution_state_changed"
              ? "execution_state_changed"
              : "provider_unavailable",
          // A write a human approved in OpenFang and that then died further
          // downstream (at the credential, say) still consumed that
          // approval; the failure receipt must carry the same approvalId the
          // success path already does, or there is no trace a release ever
          // happened for this call.
          approvalId,
        );
        await this.#settleReceipt(receiptOperation);
      }
      throw classified;
    }
    } finally {
      clearTimeout(timer);
      context.signal?.removeEventListener("abort", externalAbort);
    }
  }

  async #settleReceipt(operation: Promise<void>): Promise<boolean> {
    void operation.catch(() => undefined);
    let timer: NodeJS.Timeout | undefined;
    const timeout = new Promise<false>((resolve) => {
      timer = setTimeout(() => resolve(false), this.#receiptTimeoutMilliseconds);
    });
    try {
      return await Promise.race([operation.then(() => true, () => false), timeout]);
    } finally {
      if (timer !== undefined) clearTimeout(timer);
    }
  }

  async #settleProvider(operation: Promise<ProviderResult>): Promise<void> {
    void operation.catch(() => undefined);
    let timer: NodeJS.Timeout | undefined;
    const timeout = new Promise<void>((resolve) => {
      timer = setTimeout(resolve, this.#receiptTimeoutMilliseconds);
    });
    try {
      await Promise.race([operation.then(() => undefined, () => undefined), timeout]);
    } finally {
      if (timer !== undefined) clearTimeout(timer);
    }
  }

  async #putReceipt(
    requestId: string,
    projectId: string,
    binding: PluginToolBindingValue,
    installation: PluginInstallation,
    component: CatalogBoundPluginComponent,
    providerBinding: ProviderBinding,
    status: "success" | "failed" | "timed_out" | "denied",
    reason: "credential_missing" | "provider_unavailable" | "execution_state_changed" | "write_review_required" = "provider_unavailable",
    approvalId?: string,
  ): Promise<void> {
    const receipt = buildReceipt(Object.freeze({
      type: "execution",
      receiptId: requestId,
      projectId,
      pluginName: binding.pluginName,
      status,
      componentKind: component.kind,
      ...(status === "success" ? {} : { reason }),
      ...(providerBinding.temporaryAdapter === true ? { temporaryAdapter: true } : {}),
      output: Object.freeze({
        catalogDigest: PINNED_PLUGIN_CATALOG_DIGEST,
        installationId: installation.id,
        installationRevision: installation.revision,
        componentDigest: binding.componentDigest,
        providerBindingId: binding.providerBindingId,
        capability: binding.capability,
        ...(approvalId === undefined ? {} : { approvalId }),
      }),
    }), Object.freeze([]), Object.freeze({ maxOutputBytes: 4096 }));
    try {
      await this.#dependencies.pluginsRepository.putReceipt(receipt);
    } catch {
      throw new PluginToolRuntimeError("receipt_unavailable");
    }
  }
}
