import type { IPluginApiAuthorizationPolicy, PluginApiIdentity } from "../../policies/plugin-api-authorization.policy";
import type { IPluginsRepository } from "../../repositories/plugins.repository.interface";
import { assertDigest, assertPinnedSnapshot, componentDtosFrom, freezeOutput, serviceError } from "./plugin-service.shared";

export class ListPluginCatalogUseCase {
  constructor(private readonly dependencies: { readonly pluginsRepository: IPluginsRepository; readonly pluginApiAuthorizationPolicy: IPluginApiAuthorizationPolicy }) {}
  async execute(request: { readonly identity: PluginApiIdentity; readonly catalogDigest: string }) {
    if (request.identity.kind !== "user") serviceError("user_authentication_required");
    assertDigest(request.catalogDigest);
    const catalog = await this.dependencies.pluginsRepository.getCatalog(request.catalogDigest);
    assertPinnedSnapshot(catalog, request.catalogDigest);
    return freezeOutput(catalog.entries.map((catalogEntry) => {
      const entry = { ...catalogEntry, catalogDigest: request.catalogDigest };
      return {
        name: entry.name, pluginName: entry.pluginName, pluginVersion: entry.pluginVersion, catalogDigest: request.catalogDigest,
        admission: entry.admission.status, ...(entry.admission.status === "admitted" ? {} : { reason: entry.admission.reason }),
        components: componentDtosFrom(entry),
      };
    }));
  }
}
