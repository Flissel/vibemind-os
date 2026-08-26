import '@testing-library/jest-dom/vitest';
import { act, cleanup, fireEvent, render, renderHook, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { PluginInstallFeedback, PluginInstallReview, PluginSettings, type PluginSettingsItem } from './plugin-settings';
import { usePlugins } from '@/hooks/usePlugins';
import { SettingsDialog } from '@/components/settings-dialog';
import { rowboatPluginApiAccess, type PluginApiAccess } from '@/lib/rowboat-plugin-session';

const digest = 'a'.repeat(64);
const base: PluginSettingsItem = {
  pluginName: 'github', pluginVersion: '1.0.0', status: 'available', components: [],
};

afterEach(() => { cleanup(); vi.restoreAllMocks(); window.localStorage.clear(); window.sessionStorage.clear(); });

describe('PluginSettings', () => {
  it('integrates an accessible Plugins tab into Settings', async () => {
    Object.defineProperty(window, 'ipc', { configurable: true, value: {
      invoke: vi.fn(async () => ({ config: { rowboat: { connected: true } } })),
      on: vi.fn(() => () => undefined),
    } });
    render(<SettingsDialog><button type="button">Open settings</button></SettingsDialog>);
    fireEvent.click(screen.getByRole('button', { name: 'Open settings' }));
    expect(await screen.findByRole('tab', { name: 'Plugins' })).toBeVisible();
  });

  it.each([
    ['available', 'available', false],
    ['review_required', 'review_required', true],
    ['installed', 'installed', true],
    ['partially_available', 'partially_available', true],
    ['unavailable', 'unavailable', true],
    ['migration_required', 'migration_required', true],
    ['error', 'error', true],
  ] as const)('renders server status %s unchanged and applies install policy', (status, label, disabled) => {
    render(<PluginSettings state={{ kind: 'ready', items: [{ ...base, status }] }} onInstall={vi.fn()} />);
    expect(screen.getByText(label)).toBeVisible();
    expect(screen.getByRole('button', { name: 'Install github' })).toHaveProperty('disabled', disabled);
  });

  it('renders server reason codes and credential slot metadata without secret values', () => {
    render(<PluginSettings state={{ kind: 'ready', items: [{
      ...base,
      status: 'partially_available',
      reason: 'provider_unavailable',
      credentialSlots: [{ name: 'GITHUB_TOKEN', configured: false }],
    }] }} onInstall={vi.fn()} />);
    expect(screen.getByText('provider_unavailable')).toBeVisible();
    expect(screen.getByText('GITHUB_TOKEN — required')).toBeVisible();
    expect(screen.getByRole('button', { name: 'Install github' })).toBeDisabled();
    expect(document.body.textContent).not.toContain('secret-value');
  });

  it('requires an explicit confirmation after showing server preview metadata', () => {
    const confirm = vi.fn();
    render(<PluginInstallReview preview={{
      pluginName: 'github', status: 'available',
      components: [{ name: 'github-mcp', kind: 'mcp', status: 'available' }],
      credentialSlots: [{ name: 'GITHUB_TOKEN', configured: false }],
    }} projectId="project-1" scopeValid onConfirm={confirm} onCancel={vi.fn()} pending={false} />);
    expect(screen.getByText('Project: project-1')).toBeVisible();
    expect(screen.getByText('github-mcp · mcp · available')).toBeVisible();
    expect(screen.getByText('GITHUB_TOKEN — required')).toBeVisible();
    expect(screen.getByRole('button', { name: 'Confirm install github' })).toBeEnabled();
    expect(confirm).not.toHaveBeenCalled();
  });

  it('disables confirmation when the visible project scope is stale', () => {
    render(<PluginInstallReview preview={{ pluginName: 'github', status: 'available', components: [], credentialSlots: [] }}
      projectId="project-old" scopeValid={false} onConfirm={vi.fn()} onCancel={vi.fn()} pending={false} />);
    expect(screen.getByText('Project: project-old')).toBeVisible();
    expect(screen.getByRole('button', { name: 'Confirm install github' })).toBeDisabled();
  });

  it.each([['failed', 'digest_mismatch'], ['denied', 'license_rejected'], ['timed_out', 'provider_unavailable']] as const)
  ('does not claim success for receipt %s', (status, reason) => {
    render(<PluginInstallFeedback status={status} reason={reason} />);
    expect(screen.getByRole('alert')).toHaveTextContent(`${status} · ${reason}`);
    expect(screen.queryByText('Plugin installed.')).not.toBeInTheDocument();
  });

  it('has accessible loading, error, labels, and live status', () => {
    const { rerender } = render(<PluginSettings state={{ kind: 'loading' }} onInstall={vi.fn()} />);
    expect(screen.getByRole('status')).toHaveTextContent('Loading plugins');
    rerender(<PluginSettings state={{ kind: 'error', reason: 'plugin_api_unavailable' }} onInstall={vi.fn()} />);
    expect(screen.getByRole('alert')).toHaveTextContent('plugin_api_unavailable');
  });
});

describe('usePlugins', () => {
  const scope = { origin: 'https://rowboat.example', accountId: 'account-1' };
  const access = (overrides: Partial<PluginApiAccess> = {}): PluginApiAccess => ({
    listCatalog: async () => ({ kind: 'ok', scope, value: { items: [] } }),
    listProjectPlugins: async () => ({ kind: 'ok', scope, value: { items: [] } }),
    previewInstallation: async () => { throw new Error('unexpected_preview'); },
    installPreview: async () => { throw new Error('unexpected_install'); },
    ...overrides,
  });

  it('aborts an unmounted request and does not retain account tokens in state', async () => {
    let signal: AbortSignal | undefined;
    const listCatalog = vi.fn(async (_catalogDigest: string, currentSignal?: AbortSignal) => {
        signal = currentSignal;
        return new Promise<never>(() => undefined);
      });
    const selectedAccess = access({ listCatalog });
    const { result, unmount } = renderHook(() => usePlugins({ access: selectedAccess, catalogDigest: digest }));
    await waitFor(() => expect(listCatalog).toHaveBeenCalledOnce());
    unmount();
    expect(signal?.aborted).toBe(true);
    expect(JSON.stringify(result.current)).not.toContain('accessToken');
  });

  it('prevents stale account results from overwriting the latest response', async () => {
    const pending: Array<(value: { items: [] }) => void> = [];
    const listCatalog = vi.fn(() => new Promise<{ kind: 'ok'; scope: typeof scope; value: { items: [] } }>((resolve) => pending.push((value) => resolve({ kind: 'ok', scope, value }))));
    const first = access({ listCatalog });
    const second = access({ listCatalog });
    const { result, rerender } = renderHook(({ selectedAccess }) => usePlugins({ access: selectedAccess, catalogDigest: digest }), { initialProps: { selectedAccess: first } });
    await waitFor(() => expect(listCatalog).toHaveBeenCalledTimes(1));
    rerender({ selectedAccess: second });
    await waitFor(() => expect(listCatalog).toHaveBeenCalledTimes(2));
    await act(async () => pending[1]?.({ items: [] }));
    expect(result.current.state.kind).toBe('ready');
    await act(async () => pending[0]?.({ items: [] }));
    expect(result.current.state.kind).toBe('ready');
    expect(JSON.stringify(result.current)).not.toContain('accessToken');
  });

  it('uses authorized project state to mark a catalog item installed', async () => {
    const component = { componentDigest: 'b'.repeat(64), name: 'github-mcp', kind: 'mcp', admission: { status: 'admitted', policyVersion: 'p1' }, availability: { status: 'available' }, status: 'available' };
    const catalogItem = { ...base, name: 'github', catalogDigest: digest, sourceCommit: 'c'.repeat(40), policyVersion: 'p1', license: { declaration: 'MIT', decision: 'admitted' }, admission: 'admitted', components: [component] };
    const listProjectPlugins = vi.fn(async () => ({ kind: 'ok' as const, scope, value: { items: [{ ...catalogItem, enabled: true, revision: 3 }] } }));
    const selectedAccess = access({ listCatalog: async () => ({ kind: 'ok', scope, value: { items: [catalogItem] } }) as never, listProjectPlugins: listProjectPlugins as never });
    const { result } = renderHook(() => usePlugins({ access: selectedAccess, catalogDigest: digest, projectId: 'project-1' }));
    await waitFor(() => expect(result.current.state.kind).toBe('ready'));
    expect(result.current.state.kind === 'ready' ? result.current.state.items[0]?.status : null).toBe('installed');
    expect(listProjectPlugins).toHaveBeenCalledOnce();
    expect(JSON.stringify(result.current)).not.toContain('token');
  });

  it.each([
    ['digest', { catalogDigest: 'd'.repeat(64) }], ['version', { pluginVersion: '2.0.0' }], ['policy', { policyVersion: 'p2' }],
    ['revision', { revision: -1 }], ['status', { status: 'migration_required' }], ['admission', { admission: 'rejected' }],
    ['disabled', { enabled: false }], ['unknown', { pluginName: 'unknown' }],
  ] as const)('fails closed for inconsistent project installation %s', async (_label, overlay) => {
    const catalogItem = { ...base, name: 'github', catalogDigest: digest, sourceCommit: 'c'.repeat(40), policyVersion: 'p1', license: { declaration: 'MIT', decision: 'admitted' }, admission: 'admitted', components: [] };
    const selectedAccess = access({ listCatalog: async () => ({ kind: 'ok', scope, value: { items: [catalogItem] } }) as never,
      listProjectPlugins: async () => ({ kind: 'ok', scope, value: { items: [{ ...catalogItem, enabled: true, revision: 1, ...overlay }] } }) as never });
    const { result } = renderHook(() => usePlugins({ access: selectedAccess, catalogDigest: digest, projectId: 'project-1' }));
    await waitFor(() => expect(result.current.state.kind).toBe('error'));
    expect(result.current.state.kind === 'error' ? result.current.state.reason : null).toBe('plugin_api_response_invalid');
  });

  it('fails closed for duplicate project installations', async () => {
    const item = { ...base, name: 'github', catalogDigest: digest, sourceCommit: 'c'.repeat(40), policyVersion: 'p1', license: { declaration: 'MIT', decision: 'admitted' }, admission: 'admitted', components: [], enabled: true, revision: 1 };
    const selectedAccess = access({ listCatalog: async () => ({ kind: 'ok', scope, value: { items: [item] } }) as never,
      listProjectPlugins: async () => ({ kind: 'ok', scope, value: { items: [item, { ...item, revision: 2 }] } }) as never });
    const { result } = renderHook(() => usePlugins({ access: selectedAccess, catalogDigest: digest, projectId: 'project-1' }));
    await waitFor(() => expect(result.current.state.kind).toBe('error'));
  });

  it('rejects a preview scope mismatch before install fetch', async () => {
    const installPreview = vi.fn();
    const selectedAccess = access({ installPreview });
    const { result } = renderHook(() => usePlugins({ access: selectedAccess, catalogDigest: digest, projectId: 'project-1' }));
    await waitFor(() => expect(result.current.state.kind).toBe('ready'));
    await expect(result.current.confirm({ preview: {} as never, projectId: 'project-2', scope })).rejects.toThrow('plugin_api_scope_changed');
    expect(installPreview).not.toHaveBeenCalled();
  });

});

describe('rowboatPluginApiAccess', () => {
  it('does not export a token callback and fails closed without a stable nonsecret account identity', async () => {
    const secret = 'trusted-ephemeral-secret';
    Object.defineProperty(window, 'ipc', { configurable: true, value: {
      invoke: vi.fn(async () => ({ signedIn: true, accessToken: secret, config: {
        appUrl: 'https://rowboat.example/', websocketApiUrl: 'wss://rowboat.example/', supabaseUrl: 'https://supabase.example/',
      } })),
      on: vi.fn(() => () => undefined),
    } });
    expect(rowboatPluginApiAccess).not.toHaveProperty('run');
    await expect(rowboatPluginApiAccess.listCatalog(digest)).rejects.toThrow('plugin_session_identity_unavailable');
    expect(window.localStorage.length).toBe(0);
    expect(window.sessionStorage.length).toBe(0);
  });

  it('redacts trusted IPC and malformed descriptor errors', async () => {
    Object.defineProperty(window, 'ipc', { configurable: true, value: {
      invoke: vi.fn(async () => { throw new Error('raw-provider-secret'); }),
      on: vi.fn(() => () => undefined),
    } });
    await expect(rowboatPluginApiAccess.listCatalog(digest)).rejects.toThrow('plugin_session_unavailable');
    await expect(rowboatPluginApiAccess.listCatalog(digest)).rejects.not.toThrow('raw-provider-secret');
  });
});
