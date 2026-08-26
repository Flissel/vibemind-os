import '@testing-library/jest-dom/vitest';
import { act, cleanup, fireEvent, render, renderHook, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

vi.mock('@/hooks/useRowboatAccount', () => ({
  useRowboatAccount: () => ({ signedIn: false, accessToken: null, config: null, isLoading: false }),
}));

import { PluginInstallReview, PluginSettings, type PluginSettingsItem } from './plugin-settings';
import { usePlugins } from '@/hooks/usePlugins';
import { SettingsDialog } from '@/components/settings-dialog';

const digest = 'a'.repeat(64);
const base: PluginSettingsItem = {
  pluginName: 'github', pluginVersion: '1.0.0', status: 'available', components: [],
};

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

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
    }} onConfirm={confirm} onCancel={vi.fn()} pending={false} />);
    expect(screen.getByText('github-mcp · mcp · available')).toBeVisible();
    expect(screen.getByText('GITHUB_TOKEN — required')).toBeVisible();
    expect(screen.getByRole('button', { name: 'Confirm install github' })).toBeEnabled();
    expect(confirm).not.toHaveBeenCalled();
  });

  it('has accessible loading, error, labels, and live status', () => {
    const { rerender } = render(<PluginSettings state={{ kind: 'loading' }} onInstall={vi.fn()} />);
    expect(screen.getByRole('status')).toHaveTextContent('Loading plugins');
    rerender(<PluginSettings state={{ kind: 'error', reason: 'plugin_api_unavailable' }} onInstall={vi.fn()} />);
    expect(screen.getByRole('alert')).toHaveTextContent('plugin_api_unavailable');
  });
});

describe('usePlugins', () => {
  it('aborts an unmounted request and does not retain account tokens in state', async () => {
    let signal: AbortSignal | undefined;
    const api = {
      listCatalog: vi.fn(async (_session: unknown, _catalogDigest: string, currentSignal?: AbortSignal) => {
        signal = currentSignal;
        return new Promise<never>(() => undefined);
      }),
    };
    const account = { signedIn: true, accessToken: 'ephemeral-token', config: { appUrl: 'https://rowboat.example/' }, isLoading: false };
    const { result, unmount } = renderHook(() => usePlugins({ api, account, catalogDigest: digest }));
    await waitFor(() => expect(api.listCatalog).toHaveBeenCalledOnce());
    unmount();
    expect(signal?.aborted).toBe(true);
    expect(JSON.stringify(result.current)).not.toContain('ephemeral-token');
  });

  it('prevents stale account results from overwriting the latest response', async () => {
    const pending: Array<(value: { items: [] }) => void> = [];
    const api = { listCatalog: vi.fn(() => new Promise<{ items: [] }>((resolve) => pending.push(resolve))) };
    const first = { signedIn: true, accessToken: 'token-one', config: { appUrl: 'https://one.example/' }, isLoading: false };
    const second = { signedIn: true, accessToken: 'token-two', config: { appUrl: 'https://two.example/' }, isLoading: false };
    const { result, rerender } = renderHook(({ account }) => usePlugins({ api, account, catalogDigest: digest }), { initialProps: { account: first } });
    await waitFor(() => expect(api.listCatalog).toHaveBeenCalledTimes(1));
    rerender({ account: second });
    await waitFor(() => expect(api.listCatalog).toHaveBeenCalledTimes(2));
    await act(async () => pending[1]?.({ items: [] }));
    expect(result.current.state.kind).toBe('ready');
    await act(async () => pending[0]?.({ items: [] }));
    expect(result.current.state.kind).toBe('ready');
    expect(JSON.stringify(result.current)).not.toMatch(/token-one|token-two/);
  });

  it('uses authorized project state to mark a catalog item installed', async () => {
    const api = {
      listCatalog: vi.fn(async () => ({ items: [{ ...base, catalogDigest: digest }] })),
      listProjectPlugins: vi.fn(async () => ({ items: [{ pluginName: 'github', status: 'available', revision: 3 }] })),
    };
    const account = { signedIn: true, accessToken: 'token', config: { appUrl: 'https://rowboat.example/' }, isLoading: false };
    const { result } = renderHook(() => usePlugins({ api: api as never, account, catalogDigest: digest, projectId: 'project-1' }));
    await waitFor(() => expect(result.current.state.kind).toBe('ready'));
    expect(result.current.state.kind === 'ready' ? result.current.state.items[0]?.status : null).toBe('installed');
    expect(api.listProjectPlugins).toHaveBeenCalledOnce();
    expect(JSON.stringify(result.current)).not.toContain('token');
  });
});
