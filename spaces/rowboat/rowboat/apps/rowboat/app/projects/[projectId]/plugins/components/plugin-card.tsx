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

/**
 * Only an installed plugin offers its executable components as tools, and only
 * a component that is available and carries a pinned digest can be referenced.
 */
export function toPluginCardView(item: PluginCatalogCardItem) {
  const installed = item.status === "installed";
  return Object.freeze({
    pluginName: item.pluginName,
    pluginVersion: item.pluginVersion,
    badge: BADGES[item.status],
    reason: item.reason,
    canInstall: item.status === "available",
    components: Object.freeze(item.components.map((component) => Object.freeze({ ...component }))),
    addableComponents: Object.freeze(item.components
      .filter((component) => (component.kind === "app" || component.kind === "mcp")
        && component.status === "available"
        && typeof component.componentDigest === "string")
      .map((component) => Object.freeze({
        name: component.name,
        kind: component.kind,
        componentDigest: component.componentDigest as string,
        canAdd: installed,
      }))),
  });
}

export function PluginCard({ item, onInstall, onAddTool }: {
  readonly item: PluginCatalogCardItem;
  readonly onInstall: (item: PluginCatalogCardItem) => void;
  readonly onAddTool?: (item: PluginCatalogCardItem, componentDigest: string) => void;
}) {
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
      {onAddTool !== undefined && view.addableComponents.length > 0 && (
        <ul className="mt-3 space-y-2">
          {view.addableComponents.map((component) => (
            <li key={component.componentDigest} className="flex items-center justify-between gap-3 text-sm">
              <span className="truncate text-zinc-700 dark:text-zinc-300">{component.name} <span className="text-zinc-400">({component.kind})</span></span>
              <button
                type="button"
                disabled={!component.canAdd}
                onClick={() => onAddTool(item, component.componentDigest)}
                className="shrink-0 rounded-md border border-indigo-600 px-2 py-1 text-xs font-medium text-indigo-700 disabled:cursor-not-allowed disabled:border-zinc-300 disabled:text-zinc-400 dark:text-indigo-300 dark:disabled:border-zinc-700"
                aria-label={`Add ${component.name} to workflow`}
              >
                Add to workflow
              </button>
            </li>
          ))}
        </ul>
      )}
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
