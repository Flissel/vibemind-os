"use client";

import { useState, useTransition } from "react";
import { previewPluginInstallationAction } from "@/app/actions/plugin.actions";
import type { PluginUiCatalogItem, PluginUiPreview } from "@/src/interface-adapters/actions/plugin-action-runtime";
import { PluginCard } from "./plugin-card";
import { PluginInstallDialog } from "./plugin-install-dialog";

export function pluginCatalogPath(projectId: string): string {
  return `/projects/${encodeURIComponent(projectId)}/plugins`;
}

function safeError(error: unknown): string {
  return error instanceof Error && Object.getPrototypeOf(error) === Error.prototype ? error.message : "internal_error";
}

export function PluginCatalog({ projectId, items }: { readonly projectId: string; readonly items: readonly PluginUiCatalogItem[] }) {
  const [preview, setPreview] = useState<PluginUiPreview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pending, startTransition] = useTransition();

  const select = (item: PluginUiCatalogItem) => startTransition(async () => {
    setError(null);
    try {
      const result = await previewPluginInstallationAction({
        projectId, pluginName: item.pluginName, catalogDigest: item.catalogDigest,
      });
      setPreview(result);
    } catch (caught) { setError(safeError(caught)); }
  });

  return (
    <div aria-busy={pending}>
      {error !== null && <p role="alert" className="mb-4 break-all rounded-md bg-red-50 p-3 text-sm text-red-700 dark:bg-red-950 dark:text-red-300">{error}</p>}
      {pending && <p role="status" className="mb-4 text-sm text-zinc-500">Loading installation preview…</p>}
      {items.length === 0 ? <p className="text-zinc-500">No plugins are available in this catalog.</p> : (
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {items.map((item) => <PluginCard key={item.pluginName} item={item} onInstall={select} />)}
        </div>
      )}
      {preview !== null && <PluginInstallDialog preview={preview} onClose={() => setPreview(null)} />}
    </div>
  );
}
