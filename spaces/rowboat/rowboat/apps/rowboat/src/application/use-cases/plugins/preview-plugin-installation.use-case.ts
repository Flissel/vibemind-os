import type { IPluginApiAuthorizationPolicy, PluginApiIdentity } from "../../policies/plugin-api-authorization.policy";
import type { IPluginsRepository } from "../../repositories/plugins.repository.interface";
import { assertDigest, assertId, assertPinnedSnapshot, freezeOutput, requiredCredentialNames, serviceError } from "./plugin-service.shared";

export interface PreviewPluginInstallationRequest {
  readonly identity: PluginApiIdentity;
  readonly projectId: string;
  readonly pluginName: string;
  readonly catalogDigest: string;
}

export class PreviewPluginInstallationUseCase {
  constructor(private readonly dependencies: { readonly pluginsRepository: IPluginsRepository; readonly pluginApiAuthorizationPolicy: IPluginApiAuthorizationPolicy }) {}

  async execute(request: PreviewPluginInstallationRequest) {
    assertId(request.projectId, "project_id_invalid"); assertId(request.pluginName, "plugin_name_invalid"); assertDigest(request.catalogDigest);
    await this.dependencies.pluginApiAuthorizationPolicy.authorizeProject(request.identity, request.projectId);
    const snapshot = await this.dependencies.pluginsRepository.getCatalogSnapshot(request.catalogDigest);
    assertPinnedSnapshot(snapshot, request.catalogDigest);
    const entry = (await this.dependencies.pluginsRepository.listCatalogEntries(request.catalogDigest)).find((candidate) => candidate.name === request.pluginName);
    if (entry === undefined) serviceError("plugin_not_found");
    const installation = await this.dependencies.pluginsRepository.getInstallation(request.projectId, request.pluginName);
    const configured = new Set(installation === null ? [] : (await this.dependencies.pluginsRepository.listCredentialSlots(installation.id)).map((slot) => slot.name));
    return freezeOutput({
      pluginName: entry.pluginName,
      catalogDigest: request.catalogDigest,
      admission: entry.admission.status,
      ...(entry.admission.status === "admitted" ? {} : { reason: entry.admission.reason }),
      components: entry.components.map(({ component, admission }) => ({
        id: component.id, name: component.name, kind: component.kind, status: component.status,
        admission: admission.status, ...(component.reason === undefined ? {} : { reason: component.reason }),
      })),
      credentialSlots: requiredCredentialNames(entry).map((name) => ({ name, configured: configured.has(name) })),
    });
  }
}
