import {
  canonicalPluginOrigin,
  RowboatPluginApi,
  type PluginCatalogResponse,
  type PluginInstallReceipt,
  type PluginPreview,
  type PluginProjectItem,
} from '@/lib/rowboat-plugin-api';

export interface PluginSessionScope { readonly accountId: string; readonly origin: string }
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
    const account = record(raw, ['signedIn', 'accountId', 'accessToken', 'config']);
    const signedIn = ownData(account, 'signedIn');
    const accountId = ownData(account, 'accountId');
    const accessToken = ownData(account, 'accessToken');
    const configValue = ownData(account, 'config');
    if (signedIn === false && accountId === null && accessToken === null && configValue === null) return null;
    if (signedIn !== true || typeof accountId !== 'string' || !ACCOUNT_ID.test(accountId)
      || typeof accessToken !== 'string' || accessToken.length < 1 || accessToken.length > TOKEN_LIMIT) throw new Error();
    const config = record(configValue, ['appUrl', 'websocketApiUrl', 'supabaseUrl']);
    const appUrl = ownData(config, 'appUrl');
    const websocketApiUrl = ownData(config, 'websocketApiUrl');
    const supabaseUrl = ownData(config, 'supabaseUrl');
    if (typeof appUrl !== 'string' || typeof websocketApiUrl !== 'string' || typeof supabaseUrl !== 'string') throw new Error();
    const origin = canonicalPluginOrigin(appUrl);
    return Object.freeze({ session: Object.freeze({ baseUrl: appUrl, accessToken, accountId }), scope: Object.freeze({ accountId, origin }) });
  } catch { throw new Error('plugin_session_identity_unavailable'); }
}

class TrustedPluginApiAccess implements PluginApiAccess {
  private readonly api = new RowboatPluginApi();

  private async execute<T>(operation: 'listCatalog' | 'listProjectPlugins' | 'previewInstallation' | 'installPreview', input: unknown, signal?: AbortSignal): Promise<PluginAccessResult<T>> {
    const captured = await acquire();
    if (captured === null) return Object.freeze({ kind: 'signed_out' });
    let value: unknown;
    if (operation === 'listCatalog') value = await this.api.listCatalog(captured.session, input as string, signal);
    else if (operation === 'listProjectPlugins') {
      const selected = input as Readonly<{ projectId: string; catalogDigest: string }>;
      value = await this.api.listProjectPlugins(captured.session, selected.projectId, selected.catalogDigest, signal);
    } else if (operation === 'previewInstallation') value = await this.api.previewInstallation(captured.session, input as Readonly<{ projectId: string; pluginName: string; catalogDigest: string }>, signal);
    else value = await this.api.installPreview(captured.session, input as PluginPreview, signal);
    return Object.freeze({ kind: 'ok', scope: captured.scope, value: value as T });
  }

  listCatalog(catalogDigest: string, signal?: AbortSignal) { return this.execute<PluginCatalogResponse>('listCatalog', catalogDigest, signal); }
  listProjectPlugins(projectId: string, catalogDigest: string, signal?: AbortSignal) { return this.execute<Readonly<{ items: readonly PluginProjectItem[] }>>('listProjectPlugins', { projectId, catalogDigest }, signal); }
  previewInstallation(input: Readonly<{ projectId: string; pluginName: string; catalogDigest: string }>, signal?: AbortSignal) { return this.execute<PluginPreview>('previewInstallation', input, signal); }
  installPreview(preview: PluginPreview, signal?: AbortSignal) { return this.execute<PluginInstallReceipt>('installPreview', preview, signal); }
}

export const rowboatPluginApiAccess: PluginApiAccess = Object.freeze(new TrustedPluginApiAccess());
