import { useCallback, useEffect, useRef, useState } from 'react';

import { useRowboatAccount } from '@/hooks/useRowboatAccount';
import {
  RowboatPluginApi,
  type PluginApiSession,
  type PluginCatalogItem,
  type PluginInstallReceipt,
  type PluginPreview,
} from '@/lib/rowboat-plugin-api';

export const PINNED_DESKTOP_PLUGIN_CATALOG_DIGEST = '2e436d02b025a14960d5ef813c603bd7aec35a6d173c42d8c58274163da89a92';

interface PluginAccountSnapshot {
  readonly signedIn: boolean;
  readonly accessToken: string | null;
  readonly config: Readonly<{ appUrl: string }> | null;
  readonly isLoading?: boolean;
}

interface PluginApiBoundary {
  listCatalog(session: PluginApiSession, catalogDigest: string, signal?: AbortSignal): Promise<Readonly<{ items: readonly PluginCatalogItem[] }>>;
  listProjectPlugins?(session: PluginApiSession, projectId: string, catalogDigest: string, signal?: AbortSignal): Promise<Readonly<{ items: readonly Readonly<{ pluginName: string; status: string; revision: number }>[] }>>;
  previewInstallation?(session: PluginApiSession, input: {
    readonly projectId: string; readonly pluginName: string; readonly catalogDigest: string;
  }, signal?: AbortSignal): Promise<PluginPreview>;
  installPreview?(session: PluginApiSession, preview: PluginPreview, signal?: AbortSignal): Promise<PluginInstallReceipt>;
}

export type PluginsState =
  | Readonly<{ kind: 'loading' }>
  | Readonly<{ kind: 'signed_out' }>
  | Readonly<{ kind: 'ready'; items: readonly PluginCatalogItem[] }>
  | Readonly<{ kind: 'error'; reason: string }>;

interface UsePluginsOptions {
  readonly api?: PluginApiBoundary;
  readonly account?: PluginAccountSnapshot;
  readonly catalogDigest?: string;
  readonly projectId?: string;
}

const apiSingleton = new RowboatPluginApi();

function safeReason(error: unknown): string {
  if (error instanceof Error && Object.getPrototypeOf(error) === Error.prototype && /^plugin_api_[a-z_]+$/.test(error.message)) return error.message;
  return 'plugin_api_unavailable';
}

export function usePlugins(options: UsePluginsOptions) {
  const liveAccount = useRowboatAccount();
  const injectedAccount = options.account;
  const api = options.api ?? apiSingleton;
  const catalogDigest = options.catalogDigest ?? PINNED_DESKTOP_PLUGIN_CATALOG_DIGEST;
  const projectId = options.projectId;
  const [state, setState] = useState<PluginsState>({ kind: 'loading' });
  const generation = useRef(0);

  const acquireAccount = useCallback(async (): Promise<PluginAccountSnapshot | null> => {
    if (injectedAccount !== undefined) return injectedAccount;
    return liveAccount.refresh();
  }, [injectedAccount, liveAccount.refresh]);

  const load = useCallback(async (signal: AbortSignal, selectedGeneration: number): Promise<void> => {
    setState({ kind: 'loading' });
    try {
      const account = await acquireAccount();
      const token = account?.accessToken;
      const baseUrl = account?.config?.appUrl;
      if (account === null || !account.signedIn || token === null || token === undefined || baseUrl === undefined) { setState({ kind: 'signed_out' }); return; }
      const session = Object.freeze({ baseUrl, accessToken: token });
      const response = await api.listCatalog(session, catalogDigest, signal);
      let items = response.items;
      if (projectId !== undefined && projectId !== '' && api.listProjectPlugins !== undefined) {
        const installed = await api.listProjectPlugins(session, projectId, catalogDigest, signal);
        const names = new Set(installed.items.map((item) => item.pluginName));
        items = response.items.map((item) => names.has(item.pluginName) && item.status === 'available'
          ? Object.freeze({ ...item, status: 'installed' as const }) : item);
      }
      if (!signal.aborted && generation.current === selectedGeneration) setState(Object.freeze({ kind: 'ready', items: Object.freeze(items) }));
    } catch (error) {
      if (!signal.aborted && generation.current === selectedGeneration) setState(Object.freeze({ kind: 'error', reason: safeReason(error) }));
    }
  }, [acquireAccount, api, catalogDigest, projectId]);

  useEffect(() => {
    const controller = new AbortController();
    const selectedGeneration = ++generation.current;
    void load(controller.signal, selectedGeneration);
    return () => { generation.current += 1; controller.abort(); };
  }, [load]);

  const preview = useCallback(async (projectId: string, item: PluginCatalogItem): Promise<PluginPreview> => {
    const account = await acquireAccount();
    const token = account?.accessToken;
    const baseUrl = account?.config?.appUrl;
    if (account === null || !account.signedIn || token === null || token === undefined || baseUrl === undefined || api.previewInstallation === undefined) throw new Error('plugin_api_unavailable');
    const controller = new AbortController();
    return api.previewInstallation(Object.freeze({ baseUrl, accessToken: token }), {
      projectId, pluginName: item.pluginName, catalogDigest: item.catalogDigest,
    }, controller.signal);
  }, [acquireAccount, api]);

  const confirm = useCallback(async (selected: PluginPreview): Promise<PluginInstallReceipt> => {
    const account = await acquireAccount();
    const token = account?.accessToken;
    const baseUrl = account?.config?.appUrl;
    if (account === null || !account.signedIn || token === null || token === undefined || baseUrl === undefined || api.installPreview === undefined) throw new Error('plugin_api_unavailable');
    const controller = new AbortController();
    const receipt = await api.installPreview(Object.freeze({ baseUrl, accessToken: token }), selected, controller.signal);
    const selectedGeneration = ++generation.current;
    await load(controller.signal, selectedGeneration);
    return receipt;
  }, [acquireAccount, api, load]);

  return Object.freeze({ state, preview, confirm });
}
