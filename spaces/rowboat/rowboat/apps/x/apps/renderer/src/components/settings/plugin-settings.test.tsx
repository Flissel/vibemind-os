import '@testing-library/jest-dom/vitest';
import { act, cleanup, fireEvent, render, renderHook, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { PluginInstallFeedback, PluginInstallReview, PluginSettings, type PluginSettingsItem } from './plugin-settings';
import { usePlugins } from '@/hooks/usePlugins';
import { SettingsDialog } from '@/components/settings-dialog';
import { rowboatPluginSessionGateway, type PluginSessionGateway } from '@/lib/rowboat-plugin-session';

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
  const scope = { origin: 'https://rowboat.example', accountFingerprint: 'f'.repeat(64) };
  const gateway = (token = 'ephemeral-token'): PluginSessionGateway => ({
    run: async (operation) => ({ kind: 'ok', scope, value: await operation({ baseUrl: 'https://rowboat.example/', accessToken: token, accountFingerprint: scope.accountFingerprint }) }),
  });

  it('aborts an unmounted request and does not retain account tokens in state', async () => {
    let signal: AbortSignal | undefined;
    const api = {
      listCatalog: vi.fn(async (_session: unknown, _catalogDigest: string, currentSignal?: AbortSignal) => {
        signal = currentSignal;
        return new Promise<never>(() => undefined);
      }),
    };
    const sessionGateway = gateway();
    const { result, unmount } = renderHook(() => usePlugins({ api, sessionGateway, catalogDigest: digest }));
    await waitFor(() => expect(api.listCatalog).toHaveBeenCalledOnce());
    unmount();
    expect(signal?.aborted).toBe(true);
    expect(JSON.stringify(result.current)).not.toContain('ephemeral-token');
  });

  it('prevents stale account results from overwriting the latest response', async () => {
    const pending: Array<(value: { items: [] }) => void> = [];
    const api = { listCatalog: vi.fn(() => new Promise<{ items: [] }>((resolve) => pending.push(resolve))) };
    const first = gateway('token-one');
    const second = gateway('token-two');
    const { result, rerender } = renderHook(({ sessionGateway }) => usePlugins({ api, sessionGateway, catalogDigest: digest }), { initialProps: { sessionGateway: first } });
    await waitFor(() => expect(api.listCatalog).toHaveBeenCalledTimes(1));
    rerender({ sessionGateway: second });
    await waitFor(() => expect(api.listCatalog).toHaveBeenCalledTimes(2));
    await act(async () => pending[1]?.({ items: [] }));
    expect(result.current.state.kind).toBe('ready');
    await act(async () => pending[0]?.({ items: [] }));
    expect(result.current.state.kind).toBe('ready');
    expect(JSON.stringify(result.current)).not.toMatch(/token-one|token-two/);
  });

  it('uses authorized project state to mark a catalog item installed', async () => {
    const component = { componentDigest: 'b'.repeat(64), name: 'github-mcp', kind: 'mcp', admission: { status: 'admitted', policyVersion: 'p1' }, availability: { status: 'available' }, status: 'available' };
    const catalogItem = { ...base, name: 'github', catalogDigest: digest, sourceCommit: 'c'.repeat(40), policyVersion: 'p1', license: { declaration: 'MIT', decision: 'admitted' }, admission: 'admitted', components: [component] };
    const api = {
      listCatalog: vi.fn(async () => ({ items: [catalogItem] })),
      listProjectPlugins: vi.fn(async () => ({ items: [{ ...catalogItem, enabled: true, revision: 3 }] })),
    };
    const sessionGateway = gateway('token');
    const { result } = renderHook(() => usePlugins({ api: api as never, sessionGateway, catalogDigest: digest, projectId: 'project-1' }));
    await waitFor(() => expect(result.current.state.kind).toBe('ready'));
    expect(result.current.state.kind === 'ready' ? result.current.state.items[0]?.status : null).toBe('installed');
    expect(api.listProjectPlugins).toHaveBeenCalledOnce();
    expect(JSON.stringify(result.current)).not.toContain('token');
  });

  it.each([
    { enabled: false, admission: 'admitted', status: 'available' },
    { enabled: true, admission: 'rejected', status: 'unavailable' },
    { enabled: true, admission: 'admitted', status: 'migration_required' },
  ] as const)('does not show incomplete project truth as installed: $status/$admission', async (overlay) => {
    const catalogItem = { ...base, name: 'github', catalogDigest: digest, sourceCommit: 'c'.repeat(40), policyVersion: 'p1', license: { declaration: 'MIT', decision: 'admitted' }, admission: 'admitted', components: [] };
    const api = { listCatalog: vi.fn(async () => ({ items: [catalogItem] })), listProjectPlugins: vi.fn(async () => ({ items: [{ ...catalogItem, ...overlay, revision: 1 }] })) };
    const sessionGateway = gateway();
    const { result } = renderHook(() => usePlugins({ api: api as never, sessionGateway, catalogDigest: digest, projectId: 'project-1' }));
    await waitFor(() => expect(result.current.state.kind).toBe('ready'));
    expect(result.current.state.kind === 'ready' ? result.current.state.items[0]?.status : null).toBe('available');
  });

  it('rejects a preview scope mismatch before install fetch', async () => {
    const api = { listCatalog: vi.fn(async () => ({ items: [] })), installPreview: vi.fn() };
    const sessionGateway = gateway();
    const { result } = renderHook(() => usePlugins({ api, sessionGateway, catalogDigest: digest, projectId: 'project-1' }));
    await waitFor(() => expect(result.current.state.kind).toBe('ready'));
    await expect(result.current.confirm({ preview: {} as never, projectId: 'project-2', scope })).rejects.toThrow('plugin_api_scope_changed');
    expect(api.installPreview).not.toHaveBeenCalled();
  });

});

describe('rowboatPluginSessionGateway', () => {
  it('keeps the trusted token inside one imperative operation and out of results and storage', async () => {
    const secret = 'trusted-ephemeral-secret';
    Object.defineProperty(globalThis.crypto, 'subtle', { configurable: true, value: { digest: vi.fn(async () => new Uint8Array(32).buffer) } });
    Object.defineProperty(window, 'ipc', { configurable: true, value: {
      invoke: vi.fn(async () => ({ signedIn: true, accessToken: secret, config: {
        appUrl: 'https://rowboat.example/', websocketApiUrl: 'wss://rowboat.example/', supabaseUrl: 'https://supabase.example/',
      } })),
      on: vi.fn(() => () => undefined),
    } });
    const result = await rowboatPluginSessionGateway.run(async (session) => session.accessToken === secret ? 'authorized' : 'denied');
    expect(result.kind === 'ok' ? result.value : null).toBe('authorized');
    expect(JSON.stringify(result)).not.toContain(secret);
    expect(window.localStorage.length).toBe(0);
    expect(window.sessionStorage.length).toBe(0);
  });

  it('redacts trusted IPC and malformed descriptor errors', async () => {
    Object.defineProperty(window, 'ipc', { configurable: true, value: {
      invoke: vi.fn(async () => { throw new Error('raw-provider-secret'); }),
      on: vi.fn(() => () => undefined),
    } });
    await expect(rowboatPluginSessionGateway.run(async () => 'unused')).rejects.toThrow('plugin_session_unavailable');
    await expect(rowboatPluginSessionGateway.run(async () => 'unused')).rejects.not.toThrow('raw-provider-secret');
  });
});
