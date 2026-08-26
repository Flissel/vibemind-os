import { z } from 'zod';

export const PLUGIN_STATUSES = [
  'available', 'review_required', 'installed', 'partially_available', 'unavailable', 'migration_required', 'error',
] as const;
export const PLUGIN_REASON_CODES = [
  'source_mismatch', 'manifest_invalid', 'path_escape', 'digest_mismatch', 'license_review_required', 'license_rejected',
  'provider_unavailable', 'credential_missing', 'http_mcp_not_admitted', 'process_not_admitted', 'hook_not_admitted',
  'write_review_required', 'component_unsupported', 'migration_conflict', 'parity_failed', 'rollback_unavailable',
] as const;

export type PluginStatus = typeof PLUGIN_STATUSES[number];
export type PluginReasonCode = typeof PLUGIN_REASON_CODES[number];

export const DESKTOP_PLUGIN_CATALOG_PIN = Object.freeze({
  catalogDigest: '2e436d02b025a14960d5ef813c603bd7aec35a6d173c42d8c58274163da89a92',
  sourceCommit: '11c74d6ba24d3a6d48f54a194cd00ef3beea18f9',
  policyVersion: 'rowboat-plugin-policy-v1',
});

export interface PluginApiSession {
  readonly baseUrl: string;
  readonly accessToken: string;
  readonly accountFingerprint: string;
}

const DIGEST = /^[a-f0-9]{64}$/;
const COMMIT = /^[a-f0-9]{40}$/;
const ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/;
const IDEMPOTENCY = /^[A-Za-z0-9][A-Za-z0-9._~-]{0,127}$/;
const SAFE_TEXT = /^[\x20-\x7e]{1,256}$/;
const MAX_RESPONSE_BYTES = 262_144;
const MAX_DEPTH = 16;
const MAX_KEYS = 2_048;
const MAX_ARRAY = 512;
const MAX_STRING = 16_384;
const FORBIDDEN_KEYS = new Set(['__proto__', 'prototype', 'constructor', 'toJSON']);

const Reason = z.enum(PLUGIN_REASON_CODES);
const RawStatus = z.enum([...PLUGIN_STATUSES, 'invalid', 'unsupported', 'rejected']);
const Admission = z.union([
  z.object({ status: z.literal('admitted'), policyVersion: z.string().regex(ID) }).strict(),
  z.object({ status: z.enum(['review_required', 'rejected']), reason: Reason, policyVersion: z.string().regex(ID) }).strict(),
]);
const Availability = z.object({ status: RawStatus, reason: Reason.optional() }).strict();
const Component = z.object({
  componentDigest: z.string().regex(DIGEST),
  name: z.string().regex(/^[A-Za-z0-9][A-Za-z0-9._ ()+@-]{0,127}$/),
  kind: z.enum(['skill', 'agent', 'command', 'mcp', 'app', 'hook', 'asset']),
  admission: Admission,
  availability: Availability,
  status: RawStatus,
  reason: Reason.optional(),
}).strict();
const License = z.union([
  z.object({ declaration: z.string().regex(SAFE_TEXT), decision: z.literal('admitted') }).strict(),
  z.object({ declaration: z.string().regex(SAFE_TEXT), decision: z.enum(['review_required', 'rejected']), reason: Reason }).strict(),
]);
const PluginTruth = {
  pluginName: z.string().regex(ID), catalogDigest: z.string().regex(DIGEST), policyVersion: z.string().regex(ID),
  license: License, admission: z.enum(['admitted', 'review_required', 'rejected']), reason: Reason.optional(),
  components: z.array(Component).max(MAX_ARRAY), status: RawStatus,
} as const;
const CatalogItem = z.object({
  ...PluginTruth, name: z.string().regex(ID), pluginVersion: z.string().regex(SAFE_TEXT), sourceCommit: z.string().regex(COMMIT),
}).strict();
const CatalogEnvelope = z.object({ items: z.array(CatalogItem).max(MAX_ARRAY) }).strict();
const PreviewEnvelope = z.object({
  ...PluginTruth, sourceCommit: z.string().regex(COMMIT),
  credentialSlots: z.array(z.object({ name: z.string().regex(ID), configured: z.boolean() }).strict()).max(128),
}).strict();
const ProjectItem = z.object({
  ...PluginTruth, pluginVersion: z.string().regex(SAFE_TEXT), enabled: z.boolean(), revision: z.number().int().nonnegative(),
}).strict();
const ProjectEnvelope = z.object({ items: z.array(ProjectItem).max(MAX_ARRAY) }).strict();
const Receipt = z.object({
  type: z.literal('install'), receiptId: z.string().regex(ID), projectId: z.string().regex(ID), pluginName: z.string().regex(ID),
  status: z.enum(['success', 'failed', 'denied', 'timed_out']), reason: Reason.optional(), redactions: z.array(z.string().regex(ID)).max(64),
}).strict();

type RawCatalogItem = z.infer<typeof CatalogItem>;
export type PluginCatalogItem = Omit<RawCatalogItem, 'status'> & { readonly status: PluginStatus };
export interface PluginCatalogResponse { readonly items: readonly PluginCatalogItem[] }
export type PluginPreview = Omit<z.infer<typeof PreviewEnvelope>, 'status'> & { readonly status: PluginStatus };
export type PluginInstallReceipt = z.infer<typeof Receipt>;
export type PluginProjectItem = Omit<z.infer<typeof ProjectItem>, 'status'> & { readonly status: PluginStatus };
interface PreviewContext {
  readonly projectId: string; readonly pluginName: string; readonly catalogDigest: string; readonly expectedRevision: number;
  readonly origin: string; readonly accountFingerprint: string; readonly idempotencyKey: string;
}

interface ApiOptions {
  readonly timeoutMs?: number;
  readonly createIdempotencyKey?: () => string;
}

interface InstallInput {
  readonly projectId: string;
  readonly pluginName: string;
  readonly catalogDigest: string;
}

function fail(reason: 'plugin_api_config_invalid' | 'plugin_api_request_invalid' | 'plugin_api_response_invalid' | 'plugin_api_unavailable' | 'plugin_api_aborted' | 'plugin_api_timeout' | 'plugin_api_scope_changed'): never {
  throw new Error(reason);
}

function exactRecord(input: unknown, keys: readonly string[], failure: 'plugin_api_config_invalid' | 'plugin_api_request_invalid'): Readonly<Record<string, unknown>> {
  if (input === null || typeof input !== 'object' || Array.isArray(input)) fail(failure);
  const prototype = Object.getPrototypeOf(input);
  if (prototype !== Object.prototype && prototype !== null) fail(failure);
  const ownKeys = Reflect.ownKeys(input);
  if (ownKeys.length !== keys.length || ownKeys.some((key) => typeof key !== 'string' || !keys.includes(key))) fail(failure);
  const result: Record<string, unknown> = Object.create(null) as Record<string, unknown>;
  for (const key of ownKeys as string[]) {
    const descriptor = Object.getOwnPropertyDescriptor(input, key);
    if (descriptor === undefined || !('value' in descriptor) || !descriptor.enumerable) fail(failure);
    result[key] = descriptor.value;
  }
  return Object.freeze(result);
}

export function canonicalPluginOrigin(baseUrl: string): string {
  if (baseUrl.length < 1 || baseUrl.length > 2_048 || /[\\\0\r\n\t ]/.test(baseUrl)) fail('plugin_api_config_invalid');
  let url: URL;
  try { url = new URL(baseUrl); } catch { fail('plugin_api_config_invalid'); }
  const loopback = url.hostname === 'localhost' || url.hostname === '127.0.0.1' || url.hostname === '[::1]';
  if ((url.protocol !== 'https:' && !(url.protocol === 'http:' && loopback))
    || url.username !== '' || url.password !== '' || url.search !== '' || url.hash !== '' || url.pathname !== '/') {
    fail('plugin_api_config_invalid');
  }
  return url.origin;
}

function validateSession(input: PluginApiSession): Readonly<{ origin: string; accessToken: string; accountFingerprint: string }> {
  const record = exactRecord(input, ['baseUrl', 'accessToken', 'accountFingerprint'], 'plugin_api_config_invalid');
  if (typeof record.accountFingerprint !== 'string' || !DIGEST.test(record.accountFingerprint)) fail('plugin_api_config_invalid');
  if (typeof record.baseUrl !== 'string' || record.baseUrl.length < 1 || record.baseUrl.length > 2_048 || /[\\\0\r\n\t ]/.test(record.baseUrl)
    || typeof record.accessToken !== 'string' || record.accessToken.length < 1 || record.accessToken.length > 16_384
    || /[,\s\0]/.test(record.accessToken)) fail('plugin_api_config_invalid');
  return Object.freeze({ origin: canonicalPluginOrigin(record.baseUrl), accessToken: record.accessToken, accountFingerprint: record.accountFingerprint });
}

function validateId(value: unknown): string {
  if (typeof value !== 'string' || !ID.test(value)) fail('plugin_api_request_invalid');
  return value;
}

function validateDigest(value: unknown): string {
  if (typeof value !== 'string' || !DIGEST.test(value)) fail('plugin_api_request_invalid');
  return value;
}

class BoundedJsonParser {
  private index = 0;
  private keyCount = 0;
  private readonly text: string;
  constructor(text: string) { this.text = text; }
  parse(): unknown {
    this.whitespace();
    const result = this.value(0);
    this.whitespace();
    if (this.index !== this.text.length) fail('plugin_api_response_invalid');
    return result;
  }
  private value(depth: number): unknown {
    if (depth > MAX_DEPTH) fail('plugin_api_response_invalid');
    this.whitespace();
    const character = this.text[this.index];
    if (character === '"') return this.string();
    if (character === '{') return this.object(depth);
    if (character === '[') return this.array(depth);
    if (character === 't') return this.literal('true', true);
    if (character === 'f') return this.literal('false', false);
    if (character === 'n') return this.literal('null', null);
    return this.number();
  }
  private object(depth: number): Readonly<Record<string, unknown>> {
    this.index += 1;
    const output: Record<string, unknown> = Object.create(null) as Record<string, unknown>;
    const names = new Set<string>();
    this.whitespace();
    if (this.text[this.index] === '}') { this.index += 1; return Object.freeze(output); }
    while (true) {
      if (this.text[this.index] !== '"') fail('plugin_api_response_invalid');
      const key = this.string();
      this.keyCount += 1;
      if (this.keyCount > MAX_KEYS || names.has(key) || FORBIDDEN_KEYS.has(key)) fail('plugin_api_response_invalid');
      names.add(key);
      this.whitespace();
      if (this.text[this.index] !== ':') fail('plugin_api_response_invalid');
      this.index += 1;
      Object.defineProperty(output, key, { value: this.value(depth + 1), enumerable: true });
      this.whitespace();
      const separator = this.text[this.index++];
      if (separator === '}') return Object.freeze(output);
      if (separator !== ',') fail('plugin_api_response_invalid');
      this.whitespace();
    }
  }
  private array(depth: number): readonly unknown[] {
    this.index += 1;
    const output: unknown[] = [];
    this.whitespace();
    if (this.text[this.index] === ']') { this.index += 1; return Object.freeze(output); }
    while (true) {
      if (output.length >= MAX_ARRAY) fail('plugin_api_response_invalid');
      output.push(this.value(depth + 1));
      this.whitespace();
      const separator = this.text[this.index++];
      if (separator === ']') return Object.freeze(output);
      if (separator !== ',') fail('plugin_api_response_invalid');
      this.whitespace();
    }
  }
  private string(): string {
    this.index += 1;
    let output = '';
    while (this.index < this.text.length) {
      const character = this.text[this.index++]!;
      if (character === '"') return output;
      if (character === '\\') {
        const escaped = this.text[this.index++];
        const simple: Readonly<Record<string, string>> = Object.freeze({ '"': '"', '\\': '\\', '/': '/', b: '\b', f: '\f', n: '\n', r: '\r', t: '\t' });
        if (escaped === 'u') {
          const hex = this.text.slice(this.index, this.index + 4);
          if (!/^[a-fA-F0-9]{4}$/.test(hex)) fail('plugin_api_response_invalid');
          output += String.fromCharCode(Number.parseInt(hex, 16)); this.index += 4;
        } else if (escaped !== undefined && simple[escaped] !== undefined) output += simple[escaped];
        else fail('plugin_api_response_invalid');
      } else {
        if (character.charCodeAt(0) < 0x20) fail('plugin_api_response_invalid');
        output += character;
      }
      if (output.length > MAX_STRING) fail('plugin_api_response_invalid');
    }
    return fail('plugin_api_response_invalid');
  }
  private number(): number {
    const match = /^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?/.exec(this.text.slice(this.index));
    if (match === null) fail('plugin_api_response_invalid');
    this.index += match[0].length;
    const value = Number(match[0]);
    if (!Number.isFinite(value)) fail('plugin_api_response_invalid');
    return value;
  }
  private literal<T extends boolean | null>(literal: string, value: T): T {
    if (!this.text.startsWith(literal, this.index)) fail('plugin_api_response_invalid');
    this.index += literal.length; return value;
  }
  private whitespace(): void { while (/^[\x20\x09\x0a\x0d]$/.test(this.text[this.index] ?? '')) this.index += 1; }
}

function canonicalStatus(item: Readonly<{ status: z.infer<typeof RawStatus>; admission: 'admitted' | 'review_required' | 'rejected'; components: readonly z.infer<typeof Component>[] }>): PluginStatus {
  if (item.status === 'review_required' || item.admission === 'review_required') return 'review_required';
  if (item.status === 'rejected' || item.admission === 'rejected') return 'unavailable';
  const statuses = item.components.map((component) => component.status);
  if (item.status === 'installed') return 'installed';
  if (statuses.includes('migration_required') || item.status === 'migration_required') return 'migration_required';
  if (statuses.some((status) => status === 'error' || status === 'invalid') || item.status === 'error') return 'error';
  if (item.status === 'unavailable' || statuses.every((status) => status === 'unavailable' || status === 'unsupported')) return 'unavailable';
  if (item.status === 'partially_available' || statuses.some((status) => status !== 'available' && status !== 'installed')) return 'partially_available';
  return 'available';
}

function freeze<T>(value: T): T {
  if (Array.isArray(value)) { for (const item of value) freeze(item); return Object.freeze(value); }
  if (value !== null && typeof value === 'object') { for (const item of Object.values(value)) freeze(item); return Object.freeze(value); }
  return value;
}

function normalizeItem(item: RawCatalogItem): PluginCatalogItem {
  const reason = item.reason ?? item.components.find((component) => component.reason !== undefined)?.reason;
  return freeze({ ...item, status: canonicalStatus(item), ...(reason === undefined ? {} : { reason }) });
}

function validateComponentTruth(component: z.infer<typeof Component>): void {
  const expectedStatus = component.admission.status === 'admitted' ? component.availability.status : component.admission.status;
  const expectedReason = component.admission.status === 'admitted' ? component.availability.reason : component.admission.reason;
  if (component.status !== expectedStatus || component.reason !== expectedReason) fail('plugin_api_response_invalid');
  if (component.admission.status === 'admitted' && component.admission.policyVersion.length === 0) fail('plugin_api_response_invalid');
}

function validateCatalogBinding(parsed: z.infer<typeof CatalogEnvelope>, requestedDigest: string): void {
  if (parsed.items.length === 0) fail('plugin_api_response_invalid');
  const sourceCommit = parsed.items[0]!.sourceCommit;
  const policyVersion = parsed.items[0]!.policyVersion;
  const pluginNames = new Set<string>();
  for (const item of parsed.items) {
    if (item.catalogDigest !== requestedDigest || item.sourceCommit !== sourceCommit || item.policyVersion !== policyVersion
      || item.name !== item.pluginName || pluginNames.has(item.pluginName)) fail('plugin_api_response_invalid');
    if (requestedDigest === DESKTOP_PLUGIN_CATALOG_PIN.catalogDigest
      && (item.sourceCommit !== DESKTOP_PLUGIN_CATALOG_PIN.sourceCommit || item.policyVersion !== DESKTOP_PLUGIN_CATALOG_PIN.policyVersion)) {
      fail('plugin_api_response_invalid');
    }
    pluginNames.add(item.pluginName);
    if (item.license.decision !== item.admission) fail('plugin_api_response_invalid');
    const componentDigests = new Set<string>();
    for (const component of item.components) {
      validateComponentTruth(component);
      if (component.admission.policyVersion !== policyVersion || componentDigests.has(component.componentDigest)) fail('plugin_api_response_invalid');
      componentDigests.add(component.componentDigest);
    }
    const expectedTop = item.admission === 'admitted'
      ? (item.components.every((component) => component.status === 'available' || component.status === 'installed') ? 'available' : 'partially_available')
      : item.admission;
    if (item.status !== expectedTop) fail('plugin_api_response_invalid');
  }
}

function validatePluginTruth(item: z.infer<typeof PreviewEnvelope> | z.infer<typeof ProjectItem>, requestedDigest: string): void {
  if (item.catalogDigest !== requestedDigest || item.license.decision !== item.admission) fail('plugin_api_response_invalid');
  const componentDigests = new Set<string>();
  for (const component of item.components) {
    validateComponentTruth(component);
    if (component.admission.policyVersion !== item.policyVersion || componentDigests.has(component.componentDigest)) fail('plugin_api_response_invalid');
    componentDigests.add(component.componentDigest);
  }
  const expectedTop = item.admission === 'admitted'
    ? (item.components.every((component) => component.status === 'available' || component.status === 'installed') ? 'available' : 'partially_available')
    : item.admission;
  if (item.status !== expectedTop) fail('plugin_api_response_invalid');
}

function parseResponse<T>(schema: z.ZodType<T>, input: unknown): T {
  const result = schema.safeParse(input);
  if (!result.success) fail('plugin_api_response_invalid');
  return result.data;
}

export class RowboatPluginApi {
  private readonly fetcher: typeof fetch;
  private readonly timeoutMs: number;
  private readonly createIdempotencyKey: () => string;
  private requestCount = 0;
  private readonly previewContexts = new WeakMap<object, PreviewContext>();

  constructor(fetcher: typeof fetch = fetch, options: ApiOptions = {}) {
    this.fetcher = fetcher;
    this.timeoutMs = options.timeoutMs ?? 10_000;
    this.createIdempotencyKey = options.createIdempotencyKey ?? (() => crypto.randomUUID());
    if (!Number.isSafeInteger(this.timeoutMs) || this.timeoutMs < 1 || this.timeoutMs > 30_000) fail('plugin_api_config_invalid');
  }

  snapshot(): Readonly<{ requestCount: number }> { return Object.freeze({ requestCount: this.requestCount }); }

  async listCatalog(session: PluginApiSession, catalogDigest: string, signal?: AbortSignal): Promise<PluginCatalogResponse> {
    const requestedDigest = validateDigest(catalogDigest);
    const parsed = parseResponse(CatalogEnvelope, await this.request(session, `/api/v1/plugins?catalogDigest=${requestedDigest}`, { method: 'GET', signal }));
    validateCatalogBinding(parsed, requestedDigest);
    return freeze({ items: parsed.items.map(normalizeItem) });
  }

  async listProjectPlugins(session: PluginApiSession, projectIdInput: string, catalogDigestInput: string, signal?: AbortSignal): Promise<Readonly<{ items: readonly PluginProjectItem[] }>> {
    const projectId = validateId(projectIdInput);
    const catalogDigest = validateDigest(catalogDigestInput);
    const parsed = parseResponse(ProjectEnvelope, await this.request(session, `/api/v1/projects/${projectId}/plugins?catalogDigest=${catalogDigest}`, { method: 'GET', signal }));
    const pluginNames = new Set<string>();
    for (const item of parsed.items) {
      validatePluginTruth(item, catalogDigest);
      if (pluginNames.has(item.pluginName)) fail('plugin_api_response_invalid');
      pluginNames.add(item.pluginName);
    }
    return freeze({ items: parsed.items.map((item) => ({ ...item, status: canonicalStatus(item) })) });
  }

  async previewAndInstall(session: PluginApiSession, input: InstallInput, signal?: AbortSignal): Promise<Readonly<{ preview: PluginPreview; receipt: PluginInstallReceipt }>> {
    const preview = await this.previewInstallation(session, input, signal);
    const receipt = await this.installPreview(session, preview, signal);
    return freeze({ preview, receipt });
  }

  async previewInstallation(session: PluginApiSession, input: InstallInput, signal?: AbortSignal): Promise<PluginPreview> {
    const record = exactRecord(input, ['projectId', 'pluginName', 'catalogDigest'], 'plugin_api_request_invalid');
    const projectId = validateId(record.projectId);
    const pluginName = validateId(record.pluginName);
    const catalogDigest = validateDigest(record.catalogDigest);
    const project = await this.listProjectPlugins(session, projectId, catalogDigest, signal);
    const matches = project.items.filter((item) => item.pluginName === pluginName);
    if (matches.length > 1) fail('plugin_api_response_invalid');
    const expectedRevision = matches[0]?.revision ?? 0;
    const previewRaw = parseResponse(PreviewEnvelope, await this.request(session, `/api/v1/projects/${projectId}/plugins/${pluginName}?catalogDigest=${catalogDigest}`, { method: 'GET', signal }));
    if (previewRaw.pluginName !== pluginName || previewRaw.catalogDigest !== catalogDigest) fail('plugin_api_response_invalid');
    validatePluginTruth(previewRaw, catalogDigest);
    const preview = freeze({ ...previewRaw, status: canonicalStatus(previewRaw) });
    if (preview.status !== 'available') fail('plugin_api_request_invalid');
    const selectedSession = validateSession(session);
    const idempotencyKey = this.createIdempotencyKey();
    if (!IDEMPOTENCY.test(idempotencyKey)) fail('plugin_api_config_invalid');
    this.previewContexts.set(preview, Object.freeze({
      projectId, pluginName, catalogDigest, expectedRevision, idempotencyKey,
      origin: selectedSession.origin, accountFingerprint: selectedSession.accountFingerprint,
    }));
    return preview;
  }

  async installPreview(session: PluginApiSession, preview: PluginPreview, signal?: AbortSignal): Promise<PluginInstallReceipt> {
    const context = preview !== null && typeof preview === 'object' ? this.previewContexts.get(preview) : undefined;
    if (context === undefined) fail('plugin_api_request_invalid');
    const selectedSession = validateSession(session);
    if (selectedSession.origin !== context.origin || selectedSession.accountFingerprint !== context.accountFingerprint) fail('plugin_api_scope_changed');
    const path = `/api/v1/projects/${context.projectId}/plugins`;
    const options = Object.freeze({
      method: 'POST' as const, signal, idempotencyKey: context.idempotencyKey,
      body: JSON.stringify({ pluginName: context.pluginName, catalogDigest: context.catalogDigest, expectedRevision: context.expectedRevision }),
    });
    let raw: unknown;
    try { raw = await this.request(session, path, options); }
    catch (error) {
      if (!(error instanceof Error) || Object.getPrototypeOf(error) !== Error.prototype || error.message !== 'plugin_api_unavailable') throw error;
      raw = await this.request(session, path, options);
    }
    const receipt = parseResponse(Receipt, raw);
    if (receipt.projectId !== context.projectId || receipt.pluginName !== context.pluginName) fail('plugin_api_response_invalid');
    return freeze(receipt);
  }

  private async request(sessionInput: PluginApiSession, path: string, options: {
    readonly method: 'GET' | 'POST'; readonly signal?: AbortSignal; readonly body?: string; readonly idempotencyKey?: string;
  }): Promise<unknown> {
    const session = validateSession(sessionInput);
    const controller = new AbortController();
    let timedOut = false;
    const timer = setTimeout(() => { timedOut = true; controller.abort(); }, this.timeoutMs);
    const abort = () => controller.abort();
    options.signal?.addEventListener('abort', abort, { once: true });
    try {
      if (options.signal?.aborted) fail('plugin_api_aborted');
      this.requestCount += 1;
      const headers = new Headers({ authorization: `Bearer ${session.accessToken}`, accept: 'application/json' });
      if (options.body !== undefined) headers.set('content-type', 'application/json; charset=utf-8');
      if (options.idempotencyKey !== undefined) headers.set('idempotency-key', options.idempotencyKey);
      const response = await this.fetcher(`${session.origin}${path}`, {
        method: options.method, headers, body: options.body, signal: controller.signal, redirect: 'error', credentials: 'omit', cache: 'no-store', referrerPolicy: 'no-referrer',
      });
      try { if (Object.getPrototypeOf(response) !== Response.prototype) fail('plugin_api_response_invalid'); }
      catch { fail('plugin_api_response_invalid'); }
      const contentType = response.headers.get('content-type');
      if (contentType === null || !/^application\/json(?:; charset=utf-8)?$/i.test(contentType)) {
        if (response.body !== null) await response.body.cancel().catch(() => undefined);
        fail('plugin_api_response_invalid');
      }
      const text = await this.readBounded(response, controller.signal);
      if (!response.ok) fail('plugin_api_unavailable');
      return new BoundedJsonParser(text).parse();
    } catch (error) {
      if (timedOut) fail('plugin_api_timeout');
      if (options.signal?.aborted) fail('plugin_api_aborted');
      if (error instanceof Error && Object.getPrototypeOf(error) === Error.prototype && /^plugin_api_/.test(error.message)) throw error;
      fail('plugin_api_unavailable');
    } finally {
      clearTimeout(timer);
      options.signal?.removeEventListener('abort', abort);
    }
  }

  private async readBounded(response: Response, signal: AbortSignal): Promise<string> {
    if (response.body === null) fail('plugin_api_response_invalid');
    const reader = response.body.getReader();
    const chunks: Uint8Array[] = [];
    let size = 0;
    let rejectAbort: (() => void) | undefined;
    const aborted = new Promise<never>((_resolve, reject) => {
      rejectAbort = () => reject(new DOMException('aborted', 'AbortError'));
      signal.addEventListener('abort', rejectAbort, { once: true });
    });
    let open = true;
    try {
      while (true) {
        if (signal.aborted) throw new DOMException('aborted', 'AbortError');
        const item = await Promise.race([reader.read(), aborted]);
        if (item.done) { open = false; break; }
        size += item.value.byteLength;
        if (size > MAX_RESPONSE_BYTES) fail('plugin_api_response_invalid');
        chunks.push(item.value);
      }
    } catch (error) {
      if (open) await reader.cancel().catch(() => undefined);
      throw error;
    } finally {
      if (rejectAbort !== undefined) signal.removeEventListener('abort', rejectAbort);
      reader.releaseLock();
    }
    const bytes = new Uint8Array(size);
    let offset = 0;
    for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
    try { return new TextDecoder('utf-8', { fatal: true }).decode(bytes); } catch { return fail('plugin_api_response_invalid'); }
  }
}
