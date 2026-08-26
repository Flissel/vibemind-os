import { useEffect, useState } from 'react';

import { usePlugins, type PluginsState, type ScopedPluginPreview } from '@/hooks/usePlugins';
import type { PluginCatalogItem, PluginReasonCode, PluginStatus } from '@/lib/rowboat-plugin-api';

export interface PluginSettingsItem {
  readonly pluginName: string;
  readonly pluginVersion: string;
  readonly status: PluginStatus;
  readonly reason?: PluginReasonCode;
  readonly components: readonly unknown[];
  readonly credentialSlots?: readonly Readonly<{ name: string; configured: boolean }>[];
}

export function PluginInstallFeedback({ status, reason }: Readonly<{ status: 'success' | 'failed' | 'denied' | 'timed_out'; reason?: PluginReasonCode }>) {
  if (status === 'success') return <p role="status" aria-live="polite">Plugin installed.</p>;
  return <p role="alert">{status}{reason === undefined ? '' : ` · ${reason}`}</p>;
}

interface PluginSettingsProps {
  readonly state: PluginsState | Readonly<{ kind: 'ready'; items: readonly PluginSettingsItem[] }>;
  readonly onInstall?: (item: PluginSettingsItem) => void | Promise<void>;
}

export function PluginSettings({ state, onInstall }: PluginSettingsProps) {
  if (state.kind === 'loading') return <p role="status" aria-live="polite">Loading plugins…</p>;
  if (state.kind === 'signed_out') return <p role="status" aria-live="polite">Sign in to Rowboat to view plugins.</p>;
  if (state.kind === 'error') return <p role="alert">{state.reason}</p>;
  if (state.items.length === 0) return <p role="status" aria-live="polite">No plugins available.</p>;
  return (
    <section aria-label="Plugins" className="space-y-3" aria-live="polite">
      {state.items.map((item) => {
        const canInstall = item.status === 'available' && onInstall !== undefined;
        const credentialSlots = 'credentialSlots' in item ? item.credentialSlots : undefined;
        return (
          <article key={item.pluginName} className="rounded-lg border p-3">
            <div className="flex items-start justify-between gap-3">
              <div>
                <h4 className="font-medium">{item.pluginName}</h4>
                <p className="text-xs text-muted-foreground">Version {item.pluginVersion}</p>
              </div>
              <span className="rounded-full bg-muted px-2 py-1 text-xs">{item.status}</span>
            </div>
            {item.reason !== undefined && <p className="mt-2 break-all text-xs text-amber-700">{item.reason}</p>}
            {credentialSlots !== undefined && credentialSlots.length > 0 && (
              <ul aria-label={`${item.pluginName} credential slots`} className="mt-2 space-y-1 text-xs">
                {credentialSlots.map((slot) => <li key={slot.name}>{slot.name} — {slot.configured ? 'configured' : 'required'}</li>)}
              </ul>
            )}
            <button
              type="button"
              aria-label={`Install ${item.pluginName}`}
              disabled={!canInstall}
              onClick={() => { if (canInstall) void onInstall(item); }}
              className="mt-3 rounded-md bg-primary px-3 py-2 text-xs text-primary-foreground disabled:cursor-not-allowed disabled:opacity-50"
            >
              {item.status === 'installed' ? 'Installed' : 'Install'}
            </button>
          </article>
        );
      })}
    </section>
  );
}

interface PluginInstallReviewProps {
  readonly preview: Readonly<{
    pluginName: string;
    status: PluginStatus;
    components: readonly Readonly<{ name: string; kind: string; status: string; reason?: PluginReasonCode }>[];
    credentialSlots: readonly Readonly<{ name: string; configured: boolean }>[];
  }>;
  readonly onConfirm: () => void | Promise<void>;
  readonly onCancel: () => void;
  readonly pending: boolean;
  readonly projectId: string;
  readonly scopeValid: boolean;
}

export function PluginInstallReview({ preview, onConfirm, onCancel, pending, projectId, scopeValid }: PluginInstallReviewProps) {
  return (
    <section aria-label={`Review ${preview.pluginName} installation`} className="space-y-3 rounded-lg border p-3">
      <h4 className="font-medium">Review {preview.pluginName}</h4>
      <p className="text-xs">Project: {projectId}</p>
      <ul aria-label="Component decisions" className="space-y-1 text-xs">
        {preview.components.map((component) => <li key={`${component.kind}:${component.name}`}>
          {component.name} · {component.kind} · {component.status}{component.reason === undefined ? '' : ` · ${component.reason}`}
        </li>)}
      </ul>
      <ul aria-label="Credential requirements" className="space-y-1 text-xs">
        {preview.credentialSlots.map((slot) => <li key={slot.name}>{slot.name} — {slot.configured ? 'configured' : 'required'}</li>)}
      </ul>
      <div className="flex gap-2">
        <button type="button" onClick={onCancel} disabled={pending} className="rounded-md border px-3 py-2 text-xs">Cancel</button>
        <button type="button" aria-label={`Confirm install ${preview.pluginName}`} onClick={() => void onConfirm()} disabled={pending || !scopeValid || preview.status !== 'available'} className="rounded-md bg-primary px-3 py-2 text-xs text-primary-foreground disabled:opacity-50">
          {pending ? 'Installing…' : 'Confirm install'}
        </button>
      </div>
    </section>
  );
}

export function ConnectedPluginSettings() {
  const [projectId, setProjectId] = useState('');
  const { state, preview, confirm } = usePlugins({ projectId });
  const [selectedPreview, setSelectedPreview] = useState<ScopedPluginPreview | null>(null);
  const [mutationState, setMutationState] = useState<
    | Readonly<{ kind: 'idle' | 'pending' | 'success' }>
    | Readonly<{ kind: 'error'; reason: string }>
    | Readonly<{ kind: 'receipt'; status: 'failed' | 'denied' | 'timed_out'; reason?: PluginReasonCode }>
  >({ kind: 'idle' });
  const scopeValid = selectedPreview !== null && state.kind === 'ready' && state.projectId === projectId
    && selectedPreview.projectId === projectId && selectedPreview.scope.origin === state.scope.origin
    && selectedPreview.scope.accountFingerprint === state.scope.accountFingerprint;
  useEffect(() => {
    if (selectedPreview !== null && !scopeValid) setSelectedPreview(null);
  }, [scopeValid, selectedPreview]);
  const installPlugin = async (item: PluginSettingsItem) => {
    setMutationState({ kind: 'pending' });
    try {
      const result = await preview(projectId, item as PluginCatalogItem);
      setSelectedPreview(result);
      setMutationState({ kind: 'idle' });
    } catch (error) {
      const reason = error instanceof Error && Object.getPrototypeOf(error) === Error.prototype && /^plugin_api_[a-z_]+$/.test(error.message)
        ? error.message : 'plugin_api_unavailable';
      setMutationState({ kind: 'error', reason });
    }
  };
  const confirmInstall = async () => {
    if (selectedPreview === null || !scopeValid) { setMutationState({ kind: 'error', reason: 'plugin_api_scope_changed' }); return; }
    setMutationState({ kind: 'pending' });
    try {
      const receipt = await confirm(selectedPreview);
      if (receipt.status === 'success') {
        setSelectedPreview(null);
        setMutationState({ kind: 'success' });
      } else setMutationState({ kind: 'receipt', status: receipt.status, ...(receipt.reason === undefined ? {} : { reason: receipt.reason }) });
    } catch (error) {
      const reason = error instanceof Error && Object.getPrototypeOf(error) === Error.prototype && /^plugin_api_[a-z_]+$/.test(error.message)
        ? error.message : 'plugin_api_unavailable';
      setMutationState({ kind: 'error', reason });
    }
  };
  return (
    <div className="space-y-4">
      <div>
        <label htmlFor="plugin-project-id" className="text-sm font-medium">Rowboat project ID</label>
        <input
          id="plugin-project-id"
          value={projectId}
          onChange={(event) => setProjectId(event.target.value)}
          disabled={selectedPreview !== null || mutationState.kind === 'pending'}
          autoComplete="off"
          className="mt-1 w-full rounded-md border bg-background px-3 py-2 text-sm"
        />
        <p className="mt-1 text-xs text-muted-foreground">Required for project-authorized installation. It is kept only in this dialog.</p>
      </div>
      <PluginSettings state={state} onInstall={projectId === '' || mutationState.kind === 'pending' ? undefined : installPlugin} />
      {selectedPreview !== null && <PluginInstallReview preview={selectedPreview.preview} projectId={selectedPreview.projectId} scopeValid={scopeValid} onConfirm={confirmInstall} onCancel={() => setSelectedPreview(null)} pending={mutationState.kind === 'pending'} />}
      {mutationState.kind === 'pending' && <p role="status" aria-live="polite">Installing plugin…</p>}
      {mutationState.kind === 'success' && <PluginInstallFeedback status="success" />}
      {mutationState.kind === 'error' && <p role="alert">{mutationState.reason}</p>}
      {mutationState.kind === 'receipt' && <PluginInstallFeedback status={mutationState.status} reason={mutationState.reason} />}
    </div>
  );
}
