import type { PluginInstallationController } from "@/src/interface-adapters/controllers/plugins/plugin-installation.controller";
import {
  assertRoute, idempotencyKey, installResponse, jsonBody, params, pluginErrorResponse, projectListResponse, query, strictObject,
  type InstallationController, type RouteContext,
} from "./_responses";

type Context = RouteContext<{ projectId: string }>;

async function productionController(): Promise<InstallationController> {
  const { container } = await import("@/di/container");
  return container.resolve<PluginInstallationController>("pluginInstallationController");
}

export function createProjectPluginsRoute(controller: InstallationController) {
  return Object.freeze({
    async GET(request: Request, context: Context): Promise<Response> {
      try {
        if (controller.list === undefined) throw new Error("response_invalid");
        const routeParams = await params(context, ["projectId"]);
        assertRoute(request, "GET", ["api", "v1", "projects", routeParams.projectId, "plugins"]);
        const input = query(request, ["catalogDigest"]);
        return projectListResponse(await controller.list(request, { projectId: routeParams.projectId, catalogDigest: input.catalogDigest }));
      } catch (error) { return pluginErrorResponse(error); }
    },
    async POST(request: Request, context: Context): Promise<Response> {
      try {
        const key = idempotencyKey(request);
        const routeParams = await params(context, ["projectId"]);
        assertRoute(request, "POST", ["api", "v1", "projects", routeParams.projectId, "plugins"]);
        const body = strictObject(await jsonBody(request), ["pluginName", "catalogDigest", "expectedRevision"]);
        if (controller.install === undefined) throw new Error("response_invalid");
        return installResponse(await controller.install(request, {
          projectId: routeParams.projectId, pluginName: body.pluginName, catalogDigest: body.catalogDigest,
          expectedRevision: body.expectedRevision, idempotencyKey: key,
        }));
      } catch (error) { return pluginErrorResponse(error); }
    },
  });
}

export async function GET(request: Request, context: Context): Promise<Response> {
  try { return await createProjectPluginsRoute(await productionController()).GET(request, context); }
  catch (error) { return pluginErrorResponse(error); }
}
export async function POST(request: Request, context: Context): Promise<Response> {
  try { return await createProjectPluginsRoute(await productionController()).POST(request, context); }
  catch (error) { return pluginErrorResponse(error); }
}
