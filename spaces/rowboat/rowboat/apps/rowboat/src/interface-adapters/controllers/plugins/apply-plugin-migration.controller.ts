import type { PluginApiIdentity, IPluginApiAuthorizationPolicy } from "@/src/application/policies/plugin-api-authorization.policy";
import type { ApplyPluginMigrationUseCase } from "@/src/application/use-cases/plugins/apply-plugin-migration.use-case";
import { captureRecord } from "./plugin-controller.shared";

export class ApplyPluginMigrationController {
  constructor(private readonly dependencies: { readonly authorization: IPluginApiAuthorizationPolicy; readonly useCase: ApplyPluginMigrationUseCase }) {}
  async execute(request: Request, input: unknown): Promise<unknown> {
    const selected = captureRecord(input, ["projectId", "confirmationToken"]);
    if (typeof selected.projectId !== "string" || typeof selected.confirmationToken !== "string") throw new Error("migration_request_invalid");
    const actor: PluginApiIdentity = await this.dependencies.authorization.authenticate(request);
    return this.dependencies.useCase.execute({ actor, projectId: selected.projectId, confirmationToken: selected.confirmationToken });
  }
}
