import type { PluginApiIdentity } from "../../policies/plugin-api-authorization.policy";
import type { PluginCatalogEntry, PluginInstallation } from "../../repositories/plugins.repository.interface";
import { serviceError } from "./plugin-service.shared";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const IDENTIFIER = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/;
const DIGEST = /^[a-f0-9]{64}$/;
const MAX_TOOLS = 512;

export interface AddPluginToolRequest {
  readonly identity: PluginApiIdentity;
  readonly projectId: string;
  readonly pluginName: string;
  readonly componentDigest: string;
}

export interface AddPluginToolResult {
  readonly projectId: string;
  readonly toolName: string;
  readonly pluginName: string;
  readonly componentDigest: string;
  /** False when the tool was already present, so the action is idempotent. */
  readonly added: boolean;
}

export interface AddPluginToolDependencies {
  readonly authorizeProject: (identity: PluginApiIdentity, projectId: string) => Promise<void>;
  readonly loadInstallation: (projectId: string, pluginName: string) => Promise<PluginInstallation | null>;
  readonly loadCatalogEntry: (pluginName: string) => Promise<PluginCatalogEntry | null>;
  readonly loadDraftWorkflow: (projectId: string) => Promise<unknown>;
  readonly saveDraftWorkflow: (projectId: string, workflow: unknown) => Promise<void>;
}

/**
 * Derives the workflow tool name. The agent runtime addresses a tool by name,
 * so it is namespaced by plugin and reduced to the characters a tool name may
 * carry; a component whose name reduces to nothing falls back to its digest.
 */
export function pluginToolName(pluginName: string, componentName: string, componentDigest: string): string {
  const reduce = (value: string) => value.replace(/[^A-Za-z0-9]+/gu, "_").replace(/^_+|_+$/gu, "").toLowerCase();
  const plugin = reduce(pluginName);
  const component = reduce(componentName);
  const suffix = component.length === 0 ? componentDigest.slice(0, 12) : component;
  return `plugin_${plugin}_${suffix}`.slice(0, 96);
}

function requestInvalid(): never {
  serviceError("request_invalid");
}

/**
 * Adds an installed plugin component to a project workflow as a tool.
 *
 * The tool carries a native binding: it has no legacy tool it could displace,
 * so it is not subject to the migration runtime mode gate. Only a component
 * that the catalog admits, that the installation actually binds, and whose
 * plugin is enabled can be added.
 */
export class AddPluginToolUseCase {
  constructor(private readonly dependencies: AddPluginToolDependencies) {}

  async execute(request: AddPluginToolRequest): Promise<AddPluginToolResult> {
    if (request === null || typeof request !== "object") requestInvalid();
    if (typeof request.projectId !== "string" || !UUID.test(request.projectId)) requestInvalid();
    if (typeof request.pluginName !== "string" || !IDENTIFIER.test(request.pluginName)) requestInvalid();
    if (typeof request.componentDigest !== "string" || !DIGEST.test(request.componentDigest)) requestInvalid();
    await this.dependencies.authorizeProject(request.identity, request.projectId);

    const installation = await this.dependencies.loadInstallation(request.projectId, request.pluginName);
    if (installation === null || !installation.enabled) serviceError("installation_not_found");
    // Referenced by the pinned component digest, which is what the catalog API
    // exposes and what the resolver compares against.
    const bound = installation.providerBindings?.find(candidate => candidate.binding.componentDigest === request.componentDigest);
    if (bound === undefined) serviceError("provider_unavailable");

    const entry = await this.dependencies.loadCatalogEntry(request.pluginName);
    const admitted = entry?.components.find(candidate => candidate.component.metadata.bindingDigest === request.componentDigest);
    if (entry === null || entry === undefined || admitted === undefined) serviceError("plugin_not_found");
    // A component the catalog only admits for review is not installable as a
    // tool: the review is the decision, and it has not been made.
    if (admitted.admission.status !== "admitted") serviceError("component_not_admitted");
    if (admitted.component.status !== "available") serviceError(admitted.component.reason ?? "provider_unavailable");
    if (bound.binding.componentDigest !== admitted.component.metadata.bindingDigest) serviceError("digest_mismatch");
    if (bound.componentId !== admitted.component.id) serviceError("digest_mismatch");

    const workflow = await this.dependencies.loadDraftWorkflow(request.projectId);
    if (workflow === null || typeof workflow !== "object" || Array.isArray(workflow)) serviceError("request_invalid");
    const source = workflow as Record<string, unknown>;
    const tools = source.tools;
    if (!Array.isArray(tools) || tools.length >= MAX_TOOLS) serviceError("request_invalid");

    const toolName = pluginToolName(request.pluginName, admitted.component.name, bound.binding.componentDigest);
    const existing = tools.find(candidate => candidate !== null && typeof candidate === "object" && (candidate as { name?: unknown }).name === toolName);
    if (existing !== undefined) {
      const binding = (existing as { pluginBinding?: { installationId?: unknown; componentDigest?: unknown } }).pluginBinding;
      // Same tool, same binding: nothing to do. A different tool under that
      // name is a conflict rather than something to overwrite.
      if (binding?.installationId !== installation.id || binding.componentDigest !== bound.binding.componentDigest) serviceError("installation_conflict");
      return Object.freeze({ projectId: request.projectId, toolName, pluginName: request.pluginName, componentDigest: request.componentDigest, added: false });
    }

    const tool = Object.freeze({
      name: toolName,
      description: `${request.pluginName}: ${admitted.component.name}`,
      parameters: Object.freeze({ type: "object", properties: Object.freeze({}), required: Object.freeze([]) }),
      pluginBinding: Object.freeze({
        installationId: installation.id,
        pluginName: installation.pluginName,
        componentDigest: bound.binding.componentDigest,
        providerBindingId: bound.binding.id,
        // No read/write classification exists on a provider binding, and an
        // unknown effect is never treated as read-only.
        capability: "write" as const,
        origin: "native" as const,
      }),
    });
    await this.dependencies.saveDraftWorkflow(request.projectId, { ...source, tools: [...tools, tool] });
    return Object.freeze({ projectId: request.projectId, toolName, pluginName: request.pluginName, componentDigest: request.componentDigest, added: true });
  }
}
