import { createHash } from "node:crypto";
import { z } from "zod";
import type { PluginApiIdentity } from "@/src/application/policies/plugin-api-authorization.policy";
import { captureRecord } from "@/src/interface-adapters/controllers/plugins/plugin-controller.shared";
import { componentSelectionDigest, requestedComponentSelection } from "@/src/application/use-cases/plugins/plugin-component-selection";
import {
  signPluginPreviewEnvelope, validatePluginPreviewSecret, verifyPluginPreviewEnvelope,
  type PluginPreviewEnvelope,
} from "./plugin-preview-envelope";
import {
  catalogResponse,
  installResponse,
  previewResponse,
  projectListResponse,
} from "@/app/api/v1/projects/[projectId]/plugins/_responses";

const ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/;
const DIGEST = /^[a-f0-9]{64}$/;
const IDEMPOTENCY = /^[A-Za-z0-9][A-Za-z0-9._~-]{0,127}$/;

const ComponentDigests = z.array(z.string().regex(DIGEST)).min(1).max(512);
const ListInput = z.object({ projectId: z.string().regex(ID), catalogDigest: z.string().regex(DIGEST) }).strict();
const PreviewInput = z.object({
  projectId: z.string().regex(ID), pluginName: z.string().regex(ID), catalogDigest: z.string().regex(DIGEST),
  componentDigests: ComponentDigests.optional(), priorPreviewToken: z.string().min(1).max(4096).optional(),
}).strict();
const InstallInput = z.object({ previewToken: z.string().min(1).max(4096), componentDigests: ComponentDigests.optional() }).strict();
const PREVIEW_KEYS = Object.freeze(["projectId", "pluginName", "catalogDigest", "componentDigests", "priorPreviewToken"]);
const INSTALL_KEYS = Object.freeze(["previewToken", "componentDigests"]);

export type PluginUiStatus = "available" | "review_required" | "installed" | "partially_available" | "unavailable" | "migration_required" | "error";
export type PluginUiReason =
  | "source_mismatch" | "manifest_invalid" | "path_escape" | "digest_mismatch" | "license_review_required" | "license_rejected"
  | "provider_unavailable" | "credential_missing" | "http_mcp_not_admitted" | "process_not_admitted" | "hook_not_admitted"
  | "write_review_required" | "component_unsupported" | "migration_conflict" | "parity_failed" | "rollback_unavailable";

export interface PluginUiComponent {
  /** The pinned component identity the API already returns. */
  readonly componentDigest?: string;
  readonly name: string;
  readonly kind: "skill" | "agent" | "command" | "mcp" | "app" | "hook" | "asset";
  readonly status: string;
  readonly reason?: PluginUiReason;
}

export interface PluginUiCatalogItem {
  readonly pluginName: string;
  readonly pluginVersion: string;
  readonly catalogDigest: string;
  readonly sourceCommit: string;
  readonly status: PluginUiStatus;
  readonly reason?: PluginUiReason;
  readonly components: readonly PluginUiComponent[];
  readonly revision?: number;
}

export interface PluginUiPreview extends PluginUiCatalogItem {
  readonly previewToken: string;
  readonly credentialSlots: readonly Readonly<{ name: string; configured: boolean }>[];
}

interface CatalogController { execute(request: Request, input: unknown): Promise<unknown>; }
interface InstallationController {
  list(request: Request, input: unknown): Promise<unknown>;
  preview(request: Request, input: unknown): Promise<unknown>;
  install(request: Request, input: unknown): Promise<unknown>;
}
interface Controllers {
  readonly authenticate: (request: Request) => Promise<PluginApiIdentity>;
  readonly catalog: CatalogController;
  readonly installation: InstallationController;
  readonly findInstallReplay: (request: Request, envelope: PluginPreviewEnvelope) => Promise<unknown | null>;
}

export interface PluginActionRuntimeDependencies {
  readonly resolveControllers: () => Promise<Controllers>;
  readonly createRequest: () => Request;
  readonly createIdempotencyKey: () => string;
  readonly previewSecret: string | undefined;
  readonly pinnedCatalogDigest: string;
  readonly now?: () => number;
}

function parseInput<T>(input: unknown, allowed: readonly string[], schema: z.ZodType<T>): T {
  let captured: Readonly<Record<string, unknown>>;
  try { captured = captureRecord(input, allowed); } catch { throw new Error("request_invalid"); }
  const parsed = schema.safeParse(captured);
  if (!parsed.success) throw new Error("request_invalid");
  return parsed.data;
}

function freeze<T>(value: T): T {
  if (Array.isArray(value)) {
    for (const item of value) freeze(item);
    return Object.freeze(value);
  }
  if (value !== null && typeof value === "object") {
    for (const item of Object.values(value)) freeze(item);
    return Object.freeze(value);
  }
  return value;
}

type SerializedPlugin = Readonly<{
  pluginName: string; pluginVersion: string; catalogDigest: string; sourceCommit?: string; status: string;
  reason?: PluginUiReason; components: readonly PluginUiComponent[]; revision?: number; admission?: unknown; license?: unknown;
}>;

async function json<T>(response: Response): Promise<T> {
  return await response.json() as T;
}

function canonical(value: unknown): string {
  if (value === null || typeof value === "boolean" || typeof value === "string") return JSON.stringify(value);
  if (typeof value === "number" && Number.isFinite(value)) return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  if (typeof value !== "object") throw new Error("response_invalid");
  const entries = Object.entries(value).sort(([left], [right]) => left < right ? -1 : left > right ? 1 : 0);
  return `{${entries.map(([name, item]) => `${JSON.stringify(name)}:${canonical(item)}`).join(",")}}`;
}

function digest(value: unknown): string {
  return createHash("sha256").update(canonical(value), "utf8").digest("hex");
}

function actor(identity: PluginApiIdentity): Readonly<{ actorType: "user" | "project_api_key"; actorId: string }> {
  return identity.kind === "user"
    ? Object.freeze({ actorType: "user", actorId: identity.userId })
    : Object.freeze({ actorType: "project_api_key", actorId: identity.projectId });
}

function sameActor(identity: PluginApiIdentity, envelope: Readonly<{ actorType: string; actorId: string }>): boolean {
  const current = actor(identity);
  return current.actorType === envelope.actorType && current.actorId === envelope.actorId;
}

async function projectState(controllers: Controllers, request: Request, projectId: string, pluginName: string, catalogDigest: string) {
  const raw = await controllers.installation.list(request, { projectId, catalogDigest });
  const installed = await json<{ items: SerializedPlugin[] }>(projectListResponse(raw));
  const matches = installed.items.filter((item) => item.pluginName === pluginName);
  if (matches.length > 1) throw new Error("response_invalid");
  return Object.freeze({ present: matches.length === 1, revision: matches[0]?.revision ?? 0, item: matches[0] });
}

function previewDigests(item: SerializedPlugin & { credentialSlots: Array<{ name: string; configured: boolean }> }) {
  return Object.freeze({
    componentDecisionsDigest: digest({
      status: canonicalStatus(item), components: item.components,
      ...(item.reason === undefined ? {} : { reason: item.reason }),
      ...(item.admission === undefined ? {} : { admission: item.admission }),
      ...(item.license === undefined ? {} : { license: item.license }),
    }),
    credentialSlotsDigest: digest(item.credentialSlots),
  });
}

function canonicalStatus(item: SerializedPlugin): PluginUiStatus {
  if (item.status === "review_required") return "review_required";
  if (item.status === "rejected") return "unavailable";
  const statuses = item.components.map((component) => component.status);
  if (statuses.includes("migration_required")) return "migration_required";
  if (statuses.some((status) => status === "error" || status === "invalid")) return "error";
  if (statuses.length === 0 || statuses.every((status) => status === "available" || status === "installed")) return "available";
  if (statuses.some((status) => status === "available" || status === "installed")) return "partially_available";
  return "unavailable";
}

/**
 * The second gate, component-scoped: every selected digest has to name a
 * component the server currently reports as available - a status that already
 * collapses admission and availability. An absent selection stands for every
 * component, which is the whole-plugin gate this replaces. The licence
 * decision of the plugin itself still gates the install.
 */
function assertSelectionInstallable(item: SerializedPlugin, selection: readonly string[] | undefined): void {
  if (item.status === "review_required" || item.status === "rejected") {
    throw new Error(item.reason ?? item.components.find((component) => component.reason !== undefined)?.reason ?? "component_not_admitted");
  }
  const components = new Map(item.components.map((component) => [component.componentDigest, component] as const));
  for (const componentDigest of selection ?? item.components.map((component) => component.componentDigest)) {
    const component = componentDigest === undefined ? undefined : components.get(componentDigest);
    if (component === undefined) throw new Error("request_invalid");
    if (component.status !== "available") throw new Error(component.reason ?? "component_not_admitted");
  }
}

/**
 * What a signed envelope pinned, against what the server reports now: the
 * provenance of the plugin and the decisions the operator was shown. Install
 * checks it before mutating, and a re-signed preview checks it against the
 * envelope the operator actually reviewed, so staleness spans the whole review
 * rather than the moment between the last preview and the click.
 */
function assertEnvelopeStillCurrent(
  envelope: PluginPreviewEnvelope,
  item: SerializedPlugin,
  digests: Readonly<{ componentDecisionsDigest: string; credentialSlotsDigest: string }>,
): void {
  if (
    item.pluginName !== envelope.pluginName || item.catalogDigest !== envelope.catalogDigest
    || item.sourceCommit !== envelope.sourceCommit
    || digests.componentDecisionsDigest !== envelope.componentDecisionsDigest
    || digests.credentialSlotsDigest !== envelope.credentialSlotsDigest
  ) throw new Error("stale_preview");
}

function catalogItem(item: SerializedPlugin, installed?: SerializedPlugin): PluginUiCatalogItem {
  const catalogStatus = canonicalStatus(item);
  const status = installed === undefined || catalogStatus !== "available" ? catalogStatus : "installed";
  const reason = item.reason ?? item.components.find((component) => component.reason !== undefined)?.reason;
  return freeze({
    pluginName: item.pluginName,
    pluginVersion: item.pluginVersion,
    catalogDigest: item.catalogDigest,
    sourceCommit: item.sourceCommit ?? "",
    status,
    ...(reason === undefined ? {} : { reason }),
    components: item.components.map((component) => ({
      ...(component.componentDigest === undefined ? {} : { componentDigest: component.componentDigest }),
      name: component.name, kind: component.kind, status: component.status,
      ...(component.reason === undefined ? {} : { reason: component.reason }),
    })),
    ...(installed?.revision === undefined ? {} : { revision: installed.revision }),
  });
}

export function createPluginActionRuntime(dependencies: PluginActionRuntimeDependencies) {
  return Object.freeze({
    async list(input: unknown): Promise<Readonly<{ items: readonly PluginUiCatalogItem[] }>> {
      const parsed = parseInput(input, ["projectId", "catalogDigest"], ListInput);
      const controllers = await dependencies.resolveControllers();
      const installedRaw = await controllers.installation.list(dependencies.createRequest(), parsed);
      const installed = await json<{ items: SerializedPlugin[] }>(projectListResponse(installedRaw));
      const catalogRaw = await controllers.catalog.execute(dependencies.createRequest(), { catalogDigest: parsed.catalogDigest });
      const catalog = await json<{ items: SerializedPlugin[] }>(catalogResponse(catalogRaw));
      const byName = new Map(installed.items.map((item) => [item.pluginName, item]));
      return freeze({ items: catalog.items.map((item) => catalogItem(item, byName.get(item.pluginName))) });
    },

    async preview(input: unknown): Promise<PluginUiPreview> {
      const parsed = parseInput(input, PREVIEW_KEYS, PreviewInput);
      if (parsed.catalogDigest !== dependencies.pinnedCatalogDigest) throw new Error("request_invalid");
      const selection = parsed.componentDigests === undefined ? undefined : requestedComponentSelection(parsed.componentDigests);
      const now = dependencies.now?.() ?? Date.now();
      validatePluginPreviewSecret(dependencies.previewSecret);
      // A prior envelope only ever authorizes the plugin it was issued for; a
      // tampered or expired one authorizes nothing and is not a drift signal.
      let prior: PluginPreviewEnvelope | undefined;
      if (parsed.priorPreviewToken !== undefined) {
        try { prior = verifyPluginPreviewEnvelope(parsed.priorPreviewToken, dependencies.previewSecret, now); }
        catch { throw new Error("preview_invalid"); }
        if (prior.projectId !== parsed.projectId || prior.pluginName !== parsed.pluginName || prior.catalogDigest !== parsed.catalogDigest) throw new Error("preview_invalid");
      }
      const controllers = await dependencies.resolveControllers();
      const identity = await controllers.authenticate(dependencies.createRequest());
      if (prior !== undefined && !sameActor(identity, prior)) throw new Error("preview_invalid");
      const installation = await projectState(controllers, dependencies.createRequest(), parsed.projectId, parsed.pluginName, parsed.catalogDigest);
      const raw = await controllers.installation.preview(dependencies.createRequest(), {
        projectId: parsed.projectId, pluginName: parsed.pluginName, catalogDigest: parsed.catalogDigest,
      });
      const item = await json<SerializedPlugin & { credentialSlots: Array<{ name: string; configured: boolean }> }>(previewResponse(raw));
      if (item.pluginName !== parsed.pluginName || item.catalogDigest !== parsed.catalogDigest || item.sourceCommit === undefined) throw new Error("response_invalid");
      // A selection the caller named is checked before anything is signed, so a
      // preview is never issued for components that could not be installed.
      if (selection !== undefined) assertSelectionInstallable(item, selection);
      const idempotencyKey = dependencies.createIdempotencyKey();
      if (!IDEMPOTENCY.test(idempotencyKey)) throw new Error("response_invalid");
      const digests = previewDigests(item);
      if (prior !== undefined) assertEnvelopeStillCurrent(prior, item, digests);
      const previewToken = signPluginPreviewEnvelope({
        version: "rowboat_plugin_preview_v1", ...actor(identity), projectId: parsed.projectId, pluginName: parsed.pluginName,
        catalogDigest: parsed.catalogDigest, sourceCommit: item.sourceCommit, installationPresent: installation.present,
        expectedRevision: installation.revision, ...digests, componentSelectionDigest: componentSelectionDigest(selection),
        idempotencyKey, operation: "install", issuedAt: now, expiresAt: now + 5 * 60 * 1000,
      }, dependencies.previewSecret);
      return freeze({
        ...catalogItem(item, installation.item), previewToken,
        credentialSlots: item.credentialSlots.map(({ name, configured }) => ({ name, configured })),
      });
    },

    async install(input: unknown): Promise<Readonly<Record<string, unknown>>> {
      const parsed = parseInput(input, INSTALL_KEYS, InstallInput);
      const envelope = verifyPluginPreviewEnvelope(parsed.previewToken, dependencies.previewSecret, dependencies.now?.() ?? Date.now());
      if (envelope.catalogDigest !== dependencies.pinnedCatalogDigest) throw new Error("preview_invalid");
      const selection = parsed.componentDigests === undefined ? undefined : requestedComponentSelection(parsed.componentDigests);
      // The envelope authorizes one selection only: a token issued for
      // component A cannot be replayed to install component B.
      if (componentSelectionDigest(selection) !== envelope.componentSelectionDigest) throw new Error("preview_invalid");
      const controllers = await dependencies.resolveControllers();
      const identity = await controllers.authenticate(dependencies.createRequest());
      if (!sameActor(identity, envelope)) throw new Error("preview_invalid");
      const replay = await controllers.findInstallReplay(dependencies.createRequest(), envelope);
      if (replay !== null) return freeze(await json<Record<string, unknown>>(installResponse(replay)));
      const installation = await projectState(controllers, dependencies.createRequest(), envelope.projectId, envelope.pluginName, envelope.catalogDigest);
      const previewRaw = await controllers.installation.preview(dependencies.createRequest(), {
        projectId: envelope.projectId, pluginName: envelope.pluginName, catalogDigest: envelope.catalogDigest,
      });
      const current = await json<SerializedPlugin & { credentialSlots: Array<{ name: string; configured: boolean }> }>(previewResponse(previewRaw));
      const currentDigests = previewDigests(current);
      assertEnvelopeStillCurrent(envelope, current, currentDigests);
      if (installation.revision !== envelope.expectedRevision) throw new Error("stale_preview");
      if (installation.present !== envelope.installationPresent) {
        throw new Error("stale_preview");
      }
      // Installing what is already installed conflicts with the stored
      // installation; it says nothing about the components that were selected.
      if (envelope.installationPresent) throw new Error("installation_conflict");
      assertSelectionInstallable(current, selection);
      const result = await controllers.installation.install(dependencies.createRequest(), {
        projectId: envelope.projectId, pluginName: envelope.pluginName, catalogDigest: envelope.catalogDigest,
        expectedRevision: envelope.expectedRevision, idempotencyKey: envelope.idempotencyKey,
        ...(selection === undefined ? {} : { componentDigests: selection }),
      });
      return freeze(await json<Record<string, unknown>>(installResponse(result)));
    },
  });
}
