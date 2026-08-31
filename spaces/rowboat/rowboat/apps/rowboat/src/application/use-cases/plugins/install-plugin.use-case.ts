import type { IPluginApiAuthorizationPolicy, PluginApiIdentity } from "../../policies/plugin-api-authorization.policy";
import type { IPluginsRepository, PluginReceipt } from "../../repositories/plugins.repository.interface";
import { admissionsFrom, assertAdmitted, assertDigest, assertId, assertIdempotencyKey, assertPinnedSnapshot, fingerprint, installReceipt, installationFrom, serviceError, slotsFrom } from "./plugin-service.shared";

export interface InstallPluginRequest {
  readonly identity: PluginApiIdentity;
  readonly projectId: string;
  readonly pluginName: string;
  readonly catalogDigest: string;
  readonly idempotencyKey: string;
  readonly expectedRevision: number;
}

export class InstallPluginUseCase {
  constructor(private readonly dependencies: {
    readonly pluginsRepository: IPluginsRepository;
    readonly pluginApiAuthorizationPolicy: IPluginApiAuthorizationPolicy;
  }) {}

  async execute(request: InstallPluginRequest): Promise<PluginReceipt> {
    assertId(request.projectId, "project_id_invalid");
    assertId(request.pluginName, "plugin_name_invalid");
    assertDigest(request.catalogDigest);
    assertIdempotencyKey(request.idempotencyKey);
    if (!Number.isSafeInteger(request.expectedRevision) || request.expectedRevision < 0) serviceError("installation_revision_invalid");
    await this.dependencies.pluginApiAuthorizationPolicy.authorizeProject(request.identity, request.projectId);
    const idempotencyScope = fingerprint({ projectId: request.projectId, operation: "install", idempotencyKey: request.idempotencyKey });
    const payloadFingerprint = fingerprint({
      projectId: request.projectId, pluginName: request.pluginName, catalogDigest: request.catalogDigest,
      expectedRevision: request.expectedRevision,
    });
    const catalog = await this.dependencies.pluginsRepository.getCatalog(request.catalogDigest);
    assertPinnedSnapshot(catalog, request.catalogDigest);
    const selected = catalog.entries.find((candidate) => candidate.name === request.pluginName && candidate.pluginName === request.pluginName);
    const entry = selected === undefined ? undefined : { ...selected, catalogDigest: request.catalogDigest };
    if (entry === undefined || entry.catalogDigest !== request.catalogDigest) serviceError("plugin_not_found");
    assertAdmitted(entry);
    const replay = await this.dependencies.pluginsRepository.getIdempotentReceipt({
      scope: idempotencyScope, fingerprint: payloadFingerprint, projectId: request.projectId,
      pluginName: request.pluginName, catalogDigest: request.catalogDigest, operation: "install",
    });
    if (replay !== null) return replay;
    const existing = await this.dependencies.pluginsRepository.getInstallation(request.projectId, request.pluginName);
    if (existing === null && request.expectedRevision !== 0) serviceError("installation_conflict");
    if (existing !== null && (
      existing.revision !== request.expectedRevision || existing.sourceCommit !== entry.sourceCommit
      || existing.manifestDigest !== entry.manifestDigest || existing.treeDigest !== entry.treeDigest
      || existing.policyVersion !== entry.policyVersion
    )) serviceError("installation_conflict");
    const installation = installationFrom(entry, request.projectId);
    const receipt = installReceipt(request.projectId, request.pluginName);
    const result = await this.dependencies.pluginsRepository.installIdempotently({
      scope: idempotencyScope,
      fingerprint: payloadFingerprint,
      catalogDigest: request.catalogDigest,
      installation,
      admissions: admissionsFrom(entry, installation.id),
      credentialSlots: slotsFrom(entry, installation.id, request.projectId),
      receipt,
    });
    return result.receipt;
  }
}
