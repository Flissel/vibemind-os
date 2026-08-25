import type { PluginCatalogController } from "@/src/interface-adapters/controllers/plugins/plugin-catalog.controller";
import { assertRoute, catalogResponse, params, pluginErrorResponse, query, type CatalogController, type RouteContext } from "../../projects/[projectId]/plugins/_responses";

type Context = RouteContext<{ pluginName: string }>;

async function productionController(): Promise<CatalogController> {
  const { container } = await import("@/di/container");
  return container.resolve<PluginCatalogController>("pluginCatalogController");
}

export function createCatalogItemRoute(controller: CatalogController) {
  return async function catalogItemGET(request: Request, context: Context): Promise<Response> {
    try {
      const routeParams = await params(context, ["pluginName"]);
      assertRoute(request, "GET", ["api", "v1", "plugins", routeParams.pluginName]);
      const input = query(request, ["catalogDigest"]);
      return catalogResponse(await controller.execute(request, input), routeParams.pluginName);
    } catch (error) { return pluginErrorResponse(error); }
  };
}

export async function GET(request: Request, context: Context): Promise<Response> {
  try { return await createCatalogItemRoute(await productionController())(request, context); }
  catch (error) { return pluginErrorResponse(error); }
}
