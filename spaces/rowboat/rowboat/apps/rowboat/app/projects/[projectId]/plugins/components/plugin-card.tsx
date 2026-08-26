import type { PluginUiCatalogItem } from "@/src/interface-adapters/actions/plugin-action-runtime";

export type PluginCatalogCardItem = PluginUiCatalogItem;

const BADGES = Object.freeze({
  available: "Available",
  review_required: "Review required",
  installed: "Installed",
  partially_available: "Partially available",
  unavailable: "Unavailable",
  migration_required: "Migration required",
  error: "Error",
} as const);

export function toPluginCardView(item: PluginCatalogCardItem) {
  return Object.freeze({
    pluginName: item.pluginName,
    pluginVersion: item.pluginVersion,
    badge: BADGES[item.status],
    reason: item.reason,
    canInstall: item.status === "available",
    components: Object.freeze(item.components.map((component) => Object.freeze({ ...component }))),
  });
}

export function PluginCard({ item, onInstall }: { readonly item: PluginCatalogCardItem; readonly onInstall: (item: PluginCatalogCardItem) => void }) {
  const view = toPluginCardView(item);
  return (
    <article className="rounded-xl border border-zinc-200 bg-white p-5 shadow-sm dark:border-zinc-800 dark:bg-zinc-900">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 className="font-semibold text-zinc-900 dark:text-zinc-100">{view.pluginName}</h2>
          <p className="text-sm text-zinc-500">Version {view.pluginVersion}</p>
        </div>
        <span className="rounded-full bg-zinc-100 px-2.5 py-1 text-xs font-medium text-zinc-700 dark:bg-zinc-800 dark:text-zinc-200">{view.badge}</span>
      </div>
      {view.reason !== undefined && <p className="mt-3 break-all text-sm text-amber-700 dark:text-amber-300">{view.reason}</p>}
      <p className="mt-3 text-sm text-zinc-600 dark:text-zinc-400">{view.components.length} components</p>
      <button
        type="button"
        disabled={!view.canInstall}
        onClick={() => onInstall(item)}
        className="mt-4 rounded-md bg-indigo-600 px-3 py-2 text-sm font-medium text-white disabled:cursor-not-allowed disabled:bg-zinc-300 dark:disabled:bg-zinc-700"
        aria-label={`Install ${view.pluginName}`}
      >
        {item.status === "installed" ? "Installed" : "Review installation"}
      </button>
    </article>
  );
}
