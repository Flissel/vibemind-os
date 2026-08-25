import { z } from "zod";
import type { IPluginApiAuthorizationPolicy } from "@/src/application/policies/plugin-api-authorization.policy";
import type { ListPluginCatalogUseCase } from "@/src/application/use-cases/plugins/list-plugin-catalog.use-case";
import { captureRecord } from "./plugin-controller.shared";

const Input = z.object({ catalogDigest: z.string().regex(/^[a-f0-9]{64}$/) }).strict();

export class PluginCatalogController {
  constructor(private readonly dependencies: { readonly pluginApiAuthorizationPolicy: IPluginApiAuthorizationPolicy; readonly listPluginCatalogUseCase: ListPluginCatalogUseCase }) {}
  async execute(request: Request, input: unknown) {
    const parsed = Input.safeParse(captureRecord(input, ["catalogDigest"]));
    if (!parsed.success) throw new Error("request_invalid");
    const identity = await this.dependencies.pluginApiAuthorizationPolicy.authenticate(request);
    return this.dependencies.listPluginCatalogUseCase.execute({ identity, catalogDigest: parsed.data.catalogDigest });
  }
}
