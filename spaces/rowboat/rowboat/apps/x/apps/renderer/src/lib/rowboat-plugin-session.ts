import {
  canonicalPluginOrigin,
  parsePluginApiJson,
  RowboatPluginApi,
  type PluginCatalogResponse,
  type PluginInstallReceipt,
  type PluginPreview,
  type PluginProjectItem,
} from '@/lib/rowboat-plugin-api';

export interface PluginSessionScope { readonly kind: 'user' | 'project_api_key'; readonly id: string; readonly origin: string }
export type PluginAccessResult<T> =
  | Readonly<{ kind: 'signed_out' }>
  | Readonly<{ kind: 'ok'; scope: PluginSessionScope; value: T }>;

export interface PluginApiAccess {
  listCatalog(catalogDigest: string, signal?: AbortSignal): Promise<PluginAccessResult<PluginCatalogResponse>>;
  listProjectPlugins(projectId: string, catalogDigest: string, signal?: AbortSignal): Promise<PluginAccessResult<Readonly<{ items: readonly PluginProjectItem[] }>>>;
  previewInstallation(input: Readonly<{ projectId: string; pluginName: string; catalogDigest: string }>, signal?: AbortSignal): Promise<PluginAccessResult<PluginPreview>>;
  installPreview(preview: PluginPreview, signal?: AbortSignal): Promise<PluginAccessResult<PluginInstallReceipt>>;
}

const TOKEN_LIMIT = 16_384;
const ACCOUNT_ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/;

function ownData(record: object, key: string): unknown {
  const descriptor = Object.getOwnPropertyDescriptor(record, key);
  if (descriptor === undefined || !descriptor.enumerable || !('value' in descriptor)) throw new Error('plugin_session_unavailable');
  return descriptor.value;
}

function record(value: unknown, keys: readonly string[]): object {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) throw new Error('plugin_session_unavailable');
  const prototype = Object.getPrototypeOf(value);
  if (prototype !== Object.prototype && prototype !== null) throw new Error('plugin_session_unavailable');
  const ownKeys = Reflect.ownKeys(value);
  if (ownKeys.length !== keys.length || ownKeys.some((key) => typeof key !== 'string' || !keys.includes(key))) throw new Error('plugin_session_unavailable');
  return value;
}

async function acquire() {
  let raw: unknown;
  try { raw = await window.ipc.invoke('account:getRowboat', null); }
  catch { throw new Error('plugin_session_unavailable'); }
  try {
    const account = record(raw, ['signedIn', 'accessToken', 'config']);
    const signedIn = ownData(account, 'signedIn');
    const accessToken = ownData(account, 'accessToken');
    const configValue = ownData(account, 'config');
    if (signedIn === false && accessToken === null && configValue === null) return null;
    if (signedIn !== true || typeof accessToken !== 'string' || accessToken.length < 1 || accessToken.length > TOKEN_LIMIT || /[,\s\0]/.test(accessToken)) throw new Error();
    const config = record(configValue, ['appUrl', 'websocketApiUrl', 'supabaseUrl']);
    const appUrl = ownData(config, 'appUrl');
    const websocketApiUrl = ownData(config, 'websocketApiUrl');
    const supabaseUrl = ownData(config, 'supabaseUrl');
    if (typeof appUrl !== 'string' || typeof websocketApiUrl !== 'string' || typeof supabaseUrl !== 'string') throw new Error();
    const origin = canonicalPluginOrigin(appUrl);
    return Object.freeze({ baseUrl: appUrl, accessToken, origin });
  } catch { throw new Error('plugin_session_unavailable'); }
}

async function readIdentityBody(response: Response, signal: AbortSignal): Promise<string> {
  if (response.body === null) throw new Error('plugin_session_unavailable');
  const reader = response.body.getReader();
  const chunks: Uint8Array[] = [];
  let size = 0;
  let open = true;
  let rejectAbort: (() => void) | undefined;
  const aborted = new Promise<never>((_resolve, reject) => {
    rejectAbort = () => reject(new Error('plugin_session_unavailable'));
    signal.addEventListener('abort', rejectAbort, { once: true });
  });
  try {
    while (true) {
      if (signal.aborted) throw new Error('plugin_session_unavailable');
      const item = await Promise.race([reader.read(), aborted]);
      if (item.done) { open = false; break; }
      size += item.value.byteLength;
      if (size > 4_096) throw new Error('plugin_session_unavailable');
      chunks.push(item.value);
    }
  } catch {
    if (open) await reader.cancel().catch(() => undefined);
    throw new Error('plugin_session_unavailable');
  } finally {
    if (rejectAbort !== undefined) signal.removeEventListener('abort', rejectAbort);
    reader.releaseLock();
  }
  const bytes = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
  try { return new TextDecoder('utf-8', { fatal: true }).decode(bytes); } catch { throw new Error('plugin_session_unavailable'); }
}

async function resolveIdentity(base: Readonly<{ baseUrl: string; accessToken: string; origin: string }>, externalSignal?: AbortSignal): Promise<PluginSessionScope> {
  const controller = new AbortController();
  const abort = () => controller.abort();
  const timer = setTimeout(abort, 10_000);
  externalSignal?.addEventListener('abort', abort, { once: true });
  try {
    if (externalSignal?.aborted) throw new Error('plugin_session_unavailable');
    const response = await fetch(`${base.origin}/api/v1/plugin-session`, {
      method: 'GET', headers: { authorization: `Bearer ${base.accessToken}`, accept: 'application/json' }, signal: controller.signal,
      redirect: 'error', credentials: 'omit', cache: 'no-store', referrerPolicy: 'no-referrer',
    });
    if (Object.getPrototypeOf(response) !== Response.prototype) throw new Error('plugin_session_unavailable');
    const contentType = response.headers.get('content-type');
    if (contentType === null || !/^application\/json(?:; charset=utf-8)?$/i.test(contentType)) {
      if (response.body !== null) await response.body.cancel().catch(() => undefined);
      throw new Error('plugin_session_unavailable');
    }
    const text = await readIdentityBody(response, controller.signal);
    if (!response.ok) throw new Error('plugin_session_unavailable');
    const parsed = record(parsePluginApiJson(text), ['kind', 'id']);
    const kind = ownData(parsed, 'kind');
    const id = ownData(parsed, 'id');
    if ((kind !== 'user' && kind !== 'project_api_key') || typeof id !== 'string' || !ACCOUNT_ID.test(id)) throw new Error('plugin_session_unavailable');
    return Object.freeze({ kind, id, origin: base.origin });
  } catch (error) {
    if (error instanceof Error && Object.getPrototypeOf(error) === Error.prototype && error.message === 'plugin_session_unavailable') throw error;
    throw new Error('plugin_session_unavailable');
  } finally {
    clearTimeout(timer);
    externalSignal?.removeEventListener('abort', abort);
  }
}

class TrustedPluginApiAccess implements PluginApiAccess {
  private readonly api = new RowboatPluginApi((input, init) => fetch(input, init));

  private async execute<T>(operation: 'listCatalog' | 'listProjectPlugins' | 'previewInstallation' | 'installPreview', input: unknown, signal?: AbortSignal): Promise<PluginAccessResult<T>> {
    const captured = await acquire();
    if (captured === null) return Object.freeze({ kind: 'signed_out' });
    const scope = await resolveIdentity(captured, signal);
    const session = Object.freeze({ baseUrl: captured.baseUrl, accessToken: captured.accessToken, actorKind: scope.kind, actorId: scope.id });
    let value: unknown;
    if (operation === 'listCatalog') value = await this.api.listCatalog(session, input as string, signal);
    else if (operation === 'listProjectPlugins') {
      const selected = input as Readonly<{ projectId: string; catalogDigest: string }>;
      value = await this.api.listProjectPlugins(session, selected.projectId, selected.catalogDigest, signal);
    } else if (operation === 'previewInstallation') value = await this.api.previewInstallation(session, input as Readonly<{ projectId: string; pluginName: string; catalogDigest: string }>, signal);
    else value = await this.api.installPreview(session, input as PluginPreview, signal);
    return Object.freeze({ kind: 'ok', scope, value: value as T });
  }

  listCatalog(catalogDigest: string, signal?: AbortSignal) { return this.execute<PluginCatalogResponse>('listCatalog', catalogDigest, signal); }
  listProjectPlugins(projectId: string, catalogDigest: string, signal?: AbortSignal) { return this.execute<Readonly<{ items: readonly PluginProjectItem[] }>>('listProjectPlugins', { projectId, catalogDigest }, signal); }
  previewInstallation(input: Readonly<{ projectId: string; pluginName: string; catalogDigest: string }>, signal?: AbortSignal) { return this.execute<PluginPreview>('previewInstallation', input, signal); }
  installPreview(preview: PluginPreview, signal?: AbortSignal) { return this.execute<PluginInstallReceipt>('installPreview', preview, signal); }
}

export const rowboatPluginApiAccess: PluginApiAccess = Object.freeze(new TrustedPluginApiAccess());
