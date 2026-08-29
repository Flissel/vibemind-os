import type { PluginReceipt } from "@rowboat/openai-plugin-runtime";
import { migrationDigest } from "../use-cases/plugins/plugin-migration.shared";
import { captureMigrationJson } from "./legacy-plugin-migration";
import {
  buildParityReceipt,
  compareParityRepresentations,
  EMPTY_PARITY_REPRESENTATION,
  parityOutputDigest,
  PluginParityError,
  type ParityComparison,
  type ParityRepresentationInput,
} from "./plugin-parity-report";
import type { PluginToolBindingValue } from "./plugin-tool-runtime";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const IDENTIFIER = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/;
const DIGEST = /^[a-f0-9]{64}$/;
const REQUEST_KEYS = Object.freeze(["input", "operationName", "pluginName", "projectId"]);
const BINDING_KEYS = Object.freeze(["capability", "componentDigest", "installationId", "pluginName", "providerBindingId"]);
const RUNTIME_MODES = Object.freeze(["legacy", "shadow", "openai"] as const);

export type ShadowExecution = "not_requested" | "read_only" | "descriptor_only";
export type PluginRuntimeMode = (typeof RUNTIME_MODES)[number];

export interface ShadowInvocable {
  readonly effect?: unknown;
  readonly invoke: (input: unknown) => Promise<unknown>;
  readonly representation?: ParityRepresentationInput;
}

export interface ShadowInvocationRequest {
  readonly projectId: string;
  readonly pluginName: string;
  readonly operationName: string;
  readonly input: unknown;
}

export interface ParityReceiptSink {
  readonly putReceipt: (receipt: PluginReceipt) => Promise<void>;
}

export interface ShadowRequest {
  readonly active: ShadowInvocable;
  readonly shadow: ShadowInvocable;
  readonly request: ShadowInvocationRequest;
  readonly receipts?: ParityReceiptSink;
}

export interface ShadowEvidence {
  readonly execution: ShadowExecution;
  readonly effect: "read" | "write" | null;
  readonly comparedOutput: boolean;
  readonly outputMatched: boolean | null;
  readonly activeOutputDigest: string | null;
  readonly shadowOutputDigest: string | null;
  readonly failed: boolean;
}

export interface ParityComparisonReport extends ParityComparison {
  readonly shadow: ShadowEvidence;
  readonly reportDigest: string;
}

export interface ShadowParityReport extends ParityComparisonReport {
  readonly activeResult: unknown;
  readonly receipt: PluginReceipt;
}

function requestInvalid(): never {
  throw new PluginParityError("parity_request_invalid");
}

function dataValue(source: object, key: string): unknown {
  const descriptor = Object.getOwnPropertyDescriptor(source, key);
  if (descriptor === undefined || !("value" in descriptor) || !descriptor.enumerable) return undefined;
  return descriptor.value;
}

function deepFreeze<T>(value: T): T {
  if (Array.isArray(value)) { for (const item of value) deepFreeze(item); return Object.freeze(value) as T; }
  if (value !== null && typeof value === "object") { for (const item of Object.values(value as object)) deepFreeze(item); return Object.freeze(value); }
  return value;
}

interface CapturedIdentity {
  readonly projectId: string;
  readonly pluginName: string;
  readonly operationName: string;
  readonly input: unknown;
  readonly argumentsDigest: string;
}

function captureIdentity(request: unknown): CapturedIdentity {
  if (request === null || typeof request !== "object" || Array.isArray(request)) requestInvalid();
  const prototype = Object.getPrototypeOf(request);
  if (prototype !== Object.prototype && prototype !== null) requestInvalid();
  if (Object.keys(request).sort().join("\0") !== REQUEST_KEYS.join("\0")) requestInvalid();
  const projectId = dataValue(request, "projectId");
  const pluginName = dataValue(request, "pluginName");
  const operationName = dataValue(request, "operationName");
  const input = dataValue(request, "input");
  if (typeof projectId !== "string" || !UUID.test(projectId)) requestInvalid();
  if (typeof pluginName !== "string" || !IDENTIFIER.test(pluginName)) requestInvalid();
  if (typeof operationName !== "string" || !IDENTIFIER.test(operationName)) requestInvalid();
  let argumentsDigest: string;
  try {
    argumentsDigest = migrationDigest("rowboat:plugin-parity-arguments:v1", captureMigrationJson(input));
  } catch {
    return requestInvalid();
  }
  return Object.freeze({ projectId, pluginName, operationName, input, argumentsDigest });
}

interface CapturedInvocable {
  readonly effect: "read" | "write";
  readonly invoke: (input: unknown) => Promise<unknown>;
  readonly representation: ParityRepresentationInput;
}

/**
 * Only the exact `read` classification is read-only. An absent, misspelled, or
 * structurally different classification is write-capable, so an unknown
 * provider is never executed a second time for a comparison.
 */
function captureInvocable(invocable: unknown): CapturedInvocable {
  if (invocable === null || typeof invocable !== "object") requestInvalid();
  const invoke = dataValue(invocable, "invoke");
  if (typeof invoke !== "function") requestInvalid();
  const representation = dataValue(invocable, "representation");
  return Object.freeze({
    effect: dataValue(invocable, "effect") === "read" ? "read" as const : "write" as const,
    invoke: invoke as (input: unknown) => Promise<unknown>,
    representation: representation === undefined ? EMPTY_PARITY_REPRESENTATION : representation as ParityRepresentationInput,
  });
}

function capturedArgument(input: unknown): unknown {
  try {
    return deepFreeze(captureMigrationJson(input));
  } catch {
    return requestInvalid();
  }
}

function reportDigestOf(comparison: ParityComparison, shadow: ShadowEvidence, identity: CapturedIdentity | null): string {
  return migrationDigest("rowboat:plugin-parity-report:v1", {
    legacyDigest: comparison.legacyDigest,
    pluginDigest: comparison.pluginDigest,
    differences: comparison.differences.map(difference => [difference.dimension, difference.code, difference.id]),
    shadow: [shadow.execution, shadow.effect, shadow.comparedOutput, shadow.outputMatched, shadow.activeOutputDigest, shadow.shadowOutputDigest, shadow.failed],
    identity: identity === null ? null : [identity.projectId, identity.pluginName, identity.operationName, identity.argumentsDigest],
  });
}

/**
 * Compares two representations without invoking anything. Used for migration
 * previews and for the descriptor half of a shadow run.
 */
export function compareOnly(legacy: unknown, plugin: unknown): ParityComparisonReport {
  const comparison = compareParityRepresentations(legacy, plugin);
  const shadow: ShadowEvidence = Object.freeze({
    execution: "not_requested" as const, effect: null, comparedOutput: false,
    outputMatched: null, activeOutputDigest: null, shadowOutputDigest: null, failed: false,
  });
  return Object.freeze({ ...comparison, shadow, reportDigest: reportDigestOf(comparison, shadow, null) });
}

/**
 * Runs the active path exactly once and resolves the shadow representation
 * without duplicating any write. A write-capable or unclassified shadow is
 * compared as a descriptor only, so this can never emit a second side effect.
 */
export async function evaluateShadow(request: ShadowRequest): Promise<ShadowParityReport> {
  if (request === null || typeof request !== "object") requestInvalid();
  const identity = captureIdentity(dataValue(request, "request"));
  const active = captureInvocable(dataValue(request, "active"));
  const shadow = captureInvocable(dataValue(request, "shadow"));
  const receipts = dataValue(request, "receipts");
  if (receipts !== undefined && (receipts === null || typeof receipts !== "object" || typeof dataValue(receipts, "putReceipt") !== "function")) requestInvalid();

  const activeResult = await active.invoke(capturedArgument(identity.input));
  const execution: ShadowExecution = shadow.effect === "read" ? "read_only" : "descriptor_only";
  let shadowResult: unknown;
  let shadowExecuted = false;
  let failed = false;
  if (execution === "read_only") {
    try {
      shadowResult = await shadow.invoke(capturedArgument(identity.input));
      shadowExecuted = true;
    } catch {
      failed = true;
    }
  }

  const activeOutputDigest = parityOutputDigest(activeResult);
  const shadowOutputDigest = shadowExecuted ? parityOutputDigest(shadowResult) : null;
  const comparedOutput = shadowExecuted && activeOutputDigest !== null && shadowOutputDigest !== null;
  const evidence: ShadowEvidence = Object.freeze({
    execution,
    effect: shadow.effect,
    comparedOutput,
    outputMatched: failed ? false : comparedOutput ? activeOutputDigest === shadowOutputDigest : null,
    activeOutputDigest,
    shadowOutputDigest,
    failed,
  });

  const comparison = compareParityRepresentations(active.representation, shadow.representation);
  const reportDigest = reportDigestOf(comparison, evidence, identity);
  const matched = comparison.matched && !failed && evidence.outputMatched !== false;
  const receipt = buildParityReceipt({
    projectId: identity.projectId,
    pluginName: identity.pluginName,
    reportDigest,
    matched,
    evidence: Object.freeze({
      version: 1,
      operationName: identity.operationName,
      argumentsDigest: identity.argumentsDigest,
      reportDigest,
      legacyDigest: comparison.legacyDigest,
      pluginDigest: comparison.pluginDigest,
      differences: comparison.differences,
      shadow: evidence,
      arguments: identity.input,
      activeResult,
      ...(shadowExecuted ? { shadowResult } : {}),
    }),
  });
  if (receipts !== undefined) await (receipts as ParityReceiptSink).putReceipt(receipt);
  return Object.freeze({ ...comparison, matched, shadow: evidence, reportDigest, activeResult, receipt });
}

export const parity = Object.freeze({ compareAndRun: evaluateShadow, compareOnly });

export interface PluginBoundToolConfig {
  readonly pluginBinding?: unknown;
}

export type PlannedToolConfig<T> = {
  readonly [K in keyof T]: Omit<T[K], "pluginBinding"> & { readonly pluginBinding?: unknown };
};

export interface ShadowToolPlan<T> {
  readonly active: "legacy" | "plugin";
  readonly toolConfig: PlannedToolConfig<T>;
  readonly shadow: Readonly<{ execution: ShadowExecution; bindings: readonly PluginToolBindingValue[] }>;
}

function captureToolBinding(value: unknown): PluginToolBindingValue | null {
  if (value === null || typeof value !== "object" || Array.isArray(value)) return null;
  if (Object.keys(value).sort().join("\0") !== BINDING_KEYS.join("\0")) return null;
  const installationId = dataValue(value, "installationId");
  const pluginName = dataValue(value, "pluginName");
  const componentDigest = dataValue(value, "componentDigest");
  const providerBindingId = dataValue(value, "providerBindingId");
  const capability = dataValue(value, "capability");
  if (typeof installationId !== "string" || !UUID.test(installationId)) return null;
  if (typeof pluginName !== "string" || !IDENTIFIER.test(pluginName)) return null;
  if (typeof componentDigest !== "string" || !DIGEST.test(componentDigest)) return null;
  if (typeof providerBindingId !== "string" || !IDENTIFIER.test(providerBindingId)) return null;
  if (capability !== "read" && capability !== "write") return null;
  return Object.freeze({ installationId, pluginName, componentDigest, providerBindingId, capability });
}

/**
 * Decides which representation owns execution for one turn.
 *
 * `legacy` and `shadow` both keep the existing legacy tools authoritative, so
 * the plugin binding is removed from the executable configuration. Shadow only
 * admits a read-only comparison run when every selected binding is
 * read-classified; a single write-capable or unparseable binding downgrades the
 * whole turn to a descriptor comparison.
 */
export function planShadowToolConfig<T extends Readonly<Record<string, PluginBoundToolConfig>>>(mode: PluginRuntimeMode, toolConfig: T): ShadowToolPlan<T> {
  if (!RUNTIME_MODES.includes(mode)) throw new Error("plugin_runtime_mode_invalid");
  if (toolConfig === null || typeof toolConfig !== "object") throw new Error("plugin_runtime_tool_config_invalid");
  if (mode === "openai") {
    return Object.freeze({
      active: "plugin" as const,
      toolConfig: toolConfig as unknown as PlannedToolConfig<T>,
      shadow: Object.freeze({ execution: "not_requested" as const, bindings: Object.freeze([]) }),
    });
  }
  const bindings: PluginToolBindingValue[] = [];
  let writeCapable = false;
  let bound = false;
  const planned: Record<string, PluginBoundToolConfig> = {};
  for (const [name, config] of Object.entries(toolConfig)) {
    const binding = config === null || typeof config !== "object" ? undefined : dataValue(config, "pluginBinding");
    if (binding === undefined) {
      planned[name] = config;
      continue;
    }
    bound = true;
    const captured = captureToolBinding(binding);
    if (captured === null) writeCapable = true;
    else {
      bindings.push(captured);
      if (captured.capability !== "read") writeCapable = true;
    }
    const { pluginBinding: _removed, ...rest } = config as PluginBoundToolConfig & Record<string, unknown>;
    void _removed;
    planned[name] = rest;
  }
  const execution: ShadowExecution = mode === "shadow" && bound ? (writeCapable ? "descriptor_only" : "read_only") : "not_requested";
  return Object.freeze({
    active: "legacy" as const,
    toolConfig: Object.freeze(planned) as PlannedToolConfig<T>,
    shadow: Object.freeze({ execution, bindings: Object.freeze(mode === "shadow" ? bindings : []) }),
  });
}

/**
 * Reads the persisted runtime mode of a project. Any project document without a
 * valid plugin runtime state stays on the legacy runtime.
 */
export function resolvePluginRuntimeMode(project: unknown): PluginRuntimeMode {
  if (project === null || typeof project !== "object") return "legacy";
  const state = dataValue(project, "pluginRuntime");
  if (state === null || typeof state !== "object") return "legacy";
  const mode = dataValue(state, "mode");
  return mode === "shadow" || mode === "openai" ? mode : "legacy";
}
