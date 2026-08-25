type NoExtraExports<Module, Allowed extends PropertyKey> = Exclude<keyof Module, Allowed> extends never ? true : false;

type CatalogCollection = typeof import("@/app/api/v1/plugins/route");
type CatalogItem = typeof import("@/app/api/v1/plugins/[pluginName]/route");
type ProjectCollection = typeof import("@/app/api/v1/projects/[projectId]/plugins/route");
type ProjectItem = typeof import("@/app/api/v1/projects/[projectId]/plugins/[pluginName]/route");

const catalogCollectionExports: NoExtraExports<CatalogCollection, "GET"> = true;
const catalogItemExports: NoExtraExports<CatalogItem, "GET"> = true;
const projectCollectionExports: NoExtraExports<ProjectCollection, "GET" | "POST"> = true;
const projectItemExports: NoExtraExports<ProjectItem, "GET" | "PATCH"> = true;

void [catalogCollectionExports, catalogItemExports, projectCollectionExports, projectItemExports];
