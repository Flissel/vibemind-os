import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { SSEClientTransport } from "@modelcontextprotocol/sdk/client/sse.js";
import {
  StreamableHTTPClientTransport,
  StreamableHTTPError,
} from "@modelcontextprotocol/sdk/client/streamableHttp.js";
import type { Transport } from "@modelcontextprotocol/sdk/shared/transport.js";

import type { NormalizedHttpMcp } from "../components/mcp-normalizer.js";
import { evaluateComponentAdmission } from "../policy/capability-policy.js";
import type { PluginPolicy } from "../policy/default-policy.js";
import { capturePluginPolicy } from "../policy/policy-snapshot.js";
import {
  assertCredentialRequest,
  revealSecretValue,
  type CredentialReference,
  type CredentialResolver,
  type SecretValue,
} from "./credential-resolver.js";
import { assertBinding } from "./provider-registry.js";
import type {
  PluginProvider,
  ProviderBinding,
  ProviderContext,
  ProviderDescriptor,
  ProviderRequest,
  ProviderResult,
} from "./provider.js";

const PROVIDER_ID = /^[a-z0-9]+(?:[._-][a-z0-9]+)*$/;

export class McpCompatibilityError extends Error {
  constructor(reason: "unsupported_transport") {
    super(reason);
    this.name = "McpCompatibilityError";
  }
}

export interface HttpMcpTransport {
  readonly kind: "streamable-http" | "sse";
  readonly native?: unknown;
}

export interface HttpMcpTransportInput {
  readonly kind: HttpMcpTransport["kind"];
  readonly url: URL;
  readonly credential?: SecretValue;
}

export interface HttpMcpTransportFactory {
  create(input: HttpMcpTransportInput): HttpMcpTransport;
}

export interface HttpMcpClient {
  connect(transport: HttpMcpTransport): Promise<void>;
  callTool(input: {
    readonly name: string;
    readonly arguments: Readonly<Record<string, unknown>>;
  }): Promise<unknown>;
  close(): Promise<void>;
}

export interface HttpMcpClientFactory {
  create(): HttpMcpClient;
}

class SdkHttpMcpClient implements HttpMcpClient {
  #client: Client | undefined;

  async connect(transport: HttpMcpTransport): Promise<void> {
    if (transport.native === undefined) throw new Error("mcp_transport_invalid");
    if (this.#client !== undefined) await this.#client.close();
    const client = new Client({ name: "rowboat-openai-plugin-runtime", version: "1.0.0" });
    try {
      // SDK 1.26.0's concrete transports expose `sessionId: string | undefined`
      // while its Transport interface declares an exact optional property.
      await client.connect(transport.native as Transport);
      this.#client = client;
    } catch (error: unknown) {
      try {
        await client.close();
      } catch {
        // Preserve the classified connection outcome while still cleaning up.
      }
      if (
        transport.kind === "streamable-http"
        && error instanceof StreamableHTTPError
        && (error.code === 404 || error.code === 405)
      ) {
        throw new McpCompatibilityError("unsupported_transport");
      }
      throw error;
    }
  }

  async callTool(input: {
    readonly name: string;
    readonly arguments: Readonly<Record<string, unknown>>;
  }): Promise<unknown> {
    if (this.#client === undefined) throw new Error("mcp_client_not_connected");
    return this.#client.callTool({ name: input.name, arguments: input.arguments });
  }

  async close(): Promise<void> {
    const client = this.#client;
    this.#client = undefined;
    if (client !== undefined) await client.close();
  }
}

class SdkHttpMcpClientFactory implements HttpMcpClientFactory {
  create(): HttpMcpClient {
    return new SdkHttpMcpClient();
  }
}

function authenticatedRequestInit(credential: SecretValue | undefined): RequestInit | undefined {
  if (credential === undefined) return undefined;
  return Object.freeze({
    headers: Object.freeze({ Authorization: `Bearer ${revealSecretValue(credential)}` }),
  });
}

class SdkHttpMcpTransportFactory implements HttpMcpTransportFactory {
  create(input: HttpMcpTransportInput): HttpMcpTransport {
    const requestInit = authenticatedRequestInit(input.credential);
    if (input.kind === "streamable-http") {
      return Object.freeze({
        kind: input.kind,
        native: new StreamableHTTPClientTransport(
          input.url,
          requestInit === undefined ? undefined : { requestInit },
        ),
      });
    }

    const eventSourceInit = input.credential === undefined
      ? undefined
      : {
          fetch: async (url: string | URL, init: RequestInit): Promise<Response> => {
            const headers = new Headers(init.headers);
            headers.set("Authorization", `Bearer ${revealSecretValue(input.credential as SecretValue)}`);
            return fetch(url, { ...init, headers });
          },
        };
    return Object.freeze({
      kind: input.kind,
      native: new SSEClientTransport(input.url, {
        ...(requestInit === undefined ? {} : { requestInit }),
        ...(eventSourceInit === undefined ? {} : { eventSourceInit }),
      }),
    });
  }
}

export interface HttpMcpProviderOptions {
  readonly id: string;
  readonly binding: ProviderBinding;
  readonly server: NormalizedHttpMcp;
  readonly parentLicense: string | undefined;
  readonly policy: PluginPolicy;
  readonly credentialResolver: CredentialResolver;
  readonly clientFactory?: HttpMcpClientFactory;
  readonly transportFactory?: HttpMcpTransportFactory;
}

function validateSecureUrl(raw: string, field: "mcp_url" | "oauth_resource"): URL {
  let url: URL;
  try {
    url = new URL(raw);
  } catch {
    throw new Error(`component_invalid:${field}`);
  }
  if (
    url.protocol !== "https:"
    || url.username !== ""
    || url.password !== ""
    || url.search !== ""
    || url.hash !== ""
  ) {
    throw new Error(`component_invalid:${field}`);
  }
  return url;
}

function admissionReason(
  parentLicense: string | undefined,
  capability: "mcp_http" | "read" | "write",
  policy: PluginPolicy,
): string | undefined {
  const decision = evaluateComponentAdmission(parentLicense, { kind: capability }, policy);
  return decision.status === "admitted" ? undefined : decision.reason;
}

export class HttpMcpProvider implements PluginProvider {
  readonly id: string;
  readonly #descriptor: ProviderDescriptor;
  readonly #server: NormalizedHttpMcp;
  readonly #url: URL;
  readonly #parentLicense: string | undefined;
  readonly #policy: PluginPolicy;
  readonly #credentialResolver: CredentialResolver;
  readonly #clientFactory: HttpMcpClientFactory;
  readonly #transportFactory: HttpMcpTransportFactory;

  constructor(options: HttpMcpProviderOptions) {
    assertBinding(options.binding);
    if (
      !PROVIDER_ID.test(options.id)
      || options.binding.providerKind !== "mcp-http"
      || options.binding.componentDigest !== options.server.componentDigest
      || options.server.kind !== "mcp-http"
    ) {
      throw new Error("provider_invalid:descriptor_mismatch");
    }
    if (
      options.server.bearerTokenReference !== undefined
      && options.server.oauthResource !== undefined
    ) {
      throw new Error("credential_invalid:ambiguous");
    }

    this.id = options.id;
    this.#descriptor = Object.freeze({ id: options.id, kind: "mcp-http", temporaryAdapter: false });
    this.#server = Object.freeze({ ...options.server });
    this.#url = validateSecureUrl(options.server.url, "mcp_url");
    if (options.server.oauthResource !== undefined) {
      validateSecureUrl(options.server.oauthResource, "oauth_resource");
    }
    this.#parentLicense = options.parentLicense;
    this.#policy = capturePluginPolicy(options.policy);
    this.#credentialResolver = options.credentialResolver;
    this.#clientFactory = options.clientFactory ?? new SdkHttpMcpClientFactory();
    this.#transportFactory = options.transportFactory ?? new SdkHttpMcpTransportFactory();
    Object.freeze(this);
  }

  describe(): ProviderDescriptor {
    return this.#descriptor;
  }

  async #resolveCredential(projectId: string): Promise<SecretValue | undefined> {
    let reference: CredentialReference | undefined;
    if (this.#server.bearerTokenReference !== undefined) {
      reference = Object.freeze({ kind: "bearer", reference: this.#server.bearerTokenReference });
    } else if (this.#server.oauthResource !== undefined) {
      reference = Object.freeze({ kind: "oauth", reference: this.#server.oauthResource });
    }
    if (reference === undefined) return undefined;
    assertCredentialRequest(reference, projectId);
    try {
      return await this.#credentialResolver.resolve(reference, projectId);
    } catch {
      throw new Error("credential_missing");
    }
  }

  async invoke(request: ProviderRequest, _context: ProviderContext): Promise<ProviderResult> {
    const providerDenial = admissionReason(this.#parentLicense, "mcp_http", this.#policy);
    if (providerDenial !== undefined) throw new Error(providerDenial);
    const capabilityDenial = admissionReason(this.#parentLicense, request.capability, this.#policy);
    if (capabilityDenial !== undefined) throw new Error(capabilityDenial);

    const credential = await this.#resolveCredential(request.projectId);
    let client: HttpMcpClient;
    try {
      client = this.#clientFactory.create();
    } catch {
      return Object.freeze({ status: "failed", reason: "mcp_http_failed" });
    }
    let result: ProviderResult;
    try {
      const streamable = this.#transportFactory.create({
        kind: "streamable-http",
        url: this.#url,
        ...(credential === undefined ? {} : { credential }),
      });
      try {
        await client.connect(streamable);
      } catch (error: unknown) {
        if (!(error instanceof McpCompatibilityError)) throw error;
        const sse = this.#transportFactory.create({
          kind: "sse",
          url: this.#url,
          ...(credential === undefined ? {} : { credential }),
        });
        await client.connect(sse);
      }
      const output = await client.callTool({
        name: request.componentName,
        arguments: request.arguments,
      });
      result = Object.freeze({ status: "success", output });
    } catch {
      result = Object.freeze({ status: "failed", reason: "mcp_http_failed" });
    } finally {
      try {
        await client.close();
      } catch {
        result = Object.freeze({ status: "failed", reason: "mcp_http_failed" });
      }
    }
    return result;
  }
}
