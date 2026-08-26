"use client";

import { useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { installPluginAction } from "@/app/actions/plugin.actions";
import type { PluginUiPreview } from "@/src/interface-adapters/actions/plugin-action-runtime";

export function toPluginInstallDialogView(preview: PluginUiPreview) {
  return Object.freeze({
    pluginName: preview.pluginName,
    canInstall: preview.status === "available",
    components: Object.freeze(preview.components.map(({ name, kind, status, reason }) => Object.freeze({
      name, kind, status, ...(reason === undefined ? {} : { reason }),
    }))),
    credentialSlots: Object.freeze(preview.credentialSlots.map(({ name, configured }) => Object.freeze({ name, configured }))),
  });
}

function safeError(error: unknown): string {
  return error instanceof Error && Object.getPrototypeOf(error) === Error.prototype ? error.message : "internal_error";
}

export function PluginInstallDialog({ projectId, preview, onClose }: {
  readonly projectId: string;
  readonly preview: PluginUiPreview;
  readonly onClose: () => void;
}) {
  const view = toPluginInstallDialogView(preview);
  const router = useRouter();
  const [pending, startTransition] = useTransition();
  const [error, setError] = useState<string | null>(null);

  const install = () => startTransition(async () => {
    setError(null);
    try {
      await installPluginAction({
        projectId,
        pluginName: preview.pluginName,
        catalogDigest: preview.catalogDigest,
        expectedRevision: preview.expectedRevision,
        idempotencyKey: preview.idempotencyKey,
      });
      router.refresh();
      onClose();
    } catch (caught) { setError(safeError(caught)); }
  });

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" role="presentation">
      <section role="dialog" aria-modal="true" aria-labelledby="plugin-install-title" className="max-h-[85vh] w-full max-w-2xl overflow-auto rounded-xl bg-white p-6 shadow-xl dark:bg-zinc-900">
        <h2 id="plugin-install-title" className="text-lg font-semibold">Install {view.pluginName}</h2>
        <h3 className="mt-5 font-medium">Component decisions</h3>
        <ul className="mt-2 space-y-2">
          {view.components.map((component) => (
            <li key={`${component.kind}:${component.name}`} className="rounded-md bg-zinc-50 p-3 text-sm dark:bg-zinc-800">
              <span className="font-medium">{component.name}</span> · {component.kind} · {component.status}
              {component.reason !== undefined && <span className="block break-all text-amber-700 dark:text-amber-300">{component.reason}</span>}
            </li>
          ))}
        </ul>
        <h3 className="mt-5 font-medium">Credential requirements</h3>
        {view.credentialSlots.length === 0 ? <p className="mt-2 text-sm text-zinc-500">No credential slots required.</p> : (
          <ul className="mt-2 space-y-2">
            {view.credentialSlots.map((slot) => <li key={slot.name} className="text-sm"><code>{slot.name}</code> — {slot.configured ? "configured" : "required"}</li>)}
          </ul>
        )}
        {error !== null && <p role="alert" className="mt-4 break-all text-sm text-red-700 dark:text-red-300">{error}</p>}
        <div className="mt-6 flex justify-end gap-3">
          <button type="button" onClick={onClose} disabled={pending} className="rounded-md border px-3 py-2 text-sm">Cancel</button>
          <button type="button" onClick={install} disabled={pending || !view.canInstall} className="rounded-md bg-indigo-600 px-3 py-2 text-sm font-medium text-white disabled:cursor-not-allowed disabled:bg-zinc-400">
            {pending ? "Installing…" : "Install"}
          </button>
        </div>
      </section>
    </div>
  );
}
