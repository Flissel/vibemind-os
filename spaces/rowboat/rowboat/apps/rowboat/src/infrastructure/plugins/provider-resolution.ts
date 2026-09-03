import {
  ConnectorBridgeProvider,
  DEFAULT_POLICY,
  HttpMcpProvider,
  normalizeApp,
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
 * not mount, so that one is reported as unavailable rather than approximated.
 *
 * An app component is constructible too, but only through the connector
 * bridge and only when its declared id starts with `connector_`: the
 * catalog's inline `appDeclaration` metadata (from `metadata.appDeclaration`,
 * mirroring how the mcp branch reads `metadata.mcpServer`) is normalized and
 * handed to `ConnectorBridgeProvider`. An `asdk_app_`/`templated_apps_` id has
 * no public invocation path outside ChatGPT (spec D4/D5) and resolves to
 * unavailable, same as a process MCP server.
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
  readonly responsesModel?: string;
  readonly openAiBaseUrl?: string;
}

const UNAVAILABLE: ProviderResolution = Object.freeze({ status: "unavailable" as const, reason: "provider_unavailable" as const });

/**
 * The deliberate fail-closed default: it resolves nothing, so a call reaches
 * the provider and then fails with a missing credential instead of silently
 * running unauthenticated. OpenFang-backed credential release exists now
 * (`OpenFangCredentialResolver`, `src/infrastructure/plugins/openfang-credential-resolver.ts`)
 * -- `di/plugins-container.ts`'s `resolveProvider` wires it in only when
 * `OPENFANG_URL` and `OPENFANG_API_KEY` are both configured and the URL
 * passes its own security check. Absence or misconfiguration of either is
 * never "resolve anyway": this class stays the default, on purpose, for
 * every other case.
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
  if (request.binding.providerKind === "openai-connector-bridge" && request.component.kind === "app") {
    const declaration = request.component.metadata.appDeclaration;
    if (declaration === undefined || declaration === null || typeof declaration !== "object") return UNAVAILABLE;
    // Same identity check as the mcp branch below: the binding must name this
    // very component, not merely some component of the right kind.
    if (request.binding.componentDigest !== request.component.metadata.bindingDigest) return UNAVAILABLE;
    try {
      const app = normalizeApp(request.component.name, declaration, request.binding.componentDigest);
      // D4: asdk_app_/templated_apps_ ids have no public invocation path
      // outside ChatGPT -- refused here, before ever constructing a provider,
      // in addition to the kernel's own constructor refusing the same thing.
      if (!app.connectorId.startsWith("connector_")) return UNAVAILABLE;
      const provider = new ConnectorBridgeProvider({
        id: request.binding.id,
        binding: request.binding,
        app,
        parentLicense: request.entry.licenseDeclaration,
        policy: dependencies.policy ?? DEFAULT_POLICY,
        credentialResolver: dependencies.credentialResolver,
        ...(dependencies.responsesModel === undefined ? {} : { model: dependencies.responsesModel }),
        ...(dependencies.openAiBaseUrl === undefined ? {} : { baseUrl: dependencies.openAiBaseUrl }),
        ...(dependencies.timeoutMilliseconds === undefined ? {} : { timeoutMilliseconds: dependencies.timeoutMilliseconds }),
      });
      const registry = new ProviderRegistry();
      registry.register(request.binding, provider);
      return registry.resolve(request.binding);
    } catch {
      // A declaration that does not normalize, a non-connector_ id the
      // constructor itself refuses, a binding the registry refuses, or an
      // invalid baseUrl/timeout are all the same answer: nothing runs.
      return UNAVAILABLE;
    }
  }
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
