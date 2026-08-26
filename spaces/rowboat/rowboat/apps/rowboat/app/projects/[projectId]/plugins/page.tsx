import { PINNED_PLUGIN_CATALOG_DIGEST } from "@rowboat/openai-plugin-runtime";
import { listPluginCatalogAction } from "@/app/actions/plugin.actions";
import { PluginCatalog } from "./components/plugin-catalog";

function safeError(error: unknown): string {
  return error instanceof Error && Object.getPrototypeOf(error) === Error.prototype ? error.message : "internal_error";
}

export default async function Page({ params }: { readonly params: Promise<{ projectId: string }> }) {
  const { projectId } = await params;
  try {
    const catalog = await listPluginCatalogAction({ projectId, catalogDigest: PINNED_PLUGIN_CATALOG_DIGEST });
    return (
      <main className="h-full overflow-auto p-6">
        <div className="mx-auto max-w-7xl">
          <h1 className="text-2xl font-semibold text-zinc-900 dark:text-zinc-100">Plugins</h1>
          <p className="mb-6 mt-1 text-sm text-zinc-500">Pinned OpenAI plugin catalog</p>
          <PluginCatalog projectId={projectId} items={catalog.items} />
        </div>
      </main>
    );
  } catch (error) {
    return <main className="p-6"><h1 className="text-2xl font-semibold">Plugins</h1><p role="alert" className="mt-4 break-all text-red-700">{safeError(error)}</p></main>;
  }
}
