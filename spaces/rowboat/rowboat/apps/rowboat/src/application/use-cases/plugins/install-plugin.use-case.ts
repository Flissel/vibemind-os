import type { IPluginApiAuthorizationPolicy, PluginApiIdentity } from "../../policies/plugin-api-authorization.policy";
import type { IPluginsRepository, PluginReceipt } from "../../repositories/plugins.repository.interface";
import { admissionsFrom, assertDigest, assertId, assertIdempotencyKey, assertPinnedSnapshot, assertSelectionAdmitted, entryComponentDigests, fingerprint, installReceipt, installationFrom, serviceError, slotsFrom } from "./plugin-service.shared";
import { componentSelectionDigest, requestedComponentSelection } from "./plugin-component-selection";

export interface InstallPluginRequest {
  readonly identity: PluginApiIdentity;
  readonly projectId: string;
  readonly pluginName: string;
  readonly catalogDigest: string;
  readonly idempotencyKey: string;
  readonly expectedRevision: number;
  /** The components to install. Absent means every component of the plugin. */
  readonly componentDigests?: readonly string[];
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
    const requested = request.componentDigests === undefined ? undefined : requestedComponentSelection(request.componentDigests);
    await this.dependencies.pluginApiAuthorizationPolicy.authorizeProject(request.identity, request.projectId);
    const idempotencyScope = fingerprint({ projectId: request.projectId, operation: "install", idempotencyKey: request.idempotencyKey });
    // The selection is bound to the key by the digest the signed preview
    // envelope carries, so one key can never be replayed with another
    // selection - and the replay lookup can recompute it from that envelope
    // alone, without resolving the catalog a second time.
    const payloadFingerprint = fingerprint({
      projectId: request.projectId, pluginName: request.pluginName, catalogDigest: request.catalogDigest,
      expectedRevision: request.expectedRevision, componentSelectionDigest: componentSelectionDigest(requested),
    });
    const catalog = await this.dependencies.pluginsRepository.getCatalog(request.catalogDigest);
    assertPinnedSnapshot(catalog, request.catalogDigest);
    const selected = catalog.entries.find((candidate) => candidate.name === request.pluginName && candidate.pluginName === request.pluginName);
    const entry = selected === undefined ? undefined : { ...selected, catalogDigest: request.catalogDigest };
    if (entry === undefined || entry.catalogDigest !== request.catalogDigest) serviceError("plugin_not_found");
    const componentDigests = requested ?? entryComponentDigests(entry);
    assertSelectionAdmitted(entry, componentDigests);
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
    const installation = installationFrom(entry, request.projectId, componentDigests);
    const receipt = installReceipt(request.projectId, request.pluginName);
    const result = await this.dependencies.pluginsRepository.installIdempotently({
      scope: idempotencyScope,
      fingerprint: payloadFingerprint,
      catalogDigest: request.catalogDigest,
      installation,
      admissions: admissionsFrom(entry, installation.id, componentDigests),
      credentialSlots: slotsFrom(entry, installation.id, request.projectId),
      receipt,
    });
    return result.receipt;
  }
}
