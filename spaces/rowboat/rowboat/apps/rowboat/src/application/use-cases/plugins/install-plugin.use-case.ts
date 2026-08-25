import type { IPluginApiAuthorizationPolicy, PluginApiIdentity } from "../../policies/plugin-api-authorization.policy";
import type { IPluginsRepository, PluginReceipt } from "../../repositories/plugins.repository.interface";
import { actor, admissionsFrom, assertAdmitted, assertDigest, assertId, assertIdempotencyKey, assertPinnedSnapshot, fingerprint, installReceipt, installationFrom, serviceError, slotsFrom } from "./plugin-service.shared";

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
    const idempotencyScope = fingerprint({ projectId: request.projectId, operation: "install", actor: actor(request.identity), idempotencyKey: request.idempotencyKey });
    const payloadFingerprint = fingerprint({
      projectId: request.projectId, pluginName: request.pluginName, catalogDigest: request.catalogDigest,
      expectedRevision: request.expectedRevision,
    });
    const replay = await this.dependencies.pluginsRepository.getIdempotentReceipt(idempotencyScope, payloadFingerprint);
    if (replay !== null) return replay;
    const snapshot = await this.dependencies.pluginsRepository.getCatalogSnapshot(request.catalogDigest);
    assertPinnedSnapshot(snapshot, request.catalogDigest);
    const entries = await this.dependencies.pluginsRepository.listCatalogEntries(request.catalogDigest);
    const entry = entries.find((candidate) => candidate.name === request.pluginName && candidate.pluginName === request.pluginName);
    if (entry === undefined || entry.catalogDigest !== request.catalogDigest) serviceError("plugin_not_found");
    assertAdmitted(entry);
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
      installation,
      admissions: admissionsFrom(entry, installation.id),
      credentialSlots: slotsFrom(entry, installation.id, request.projectId),
      receipt,
    });
    return result.receipt;
  }
}
