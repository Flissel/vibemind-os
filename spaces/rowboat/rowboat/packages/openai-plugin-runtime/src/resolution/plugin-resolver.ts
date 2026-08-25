import { isProxy } from "node:util/types";
import {
  type CatalogComponentAdmission,
  type PluginCatalogEntry,
  type PluginCatalogLock,
} from "../domain/catalog.js";
import type { PluginInstallation } from "../domain/installation.js";
import type {
  NormalizedPluginComponent,
  PluginComponentKind,
  PluginReasonCode,
} from "../domain/plugin.js";
import { DEFAULT_POLICY, type PluginPolicy } from "../policy/default-policy.js";
import type { ProviderBinding } from "../providers/provider.js";
import type { PluginProvider } from "../providers/provider.js";
import { ProviderRegistry } from "../providers/provider-registry.js";
import { validatePluginCatalogLock } from "../import/catalog-validator.js";

export const PINNED_PLUGIN_CATALOG_DIGEST =
  "2e436d02b025a14960d5ef813c603bd7aec35a6d173c42d8c58274163da89a92" as const;

export interface ResolvedComponent {
  readonly id: string;
  readonly name: string;
  readonly kind: PluginComponentKind;
  readonly status: "available" | "unavailable";
  readonly reason?: PluginReasonCode;
  readonly metadata: Readonly<Record<string, unknown>>;
}

export interface ResolvedInstallation {
  readonly status: "available" | "partially_available" | "unavailable";
  readonly reason?: PluginReasonCode;
  readonly components: readonly ResolvedComponent[];
  readonly skills: readonly ResolvedComponent[];
  readonly agents: readonly ResolvedComponent[];
  readonly commands: readonly ResolvedComponent[];
  readonly mcp: readonly ResolvedComponent[];
  readonly apps: readonly ResolvedComponent[];
  readonly hooks: readonly ResolvedComponent[];
  readonly assets: readonly ResolvedComponent[];
}

interface CaptureBudget {
  nodes: number;
  strings: number;
}

function fail(): never {
  throw new Error("resolution_invalid");
}

function capturePlain(value: unknown, depth = 0, budget: CaptureBudget = { nodes: 0, strings: 0 }): unknown {
  budget.nodes += 1;
  if (budget.nodes > 50_000 || depth > 16) fail();
  if (typeof value === "string") {
    budget.strings += Buffer.byteLength(value, "utf8");
    if (budget.strings > 8 * 1024 * 1024 || Buffer.byteLength(value, "utf8") > 64 * 1024) fail();
    return value;
  }
  if (value === null || typeof value === "boolean") return value;
  if (typeof value === "number") {
    if (!Number.isFinite(value)) fail();
    return value;
  }
  if (typeof value !== "object" || isProxy(value)) fail();
  const array = Array.isArray(value);
  const prototype = Object.getPrototypeOf(value);
  if (prototype !== (array ? Array.prototype : Object.prototype) && prototype !== null) fail();
  if (Object.getOwnPropertySymbols(value).length !== 0) fail();
  const descriptors = Object.getOwnPropertyDescriptors(value);
  if (array) {
    if (value.length > 20_000) fail();
    const result: unknown[] = [];
    for (let index = 0; index < value.length; index += 1) {
      const descriptor = descriptors[String(index)];
      if (descriptor === undefined || !("value" in descriptor) || !descriptor.enumerable) fail();
      result.push(capturePlain(descriptor.value, depth + 1, budget));
    }
    const extras = Object.keys(descriptors).filter((key) => key !== "length" && !/^(0|[1-9][0-9]*)$/.test(key));
    if (extras.length !== 0) fail();
    return Object.freeze(result);
  }
  const result: Record<string, unknown> = Object.create(null) as Record<string, unknown>;
  for (const key of Object.keys(descriptors).sort()) {
    if (key === "__proto__" || key === "prototype" || key === "constructor" || key.length > 128) fail();
    const descriptor = descriptors[key];
    if (descriptor === undefined || !("value" in descriptor) || !descriptor.enumerable) fail();
    result[key] = capturePlain(descriptor.value, depth + 1, budget);
  }
  return Object.freeze(result);
}

function validateCatalog(input: unknown): PluginCatalogLock {
  try {
    const catalog = validatePluginCatalogLock(input);
    if (catalog.catalogDigest !== PINNED_PLUGIN_CATALOG_DIGEST || catalog.policyVersion !== DEFAULT_POLICY.version) fail();
    return catalog;
  } catch {
    fail();
  }
}

const INSTALLATION_KEYS = new Set([
  "id", "projectId", "pluginName", "pluginVersion", "sourceCommit", "manifestDigest",
  "treeDigest", "policyVersion", "enabled", "revision", "providerBindings",
]);
const ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/;
const COMPONENT_ID = /^[A-Za-z0-9][A-Za-z0-9._:/#-]{0,255}$/;
const DIGEST = /^[a-f0-9]{64}$/;
const COMMIT = /^[a-f0-9]{40}$/;
const REGISTRY_PROBE_BINDING: ProviderBinding = Object.freeze({
  id: "rowboat-registry-integrity-probe",
  providerKind: "rowboat-native",
  componentDigest: "0".repeat(64),
});

function captureRegistryResolverIntrinsic(): ProviderRegistry["resolve"] | undefined {
  const descriptor = Object.getOwnPropertyDescriptor(ProviderRegistry.prototype, "resolve");
  if (
    descriptor === undefined
    || !("value" in descriptor)
    || typeof descriptor.value !== "function"
  ) return undefined;
  return descriptor.value as ProviderRegistry["resolve"];
}

function captureRegistryRegisterIntrinsic(): ProviderRegistry["register"] | undefined {
  const descriptor = Object.getOwnPropertyDescriptor(ProviderRegistry.prototype, "register");
  if (
    descriptor === undefined
    || !("value" in descriptor)
    || typeof descriptor.value !== "function"
  ) return undefined;
  return descriptor.value as ProviderRegistry["register"];
}

const TRUSTED_REGISTRY_RESOLVE = captureRegistryResolverIntrinsic();
const TRUSTED_REGISTRY_REGISTER = captureRegistryRegisterIntrinsic();
const resolverAuthorizedRegistries = new WeakMap<ProviderRegistry, ReadonlySet<string>>();

function resolverBindingSignature(binding: ProviderBinding): string {
  return JSON.stringify({
    id: binding.id,
    providerKind: binding.providerKind,
    componentDigest: binding.componentDigest,
    pairedComponentDigests: binding.pairedComponentDigests ?? null,
    temporaryAdapter: binding.temporaryAdapter ?? false,
  });
}

export interface TrustedProviderAssembly {
  readonly pluginName: string;
  readonly componentId: string;
  readonly binding: ProviderBinding;
  readonly provider: PluginProvider;
}

function captureAssembly(input: unknown): TrustedProviderAssembly {
  if (input === null || typeof input !== "object" || Array.isArray(input) || isProxy(input)) fail();
  const prototype = Object.getPrototypeOf(input);
  if (prototype !== Object.prototype && prototype !== null) fail();
  if (Object.getOwnPropertySymbols(input).length !== 0) fail();
  const descriptors = Object.getOwnPropertyDescriptors(input);
  const expected = ["binding", "componentId", "pluginName", "provider"];
  if (Object.keys(descriptors).sort().join("\0") !== expected.join("\0")) fail();
  for (const key of expected) {
    const descriptor = descriptors[key];
    if (descriptor === undefined || !("value" in descriptor) || !descriptor.enumerable) fail();
  }
  const pluginName = descriptors.pluginName!.value as unknown;
  const componentId = descriptors.componentId!.value as unknown;
  const binding = capturePlain(descriptors.binding!.value) as ProviderBinding;
  const provider = descriptors.provider!.value as unknown;
  if (
    typeof pluginName !== "string"
    || !ID.test(pluginName)
    || typeof componentId !== "string"
    || !COMPONENT_ID.test(componentId)
  ) fail();
  if (provider === null || typeof provider !== "object" || Array.isArray(provider) || isProxy(provider)) fail();
  const providerPrototype = Object.getPrototypeOf(provider);
  if (providerPrototype !== Object.prototype && providerPrototype !== null) fail();
  if (Object.getOwnPropertySymbols(provider).length !== 0) fail();
  const providerDescriptors = Object.getOwnPropertyDescriptors(provider);
  if (Object.keys(providerDescriptors).sort().join("\0") !== ["describe", "id", "invoke"].join("\0")) fail();
  for (const key of ["describe", "id", "invoke"] as const) {
    const descriptor = providerDescriptors[key];
    if (descriptor === undefined || !("value" in descriptor) || !descriptor.enumerable) fail();
  }
  if (
    typeof providerDescriptors.id!.value !== "string"
    || typeof providerDescriptors.describe!.value !== "function"
    || typeof providerDescriptors.invoke!.value !== "function"
  ) fail();
  return Object.freeze({ pluginName, componentId, binding, provider: provider as PluginProvider });
}

export function assembleTrustedProviderRegistry(
  catalogInput: unknown,
  assembliesInput: readonly TrustedProviderAssembly[],
): ProviderRegistry {
  if (TRUSTED_REGISTRY_REGISTER === undefined) fail();
  const catalog = validateCatalog(catalogInput);
  if (!Array.isArray(assembliesInput) || isProxy(assembliesInput)) fail();
  const prototype = Object.getPrototypeOf(assembliesInput);
  if (prototype !== Array.prototype && prototype !== null) fail();
  const lengthDescriptor = Object.getOwnPropertyDescriptor(assembliesInput, "length");
  if (
    lengthDescriptor === undefined
    || !("value" in lengthDescriptor)
    || !Number.isSafeInteger(lengthDescriptor.value)
    || (lengthDescriptor.value as number) < 0
    || (lengthDescriptor.value as number) > 256
  ) fail();
  const length = lengthDescriptor.value as number;
  const ownKeys = Reflect.ownKeys(assembliesInput);
  if (ownKeys.length !== length + 1 || ownKeys.some((key) => typeof key !== "string")) fail();
  const assemblyDescriptors = Object.getOwnPropertyDescriptors(assembliesInput);
  const assemblies: TrustedProviderAssembly[] = [];
  const componentIds = new Set<string>();
  const bindingIds = new Set<string>();
  const bindingSignatures = new Set<string>();
  for (let index = 0; index < length; index += 1) {
    const descriptor = assemblyDescriptors[String(index)];
    if (descriptor === undefined || !("value" in descriptor) || !descriptor.enumerable) fail();
    const assembly = captureAssembly(descriptor.value);
    if (componentIds.has(assembly.componentId) || bindingIds.has(assembly.binding.id)) fail();
    const entry = catalog.entries.find((candidate) => candidate.name === assembly.pluginName);
    const selected = entry?.components.find(({ component }) => component.id === assembly.componentId);
    const transport = assembly.binding.providerKind === "mcp-http"
      ? "http"
      : assembly.binding.providerKind === "mcp-process" ? "process" : undefined;
    const pairedDigests = assembly.binding.pairedComponentDigests;
    const paired = pairedDigests === undefined
      ? undefined
      : entry?.components.find(({ component }) => component.metadata.bindingDigest === pairedDigests[1]);
    const bindingCompatible = pairedDigests === undefined
      ? transport === undefined || (selected?.component.kind === "mcp" && selected.component.metadata.transport === transport)
      : transport !== undefined
        && selected?.component.kind === "app"
        && pairedDigests[0] === assembly.binding.componentDigest
        && paired?.component.kind === "mcp"
        && paired.admission.status === "admitted"
        && paired.component.metadata.transport === transport;
    if (
      entry?.admission.status !== "admitted"
      || selected?.admission.status !== "admitted"
      || (selected.component.kind !== "app" && selected.component.kind !== "mcp")
      || selected.component.metadata.bindingDigest !== assembly.binding.componentDigest
      || !bindingCompatible
    ) fail();
    componentIds.add(assembly.componentId);
    bindingIds.add(assembly.binding.id);
    bindingSignatures.add(resolverBindingSignature(assembly.binding));
    assemblies.push(assembly);
  }
  const registry = new ProviderRegistry();
  for (const assembly of assemblies) {
    Reflect.apply(TRUSTED_REGISTRY_REGISTER, registry, [assembly.binding, assembly.provider]);
  }
  resolverAuthorizedRegistries.set(registry, bindingSignatures);
  return registry;
}

function validateInstallation(input: unknown): PluginInstallation {
  const installation = capturePlain(input) as PluginInstallation;
  if (Object.keys(installation).some((key) => !INSTALLATION_KEYS.has(key))) fail();
  if (
    !ID.test(installation.id) || !ID.test(installation.projectId) || !ID.test(installation.pluginName)
    || typeof installation.pluginVersion !== "string" || installation.pluginVersion.length > 128
    || !COMMIT.test(installation.sourceCommit) || !DIGEST.test(installation.manifestDigest)
    || !DIGEST.test(installation.treeDigest) || installation.policyVersion !== DEFAULT_POLICY.version
    || typeof installation.enabled !== "boolean" || !Number.isSafeInteger(installation.revision)
    || installation.revision < 0
  ) fail();
  if (installation.providerBindings !== undefined) {
    if (!Array.isArray(installation.providerBindings) || installation.providerBindings.length > 256) fail();
    const ids = new Set<string>();
    for (const selected of installation.providerBindings) {
      if (!COMPONENT_ID.test(selected.componentId) || ids.has(selected.componentId)) fail();
      if (Object.keys(selected).some((key) => key !== "componentId" && key !== "binding")) fail();
      if (Object.keys(selected.binding).some((key) => !["id", "providerKind", "componentDigest", "pairedComponentDigests", "temporaryAdapter"].includes(key))) fail();
      ids.add(selected.componentId);
    }
  }
  return installation;
}

function empty(reason: PluginReasonCode): ResolvedInstallation {
  const components = Object.freeze([]) as readonly ResolvedComponent[];
  return Object.freeze({
    status: "unavailable", reason, components,
    skills: components, agents: components, commands: components, mcp: components,
    apps: components, hooks: components, assets: components,
  });
}

function admissionReason(admission: CatalogComponentAdmission["admission"]): PluginReasonCode | undefined {
  return admission.status === "admitted" ? undefined : admission.reason;
}

function providerBinding(
  installation: PluginInstallation,
  componentId: string,
): ProviderBinding | undefined {
  return installation.providerBindings?.find((selected) => selected.componentId === componentId)?.binding;
}

function resolveComponent(
  item: CatalogComponentAdmission,
  installation: PluginInstallation,
  registry: ProviderRegistry,
): ResolvedComponent {
  const component: NormalizedPluginComponent = item.component;
  let reason = admissionReason(item.admission);
  if (reason === undefined && component.status !== "available") {
    reason = component.reason ?? "component_unsupported";
  }
  if (reason === undefined && (component.kind === "app" || component.kind === "mcp")) {
    const binding = providerBinding(installation, component.id);
    if (
      binding === undefined
      || binding.componentDigest !== component.metadata.bindingDigest
      || resolveProviderIntrinsic(registry, binding) !== "available"
    ) reason = "provider_unavailable";
  }
  return Object.freeze({
    id: component.id,
    name: component.name,
    kind: component.kind,
    status: reason === undefined ? "available" : "unavailable",
    ...(reason === undefined ? {} : { reason }),
    metadata: component.metadata,
  });
}

function resolveProviderIntrinsic(registry: ProviderRegistry, binding: ProviderBinding): "available" | "unavailable" {
  if (
    TRUSTED_REGISTRY_RESOLVE === undefined
    || resolverAuthorizedRegistries.get(registry)?.has(resolverBindingSignature(binding)) !== true
  ) return "unavailable";
  try {
    const resolution = Reflect.apply(TRUSTED_REGISTRY_RESOLVE, registry, [binding]) as { readonly status?: unknown };
    return resolution.status === "available" ? "available" : "unavailable";
  } catch {
    return "unavailable";
  }
}

function isTrustedProviderRegistry(registry: ProviderRegistry): boolean {
  if (
    TRUSTED_REGISTRY_RESOLVE === undefined
    || isProxy(registry)
    || Object.getPrototypeOf(registry) !== ProviderRegistry.prototype
    || Reflect.ownKeys(registry).length !== 0
  ) return false;
  try {
    const probe = Reflect.apply(TRUSTED_REGISTRY_RESOLVE, registry, [REGISTRY_PROBE_BINDING]) as {
      readonly status?: unknown;
      readonly reason?: unknown;
    };
    return probe.status === "unavailable" && probe.reason === "provider_unavailable";
  } catch {
    return false;
  }
}

function byKindName(left: ResolvedComponent, right: ResolvedComponent): number {
  return left.kind < right.kind ? -1 : left.kind > right.kind ? 1 : left.name < right.name ? -1 : left.name > right.name ? 1 : 0;
}

export function resolveInstallation(
  installationInput: unknown,
  catalogInput: unknown,
  registry: ProviderRegistry,
  policy: PluginPolicy,
): ResolvedInstallation {
  let installation: PluginInstallation;
  let catalog: PluginCatalogLock;
  try {
    installation = validateInstallation(installationInput);
    catalog = validateCatalog(catalogInput);
    if (
      policy !== DEFAULT_POLICY
      || !isTrustedProviderRegistry(registry)
    ) fail();
  } catch {
    return empty("source_mismatch");
  }
  if (!installation.enabled) return empty("component_unsupported");
  const matches = catalog.entries.filter((candidate) => candidate.name === installation.pluginName);
  if (matches.length !== 1) return empty("source_mismatch");
  const entry: PluginCatalogEntry = matches[0]!;
  if (
    installation.sourceCommit !== entry.sourceCommit
    || installation.pluginVersion !== entry.pluginVersion
    || installation.manifestDigest !== entry.manifestDigest
    || installation.treeDigest !== entry.treeDigest
    || installation.policyVersion !== entry.policyVersion
  ) return empty("digest_mismatch");
  if (entry.admission.status !== "admitted") return empty(entry.admission.reason);

  const ids = new Set<string>();
  const names = new Set<string>();
  for (const item of entry.components) {
    const key = `${item.component.kind}\u0000${item.component.name}`;
    if (ids.has(item.component.id) || names.has(key)) return empty("digest_mismatch");
    ids.add(item.component.id);
    names.add(key);
  }
  if (installation.providerBindings?.some(({ componentId }) => !ids.has(componentId)) === true) return empty("digest_mismatch");
  const components = Object.freeze(entry.components.map((item) => resolveComponent(item, installation, registry)).sort(byKindName));
  const forKind = (kind: PluginComponentKind): readonly ResolvedComponent[] =>
    Object.freeze(components.filter((component) => component.kind === kind));
  const available = components.filter((component) => component.status === "available").length;
  const status = available === components.length ? "available" : available === 0 ? "unavailable" : "partially_available";
  return Object.freeze({
    status,
    components,
    skills: forKind("skill"), agents: forKind("agent"), commands: forKind("command"),
    mcp: forKind("mcp"), apps: forKind("app"), hooks: forKind("hook"), assets: forKind("asset"),
  });
}
