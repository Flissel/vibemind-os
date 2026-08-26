import { assertRouteWithoutQuery, jsonBody, params, pluginErrorResponse, pluginJson, requestSignal, strictObject, type RouteContext } from "@/app/api/v1/projects/[projectId]/plugins/_responses";

interface MigrationController { execute(request: Request, input: Readonly<Record<string, unknown>>): Promise<unknown> }
type Source = MigrationController | (() => Promise<MigrationController>);
type Context = RouteContext<{ projectId: string }>;
const selected = (source: Source): Promise<MigrationController> => typeof source === "function" ? source() : Promise.resolve(source);

export function createMigrationPreviewRoute(source: Source) {
  return async function migrationPreviewGET(request: Request, context: Context): Promise<Response> {
    try {
      const routeParams = await params(context, ["projectId"]);
      assertRouteWithoutQuery(request, "GET", ["api", "v1", "projects", routeParams.projectId, "plugins", "migration", "preview"]);
      const callerSignal = requestSignal(request);
      return pluginJson(await (await selected(source)).execute(request, Object.freeze({ projectId: routeParams.projectId, callerSignal })));
    } catch (error) { return pluginErrorResponse(error); }
  };
}

export function createMigrationApplyRoute(source: Source, options: { readonly bodyReadTimeoutMs?: number } = {}) {
  return async function migrationApplyPOST(request: Request, context: Context): Promise<Response> {
    try {
      const routeParams = await params(context, ["projectId"]);
      assertRouteWithoutQuery(request, "POST", ["api", "v1", "projects", routeParams.projectId, "plugins", "migration", "apply"]);
      const callerSignal = requestSignal(request);
      const body = strictObject(await jsonBody(request, options.bodyReadTimeoutMs), ["confirmationToken"]);
      return pluginJson(await (await selected(source)).execute(request, Object.freeze({ projectId: routeParams.projectId, confirmationToken: body.confirmationToken, callerSignal })));
    } catch (error) { return pluginErrorResponse(error); }
  };
}

async function previewController(): Promise<MigrationController> { return (await import("@/di/plugin-migration-container")).resolveMigrationPreviewController(); }
async function applyController(): Promise<MigrationController> { return (await import("@/di/plugin-migration-container")).resolveMigrationApplyController(); }
export const migrationPreviewGET = createMigrationPreviewRoute(previewController);
export const migrationApplyPOST = createMigrationApplyRoute(applyController);
