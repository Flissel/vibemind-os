import type { IPluginApiAuthorizationPolicy, PluginApiIdentity } from "../../policies/plugin-api-authorization.policy";
import type { IPluginsRepository, PluginInstallation } from "../../repositories/plugins.repository.interface";
import { assertDigest, assertId, assertIdempotencyKey, assertPinnedSnapshot, assertSelectionAdmitted, fingerprint, installReceipt, serviceError } from "./plugin-service.shared";
import { canonicalComponentSelection } from "./plugin-component-selection";

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
    const installation = await this.dependencies.pluginsRepository.getInstallation(request.projectId, request.pluginName);
    if (installation === null) serviceError("installation_not_found");
    if (installation.sourceCommit !== entry.sourceCommit || installation.manifestDigest !== entry.manifestDigest || installation.treeDigest !== entry.treeDigest || installation.policyVersion !== entry.policyVersion) serviceError("catalog_digest_mismatch");
    // The installed selection is what the admission rows record - the durable
    // evidence of the install - so a partial installation is toggled against
    // exactly the components it actually holds.
    const admissions = await this.dependencies.pluginsRepository.listAdmissions(installation.id);
    // The installation exists; what it records does not describe the plugin. That
    // is a conflict with the stored installation, not a missing one.
    if (admissions.length === 0 && entry.components.length > 0) serviceError("installation_conflict");
    assertSelectionAdmitted(entry, canonicalComponentSelection(admissions.map((admission) => admission.componentDigest)));
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
