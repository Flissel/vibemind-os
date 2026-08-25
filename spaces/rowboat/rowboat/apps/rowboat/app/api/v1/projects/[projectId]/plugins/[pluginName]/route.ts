import type { PluginInstallationController } from "@/src/interface-adapters/controllers/plugins/plugin-installation.controller";
import {
  assertRoute, idempotencyKey, installationResponse, jsonBody, params, pluginErrorResponse, previewResponse, query, strictObject,
  type InstallationController, type RouteContext,
} from "../_responses";

type Context = RouteContext<{ projectId: string; pluginName: string }>;

async function productionController(): Promise<InstallationController> {
  const { container } = await import("@/di/container");
  return container.resolve<PluginInstallationController>("pluginInstallationController");
}

export function createProjectPluginRoute(controller: InstallationController) {
  return Object.freeze({
    async GET(request: Request, context: Context): Promise<Response> {
      try {
        const routeParams = await params(context, ["projectId", "pluginName"]);
        assertRoute(request, "GET", ["api", "v1", "projects", routeParams.projectId, "plugins", routeParams.pluginName]);
        const input = query(request, ["catalogDigest"]);
        if (controller.preview === undefined) throw new Error("response_invalid");
        return previewResponse(await controller.preview(request, { ...routeParams, catalogDigest: input.catalogDigest }));
      } catch (error) { return pluginErrorResponse(error); }
    },
    async PATCH(request: Request, context: Context): Promise<Response> {
      try {
        const key = idempotencyKey(request);
        const routeParams = await params(context, ["projectId", "pluginName"]);
        assertRoute(request, "PATCH", ["api", "v1", "projects", routeParams.projectId, "plugins", routeParams.pluginName]);
        const body = strictObject(await jsonBody(request), ["catalogDigest", "enabled", "expectedRevision"]);
        if (controller.setEnabled === undefined) throw new Error("response_invalid");
        const output = await controller.setEnabled(request, {
          ...routeParams, catalogDigest: body.catalogDigest, enabled: body.enabled,
          expectedRevision: body.expectedRevision, idempotencyKey: key,
        });
        return installationResponse(output, String(body.catalogDigest));
      } catch (error) { return pluginErrorResponse(error); }
    },
  });
}

export async function GET(request: Request, context: Context): Promise<Response> {
  try { return await createProjectPluginRoute(await productionController()).GET(request, context); }
  catch (error) { return pluginErrorResponse(error); }
}
export async function PATCH(request: Request, context: Context): Promise<Response> {
  try { return await createProjectPluginRoute(await productionController()).PATCH(request, context); }
  catch (error) { return pluginErrorResponse(error); }
}
