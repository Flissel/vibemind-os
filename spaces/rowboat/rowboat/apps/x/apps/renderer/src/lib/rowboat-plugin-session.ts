import { canonicalPluginOrigin, type PluginApiSession } from '@/lib/rowboat-plugin-api';

export interface PluginSessionScope {
  readonly accountFingerprint: string;
  readonly origin: string;
}

export type PluginSessionResult<T> =
  | Readonly<{ kind: 'signed_out' }>
  | Readonly<{ kind: 'ok'; scope: PluginSessionScope; value: T }>;

export interface PluginSessionGateway {
  run<T>(operation: (session: PluginApiSession) => Promise<T>): Promise<PluginSessionResult<T>>;
}

const TOKEN_LIMIT = 16_384;

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

async function fingerprint(token: string): Promise<string> {
  const bytes = new TextEncoder().encode(`rowboat-plugin-account-v1\0${token}`);
  const digest = await crypto.subtle.digest('SHA-256', bytes);
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, '0')).join('');
}

async function captureSession(raw: unknown): Promise<Readonly<{ session: PluginApiSession; scope: PluginSessionScope }> | null> {
  try {
    const account = record(raw, ['signedIn', 'accessToken', 'config']);
    const signedIn = ownData(account, 'signedIn');
    const accessToken = ownData(account, 'accessToken');
    const configValue = ownData(account, 'config');
    if (signedIn === false || accessToken === null || configValue === null) return null;
    if (signedIn !== true || typeof accessToken !== 'string' || accessToken.length < 1 || accessToken.length > TOKEN_LIMIT) throw new Error();
    const config = record(configValue, ['appUrl', 'websocketApiUrl', 'supabaseUrl']);
    const appUrl = ownData(config, 'appUrl');
    const websocketApiUrl = ownData(config, 'websocketApiUrl');
    const supabaseUrl = ownData(config, 'supabaseUrl');
    if (typeof appUrl !== 'string' || typeof websocketApiUrl !== 'string' || typeof supabaseUrl !== 'string') throw new Error();
    const origin = canonicalPluginOrigin(appUrl);
    const accountFingerprint = await fingerprint(accessToken);
    return Object.freeze({ session: Object.freeze({ baseUrl: appUrl, accessToken, accountFingerprint }), scope: Object.freeze({ accountFingerprint, origin }) });
  } catch { throw new Error('plugin_session_unavailable'); }
}

export const rowboatPluginSessionGateway: PluginSessionGateway = Object.freeze({
  async run<T>(operation: (session: PluginApiSession) => Promise<T>): Promise<PluginSessionResult<T>> {
    let raw: unknown;
    try { raw = await window.ipc.invoke('account:getRowboat', null); }
    catch { throw new Error('plugin_session_unavailable'); }
    const captured = await captureSession(raw);
    if (captured === null) return Object.freeze({ kind: 'signed_out' });
    const value = await operation(captured.session);
    return Object.freeze({ kind: 'ok', scope: captured.scope, value });
  },
});
