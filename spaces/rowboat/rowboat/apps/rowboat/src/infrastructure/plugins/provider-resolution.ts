import {
  DEFAULT_POLICY,
  HttpMcpProvider,
  normalizeMcpServer,
  ProviderRegistry,
  SecretValue,
  type CredentialReference,
  type CredentialResolver,
  type PluginPolicy,
  type ProviderBinding,
  type ProviderResolution,
} from "@rowboat/openai-plugin-runtime";

/**
 * Resolves the provider that executes a plugin component.
 *
 * Only HTTP MCP servers are constructible from the pinned catalog alone: their
 * declaration travels with the catalog record. A process MCP server needs a
 * verified execution root from the content store, which the web runtime does
 * not mount, and an app component is refused by the registry itself - there is
 * no connector bridge implementation. Both are reported as unavailable rather
 * than approximated.
 *
 * Every resolution goes through a fresh registry, so a provider is only ever
 * reachable through a binding the registry itself admitted.
 */
export interface PluginProviderResolutionRequest {
  readonly component: Readonly<{ id: string; name: string; kind: string; metadata: Readonly<Record<string, unknown>> }>;
  readonly entry: Readonly<{ licenseDeclaration?: string }>;
  readonly binding: ProviderBinding;
}

export interface PluginProviderResolutionDependencies {
  readonly credentialResolver: CredentialResolver;
  readonly policy?: PluginPolicy;
  readonly timeoutMilliseconds?: number;
}

const UNAVAILABLE: ProviderResolution = Object.freeze({ status: "unavailable" as const, reason: "provider_unavailable" as const });

/**
 * Stands in until credentials are released from OpenFang. It resolves nothing,
 * so a call reaches the provider and then fails with a missing credential
 * instead of silently running unauthenticated.
 */
export class UnreleasedCredentialResolver implements CredentialResolver {
  async resolve(reference: CredentialReference, _projectId: string): Promise<SecretValue> {
    void reference;
    throw new Error("credential_missing");
  }
}

export function resolvePluginProvider(
  request: PluginProviderResolutionRequest,
  dependencies: PluginProviderResolutionDependencies,
): ProviderResolution {
  if (request.binding.providerKind !== "mcp-http") return UNAVAILABLE;
  if (request.component.kind !== "mcp") return UNAVAILABLE;
  // The binding must name this very component: the digest is the identity the
  // catalog pinned, and a binding for anything else resolves to nothing.
  if (request.binding.componentDigest !== request.component.metadata.bindingDigest) return UNAVAILABLE;
  const declaration = request.component.metadata.mcpServer;
  if (declaration === undefined || declaration === null || typeof declaration !== "object") return UNAVAILABLE;
  try {
    const server = normalizeMcpServer(request.component.name, declaration, request.binding.componentDigest);
    if (server.kind !== "mcp-http") return UNAVAILABLE;
    const provider = new HttpMcpProvider({
      id: request.binding.id,
      binding: request.binding,
      server,
      parentLicense: request.entry.licenseDeclaration,
      policy: dependencies.policy ?? DEFAULT_POLICY,
      credentialResolver: dependencies.credentialResolver,
      ...(dependencies.timeoutMilliseconds === undefined ? {} : { timeoutMilliseconds: dependencies.timeoutMilliseconds }),
    });
    const registry = new ProviderRegistry();
    registry.register(request.binding, provider);
    return registry.resolve(request.binding);
  } catch {
    // A declaration that does not normalize, a binding the registry refuses, or
    // an insecure URL are all the same answer: nothing runs.
    return UNAVAILABLE;
  }
}
