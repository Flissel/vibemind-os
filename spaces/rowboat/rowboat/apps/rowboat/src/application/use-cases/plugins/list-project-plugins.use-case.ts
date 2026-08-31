import type { IPluginApiAuthorizationPolicy, PluginApiIdentity } from "../../policies/plugin-api-authorization.policy";
import type { IPluginsRepository } from "../../repositories/plugins.repository.interface";
import {
  assertDigest,
  assertId,
  assertPinnedSnapshot,
  componentDtosFrom,
  freezeOutput,
  serviceError,
} from "./plugin-service.shared";

export class ListProjectPluginsUseCase {
  constructor(private readonly dependencies: {
    readonly pluginsRepository: IPluginsRepository;
    readonly pluginApiAuthorizationPolicy: IPluginApiAuthorizationPolicy;
  }) {}

  async execute(request: {
    readonly identity: PluginApiIdentity;
    readonly projectId: string;
    readonly catalogDigest: string;
  }) {
    assertId(request.projectId, "project_id_invalid");
    assertDigest(request.catalogDigest);
    await this.dependencies.pluginApiAuthorizationPolicy.authorizeProject(request.identity, request.projectId);

    const catalog = await this.dependencies.pluginsRepository.getCatalog(request.catalogDigest);
    assertPinnedSnapshot(catalog, request.catalogDigest);
    const entries = new Map(catalog.entries.map((entry) => [entry.pluginName, entry]));
    const installations = await this.dependencies.pluginsRepository.listInstallations(request.projectId);
    if (installations.length > 512) serviceError("plugin_record_too_large");

    const names = new Set<string>();
    const output = installations.map((installation) => {
      if (installation.projectId !== request.projectId) serviceError("plugin_record_invalid");
      if (names.has(installation.pluginName)) serviceError("plugin_record_invalid");
      names.add(installation.pluginName);
      const entry = entries.get(installation.pluginName);
      if (
        entry === undefined
        || entry.pluginVersion !== installation.pluginVersion
        || entry.sourceCommit !== installation.sourceCommit
        || entry.manifestDigest !== installation.manifestDigest
        || entry.treeDigest !== installation.treeDigest
        || entry.policyVersion !== installation.policyVersion
      ) serviceError("catalog_digest_mismatch");
      return {
        pluginName: entry.pluginName,
        pluginVersion: entry.pluginVersion,
        catalogDigest: request.catalogDigest,
        policyVersion: entry.policyVersion,
        license: {
          declaration: entry.licenseDeclaration ?? serviceError("catalog_entry_invalid"),
          decision: entry.admission.status,
          ...(entry.admission.status === "admitted" ? {} : { reason: entry.admission.reason }),
        },
        admission: entry.admission.status,
        ...(entry.admission.status === "admitted" ? {} : { reason: entry.admission.reason }),
        components: componentDtosFrom({ ...entry, catalogDigest: request.catalogDigest }),
        enabled: installation.enabled,
        revision: installation.revision,
      };
    });
    output.sort((left, right) => left.pluginName < right.pluginName ? -1 : left.pluginName > right.pluginName ? 1 : 0);
    return freezeOutput(output);
  }
}
