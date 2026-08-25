import { createHash, randomUUID } from "node:crypto";
import type { PluginCatalogEntry, PluginComponentAdmission, PluginCredentialSlot, PluginInstallation, PluginReceipt } from "../../repositories/plugins.repository.interface";
import type { PluginCatalogSnapshot } from "../../repositories/plugins.repository.interface";
import { PINNED_OPENAI_PLUGINS_COMMIT, PINNED_PLUGIN_CATALOG_DIGEST } from "@rowboat/openai-plugin-runtime";

const ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/;
const DIGEST = /^[a-f0-9]{64}$/;
const IDEMPOTENCY = /^[\x21-\x7e]{1,128}$/;

export function serviceError(reason: string): never { throw new Error(reason); }
export function assertId(value: string, reason: string): void { if (!ID.test(value)) serviceError(reason); }
export function assertDigest(value: string): void { if (!DIGEST.test(value)) serviceError("catalog_digest_invalid"); }
export function assertPinnedSnapshot(snapshot: PluginCatalogSnapshot | null, requestedDigest: string): asserts snapshot is PluginCatalogSnapshot {
  if (snapshot === null || requestedDigest !== PINNED_PLUGIN_CATALOG_DIGEST || snapshot.catalogDigest !== PINNED_PLUGIN_CATALOG_DIGEST || snapshot.sourceCommit !== PINNED_OPENAI_PLUGINS_COMMIT) serviceError("catalog_digest_mismatch");
}
export function assertIdempotencyKey(value: string): void { if (!IDEMPOTENCY.test(value)) serviceError("idempotency_key_invalid"); }

function canonicalValue(value: unknown): unknown {
  if (value === null || typeof value === "string" || typeof value === "boolean") return value;
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (Array.isArray(value)) return value.map(canonicalValue);
  if (typeof value === "object") {
    const output: Record<string, unknown> = Object.create(null) as Record<string, unknown>;
    for (const key of Object.keys(value as object).sort()) output[key] = canonicalValue((value as Record<string, unknown>)[key]);
    return output;
  }
  serviceError("request_invalid");
}

export function fingerprint(value: unknown): string {
  return createHash("sha256").update(JSON.stringify(canonicalValue(value))).digest("hex");
}

export function requiredCredentialNames(entry: PluginCatalogEntry): readonly string[] {
  const names = new Set<string>();
  for (const { component } of entry.components) {
    const candidate = component.metadata.credentialSlots;
    if (!Array.isArray(candidate)) continue;
    for (const name of candidate) if (typeof name === "string" && ID.test(name)) names.add(name);
  }
  return Object.freeze([...names].sort());
}

export function assertAdmitted(entry: PluginCatalogEntry): void {
  if (entry.admission.status !== "admitted") serviceError(entry.admission.reason);
  if (entry.components.some(({ admission }) => admission.status !== "admitted")) serviceError("component_not_admitted");
  const unavailable = entry.components.find(({ component }) => component.status === "unavailable" || component.status === "error" || component.reason === "provider_unavailable");
  if (unavailable !== undefined) serviceError(unavailable.component.reason ?? "provider_unavailable");
}

export function installationFrom(entry: PluginCatalogEntry, projectId: string): PluginInstallation {
  const providerBindings = entry.components.flatMap(({ component }) => {
    const binding = component.metadata.providerBinding;
    if (binding === null || typeof binding !== "object" || Array.isArray(binding)) return [];
    return [{ componentId: component.id, binding }] as unknown as NonNullable<PluginInstallation["providerBindings"]>;
  });
  return Object.freeze({
    id: randomUUID(), projectId, pluginName: entry.pluginName, pluginVersion: entry.pluginVersion,
    sourceCommit: entry.sourceCommit, manifestDigest: entry.manifestDigest, treeDigest: entry.treeDigest,
    policyVersion: entry.policyVersion, enabled: true, revision: 0,
    ...(providerBindings.length === 0 ? {} : { providerBindings: Object.freeze(providerBindings) }),
  });
}

export function admissionsFrom(entry: PluginCatalogEntry, installationId: string): readonly PluginComponentAdmission[] {
  return Object.freeze(entry.components.map(({ component, admission }) => Object.freeze({
    installationId, componentDigest: String(component.metadata.digest), componentKind: component.kind,
    componentName: component.name, status: admission.status, ...(admission.status === "admitted" ? {} : { reason: admission.reason }),
    policyVersion: admission.policyVersion,
  })));
}

export function slotsFrom(_entry: PluginCatalogEntry, _installationId: string, _projectId: string): readonly PluginCredentialSlot[] {
  // Credential references are configured through their dedicated secret boundary;
  // installation never invents a reference or treats an absent secret as configured.
  return Object.freeze([]);
}

export function installReceipt(projectId: string, pluginName: string): PluginReceipt {
  return Object.freeze({ type: "install", receiptId: randomUUID(), projectId, pluginName, status: "success", redactions: Object.freeze([]) });
}

export function freezeOutput<T>(value: T): T {
  if (Array.isArray(value)) { for (const item of value) freezeOutput(item); return Object.freeze(value) as T; }
  if (value !== null && typeof value === "object") { for (const item of Object.values(value as object)) freezeOutput(item); return Object.freeze(value); }
  return value;
}
