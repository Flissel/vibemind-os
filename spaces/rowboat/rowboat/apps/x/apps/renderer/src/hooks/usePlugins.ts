import { useCallback, useEffect, useRef, useState } from 'react';

import { canonicalPluginOrigin, DESKTOP_PLUGIN_CATALOG_PIN, RowboatPluginApi, type PluginApiSession, type PluginCatalogItem, type PluginInstallReceipt, type PluginPreview, type PluginProjectItem } from '@/lib/rowboat-plugin-api';
import { rowboatPluginSessionGateway, type PluginSessionGateway, type PluginSessionScope } from '@/lib/rowboat-plugin-session';

export const PINNED_DESKTOP_PLUGIN_CATALOG_DIGEST = DESKTOP_PLUGIN_CATALOG_PIN.catalogDigest;

interface PluginApiBoundary {
  listCatalog(session: PluginApiSession, catalogDigest: string, signal?: AbortSignal): Promise<Readonly<{ items: readonly PluginCatalogItem[] }>>;
  listProjectPlugins?(session: PluginApiSession, projectId: string, catalogDigest: string, signal?: AbortSignal): Promise<Readonly<{ items: readonly PluginProjectItem[] }>>;
  previewInstallation?(session: PluginApiSession, input: { readonly projectId: string; readonly pluginName: string; readonly catalogDigest: string }, signal?: AbortSignal): Promise<PluginPreview>;
  installPreview?(session: PluginApiSession, preview: PluginPreview, signal?: AbortSignal): Promise<PluginInstallReceipt>;
}

export type PluginsState =
  | Readonly<{ kind: 'loading' }>
  | Readonly<{ kind: 'signed_out' }>
  | Readonly<{ kind: 'ready'; items: readonly PluginCatalogItem[]; scope: PluginSessionScope; projectId: string }>
  | Readonly<{ kind: 'error'; reason: string }>;

export interface ScopedPluginPreview { readonly preview: PluginPreview; readonly projectId: string; readonly scope: PluginSessionScope }

interface UsePluginsOptions { readonly api?: PluginApiBoundary; readonly sessionGateway?: PluginSessionGateway; readonly catalogDigest?: string; readonly projectId?: string }

const apiSingleton = new RowboatPluginApi();

function safeReason(error: unknown): string {
  if (error instanceof Error && Object.getPrototypeOf(error) === Error.prototype && /^plugin_(?:api|session)_[a-z_]+$/.test(error.message)) return error.message;
  return 'plugin_api_unavailable';
}

function isInstalled(catalog: PluginCatalogItem, installation: PluginProjectItem | undefined, digest: string): boolean {
  if (installation === undefined || !installation.enabled || installation.admission !== 'admitted'
    || (installation.status !== 'available' && installation.status !== 'installed')
    || installation.catalogDigest !== digest || catalog.catalogDigest !== digest
    || installation.pluginName !== catalog.pluginName || installation.pluginVersion !== catalog.pluginVersion
    || installation.policyVersion !== catalog.policyVersion || catalog.components.length !== installation.components.length) return false;
  const byDigest = new Map(installation.components.map((component) => [component.componentDigest, component]));
  return catalog.components.every((component) => {
    const current = byDigest.get(component.componentDigest);
    return current !== undefined && current.name === component.name && current.kind === component.kind
      && current.admission.status === 'admitted' && (current.status === 'available' || current.status === 'installed');
  });
}

export function usePlugins(options: UsePluginsOptions) {
  const api = options.api ?? apiSingleton;
  const sessionGateway = options.sessionGateway ?? rowboatPluginSessionGateway;
  const catalogDigest = options.catalogDigest ?? PINNED_DESKTOP_PLUGIN_CATALOG_DIGEST;
  const projectId = options.projectId ?? '';
  const [state, setState] = useState<PluginsState>({ kind: 'loading' });
  const [accountRevision, setAccountRevision] = useState(0);
  const generation = useRef(0);
  const operations = useRef(new Set<AbortController>());

  const load = useCallback(async (signal: AbortSignal, selectedGeneration: number): Promise<void> => {
    setState({ kind: 'loading' });
    try {
      const result = await sessionGateway.run(async (session) => {
        const response = await api.listCatalog(session, catalogDigest, signal);
        if (response.items.some((item) => item.catalogDigest !== catalogDigest)) throw new Error('plugin_api_response_invalid');
        let items = response.items;
        if (projectId !== '' && api.listProjectPlugins !== undefined) {
          const installed = await api.listProjectPlugins(session, projectId, catalogDigest, signal);
          const byName = new Map(installed.items.map((item) => [item.pluginName, item]));
          items = response.items.map((item) => isInstalled(item, byName.get(item.pluginName), catalogDigest)
            ? Object.freeze({ ...item, status: 'installed' as const }) : item);
        }
        return Object.freeze(items);
      });
      if (signal.aborted || generation.current !== selectedGeneration) return;
      if (result.kind === 'signed_out') setState({ kind: 'signed_out' });
      else setState(Object.freeze({ kind: 'ready', items: result.value, scope: result.scope, projectId }));
    } catch (error) {
      if (!signal.aborted && generation.current === selectedGeneration) setState(Object.freeze({ kind: 'error', reason: safeReason(error) }));
    }
  }, [api, catalogDigest, projectId, sessionGateway, accountRevision]);

  useEffect(() => {
    const controller = new AbortController();
    const selectedGeneration = ++generation.current;
    void load(controller.signal, selectedGeneration);
    return () => { generation.current += 1; controller.abort(); };
  }, [load]);

  useEffect(() => window.ipc.on('oauth:didConnect', () => {
    for (const operation of operations.current) operation.abort();
    operations.current.clear();
    setAccountRevision((current) => current + 1);
  }), []);

  useEffect(() => () => {
    for (const operation of operations.current) operation.abort();
    operations.current.clear();
  }, [projectId, accountRevision]);

  const preview = useCallback(async (selectedProjectId: string, item: PluginCatalogItem): Promise<ScopedPluginPreview> => {
    if (selectedProjectId !== projectId || selectedProjectId === '' || item.catalogDigest !== catalogDigest || api.previewInstallation === undefined) throw new Error('plugin_api_scope_changed');
    const controller = new AbortController();
    operations.current.add(controller);
    try {
      const result = await sessionGateway.run(async (session) => api.previewInstallation!(session, { projectId: selectedProjectId, pluginName: item.pluginName, catalogDigest }, controller.signal));
      if (result.kind !== 'ok') throw new Error('plugin_api_unavailable');
      if (result.value.sourceCommit !== item.sourceCommit || result.value.policyVersion !== item.policyVersion) throw new Error('plugin_api_response_invalid');
      return Object.freeze({ preview: result.value, projectId: selectedProjectId, scope: result.scope });
    } finally { operations.current.delete(controller); }
  }, [api, catalogDigest, projectId, sessionGateway]);

  const confirm = useCallback(async (selected: ScopedPluginPreview): Promise<PluginInstallReceipt> => {
    if (selected.projectId !== projectId || state.kind !== 'ready' || state.projectId !== projectId
      || selected.scope.origin !== state.scope.origin || selected.scope.accountFingerprint !== state.scope.accountFingerprint
      || api.installPreview === undefined) throw new Error('plugin_api_scope_changed');
    const controller = new AbortController();
    operations.current.add(controller);
    try {
      const result = await sessionGateway.run(async (session) => {
        if (canonicalPluginOrigin(session.baseUrl) !== selected.scope.origin || session.accountFingerprint !== selected.scope.accountFingerprint) throw new Error('plugin_api_scope_changed');
        return api.installPreview!(session, selected.preview, controller.signal);
      });
      if (result.kind !== 'ok') throw new Error('plugin_api_scope_changed');
      if (result.value.status === 'success') {
        const selectedGeneration = ++generation.current;
        await load(controller.signal, selectedGeneration);
      }
      return result.value;
    } finally { operations.current.delete(controller); }
  }, [api, load, projectId, sessionGateway, state]);

  return Object.freeze({ state, preview, confirm });
}
