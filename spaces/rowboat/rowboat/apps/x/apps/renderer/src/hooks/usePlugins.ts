import { useCallback, useEffect, useRef, useState } from 'react';

import { DESKTOP_PLUGIN_CATALOG_PIN, type PluginCatalogItem, type PluginInstallReceipt, type PluginPreview, type PluginProjectItem } from '@/lib/rowboat-plugin-api';
import { rowboatPluginApiAccess, type PluginApiAccess, type PluginSessionScope } from '@/lib/rowboat-plugin-session';

export const PINNED_DESKTOP_PLUGIN_CATALOG_DIGEST = DESKTOP_PLUGIN_CATALOG_PIN.catalogDigest;

export type PluginsState =
  | Readonly<{ kind: 'loading' }>
  | Readonly<{ kind: 'signed_out' }>
  | Readonly<{ kind: 'ready'; items: readonly PluginCatalogItem[]; scope: PluginSessionScope; projectId: string }>
  | Readonly<{ kind: 'error'; reason: string }>;

export interface ScopedPluginPreview { readonly preview: PluginPreview; readonly projectId: string; readonly scope: PluginSessionScope }
interface UsePluginsOptions { readonly access?: PluginApiAccess; readonly catalogDigest?: string; readonly projectId?: string }

function safeReason(error: unknown): string {
  if (error instanceof Error && Object.getPrototypeOf(error) === Error.prototype && /^plugin_(?:api|session)_[a-z_]+$/.test(error.message)) return error.message;
  return 'plugin_api_unavailable';
}

function sameScope(left: PluginSessionScope, right: PluginSessionScope): boolean {
  return left.origin === right.origin && left.kind === right.kind && left.id === right.id;
}

function exactInstalledTruth(catalog: PluginCatalogItem, installation: PluginProjectItem, digest: string): boolean {
  if (!Number.isSafeInteger(installation.revision) || installation.revision < 0 || !installation.enabled
    || installation.status !== 'available' || installation.admission !== 'admitted' || installation.reason !== undefined
    || installation.catalogDigest !== digest || catalog.catalogDigest !== digest
    || installation.pluginName !== catalog.pluginName || installation.pluginVersion !== catalog.pluginVersion
    || installation.policyVersion !== catalog.policyVersion || installation.license.decision !== catalog.license.decision
    || installation.license.declaration !== catalog.license.declaration || catalog.components.length !== installation.components.length) return false;
  const byDigest = new Map(installation.components.map((component) => [component.componentDigest, component]));
  if (byDigest.size !== installation.components.length) return false;
  return catalog.components.every((component) => {
    const current = byDigest.get(component.componentDigest);
    return current !== undefined && current.name === component.name && current.kind === component.kind
      && current.admission.status === 'admitted' && current.admission.policyVersion === catalog.policyVersion
      && current.reason === undefined && (current.status === 'available' || current.status === 'installed');
  });
}

function overlayInstallations(catalog: readonly PluginCatalogItem[], installations: readonly PluginProjectItem[], digest: string): readonly PluginCatalogItem[] {
  const catalogByName = new Map(catalog.map((item) => [item.pluginName, item]));
  if (catalogByName.size !== catalog.length) throw new Error('plugin_api_response_invalid');
  const installedNames = new Set<string>();
  for (const installation of installations) {
    const item = catalogByName.get(installation.pluginName);
    if (item === undefined || installedNames.has(installation.pluginName) || !exactInstalledTruth(item, installation, digest)) throw new Error('plugin_api_response_invalid');
    installedNames.add(installation.pluginName);
  }
  return Object.freeze(catalog.map((item) => installedNames.has(item.pluginName) ? Object.freeze({ ...item, status: 'installed' as const }) : item));
}

export function usePlugins(options: UsePluginsOptions) {
  const access = options.access ?? rowboatPluginApiAccess;
  const catalogDigest = options.catalogDigest ?? PINNED_DESKTOP_PLUGIN_CATALOG_DIGEST;
  const projectId = options.projectId ?? '';
  const [state, setState] = useState<PluginsState>({ kind: 'loading' });
  const [accountRevision, setAccountRevision] = useState(0);
  const generation = useRef(0);
  const operations = useRef(new Set<AbortController>());

  const load = useCallback(async (signal: AbortSignal, selectedGeneration: number): Promise<void> => {
    setState({ kind: 'loading' });
    try {
      const catalog = await access.listCatalog(catalogDigest, signal);
      if (signal.aborted || generation.current !== selectedGeneration) return;
      if (catalog.kind === 'signed_out') { setState({ kind: 'signed_out' }); return; }
      if (catalog.value.items.some((item) => item.catalogDigest !== catalogDigest)) throw new Error('plugin_api_response_invalid');
      let items = catalog.value.items;
      if (projectId !== '') {
        const project = await access.listProjectPlugins(projectId, catalogDigest, signal);
        if (project.kind !== 'ok' || !sameScope(project.scope, catalog.scope)) throw new Error('plugin_api_scope_changed');
        items = overlayInstallations(items, project.value.items, catalogDigest);
      }
      if (!signal.aborted && generation.current === selectedGeneration) setState(Object.freeze({ kind: 'ready', items, scope: catalog.scope, projectId }));
    } catch (error) {
      if (!signal.aborted && generation.current === selectedGeneration) setState(Object.freeze({ kind: 'error', reason: safeReason(error) }));
    }
  }, [access, accountRevision, catalogDigest, projectId]);

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
    if (selectedProjectId !== projectId || selectedProjectId === '' || item.catalogDigest !== catalogDigest || state.kind !== 'ready') throw new Error('plugin_api_scope_changed');
    const controller = new AbortController();
    operations.current.add(controller);
    try {
      const result = await access.previewInstallation({ projectId: selectedProjectId, pluginName: item.pluginName, catalogDigest }, controller.signal);
      if (result.kind !== 'ok' || !sameScope(result.scope, state.scope)) throw new Error('plugin_api_scope_changed');
      if (result.value.sourceCommit !== item.sourceCommit || result.value.policyVersion !== item.policyVersion) throw new Error('plugin_api_response_invalid');
      return Object.freeze({ preview: result.value, projectId: selectedProjectId, scope: result.scope });
    } finally { operations.current.delete(controller); }
  }, [access, catalogDigest, projectId, state]);

  const confirm = useCallback(async (selected: ScopedPluginPreview): Promise<PluginInstallReceipt> => {
    if (selected.projectId !== projectId || state.kind !== 'ready' || state.projectId !== projectId || !sameScope(selected.scope, state.scope)) throw new Error('plugin_api_scope_changed');
    const controller = new AbortController();
    operations.current.add(controller);
    try {
      const result = await access.installPreview(selected.preview, controller.signal);
      if (result.kind !== 'ok' || !sameScope(result.scope, selected.scope)) throw new Error('plugin_api_scope_changed');
      if (result.value.status === 'success') {
        const selectedGeneration = ++generation.current;
        await load(controller.signal, selectedGeneration);
      }
      return result.value;
    } finally { operations.current.delete(controller); }
  }, [access, load, projectId, state]);

  return Object.freeze({ state, preview, confirm });
}
