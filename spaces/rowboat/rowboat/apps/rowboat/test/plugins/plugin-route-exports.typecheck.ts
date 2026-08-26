type NoExtraExports<Module, Allowed extends PropertyKey> = Exclude<keyof Module, Allowed> extends never ? true : false;

type CatalogCollection = typeof import("@/app/api/v1/plugins/route");
type CatalogItem = typeof import("@/app/api/v1/plugins/[pluginName]/route");
type ProjectCollection = typeof import("@/app/api/v1/projects/[projectId]/plugins/route");
type ProjectItem = typeof import("@/app/api/v1/projects/[projectId]/plugins/[pluginName]/route");
type PluginSession = typeof import("@/app/api/v1/plugin-session/route");

const catalogCollectionExports: NoExtraExports<CatalogCollection, "GET" | "dynamic"> = true;
const catalogItemExports: NoExtraExports<CatalogItem, "GET" | "dynamic"> = true;
const projectCollectionExports: NoExtraExports<ProjectCollection, "GET" | "POST" | "dynamic"> = true;
const projectItemExports: NoExtraExports<ProjectItem, "GET" | "PATCH" | "dynamic"> = true;
const pluginSessionExports: NoExtraExports<PluginSession, "GET" | "dynamic"> = true;
const dynamicValues: readonly [CatalogCollection["dynamic"], CatalogItem["dynamic"], ProjectCollection["dynamic"], ProjectItem["dynamic"], PluginSession["dynamic"]] = [
  "force-dynamic", "force-dynamic", "force-dynamic", "force-dynamic", "force-dynamic",
];

void [catalogCollectionExports, catalogItemExports, projectCollectionExports, projectItemExports, pluginSessionExports, dynamicValues];
