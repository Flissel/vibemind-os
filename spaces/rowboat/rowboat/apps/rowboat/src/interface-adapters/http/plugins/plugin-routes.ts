import {
  assertRoute, catalogResponse, idempotencyKey, installationResponse, installResponse, jsonBody, params,
  pluginErrorResponse, previewResponse, projectListResponse, query, strictObject,
  type CatalogController, type InstallationController, type RouteContext,
} from "@/app/api/v1/projects/[projectId]/plugins/_responses";

type ControllerSource<T> = T | (() => Promise<T>);
type ProjectContext = RouteContext<{ projectId: string }>;
type PluginContext = RouteContext<{ projectId: string; pluginName: string }>;
type CatalogItemContext = RouteContext<{ pluginName: string }>;

function controller<T>(source: ControllerSource<T>): Promise<T> {
  return typeof source === "function" ? (source as () => Promise<T>)() : Promise.resolve(source);
}

async function catalogController(): Promise<CatalogController> {
  const { resolvePluginCatalogController } = await import("@/di/plugins-container");
  return resolvePluginCatalogController();
}

async function installationController(): Promise<InstallationController> {
  const { resolvePluginInstallationController } = await import("@/di/plugins-container");
  return resolvePluginInstallationController();
}

export function createCatalogCollectionRoute(source: ControllerSource<CatalogController>) {
  return async function catalogCollectionGET(request: Request): Promise<Response> {
    try {
      assertRoute(request, "GET", ["api", "v1", "plugins"]);
      const input = query(request, ["catalogDigest"]);
      return catalogResponse(await (await controller(source)).execute(request, input));
    } catch (error) { return pluginErrorResponse(error); }
  };
}

export function createCatalogItemRoute(source: ControllerSource<CatalogController>) {
  return async function catalogItemGET(request: Request, context: CatalogItemContext): Promise<Response> {
    try {
      const routeParams = await params(context, ["pluginName"]);
      assertRoute(request, "GET", ["api", "v1", "plugins", routeParams.pluginName]);
      const input = query(request, ["catalogDigest"]);
      return catalogResponse(await (await controller(source)).execute(request, input), routeParams.pluginName);
    } catch (error) { return pluginErrorResponse(error); }
  };
}

export function createProjectPluginsRoute(source: ControllerSource<InstallationController>, options: { readonly bodyReadTimeoutMs?: number } = {}) {
  return Object.freeze({
    async GET(request: Request, context: ProjectContext): Promise<Response> {
      try {
        const routeParams = await params(context, ["projectId"]);
        assertRoute(request, "GET", ["api", "v1", "projects", routeParams.projectId, "plugins"]);
        const input = query(request, ["catalogDigest"]);
        const selected = await controller(source);
        if (selected.list === undefined) throw new Error("response_invalid");
        return projectListResponse(await selected.list(request, { projectId: routeParams.projectId, catalogDigest: input.catalogDigest }));
      } catch (error) { return pluginErrorResponse(error); }
    },
    async POST(request: Request, context: ProjectContext): Promise<Response> {
      try {
        const key = idempotencyKey(request);
        const routeParams = await params(context, ["projectId"]);
        assertRoute(request, "POST", ["api", "v1", "projects", routeParams.projectId, "plugins"]);
        const body = strictObject(await jsonBody(request, options.bodyReadTimeoutMs), ["pluginName", "catalogDigest", "expectedRevision"]);
        const selected = await controller(source);
        if (selected.install === undefined) throw new Error("response_invalid");
        return installResponse(await selected.install(request, {
          projectId: routeParams.projectId, pluginName: body.pluginName, catalogDigest: body.catalogDigest,
          expectedRevision: body.expectedRevision, idempotencyKey: key,
        }));
      } catch (error) { return pluginErrorResponse(error); }
    },
  });
}

export function createProjectPluginRoute(source: ControllerSource<InstallationController>, options: { readonly bodyReadTimeoutMs?: number } = {}) {
  return Object.freeze({
    async GET(request: Request, context: PluginContext): Promise<Response> {
      try {
        const routeParams = await params(context, ["projectId", "pluginName"]);
        assertRoute(request, "GET", ["api", "v1", "projects", routeParams.projectId, "plugins", routeParams.pluginName]);
        const input = query(request, ["catalogDigest"]);
        const selected = await controller(source);
        if (selected.preview === undefined) throw new Error("response_invalid");
        return previewResponse(await selected.preview(request, { ...routeParams, catalogDigest: input.catalogDigest }));
      } catch (error) { return pluginErrorResponse(error); }
    },
    async PATCH(request: Request, context: PluginContext): Promise<Response> {
      try {
        const key = idempotencyKey(request);
        const routeParams = await params(context, ["projectId", "pluginName"]);
        assertRoute(request, "PATCH", ["api", "v1", "projects", routeParams.projectId, "plugins", routeParams.pluginName]);
        const body = strictObject(await jsonBody(request, options.bodyReadTimeoutMs), ["catalogDigest", "enabled", "expectedRevision"]);
        const selected = await controller(source);
        if (selected.setEnabled === undefined) throw new Error("response_invalid");
        const output = await selected.setEnabled(request, {
          ...routeParams, catalogDigest: body.catalogDigest, enabled: body.enabled,
          expectedRevision: body.expectedRevision, idempotencyKey: key,
        });
        return installationResponse(output, String(body.catalogDigest));
      } catch (error) { return pluginErrorResponse(error); }
    },
  });
}

export const catalogCollectionGET = createCatalogCollectionRoute(catalogController);
export const catalogItemGET = createCatalogItemRoute(catalogController);
export const projectPluginsRoutes = createProjectPluginsRoute(installationController);
export const projectPluginRoutes = createProjectPluginRoute(installationController);
