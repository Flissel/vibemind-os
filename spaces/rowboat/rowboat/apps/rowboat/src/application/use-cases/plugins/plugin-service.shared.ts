import { createHash, randomUUID } from "node:crypto";
import type { PluginCatalogEntry, PluginComponentAdmission, PluginCredentialSlot, PluginInstallation, PluginReceipt } from "../../repositories/plugins.repository.interface";
import type { PluginCatalogSnapshot } from "../../repositories/plugins.repository.interface";
import { canonicalComponentSelection } from "./plugin-component-selection";
import {
  OPENAI_API_KEY_CREDENTIAL_REFERENCE,
  PINNED_OPENAI_PLUGINS_COMMIT,
  PINNED_PLUGIN_CATALOG_DIGEST,
  deriveConnectorReference,
  deriveOAuthBearerReference,
  type PluginComponentKind,
  type PluginComponentStatus,
  type PluginReasonCode,
} from "@rowboat/openai-plugin-runtime";

const ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/;
const DIGEST = /^[a-f0-9]{64}$/;
const IDEMPOTENCY = /^[\x21-\x7e]{1,128}$/;
const COMPONENT_NAME = /^[A-Za-z0-9][A-Za-z0-9._ ()+@-]{0,127}$/;
const COMPONENT_KINDS = new Set<PluginComponentKind>(["skill", "agent", "command", "mcp", "app", "hook", "asset"]);
const COMPONENT_STATUSES = new Set<PluginComponentStatus>(["available", "review_required", "installed", "partially_available", "unavailable", "migration_required", "error", "invalid", "unsupported"]);
const REASONS = new Set<PluginReasonCode>(["source_mismatch", "manifest_invalid", "path_escape", "digest_mismatch", "license_review_required", "license_rejected", "provider_unavailable", "credential_missing", "http_mcp_not_admitted", "process_not_admitted", "hook_not_admitted", "write_review_required", "component_unsupported", "migration_conflict", "parity_failed", "rollback_unavailable"]);

export interface PluginComponentDto {
  readonly componentDigest: string;
  readonly name: string;
  readonly kind: PluginComponentKind;
  readonly admission: Readonly<{ readonly status: "admitted" | "review_required" | "rejected"; readonly reason?: PluginReasonCode; readonly policyVersion: string }>;
  readonly availability: Readonly<{ readonly status: PluginComponentStatus; readonly reason?: PluginReasonCode }>;
}

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

function stringField(record: Readonly<Record<string, unknown>>, key: string): string | undefined {
  const value = record[key];
  return typeof value === "string" && value.length > 0 ? value : undefined;
}

/**
 * Every name this reports must be the exact name OpenFang resolves the
 * credential by - the same derivations `OpenFangCredentialResolver.
 * #deriveOpenFangReference` and `ConnectorBridgeProvider` use at call time
 * (both now read `deriveOAuthBearerReference` / `deriveConnectorReference`
 * from the kernel, "@rowboat/openai-plugin-runtime", so this reads the same
 * functions rather than re-deriving the shape a third time) - never the raw
 * oauth resource URL, and never silence for a component that in fact needs
 * one.
 */
export function requiredCredentialNames(entry: PluginCatalogEntry): readonly string[] {
  // Only a component the catalog actually admits can ever be invoked (a
  // rejected or process-MCP component never reaches a provider), so only an
  // admitted `mcp`-over-HTTP or `app` component contributes to what the
  // install dialog asks the operator for.
  const names = new Set<string>();
  for (const { component, admission } of entry.components) {
    if (admission.status !== "admitted") continue;

    if (component.kind === "mcp" && component.metadata.transport === "http") {
      // `credentialSlots` only ever carries the importer's env-var-style
      // names (`bearer_token_env_var` / `env_vars` - see
      // `credentialSlotNames` in `component-discovery.ts`, out of scope to
      // change here); a bearer reference is used verbatim, so its name here
      // is already the name OpenFang resolves it by.
      const slots = component.metadata.credentialSlots;
      if (Array.isArray(slots)) {
        for (const name of slots) if (typeof name === "string" && ID.test(name)) names.add(name);
      }
      const mcpServer: unknown = component.metadata.mcpServer;
      if (mcpServer !== null && typeof mcpServer === "object" && !Array.isArray(mcpServer)) {
        const record = mcpServer as Readonly<Record<string, unknown>>;
        const oauthResourceDeclared = stringField(record, "oauth_resource");
        const bearerDeclared = stringField(record, "bearer_token_env_var");
        // Mirrors mcp-normalizer.ts's `normalizeMcpServer`: a server that
        // declares neither credential is still an OAuth-protected resource,
        // at its own URL - "declares nothing" must never read as "needs
        // nothing" (this was cloudflare's gap: the catalog carries no
        // `oauth_resource` hint at all, yet the rule still applies).
        const oauthResource = oauthResourceDeclared
          ?? (bearerDeclared === undefined ? stringField(record, "url") : undefined);
        if (oauthResource !== undefined) {
          const derived = deriveOAuthBearerReference(oauthResource);
          if (derived !== undefined) names.add(derived);
        }
      }
      continue;
    }

    if (component.kind === "app") {
      const appDeclaration: unknown = component.metadata.appDeclaration;
      if (appDeclaration !== null && typeof appDeclaration === "object" && !Array.isArray(appDeclaration)) {
        const id = stringField(appDeclaration as Readonly<Record<string, unknown>>, "id");
        // Only a `connector_...` id has a public invocation path at all (the
        // connector-bridge provider itself refuses `asdk_app_`/
        // `templated_apps_` ids, provider_unavailable:not_a_connector), and
        // it always needs both the platform key and its own per-app token.
        if (id !== undefined && id.startsWith("connector_") && typeof component.name === "string" && component.name.length > 0) {
          names.add(OPENAI_API_KEY_CREDENTIAL_REFERENCE);
          names.add(deriveConnectorReference(component.name));
        }
      }
    }
  }
  return Object.freeze([...names].sort());
}

export function componentDtosFrom(entry: PluginCatalogEntry): readonly PluginComponentDto[] {
  if (!Array.isArray(entry.components) || entry.components.length > 512) serviceError("catalog_entry_invalid");
  const ids = new Set<string>();
  return entry.components.map((selected) => {
    if (selected === null || typeof selected !== "object" || selected.component === undefined || selected.admission === undefined) serviceError("catalog_entry_invalid");
    const { component, admission } = selected;
    const digest = component.metadata.bindingDigest;
    if (typeof component.id !== "string" || component.id.length === 0 || component.id.length > 512 || typeof digest !== "string" || !DIGEST.test(digest)) serviceError("catalog_entry_invalid");
    if (ids.has(component.id)) serviceError("catalog_entry_invalid");
    ids.add(component.id);
    if (!COMPONENT_NAME.test(component.name) || !COMPONENT_KINDS.has(component.kind) || !COMPONENT_STATUSES.has(component.status)) serviceError("catalog_entry_invalid");
    if (admission.policyVersion !== entry.policyVersion || (admission.status !== "admitted" && admission.status !== "review_required" && admission.status !== "rejected")) serviceError("catalog_entry_invalid");
    if ((admission.status === "admitted" && admission.reason !== undefined) || (admission.status !== "admitted" && (admission.reason === undefined || !REASONS.has(admission.reason)))) serviceError("catalog_entry_invalid");
    if (component.reason !== undefined && !REASONS.has(component.reason)) serviceError("catalog_entry_invalid");
    return {
      componentDigest: digest,
      name: component.name,
      kind: component.kind,
      admission: { status: admission.status, ...(admission.status === "admitted" ? {} : { reason: admission.reason }), policyVersion: admission.policyVersion },
      availability: { status: component.status, ...(component.reason === undefined ? {} : { reason: component.reason }) },
    };
  });
}

type PluginCatalogComponent = PluginCatalogEntry["components"][number]["component"];

function bindingDigestOf(component: PluginCatalogComponent): string {
  const value = component.metadata.bindingDigest;
  if (typeof value !== "string" || !DIGEST.test(value)) serviceError("catalog_entry_invalid");
  return value;
}

/** Every component of the plugin - the selection an absent one stands for. */
export function entryComponentDigests(entry: PluginCatalogEntry): readonly string[] {
  return canonicalComponentSelection(entry.components.map(({ component }) => bindingDigestOf(component)));
}

/**
 * Installation is component-scoped: only the selected components have to be
 * admitted and runnable. The plugin's own licence admission still gates the
 * whole install; a selection that names a digest this plugin does not have, and
 * an empty selection on a plugin that has components, are request errors rather
 * than a silently empty install - whoever derived the selection, a caller or the
 * admission rows of an existing installation.
 */
export function assertSelectionAdmitted(entry: PluginCatalogEntry, componentDigests: readonly string[]): void {
  if (entry.admission.status !== "admitted") serviceError(entry.admission.reason);
  if (componentDigests.length === 0 && entry.components.length > 0) serviceError("request_invalid");
  const known = new Set(entry.components.map(({ component }) => bindingDigestOf(component)));
  if (componentDigests.some((componentDigest) => !known.has(componentDigest))) serviceError("request_invalid");
  const selection = new Set(componentDigests);
  const selected = entry.components.filter(({ component }) => selection.has(bindingDigestOf(component)));
  if (selected.some(({ admission }) => admission.status !== "admitted")) serviceError("component_not_admitted");
  const unavailable = selected.find(({ component }) => component.status === "unavailable" || component.status === "error" || component.reason === "provider_unavailable");
  if (unavailable !== undefined) serviceError(unavailable.component.reason ?? "provider_unavailable");
}

export function installationFrom(entry: PluginCatalogEntry, projectId: string, componentDigests: readonly string[]): PluginInstallation {
  const selection = new Set(componentDigests);
  const providerBindings = entry.components.flatMap(({ component }) => {
    const binding = component.metadata.providerBinding;
    if (!selection.has(bindingDigestOf(component)) || binding === null || typeof binding !== "object" || Array.isArray(binding)) return [];
    return [{ componentId: component.id, binding }] as unknown as NonNullable<PluginInstallation["providerBindings"]>;
  });
  return Object.freeze({
    id: randomUUID(), projectId, pluginName: entry.pluginName, pluginVersion: entry.pluginVersion,
    sourceCommit: entry.sourceCommit, manifestDigest: entry.manifestDigest, treeDigest: entry.treeDigest,
    policyVersion: entry.policyVersion, enabled: true, revision: 0,
    ...(providerBindings.length === 0 ? {} : { providerBindings: Object.freeze(providerBindings) }),
  });
}

export function admissionsFrom(entry: PluginCatalogEntry, installationId: string, componentDigests: readonly string[]): readonly PluginComponentAdmission[] {
  const selection = new Set(componentDigests);
  return Object.freeze(entry.components
    .filter(({ component }) => selection.has(bindingDigestOf(component)))
    .map(({ component, admission }) => Object.freeze({
      installationId, componentDigest: bindingDigestOf(component), componentKind: component.kind,
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
