import type { PluginCatalogController } from "@/src/interface-adapters/controllers/plugins/plugin-catalog.controller";
import { assertRoute, catalogResponse, pluginErrorResponse, query, type CatalogController } from "../projects/[projectId]/plugins/_responses";

async function productionController(): Promise<CatalogController> {
  const { container } = await import("@/di/container");
  return container.resolve<PluginCatalogController>("pluginCatalogController");
}

export function createCatalogCollectionRoute(controller: CatalogController) {
  return async function catalogCollectionGET(request: Request): Promise<Response> {
    try {
      assertRoute(request, "GET", ["api", "v1", "plugins"]);
      const input = query(request, ["catalogDigest"]);
      return catalogResponse(await controller.execute(request, input));
    } catch (error) { return pluginErrorResponse(error); }
  };
}

export async function GET(request: Request): Promise<Response> {
  try { return await createCatalogCollectionRoute(await productionController())(request); }
  catch (error) { return pluginErrorResponse(error); }
}
