import type { IPluginApiAuthorizationPolicy, PluginApiIdentity } from "../../policies/plugin-api-authorization.policy";
import type { IPluginsRepository, PluginInstallation } from "../../repositories/plugins.repository.interface";
import { assertAdmitted, assertDigest, assertId, assertIdempotencyKey, assertPinnedSnapshot, fingerprint, installReceipt, serviceError } from "./plugin-service.shared";

export class SetPluginEnabledUseCase {
  constructor(private readonly dependencies: { readonly pluginsRepository: IPluginsRepository; readonly pluginApiAuthorizationPolicy: IPluginApiAuthorizationPolicy }) {}
  async execute(request: { readonly identity: PluginApiIdentity; readonly projectId: string; readonly pluginName: string; readonly catalogDigest: string; readonly enabled: boolean; readonly expectedRevision: number; readonly idempotencyKey: string }): Promise<PluginInstallation> {
    assertId(request.projectId, "project_id_invalid"); assertId(request.pluginName, "plugin_name_invalid"); assertDigest(request.catalogDigest); assertIdempotencyKey(request.idempotencyKey);
    if (typeof request.enabled !== "boolean" || !Number.isSafeInteger(request.expectedRevision) || request.expectedRevision < 0) serviceError("installation_update_invalid");
    await this.dependencies.pluginApiAuthorizationPolicy.authorizeProject(request.identity, request.projectId);
    const catalog = await this.dependencies.pluginsRepository.getCatalog(request.catalogDigest);
    assertPinnedSnapshot(catalog, request.catalogDigest);
    const selected = catalog.entries.find((candidate) => candidate.name === request.pluginName);
    const entry = selected === undefined ? undefined : { ...selected, catalogDigest: request.catalogDigest };
    if (entry === undefined) serviceError("plugin_not_found");
    assertAdmitted(entry);
    const installation = await this.dependencies.pluginsRepository.getInstallation(request.projectId, request.pluginName);
    if (installation === null) serviceError("installation_not_found");
    if (installation.sourceCommit !== entry.sourceCommit || installation.manifestDigest !== entry.manifestDigest || installation.treeDigest !== entry.treeDigest || installation.policyVersion !== entry.policyVersion) serviceError("catalog_digest_mismatch");
    const result = await this.dependencies.pluginsRepository.setInstallationEnabledIdempotently({
      scope: fingerprint({ projectId: request.projectId, operation: "set_enabled", idempotencyKey: request.idempotencyKey }),
      fingerprint: fingerprint({ projectId: request.projectId, pluginName: request.pluginName, catalogDigest: request.catalogDigest, enabled: request.enabled, expectedRevision: request.expectedRevision }),
      projectId: request.projectId, pluginName: request.pluginName, catalogDigest: request.catalogDigest,
      installationId: installation.id, enabled: request.enabled, expectedRevision: request.expectedRevision,
      receipt: installReceipt(request.projectId, request.pluginName),
    });
    return result.installation;
  }
}
