import {
  assertRoute, catalogResponse, idempotencyKey, installationResponse, installResponse, jsonBody, params,
  pluginErrorResponse, previewResponse, projectListResponse, query, strictObject, toolBindingResponse,
  assertRouteWithoutQuery, pluginSessionResponse, type CatalogController, type InstallationController, type PluginSessionControllerLike, type RouteContext,
} from "@/app/api/v1/projects/[projectId]/plugins/_responses";

type ControllerSource<T> = T | (() => Promise<T>);
type ProjectContext = RouteContext<{ projectId: string }>;
type PluginContext = RouteContext<{ projectId: string; pluginName: string }>;
type CatalogItemContext = RouteContext<{ pluginName: string }>;

export interface PluginToolController {
  add(request: Request, input: Readonly<{ projectId: string; pluginName: string; componentDigest: unknown }>): Promise<unknown>;
}

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

async function sessionController(): Promise<PluginSessionControllerLike> {
  const { resolvePluginSessionController } = await import("@/di/plugins-container");
  return resolvePluginSessionController();
}

async function toolController(): Promise<PluginToolController> {
  const { resolveAddPluginTool, resolvePluginActionIdentity } = await import("@/di/plugins-container");
  return {
    add: async (request, input) => {
      // The tool name is derived server-side (pluginToolName), never taken
      // from the caller, so nothing here reads or forwards one. The digest
      // format itself is validated by the use case -- this is only the
      // narrowing a Request-shaped `unknown` needs before it can be handed
      // to a function whose signature demands a string.
      if (typeof input.componentDigest !== "string") throw new Error("request_invalid");
      const identity = await resolvePluginActionIdentity(request);
      return resolveAddPluginTool({
        identity, projectId: input.projectId, pluginName: input.pluginName, componentDigest: input.componentDigest,
      });
    },
  };
}

export function createPluginSessionRoute(source: ControllerSource<PluginSessionControllerLike>) {
  return async function pluginSessionGET(request: Request): Promise<Response> {
    try {
      assertRouteWithoutQuery(request, "GET", ["api", "v1", "plugin-session"]);
      return pluginSessionResponse(await (await controller(source)).execute(request));
    } catch (error) { return pluginErrorResponse(error); }
  };
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
        const body = strictObject(await jsonBody(request, options.bodyReadTimeoutMs), ["pluginName", "catalogDigest", "expectedRevision"], ["componentDigests"]);
        const selected = await controller(source);
        if (selected.install === undefined) throw new Error("response_invalid");
        return installResponse(await selected.install(request, {
          projectId: routeParams.projectId, pluginName: body.pluginName, catalogDigest: body.catalogDigest,
          expectedRevision: body.expectedRevision, idempotencyKey: key,
          // Absent means every component, which is what every client that
          // predates component-scoped installation already sends.
          ...(body.componentDigests === undefined ? {} : { componentDigests: body.componentDigests }),
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

export function createProjectPluginToolsRoute(source: ControllerSource<PluginToolController>, options: { readonly bodyReadTimeoutMs?: number } = {}) {
  return async function projectPluginToolsPOST(request: Request, context: PluginContext): Promise<Response> {
    try {
      const routeParams = await params(context, ["projectId", "pluginName"]);
      assertRoute(request, "POST", ["api", "v1", "projects", routeParams.projectId, "plugins", routeParams.pluginName, "tools"]);
      const body = strictObject(await jsonBody(request, options.bodyReadTimeoutMs), ["componentDigest"]);
      const selected = await controller(source);
      const output = await selected.add(request, {
        projectId: routeParams.projectId, pluginName: routeParams.pluginName, componentDigest: body.componentDigest,
      });
      return toolBindingResponse(output);
    } catch (error) { return pluginErrorResponse(error); }
  };
}

export const catalogCollectionGET = createCatalogCollectionRoute(catalogController);
export const pluginSessionGET = createPluginSessionRoute(sessionController);
export const catalogItemGET = createCatalogItemRoute(catalogController);
export const projectPluginsRoutes = createProjectPluginsRoute(installationController);
export const projectPluginRoutes = createProjectPluginRoute(installationController);
export const projectPluginToolsPOST = createProjectPluginToolsRoute(toolController);
