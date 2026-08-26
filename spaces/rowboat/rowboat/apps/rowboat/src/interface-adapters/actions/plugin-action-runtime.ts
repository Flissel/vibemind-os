import { z } from "zod";
import { captureRecord } from "@/src/interface-adapters/controllers/plugins/plugin-controller.shared";
import {
  catalogResponse,
  installResponse,
  previewResponse,
  projectListResponse,
} from "@/app/api/v1/projects/[projectId]/plugins/_responses";

const ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/;
const DIGEST = /^[a-f0-9]{64}$/;
const IDEMPOTENCY = /^[A-Za-z0-9][A-Za-z0-9._~-]{0,127}$/;

const ListInput = z.object({ projectId: z.string().regex(ID), catalogDigest: z.string().regex(DIGEST) }).strict();
const PreviewInput = z.object({
  projectId: z.string().regex(ID), pluginName: z.string().regex(ID), catalogDigest: z.string().regex(DIGEST),
  expectedRevision: z.number().int().nonnegative(),
}).strict();
const InstallInput = PreviewInput.extend({ idempotencyKey: z.string().regex(IDEMPOTENCY) }).strict();

export type PluginUiStatus = "available" | "review_required" | "installed" | "partially_available" | "unavailable" | "migration_required" | "error";
export type PluginUiReason =
  | "source_mismatch" | "manifest_invalid" | "path_escape" | "digest_mismatch" | "license_review_required" | "license_rejected"
  | "provider_unavailable" | "credential_missing" | "http_mcp_not_admitted" | "process_not_admitted" | "hook_not_admitted"
  | "write_review_required" | "component_unsupported" | "migration_conflict" | "parity_failed" | "rollback_unavailable";

export interface PluginUiComponent {
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
  readonly expectedRevision: number;
  readonly idempotencyKey: string;
  readonly credentialSlots: readonly Readonly<{ name: string; configured: boolean }>[];
}

interface CatalogController { execute(request: Request, input: unknown): Promise<unknown>; }
interface InstallationController {
  list(request: Request, input: unknown): Promise<unknown>;
  preview(request: Request, input: unknown): Promise<unknown>;
  install(request: Request, input: unknown): Promise<unknown>;
}
interface Controllers { readonly catalog: CatalogController; readonly installation: InstallationController; }

export interface PluginActionRuntimeDependencies {
  readonly resolveControllers: () => Promise<Controllers>;
  readonly createRequest: () => Request;
  readonly createIdempotencyKey: () => string;
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
  reason?: PluginUiReason; components: readonly PluginUiComponent[]; revision?: number;
}>;

async function json<T>(response: Response): Promise<T> {
  return await response.json() as T;
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
      const parsed = parseInput(input, ["projectId", "pluginName", "catalogDigest", "expectedRevision"], PreviewInput);
      const controllers = await dependencies.resolveControllers();
      const raw = await controllers.installation.preview(dependencies.createRequest(), {
        projectId: parsed.projectId, pluginName: parsed.pluginName, catalogDigest: parsed.catalogDigest,
      });
      const item = await json<SerializedPlugin & { credentialSlots: Array<{ name: string; configured: boolean }> }>(previewResponse(raw));
      if (item.pluginName !== parsed.pluginName || item.catalogDigest !== parsed.catalogDigest || item.sourceCommit === undefined) throw new Error("response_invalid");
      const idempotencyKey = dependencies.createIdempotencyKey();
      if (!IDEMPOTENCY.test(idempotencyKey)) throw new Error("response_invalid");
      return freeze({
        ...catalogItem(item), expectedRevision: parsed.expectedRevision, idempotencyKey,
        credentialSlots: item.credentialSlots.map(({ name, configured }) => ({ name, configured })),
      });
    },

    async install(input: unknown): Promise<Readonly<Record<string, unknown>>> {
      const parsed = parseInput(input, ["projectId", "pluginName", "catalogDigest", "expectedRevision", "idempotencyKey"], InstallInput);
      const controllers = await dependencies.resolveControllers();
      const previewRaw = await controllers.installation.preview(dependencies.createRequest(), {
        projectId: parsed.projectId, pluginName: parsed.pluginName, catalogDigest: parsed.catalogDigest,
      });
      const current = await json<SerializedPlugin & { credentialSlots: Array<{ name: string; configured: boolean }> }>(previewResponse(previewRaw));
      if (current.pluginName !== parsed.pluginName || current.catalogDigest !== parsed.catalogDigest || current.sourceCommit === undefined) throw new Error("response_invalid");
      if (canonicalStatus(current) !== "available") {
        throw new Error(current.reason ?? current.components.find((component) => component.reason !== undefined)?.reason ?? "component_not_admitted");
      }
      const result = await controllers.installation.install(dependencies.createRequest(), parsed);
      return freeze(await json<Record<string, unknown>>(installResponse(result)));
    },
  });
}
