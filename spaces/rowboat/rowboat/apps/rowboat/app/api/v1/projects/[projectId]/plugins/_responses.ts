import { types as utilTypes } from "node:util";
import { NextRequest } from "next/server";
import { z } from "zod";

const DIGEST = /^[a-f0-9]{64}$/;
const COMMIT = /^[a-f0-9]{40}$/;
const ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/;
const IDEMPOTENCY = /^[\x21-\x7e]{1,128}$/;
const SAFE_TEXT = /^[\x20-\x7e]{1,256}$/;
const MAX_BODY_BYTES = 65_536;
const MAX_DEPTH = 8;
const MAX_KEYS = 64;
const MAX_ARRAY = 64;
const MAX_STRING = 16_384;
const FORBIDDEN_KEYS = new Set(["__proto__", "prototype", "constructor", "toJSON"]);

const Reason = z.enum([
  "source_mismatch", "manifest_invalid", "path_escape", "digest_mismatch", "license_review_required", "license_rejected",
  "provider_unavailable", "credential_missing", "http_mcp_not_admitted", "process_not_admitted", "hook_not_admitted",
  "write_review_required", "component_unsupported", "migration_conflict", "parity_failed", "rollback_unavailable",
]);
const Admission = z.union([
  z.object({ status: z.literal("admitted"), policyVersion: z.string().regex(ID) }).strict(),
  z.object({ status: z.enum(["review_required", "rejected"]), reason: Reason, policyVersion: z.string().regex(ID) }).strict(),
]);
const Availability = z.object({
  status: z.enum(["available", "review_required", "installed", "partially_available", "unavailable", "migration_required", "error", "invalid", "unsupported"]),
  reason: Reason.optional(),
}).strict();
const Component = z.object({
  componentDigest: z.string().regex(DIGEST),
  name: z.string().regex(/^[A-Za-z0-9][A-Za-z0-9._ ()+@-]{0,127}$/),
  kind: z.enum(["skill", "agent", "command", "mcp", "app", "hook", "asset"]),
  admission: Admission,
  availability: Availability,
}).strict();
const License = z.union([
  z.object({ declaration: z.string().regex(SAFE_TEXT), decision: z.literal("admitted") }).strict(),
  z.object({ declaration: z.string().regex(SAFE_TEXT), decision: z.enum(["review_required", "rejected"]), reason: Reason }).strict(),
]);
const CatalogItem = z.object({
  name: z.string().regex(ID), pluginName: z.string().regex(ID), pluginVersion: z.string().regex(SAFE_TEXT),
  catalogDigest: z.string().regex(DIGEST), sourceCommit: z.string().regex(COMMIT), policyVersion: z.string().regex(ID),
  license: License, admission: z.enum(["admitted", "review_required", "rejected"]), reason: Reason.optional(),
  components: z.array(Component).max(512),
}).strict();
const ProjectItem = z.object({
  pluginName: z.string().regex(ID), pluginVersion: z.string().regex(SAFE_TEXT), catalogDigest: z.string().regex(DIGEST),
  policyVersion: z.string().regex(ID), license: License, admission: z.enum(["admitted", "review_required", "rejected"]),
  reason: Reason.optional(), components: z.array(Component).max(512), enabled: z.boolean(), revision: z.number().int().nonnegative(),
}).strict();
const Preview = z.object({
  pluginName: z.string().regex(ID), catalogDigest: z.string().regex(DIGEST), sourceCommit: z.string().regex(COMMIT),
  policyVersion: z.string().regex(ID), license: License, admission: z.enum(["admitted", "review_required", "rejected"]),
  reason: Reason.optional(), components: z.array(Component).max(512),
  credentialSlots: z.array(z.object({ name: z.string().regex(ID), configured: z.boolean() }).strict()).max(128),
}).strict();
const InstallReceipt = z.object({
  type: z.literal("install"), receiptId: z.string().regex(ID), projectId: z.string().regex(ID), pluginName: z.string().regex(ID),
  status: z.enum(["success", "failed", "denied", "timed_out"]), reason: Reason.optional(),
  redactions: z.array(z.string().regex(ID)).max(64),
}).strict();
const Installation = z.object({
  id: z.string().regex(ID), projectId: z.string().regex(ID), pluginName: z.string().regex(ID), pluginVersion: z.string().regex(SAFE_TEXT),
  sourceCommit: z.string().regex(COMMIT), manifestDigest: z.string().regex(DIGEST), treeDigest: z.string().regex(DIGEST),
  policyVersion: z.string().regex(ID), enabled: z.boolean(), revision: z.number().int().nonnegative(), providerBindings: z.array(z.unknown()).optional(),
}).strict();

export interface CatalogController {
  execute(request: Request, input: unknown): Promise<unknown>;
}
export interface InstallationController {
  list?(request: Request, input: unknown): Promise<unknown>;
  preview?(request: Request, input: unknown): Promise<unknown>;
  install?(request: Request, input: unknown): Promise<unknown>;
  setEnabled?(request: Request, input: unknown): Promise<unknown>;
}
export interface RouteContext<P extends Record<string, string>> { readonly params: Promise<P>; }

function headers(): Readonly<Record<string, string>> {
  return Object.freeze({
    "cache-control": "no-store",
    "content-type": "application/json; charset=utf-8",
    "x-content-type-options": "nosniff",
    "content-security-policy": "default-src 'none'; frame-ancestors 'none'",
    "referrer-policy": "no-referrer",
  });
}

export function pluginJson(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), { status, headers: headers() });
}

const ERROR_STATUS = Object.freeze({
  unauthenticated: 401, user_authentication_required: 401, authorization_invalid: 400, forbidden: 403,
  plugin_not_found: 404, installation_not_found: 404, idempotency_conflict: 409, installation_conflict: 409,
  catalog_digest_mismatch: 409, provider_unavailable: 503, license_rejected: 422, component_not_admitted: 422,
  request_invalid: 400, catalog_digest_invalid: 400, project_id_invalid: 400, plugin_name_invalid: 400,
  idempotency_key_required: 400, idempotency_key_invalid: 400, installation_revision_invalid: 400,
  installation_update_invalid: 400,
} as const);

function safeErrorReason(error: unknown): keyof typeof ERROR_STATUS | null {
  if (utilTypes.isProxy(error) || !(error instanceof Error) || Object.getPrototypeOf(error) !== Error.prototype) return null;
  const descriptor = Object.getOwnPropertyDescriptor(error, "message");
  if (descriptor === undefined || !("value" in descriptor) || typeof descriptor.value !== "string") return null;
  return Object.prototype.hasOwnProperty.call(ERROR_STATUS, descriptor.value) ? descriptor.value as keyof typeof ERROR_STATUS : null;
}

export function pluginErrorResponse(error: unknown): Response {
  const reason = safeErrorReason(error);
  return reason === null ? pluginJson({ error: "internal_error" }, 500) : pluginJson({ error: reason }, ERROR_STATUS[reason]);
}

function assertRequest(request: Request): void {
  if (utilTypes.isProxy(request)) throw new Error("request_invalid");
  const prototype = Object.getPrototypeOf(request);
  if (prototype !== Request.prototype && prototype !== NextRequest.prototype) throw new Error("request_invalid");
  for (const key of ["method", "url", "headers", "body"]) if (Object.getOwnPropertyDescriptor(request, key) !== undefined) throw new Error("request_invalid");
}

export function assertRoute(request: Request, method: "GET" | "POST" | "PATCH", expectedSegments: readonly string[]): void {
  const url = requestUrl(request);
  if (request.method !== method || !url.pathname.startsWith("/") || url.pathname.endsWith("/")) throw new Error("request_invalid");
  let segments: string[];
  try { segments = url.pathname.slice(1).split("/").map((segment) => decodeURIComponent(segment)); }
  catch { throw new Error("request_invalid"); }
  if (segments.length !== expectedSegments.length || segments.some((segment, index) => segment !== expectedSegments[index])) throw new Error("request_invalid");
}

function requestUrl(request: Request): URL {
  assertRequest(request);
  try { return new URL(request.url); } catch { throw new Error("request_invalid"); }
}

function requestHeader(request: Request, name: string): string | null {
  assertRequest(request);
  let value: string | null;
  try { value = request.headers.get(name); } catch { throw new Error("request_invalid"); }
  if (value !== null && (value.includes("\0") || value.includes("\r") || value.includes("\n"))) throw new Error("request_invalid");
  return value;
}

export function query(request: Request, allowed: readonly string[]): Readonly<Record<string, string>> {
  const parameters = requestUrl(request).searchParams;
  const output: Record<string, string> = Object.create(null) as Record<string, string>;
  for (const key of new Set(parameters.keys())) {
    if (!allowed.includes(key) || parameters.getAll(key).length !== 1) throw new Error("request_invalid");
    output[key] = parameters.get(key)!;
  }
  if (Object.keys(output).length !== allowed.length || allowed.some((key) => output[key] === undefined)) throw new Error("request_invalid");
  if (output.catalogDigest !== undefined && !DIGEST.test(output.catalogDigest)) throw new Error("request_invalid");
  return Object.freeze(output);
}

export async function params<P extends Record<string, string>>(context: RouteContext<P>, allowed: readonly (keyof P)[]): Promise<Readonly<P>> {
  let candidate: unknown;
  try { candidate = await context.params; } catch { throw new Error("request_invalid"); }
  if (candidate === null || typeof candidate !== "object" || utilTypes.isProxy(candidate)) throw new Error("request_invalid");
  const prototype = Object.getPrototypeOf(candidate);
  if (prototype !== Object.prototype && prototype !== null) throw new Error("request_invalid");
  const keys = Reflect.ownKeys(candidate);
  if (keys.length !== allowed.length || keys.some((key) => typeof key !== "string" || !allowed.includes(key))) throw new Error("request_invalid");
  const output: Record<string, string> = Object.create(null) as Record<string, string>;
  for (const key of keys as string[]) {
    const descriptor = Object.getOwnPropertyDescriptor(candidate, key);
    if (descriptor === undefined || !("value" in descriptor) || typeof descriptor.value !== "string" || !ID.test(descriptor.value)) throw new Error("request_invalid");
    output[key] = descriptor.value;
  }
  return Object.freeze(output) as Readonly<P>;
}

export function idempotencyKey(request: Request): string {
  const value = requestHeader(request, "idempotency-key");
  if (value === null) throw new Error("idempotency_key_required");
  if (!IDEMPOTENCY.test(value)) throw new Error("idempotency_key_invalid");
  return value;
}

function inspectJson(value: unknown, depth = 0): void {
  if (depth > MAX_DEPTH) throw new Error("request_invalid");
  if (value === null || typeof value === "boolean") return;
  if (typeof value === "string") { if (value.length > MAX_STRING) throw new Error("request_invalid"); return; }
  if (typeof value === "number") { if (!Number.isFinite(value)) throw new Error("request_invalid"); return; }
  if (Array.isArray(value)) { if (value.length > MAX_ARRAY) throw new Error("request_invalid"); for (const item of value) inspectJson(item, depth + 1); return; }
  if (typeof value !== "object" || utilTypes.isProxy(value) || Object.getPrototypeOf(value) !== Object.prototype) throw new Error("request_invalid");
  const keys = Reflect.ownKeys(value);
  if (keys.length > MAX_KEYS) throw new Error("request_invalid");
  for (const key of keys) {
    if (typeof key !== "string" || FORBIDDEN_KEYS.has(key)) throw new Error("request_invalid");
    const descriptor = Object.getOwnPropertyDescriptor(value, key);
    if (descriptor === undefined || !("value" in descriptor) || !descriptor.enumerable) throw new Error("request_invalid");
    inspectJson(descriptor.value, depth + 1);
  }
}

export async function jsonBody(request: Request): Promise<unknown> {
  const contentType = requestHeader(request, "content-type");
  if (contentType === null || !/^application\/json(?:; charset=utf-8)?$/i.test(contentType)) throw new Error("request_invalid");
  assertRequest(request);
  const stream = request.body;
  if (stream === null) throw new Error("request_invalid");
  const reader = stream.getReader();
  const chunks: Uint8Array[] = [];
  let size = 0;
  try {
    while (true) {
      const item = await reader.read();
      if (item.done) break;
      size += item.value.byteLength;
      if (size > MAX_BODY_BYTES) throw new Error("request_invalid");
      chunks.push(item.value);
    }
  } catch (error) {
    await reader.cancel().catch(() => undefined);
    if (safeErrorReason(error) === "request_invalid") throw new Error("request_invalid");
    throw new Error("request_invalid");
  }
  const bytes = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
  let parsed: unknown;
  try { parsed = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes)) as unknown; } catch { throw new Error("request_invalid"); }
  inspectJson(parsed);
  return parsed;
}

export function strictObject(value: unknown, allowed: readonly string[]): Readonly<Record<string, unknown>> {
  inspectJson(value);
  if (value === null || typeof value !== "object" || Array.isArray(value)) throw new Error("request_invalid");
  const keys = Object.keys(value);
  if (keys.length !== allowed.length || keys.some((key) => !allowed.includes(key))) throw new Error("request_invalid");
  const output: Record<string, unknown> = Object.create(null) as Record<string, unknown>;
  for (const key of keys) output[key] = (value as Record<string, unknown>)[key];
  return Object.freeze(output);
}

function status(components: readonly z.infer<typeof Component>[]): "available" | "partially_available" {
  return components.every((component) => component.availability.status === "available" || component.availability.status === "installed")
    ? "available" : "partially_available";
}

function inspectOutput(value: unknown, depth = 0): void {
  if (depth > 16) throw new Error("response_invalid");
  if (value === null || typeof value === "boolean") return;
  if (typeof value === "string") { if (value.length > MAX_STRING) throw new Error("response_invalid"); return; }
  if (typeof value === "number") { if (!Number.isFinite(value)) throw new Error("response_invalid"); return; }
  if (typeof value !== "object" || utilTypes.isProxy(value)) throw new Error("response_invalid");
  if (Array.isArray(value)) {
    if (Object.getPrototypeOf(value) !== Array.prototype || value.length > 512) throw new Error("response_invalid");
    for (let index = 0; index < value.length; index += 1) {
      const descriptor = Object.getOwnPropertyDescriptor(value, String(index));
      if (descriptor === undefined || !("value" in descriptor) || !descriptor.enumerable) throw new Error("response_invalid");
      inspectOutput(descriptor.value, depth + 1);
    }
    return;
  }
  const prototype = Object.getPrototypeOf(value);
  if (prototype !== Object.prototype && prototype !== null) throw new Error("response_invalid");
  const keys = Reflect.ownKeys(value);
  if (keys.length > MAX_KEYS) throw new Error("response_invalid");
  for (const key of keys) {
    if (typeof key !== "string" || FORBIDDEN_KEYS.has(key)) throw new Error("response_invalid");
    const descriptor = Object.getOwnPropertyDescriptor(value, key);
    if (descriptor === undefined || !("value" in descriptor) || !descriptor.enumerable) throw new Error("response_invalid");
    inspectOutput(descriptor.value, depth + 1);
  }
}

function parsed<T>(schema: z.ZodType<T>, value: unknown): T {
  inspectOutput(value);
  const result = schema.safeParse(value);
  if (!result.success) throw new Error("response_invalid");
  return result.data;
}

export function catalogResponse(value: unknown, selectedName?: string): Response {
  const items = parsed(z.array(CatalogItem).max(512), value).map((item) => ({ ...item, status: status(item.components) }));
  if (selectedName !== undefined) {
    const selected = items.find((item) => item.name === selectedName && item.pluginName === selectedName);
    if (selected === undefined) throw new Error("plugin_not_found");
    return pluginJson(selected);
  }
  return pluginJson({ items });
}

export function projectListResponse(value: unknown): Response {
  const items = parsed(z.array(ProjectItem).max(512), value).map((item) => ({ ...item, status: status(item.components) }));
  return pluginJson({ items });
}

export function previewResponse(value: unknown): Response {
  const item = parsed(Preview, value);
  return pluginJson({ ...item, status: status(item.components) });
}

export function installResponse(value: unknown): Response { return pluginJson(parsed(InstallReceipt, value), 201); }

export function installationResponse(value: unknown, catalogDigest: string): Response {
  const item = parsed(Installation, value);
  return pluginJson({
    projectId: item.projectId, pluginName: item.pluginName, pluginVersion: item.pluginVersion,
    catalogDigest, policyVersion: item.policyVersion, enabled: item.enabled, revision: item.revision,
  });
}
