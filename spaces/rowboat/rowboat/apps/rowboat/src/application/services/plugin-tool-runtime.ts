import { randomUUID } from "node:crypto";
import { isProxy } from "node:util/types";
import {
  buildReceipt,
  DEFAULT_POLICY,
  evaluateCapability,
  PINNED_PLUGIN_CATALOG_DIGEST,
  validatePluginCatalogLock,
  type CatalogBoundPluginComponent,
  type PluginCatalogEntry,
  type PluginProvider,
  type ProviderBinding,
  type ProviderResolution,
  type ProviderResult,
} from "@rowboat/openai-plugin-runtime";
import type {
  IPluginsRepository,
  PluginCredentialSlot,
  PluginInstallation,
} from "@/src/application/repositories/plugins.repository.interface";

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
const DANGEROUS_KEYS = new Set(["__proto__", "constructor", "prototype"]);

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

export interface PluginProviderResolutionInput {
  readonly catalog: ReturnType<typeof validatePluginCatalogLock>;
  readonly entry: PluginCatalogEntry;
  readonly component: CatalogBoundPluginComponent;
  readonly installation: PluginInstallation;
  readonly binding: ProviderBinding;
  readonly credentialSlots: readonly PluginCredentialSlot[];
  readonly signal: AbortSignal;
}

export interface PluginToolRuntimeDependencies {
  readonly pluginsRepository: IPluginsRepository;
  readonly authorizeProject: (projectId: string) => Promise<void>;
  readonly classifyOperation: (input: Readonly<{
    readonly pluginName: string;
    readonly component: CatalogBoundPluginComponent;
    readonly operationName: string;
  }>) => "read" | "write";
  readonly resolveProvider: (input: PluginProviderResolutionInput) => Promise<ProviderResolution>;
  readonly timeoutMilliseconds?: number;
  readonly createRequestId?: () => string;
  readonly onCancel?: () => void;
}

export type PluginToolRuntimeErrorCode =
  | "binding_invalid"
  | "request_invalid"
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

function captureJson(input: unknown, depth: number, budget: CaptureBudget): unknown {
  budget.nodes += 1;
  if (budget.nodes > MAX_ARGUMENT_NODES || depth > MAX_ARGUMENT_DEPTH) throw new PluginToolRuntimeError("request_invalid");
  if (typeof input === "string") {
    budget.bytes += Buffer.byteLength(input, "utf8") + 2;
    if (budget.bytes > MAX_ARGUMENT_BYTES) throw new PluginToolRuntimeError("request_invalid");
    return input;
  }
  if (input === null || typeof input === "boolean") return input;
  if (typeof input === "number" && Number.isFinite(input)) return input;
  if (typeof input !== "object" || isProxy(input) || budget.seen.has(input)) throw new PluginToolRuntimeError("request_invalid");
  budget.seen.add(input);
  try {
    if (Array.isArray(input)) {
      if (Object.getPrototypeOf(input) !== Array.prototype || input.length > MAX_ARGUMENT_NODES) throw new PluginToolRuntimeError("request_invalid");
      const descriptors = Object.getOwnPropertyDescriptors(input);
      const values: unknown[] = [];
      for (let index = 0; index < input.length; index += 1) {
        const descriptor = descriptors[String(index)];
        if (descriptor === undefined || !("value" in descriptor) || !descriptor.enumerable) throw new PluginToolRuntimeError("request_invalid");
        values.push(captureJson(descriptor.value, depth + 1, budget));
      }
      if (Reflect.ownKeys(input).length !== input.length + 1) throw new PluginToolRuntimeError("request_invalid");
      return Object.freeze(values);
    }
    const prototype = Object.getPrototypeOf(input);
    if (prototype !== Object.prototype && prototype !== null) throw new PluginToolRuntimeError("request_invalid");
    const descriptors = Object.getOwnPropertyDescriptors(input);
    if (Object.getOwnPropertySymbols(input).length !== 0) throw new PluginToolRuntimeError("request_invalid");
    const captured: Record<string, unknown> = Object.create(null) as Record<string, unknown>;
    for (const [key, descriptor] of Object.entries(descriptors)) {
      if (DANGEROUS_KEYS.has(key) || !("value" in descriptor) || !descriptor.enumerable) throw new PluginToolRuntimeError("request_invalid");
      budget.bytes += Buffer.byteLength(key, "utf8") + 3;
      captured[key] = captureJson(descriptor.value, depth + 1, budget);
    }
    return Object.freeze(captured);
  } finally {
    budget.seen.delete(input);
  }
}

function captureArguments(input: unknown): Readonly<Record<string, unknown>> {
  const captured = captureJson(input, 0, { nodes: 0, bytes: 0, seen: new Set() });
  if (captured === null || typeof captured !== "object" || Array.isArray(captured)) throw new PluginToolRuntimeError("request_invalid");
  return captured as Readonly<Record<string, unknown>>;
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

  constructor(dependencies: PluginToolRuntimeDependencies) {
    const timeout = dependencies.timeoutMilliseconds ?? DEFAULT_TIMEOUT_MILLISECONDS;
    if (!Number.isSafeInteger(timeout) || timeout < 1 || timeout > 300_000) throw new PluginToolRuntimeError("request_invalid");
    this.#dependencies = dependencies;
    this.#timeoutMilliseconds = timeout;
    Object.freeze(this);
  }

  async invoke(bindingInput: unknown, argumentsInput: unknown, contextInput: unknown): Promise<ProviderResult> {
    const binding = captureBinding(bindingInput);
    const args = captureArguments(argumentsInput);
    const context = captureContext(contextInput);
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
    await awaitDeadline(this.#dependencies.authorizeProject(context.projectId), controller.signal, context.signal);

    const catalogValue = await awaitDeadline(this.#dependencies.pluginsRepository.getCatalog(PINNED_PLUGIN_CATALOG_DIGEST), controller.signal, context.signal);
    if (catalogValue === null) throw new PluginToolRuntimeError("catalog_unavailable");
    const catalog = exactCatalog(catalogValue);
    const installation = await awaitDeadline(this.#dependencies.pluginsRepository.getInstallation(context.projectId, binding.pluginName), controller.signal, context.signal);
    if (installation === null || !installation.enabled) throw new PluginToolRuntimeError("installation_unavailable");
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

    const trustedCapability = this.#dependencies.classifyOperation(Object.freeze({
      pluginName: entry.name, component: component.component, operationName: context.operationName,
    }));
    if (trustedCapability !== "read" && trustedCapability !== "write") throw new PluginToolRuntimeError("admission_denied");
    if (binding.capability !== trustedCapability) throw new PluginToolRuntimeError("capability_mismatch");
    const capabilityDecision = evaluateCapability({ kind: trustedCapability }, DEFAULT_POLICY);
    if (capabilityDecision.status !== "admitted") throw new PluginToolRuntimeError(capabilityDecision.reason === "write_review_required" ? "write_review_required" : "admission_denied");

    const admissions = await awaitDeadline(this.#dependencies.pluginsRepository.listAdmissions(installation.id), controller.signal, context.signal);
    const currentAdmissions = admissions.filter((candidate) => candidate.componentDigest === binding.componentDigest);
    if (
      currentAdmissions.length !== 1 || currentAdmissions[0]!.installationId !== installation.id
      || currentAdmissions[0]!.status !== "admitted" || currentAdmissions[0]!.policyVersion !== catalog.policyVersion
      || currentAdmissions[0]!.componentKind !== component.component.kind
      || currentAdmissions[0]!.componentName !== component.component.name
    ) throw new PluginToolRuntimeError("admission_denied");

    const credentialSlots = await awaitDeadline(this.#dependencies.pluginsRepository.listCredentialSlots(installation.id), controller.signal, context.signal);
    if (credentialSlots.some((slot) => slot.projectId !== context.projectId || slot.installationId !== installation.id)) {
      throw new PluginToolRuntimeError("credential_invalid");
    }
    const requestId = this.#dependencies.createRequestId?.() ?? randomUUID();
    let operation: Promise<ProviderResult> | undefined;
    try {
      const resolutionOperation = this.#dependencies.resolveProvider(Object.freeze({
        catalog, entry, component: component.component, installation, binding: installedBinding,
        credentialSlots: Object.freeze([...credentialSlots]), signal: controller.signal,
      }));
      const resolution = await awaitDeadline(resolutionOperation, controller.signal, context.signal);
      const provider = exactProvider(resolution, installedBinding);
      if (controller.signal.aborted) throw new PluginToolRuntimeError(isAborted(context.signal) ? "request_aborted" : "provider_timed_out");
      operation = Promise.resolve().then(() => provider.invoke(Object.freeze({
        projectId: context.projectId,
        pluginName: binding.pluginName,
        componentName: component.component.name,
        operationName: context.operationName,
        capability: trustedCapability,
        arguments: args,
      }), Object.freeze({ requestId })));
      const result = await awaitDeadline(operation, controller.signal, context.signal);
      if (result.status !== "success") throw new PluginToolRuntimeError("provider_failed");
      await awaitDeadline(this.#putReceipt(requestId, context.projectId, binding, installation, component.component, installedBinding, "success"), controller.signal, context.signal);
      return result;
    } catch (error: unknown) {
      if (operation !== undefined) void operation.catch(() => undefined);
      const classified = classifyFailure(error);
      if (["provider_failed", "provider_timed_out", "request_aborted", "provider_unavailable", "credential_invalid", "credential_missing"].includes(classified.code)) {
        const receiptOperation = this.#putReceipt(
          requestId, context.projectId, binding, installation, component.component, installedBinding,
          classified.code === "provider_timed_out" || classified.code === "request_aborted" ? "timed_out" : "failed",
          classified.code === "credential_missing" ? "credential_missing" : "provider_unavailable",
        );
        if (controller.signal.aborted) await receiptOperation;
        else await awaitDeadline(receiptOperation, controller.signal, context.signal);
      }
      throw classified;
    }
    } finally {
      clearTimeout(timer);
      context.signal?.removeEventListener("abort", externalAbort);
    }
  }

  async #putReceipt(
    requestId: string,
    projectId: string,
    binding: PluginToolBindingValue,
    installation: PluginInstallation,
    component: CatalogBoundPluginComponent,
    providerBinding: ProviderBinding,
    status: "success" | "failed" | "timed_out",
    reason: "credential_missing" | "provider_unavailable" = "provider_unavailable",
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
      }),
    }), Object.freeze([]), Object.freeze({ maxOutputBytes: 4096 }));
    try {
      await this.#dependencies.pluginsRepository.putReceipt(receipt);
    } catch {
      throw new PluginToolRuntimeError("receipt_unavailable");
    }
  }
}
