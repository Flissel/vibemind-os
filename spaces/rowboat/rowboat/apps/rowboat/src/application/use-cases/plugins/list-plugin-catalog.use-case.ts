import type { IPluginApiAuthorizationPolicy, PluginApiIdentity } from "../../policies/plugin-api-authorization.policy";
import type { IPluginsRepository } from "../../repositories/plugins.repository.interface";
import { assertDigest, assertPinnedSnapshot, componentDtosFrom, freezeOutput, serviceError } from "./plugin-service.shared";

export class ListPluginCatalogUseCase {
  constructor(private readonly dependencies: { readonly pluginsRepository: IPluginsRepository; readonly pluginApiAuthorizationPolicy: IPluginApiAuthorizationPolicy }) {}
  async execute(request: { readonly identity: PluginApiIdentity; readonly catalogDigest: string }) {
    if (request.identity.kind !== "user") serviceError("user_authentication_required");
    assertDigest(request.catalogDigest);
    assertPinnedSnapshot(await this.dependencies.pluginsRepository.getCatalogSnapshot(request.catalogDigest), request.catalogDigest);
    return freezeOutput((await this.dependencies.pluginsRepository.listCatalogEntries(request.catalogDigest)).map((entry) => ({
      name: entry.name, pluginName: entry.pluginName, pluginVersion: entry.pluginVersion, catalogDigest: entry.catalogDigest,
      admission: entry.admission.status, ...(entry.admission.status === "admitted" ? {} : { reason: entry.admission.reason }),
      components: componentDtosFrom(entry),
    })));
  }
}
