import type { PluginApiIdentity, IPluginApiAuthorizationPolicy } from "@/src/application/policies/plugin-api-authorization.policy";
import type { PreviewPluginMigrationUseCase } from "@/src/application/use-cases/plugins/preview-plugin-migration.use-case";
import { captureCallerSignal, captureRecord } from "./plugin-controller.shared";

export class PreviewPluginMigrationController {
  constructor(private readonly dependencies: { readonly authorization: IPluginApiAuthorizationPolicy; readonly useCase: PreviewPluginMigrationUseCase }) {}
  async execute(request: Request, input: unknown): Promise<unknown> {
    const selected = captureRecord(input, ["projectId", "callerSignal"]);
    if (typeof selected.projectId !== "string") throw new Error("project_id_invalid");
    const callerSignal = captureCallerSignal(selected.callerSignal);
    const actor: PluginApiIdentity = await this.dependencies.authorization.authenticate(request);
    return this.dependencies.useCase.execute({ actor, scope: "project", projectId: selected.projectId, callerSignal });
  }
}
