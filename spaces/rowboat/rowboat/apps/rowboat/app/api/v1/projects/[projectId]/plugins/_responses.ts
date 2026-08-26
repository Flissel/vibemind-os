import { types as utilTypes } from "node:util";
import { NextRequest } from "next/server";
import { z } from "zod";

const DIGEST = /^[a-f0-9]{64}$/;
const COMMIT = /^[a-f0-9]{40}$/;
const ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/;
const IDEMPOTENCY = /^[A-Za-z0-9][A-Za-z0-9._~-]{0,127}$/;
const SAFE_TEXT = /^[\x20-\x7e]{1,256}$/;
const MAX_BODY_BYTES = 65_536;
const MAX_DEPTH = 8;
const MAX_KEYS = 64;
const MAX_ARRAY = 64;
const MAX_STRING = 16_384;
const MAX_URL = 4_096;
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
export interface PluginSessionControllerLike {
  execute(request: Request): Promise<unknown>;
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

const PluginSession = z.object({ kind: z.enum(["user", "project_api_key"]), id: z.string().regex(ID) }).strict();
export function pluginSessionResponse(value: unknown): Response {
  return pluginJson(parsed(PluginSession, value));
}

const ERROR_STATUS = Object.freeze({
  unauthenticated: 401, user_authentication_required: 401, authorization_invalid: 400, forbidden: 403,
  plugin_not_found: 404, installation_not_found: 404, idempotency_conflict: 409, installation_conflict: 409,
  catalog_digest_mismatch: 409, provider_unavailable: 503, license_rejected: 422, component_not_admitted: 422,
  request_invalid: 400, catalog_digest_invalid: 400, project_id_invalid: 400, plugin_name_invalid: 400,
  idempotency_key_required: 400, idempotency_key_invalid: 400, installation_revision_invalid: 400,
  installation_update_invalid: 400, request_aborted: 400, request_timeout: 408,
  migration_request_invalid: 400, migration_confirmation_required: 400, migration_confirmation_invalid: 403,
  migration_confirmation_expired: 403, migration_confirmation_replayed: 409, migration_preview_stale: 409,
  migration_preview_blocked: 409, migration_snapshot_invalid: 409, migration_snapshot_stale: 409,
  migration_pointer_invalid: 409, migration_pointer_conflict: 409, migration_installation_conflict: 409,
  migration_admission_conflict: 409, migration_record_conflict: 409, migration_rollback_conflict: 409,
  migration_idempotency_conflict: 409, migration_snapshot_changed: 409, project_not_found: 404,
  migration_project_invalid: 400, migration_project_limit: 413, migration_manifest_limit: 413, migration_preview_timeout: 408,
  migration_confirmation_secret_invalid: 503, migration_repository_failed: 500, migration_system_failed: 500,
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

const NEXT_REQUEST_URL_GETTER = Object.getOwnPropertyDescriptor(NextRequest.prototype, "url")?.get;
const REQUEST_METHOD_GETTER = Object.getOwnPropertyDescriptor(Request.prototype, "method")?.get;
const REQUEST_HEADERS_GETTER = Object.getOwnPropertyDescriptor(Request.prototype, "headers")?.get;
const REQUEST_BODY_GETTER = Object.getOwnPropertyDescriptor(Request.prototype, "body")?.get;
const REQUEST_SIGNAL_GETTER = Object.getOwnPropertyDescriptor(Request.prototype, "signal")?.get;
const HEADERS_GET = Headers.prototype.get;

function validatedHeaders(request: Request): Headers {
  let candidate: unknown;
  try { candidate = REQUEST_HEADERS_GETTER!.call(request); } catch { throw new Error("request_invalid"); }
  if (candidate === null || typeof candidate !== "object" || utilTypes.isProxy(candidate)
    || Object.getPrototypeOf(candidate) !== Headers.prototype || Object.getOwnPropertyDescriptor(candidate, "get") !== undefined) {
    throw new Error("request_invalid");
  }
  return candidate as Headers;
}

function assertRequest(request: Request): asserts request is NextRequest {
  if (utilTypes.isProxy(request) || Object.getPrototypeOf(request) !== NextRequest.prototype) throw new Error("request_invalid");
  if (NEXT_REQUEST_URL_GETTER === undefined || REQUEST_METHOD_GETTER === undefined || REQUEST_HEADERS_GETTER === undefined || REQUEST_BODY_GETTER === undefined || REQUEST_SIGNAL_GETTER === undefined) throw new Error("request_invalid");
  for (const key of ["method", "url", "headers", "body", "signal"]) if (Object.getOwnPropertyDescriptor(request, key) !== undefined) throw new Error("request_invalid");
  try {
    NEXT_REQUEST_URL_GETTER.call(request);
    REQUEST_METHOD_GETTER.call(request);
    REQUEST_HEADERS_GETTER.call(request);
    REQUEST_BODY_GETTER.call(request);
    REQUEST_SIGNAL_GETTER.call(request);
  } catch { throw new Error("request_invalid"); }
  validatedHeaders(request);
}

export function assertRoute(request: Request, method: "GET" | "POST" | "PATCH", expectedSegments: readonly string[]): void {
  const { raw, url } = requestUrl(request);
  let actualMethod: string;
  try { actualMethod = REQUEST_METHOD_GETTER!.call(request) as string; } catch { throw new Error("request_invalid"); }
  const expectedPath = `/${expectedSegments.join("/")}`;
  if (
    actualMethod !== method || url.pathname !== expectedPath || raw !== `${url.origin}${url.pathname}${url.search}`
    || url.username !== "" || url.password !== "" || url.hash !== "" || url.pathname.endsWith("/")
  ) throw new Error("request_invalid");
}

export function assertRouteWithoutQuery(request: Request, method: "GET" | "POST" | "PATCH", expectedSegments: readonly string[]): void {
  assertRoute(request, method, expectedSegments);
  const { raw, url } = requestUrl(request);
  if (url.search !== "" || raw !== `${url.origin}${url.pathname}`) throw new Error("request_invalid");
}

function requestUrl(request: Request): Readonly<{ raw: string; url: URL }> {
  assertRequest(request);
  const candidates: string[] = [];
  for (const symbol of Object.getOwnPropertySymbols(request)) {
    const stateDescriptor = Object.getOwnPropertyDescriptor(request, symbol);
    if (stateDescriptor === undefined || !("value" in stateDescriptor) || stateDescriptor.value === null
      || typeof stateDescriptor.value !== "object" || utilTypes.isProxy(stateDescriptor.value)) continue;
    const urlDescriptor = Object.getOwnPropertyDescriptor(stateDescriptor.value, "url");
    if (urlDescriptor !== undefined && "value" in urlDescriptor && typeof urlDescriptor.value === "string") candidates.push(urlDescriptor.value);
  }
  if (candidates.length !== 1) throw new Error("request_invalid");
  const raw = candidates[0]!;
  if (raw.length === 0 || raw.length > MAX_URL || /[%\\\0\r\n#]/.test(raw)) throw new Error("request_invalid");
  let url: URL;
  try { url = new URL(raw); } catch { throw new Error("request_invalid"); }
  return Object.freeze({ raw, url });
}

function requestHeader(request: Request, name: string): string | null {
  assertRequest(request);
  let value: string | null;
  try { value = HEADERS_GET.call(validatedHeaders(request), name); } catch { throw new Error("request_invalid"); }
  if (value !== null && (value.includes("\0") || value.includes("\r") || value.includes("\n"))) throw new Error("request_invalid");
  return value;
}

export function query(request: Request, allowed: readonly string[]): Readonly<Record<string, string>> {
  const { raw, url } = requestUrl(request);
  const marker = raw.indexOf("?");
  if (marker < 0 || raw.indexOf("?", marker + 1) >= 0 || url.search.length <= 1) throw new Error("request_invalid");
  const output: Record<string, string> = Object.create(null) as Record<string, string>;
  for (const pair of raw.slice(marker + 1).split("&")) {
    const equals = pair.indexOf("=");
    if (equals <= 0 || pair.indexOf("=", equals + 1) >= 0) throw new Error("request_invalid");
    const key = pair.slice(0, equals);
    const value = pair.slice(equals + 1);
    if (!allowed.includes(key) || output[key] !== undefined || value.length === 0) throw new Error("request_invalid");
    output[key] = value;
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
  if (typeof value !== "object" || utilTypes.isProxy(value)) throw new Error("request_invalid");
  const prototype = Object.getPrototypeOf(value);
  if (prototype !== Object.prototype && prototype !== null) throw new Error("request_invalid");
  const keys = Reflect.ownKeys(value);
  if (keys.length > MAX_KEYS) throw new Error("request_invalid");
  for (const key of keys) {
    if (typeof key !== "string" || FORBIDDEN_KEYS.has(key)) throw new Error("request_invalid");
    const descriptor = Object.getOwnPropertyDescriptor(value, key);
    if (descriptor === undefined || !("value" in descriptor) || !descriptor.enumerable) throw new Error("request_invalid");
    inspectJson(descriptor.value, depth + 1);
  }
}

class BoundedJsonParser {
  private index = 0;
  private totalKeys = 0;

  constructor(private readonly text: string) {}

  parse(): unknown {
    this.whitespace();
    const value = this.value(0);
    this.whitespace();
    if (this.index !== this.text.length) throw new Error("request_invalid");
    return value;
  }

  private value(depth: number): unknown {
    if (depth > MAX_DEPTH) throw new Error("request_invalid");
    this.whitespace();
    const character = this.text[this.index];
    if (character === '"') return this.string();
    if (character === "{") return this.object(depth);
    if (character === "[") return this.array(depth);
    if (character === "t") return this.literal("true", true);
    if (character === "f") return this.literal("false", false);
    if (character === "n") return this.literal("null", null);
    return this.number();
  }

  private object(depth: number): Readonly<Record<string, unknown>> {
    this.index += 1;
    const output: Record<string, unknown> = Object.create(null) as Record<string, unknown>;
    const names = new Set<string>();
    this.whitespace();
    if (this.text[this.index] === "}") { this.index += 1; return Object.freeze(output); }
    while (true) {
      if (this.text[this.index] !== '"') throw new Error("request_invalid");
      const key = this.string();
      this.totalKeys += 1;
      if (this.totalKeys > MAX_KEYS || names.has(key) || FORBIDDEN_KEYS.has(key)) throw new Error("request_invalid");
      names.add(key);
      this.whitespace();
      if (this.text[this.index] !== ":") throw new Error("request_invalid");
      this.index += 1;
      const selected = this.value(depth + 1);
      Object.defineProperty(output, key, { value: selected, enumerable: true, writable: false, configurable: false });
      this.whitespace();
      const separator = this.text[this.index];
      this.index += 1;
      if (separator === "}") return Object.freeze(output);
      if (separator !== ",") throw new Error("request_invalid");
      this.whitespace();
    }
  }

  private array(depth: number): readonly unknown[] {
    this.index += 1;
    const output: unknown[] = [];
    this.whitespace();
    if (this.text[this.index] === "]") { this.index += 1; return Object.freeze(output); }
    while (true) {
      if (output.length >= MAX_ARRAY) throw new Error("request_invalid");
      output.push(this.value(depth + 1));
      this.whitespace();
      const separator = this.text[this.index];
      this.index += 1;
      if (separator === "]") return Object.freeze(output);
      if (separator !== ",") throw new Error("request_invalid");
      this.whitespace();
    }
  }

  private string(): string {
    this.index += 1;
    let output = "";
    while (this.index < this.text.length) {
      const character = this.text[this.index]!;
      this.index += 1;
      if (character === '"') return output;
      if (character === "\\") {
        const escaped = this.text[this.index];
        this.index += 1;
        const simple: Readonly<Record<string, string>> = Object.freeze({ '"': '"', "\\": "\\", "/": "/", b: "\b", f: "\f", n: "\n", r: "\r", t: "\t" });
        if (escaped === "u") {
          const hex = this.text.slice(this.index, this.index + 4);
          if (!/^[a-fA-F0-9]{4}$/.test(hex)) throw new Error("request_invalid");
          output += String.fromCharCode(Number.parseInt(hex, 16));
          this.index += 4;
        } else if (escaped !== undefined && simple[escaped] !== undefined) output += simple[escaped];
        else throw new Error("request_invalid");
      } else {
        if (character.charCodeAt(0) < 0x20) throw new Error("request_invalid");
        output += character;
      }
      if (output.length > MAX_STRING) throw new Error("request_invalid");
    }
    throw new Error("request_invalid");
  }

  private number(): number {
    const match = /^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?/.exec(this.text.slice(this.index));
    if (match === null) throw new Error("request_invalid");
    this.index += match[0].length;
    const value = Number(match[0]);
    if (!Number.isFinite(value)) throw new Error("request_invalid");
    return value;
  }

  private literal<T extends boolean | null>(literal: string, value: T): T {
    if (!this.text.startsWith(literal, this.index)) throw new Error("request_invalid");
    this.index += literal.length;
    return value;
  }

  private whitespace(): void {
    while (this.index < this.text.length && /[\x20\x09\x0a\x0d]/.test(this.text[this.index]!)) this.index += 1;
  }
}

export async function jsonBody(request: Request, timeoutMs = 5_000): Promise<unknown> {
  const contentType = requestHeader(request, "content-type");
  if (contentType === null || !/^application\/json(?:; charset=utf-8)?$/i.test(contentType)) throw new Error("request_invalid");
  if (!Number.isSafeInteger(timeoutMs) || timeoutMs < 1 || timeoutMs > 30_000) throw new Error("request_invalid");
  assertRequest(request);
  const stream = REQUEST_BODY_GETTER!.call(request) as ReadableStream<Uint8Array> | null;
  if (stream === null) throw new Error("request_invalid");
  const signal = REQUEST_SIGNAL_GETTER!.call(request) as AbortSignal;
  if (signal.aborted) throw new Error("request_aborted");
  const reader = stream.getReader();
  const chunks: Uint8Array[] = [];
  let size = 0;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let rejectBoundary: ((reason: Error) => void) | undefined;
  const boundary = new Promise<never>((_resolve, reject) => { rejectBoundary = reject; });
  const onAbort = () => { rejectBoundary?.(new Error("request_aborted")); };
  signal.addEventListener("abort", onAbort, { once: true });
  timer = setTimeout(() => { rejectBoundary?.(new Error("request_timeout")); }, timeoutMs);
  const cleanupBoundary = () => {
    if (timer !== undefined) clearTimeout(timer);
    timer = undefined;
    rejectBoundary = undefined;
    signal.removeEventListener("abort", onAbort);
  };
  const release = () => { try { reader.releaseLock(); } catch { /* pending reads release after cancellation */ } };
  const cancel = () => {
    try { void reader.cancel().then(release, release); } catch { release(); }
  };
  try {
    while (true) {
      const item = await Promise.race([reader.read(), boundary]);
      if (item.done) break;
      size += item.value.byteLength;
      if (size > MAX_BODY_BYTES) throw new Error("request_invalid");
      chunks.push(item.value);
    }
  } catch (error) {
    cleanupBoundary();
    cancel();
    const reason = safeErrorReason(error);
    if (reason === "request_invalid" || reason === "request_aborted" || reason === "request_timeout") throw new Error(reason);
    throw new Error("request_invalid");
  }
  cleanupBoundary();
  release();
  const bytes = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
  let parsed: unknown;
  try { parsed = new BoundedJsonParser(new TextDecoder("utf-8", { fatal: true }).decode(bytes)).parse(); } catch { throw new Error("request_invalid"); }
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

function serializedComponents(components: readonly z.infer<typeof Component>[]) {
  return components.map((component) => {
    const availabilityReasons: Readonly<Record<z.infer<typeof Availability>["status"], readonly z.infer<typeof Reason>[]>> = Object.freeze({
      available: [], installed: [], review_required: ["license_review_required", "write_review_required"],
      partially_available: ["provider_unavailable", "credential_missing", "component_unsupported"],
      unavailable: ["provider_unavailable", "credential_missing", "component_unsupported"], unsupported: ["component_unsupported"],
      migration_required: ["migration_conflict"],
      error: ["source_mismatch", "manifest_invalid", "path_escape", "digest_mismatch", "provider_unavailable", "credential_missing", "parity_failed", "rollback_unavailable"],
      invalid: ["source_mismatch", "manifest_invalid", "path_escape", "digest_mismatch", "parity_failed"],
    });
    const allowed = availabilityReasons[component.availability.status];
    if ((allowed.length === 0 && component.availability.reason !== undefined)
      || (allowed.length > 0 && (component.availability.reason === undefined || !allowed.includes(component.availability.reason)))) throw new Error("response_invalid");
    if (component.admission.status === "review_required" && !(["license_review_required", "write_review_required"] as readonly string[]).includes(component.admission.reason)) throw new Error("response_invalid");
    if (component.admission.status === "rejected" && !(["license_rejected", "http_mcp_not_admitted", "process_not_admitted", "hook_not_admitted", "component_unsupported"] as readonly string[]).includes(component.admission.reason)) throw new Error("response_invalid");
    if (component.admission.status !== "admitted") {
      return { ...component, status: component.admission.status, reason: component.admission.reason };
    }
    return {
      ...component,
      status: component.availability.status,
      ...(component.availability.reason === undefined ? {} : { reason: component.availability.reason }),
    };
  });
}

function serializedPlugin<T extends { readonly admission: "admitted" | "review_required" | "rejected"; readonly reason?: z.infer<typeof Reason>; readonly license: z.infer<typeof License>; readonly components: readonly z.infer<typeof Component>[] }>(item: T) {
  const components = serializedComponents(item.components);
  const derived = item.admission === "admitted"
    ? (components.every((component) => component.status === "available" || component.status === "installed") ? "available" : "partially_available")
    : item.admission;
  if (item.admission === "admitted") {
    if (item.license.decision !== "admitted") throw new Error("response_invalid");
    const componentReason = components.find((component) => component.reason !== undefined)?.reason;
    if (item.reason !== undefined && item.reason !== componentReason) throw new Error("response_invalid");
    if (components.every((component) => component.status === "available" || component.status === "installed") && item.reason !== undefined) throw new Error("response_invalid");
  } else {
    if (item.license.decision === "admitted" || item.license.decision !== item.admission || item.reason === undefined || item.license.reason !== item.reason) throw new Error("response_invalid");
    if (item.admission === "review_required" && !(["license_review_required", "write_review_required"] as readonly string[]).includes(item.reason)) throw new Error("response_invalid");
    if (item.admission === "rejected" && !(["license_rejected", "http_mcp_not_admitted", "process_not_admitted", "hook_not_admitted", "component_unsupported"] as readonly string[]).includes(item.reason)) throw new Error("response_invalid");
  }
  return { ...item, components, status: derived };
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
  const items = parsed(z.array(CatalogItem).max(512), value).map(serializedPlugin);
  if (selectedName !== undefined) {
    const selected = items.find((item) => item.name === selectedName && item.pluginName === selectedName);
    if (selected === undefined) throw new Error("plugin_not_found");
    return pluginJson(selected);
  }
  return pluginJson({ items });
}

export function projectListResponse(value: unknown): Response {
  const items = parsed(z.array(ProjectItem).max(512), value).map(serializedPlugin);
  return pluginJson({ items });
}

export function previewResponse(value: unknown): Response {
  const item = parsed(Preview, value);
  return pluginJson(serializedPlugin(item));
}

export function installResponse(value: unknown): Response { return pluginJson(parsed(InstallReceipt, value), 201); }

export function installationResponse(value: unknown, catalogDigest: string): Response {
  const item = parsed(Installation, value);
  return pluginJson({
    projectId: item.projectId, pluginName: item.pluginName, pluginVersion: item.pluginVersion,
    catalogDigest, policyVersion: item.policyVersion, enabled: item.enabled, revision: item.revision,
  });
}
