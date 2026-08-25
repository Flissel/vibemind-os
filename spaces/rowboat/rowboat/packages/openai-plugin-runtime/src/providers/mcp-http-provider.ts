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
import {
  captureMcpOperationBindings,
  validateMcpInvocation,
  type McpOperationBinding,
} from "./mcp-request.js";
import { captureProviderInvocation } from "./provider-invocation.js";
import type {
  PluginProvider,
  ProviderBinding,
  ProviderContext,
  ProviderDescriptor,
  ProviderRequest,
  ProviderResult,
} from "./provider.js";

const PROVIDER_ID = /^[a-z0-9]+(?:[._-][a-z0-9]+)*$/;
const DEFAULT_HTTP_TIMEOUT_MS = 30_000;
const MAX_HTTP_TIMEOUT_MS = 300_000;
const HTTP_CLEANUP_TIMEOUT_MS = 25;

class HttpInvocationTimeoutError extends Error {
  constructor() {
    super("mcp_http_timed_out");
    this.name = "HttpInvocationTimeoutError";
  }
}

function awaitWithSignal<T>(promise: Promise<T>, signal: AbortSignal): Promise<T> {
  if (signal.aborted) return Promise.reject(new HttpInvocationTimeoutError());
  return new Promise<T>((resolve, reject) => {
    const onAbort = (): void => reject(new HttpInvocationTimeoutError());
    signal.addEventListener("abort", onAbort, { once: true });
    promise.then(
      (value) => {
        signal.removeEventListener("abort", onAbort);
        if (signal.aborted) reject(new HttpInvocationTimeoutError());
        else resolve(value);
      },
      (error: unknown) => {
        signal.removeEventListener("abort", onAbort);
        reject(error);
      },
    );
  });
}

async function boundedCleanup(
  operation: (signal: AbortSignal) => Promise<void>,
): Promise<boolean> {
  const controller = new AbortController();
  let timer: NodeJS.Timeout | undefined;
  const timeout = new Promise<false>((resolve) => {
    timer = setTimeout(() => {
      controller.abort();
      resolve(false);
    }, HTTP_CLEANUP_TIMEOUT_MS);
  });
  try {
    const cleanup = Promise.resolve().then(() => operation(controller.signal));
    return await Promise.race([cleanup.then(() => true, () => false), timeout]);
  } finally {
    if (timer !== undefined) clearTimeout(timer);
  }
}

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
  readonly signal: AbortSignal;
}

export interface HttpMcpTransportFactory {
  create(input: HttpMcpTransportInput): HttpMcpTransport;
}

export interface HttpMcpClient {
  connect(transport: HttpMcpTransport, options?: Readonly<{ readonly signal: AbortSignal }>): Promise<void>;
  callTool(input: {
    readonly name: string;
    readonly arguments: Readonly<Record<string, unknown>>;
  }, options?: Readonly<{ readonly signal: AbortSignal }>): Promise<unknown>;
  close(options?: Readonly<{ readonly signal: AbortSignal }>): Promise<void>;
}

export interface HttpMcpClientFactory {
  create(options?: Readonly<{ readonly signal: AbortSignal }>): HttpMcpClient;
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
  }, options?: Readonly<{ readonly signal: AbortSignal }>): Promise<unknown> {
    if (this.#client === undefined) throw new Error("mcp_client_not_connected");
    return this.#client.callTool(
      { name: input.name, arguments: input.arguments },
      undefined,
      options === undefined ? undefined : { signal: options.signal },
    );
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

function authenticatedRequestInit(credential: SecretValue | undefined, signal: AbortSignal): RequestInit {
  return Object.freeze({
    signal,
    ...(credential === undefined
      ? {}
      : { headers: Object.freeze({ Authorization: `Bearer ${revealSecretValue(credential)}` }) }),
  });
}

class SdkHttpMcpTransportFactory implements HttpMcpTransportFactory {
  create(input: HttpMcpTransportInput): HttpMcpTransport {
    const requestInit = authenticatedRequestInit(input.credential, input.signal);
    if (input.kind === "streamable-http") {
      return Object.freeze({
        kind: input.kind,
        native: new StreamableHTTPClientTransport(
          input.url,
          { requestInit },
        ),
      });
    }

    const eventSourceInit = input.credential === undefined
      ? undefined
      : {
          fetch: async (url: string | URL, init: RequestInit): Promise<Response> => {
            const headers = new Headers(init.headers);
            headers.set("Authorization", `Bearer ${revealSecretValue(input.credential as SecretValue)}`);
            return fetch(url, { ...init, headers, signal: input.signal });
          },
        };
    return Object.freeze({
      kind: input.kind,
      native: new SSEClientTransport(input.url, {
        requestInit,
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
  readonly operations: readonly McpOperationBinding[];
  readonly credentialResolver: CredentialResolver;
  readonly clientFactory?: HttpMcpClientFactory;
  readonly transportFactory?: HttpMcpTransportFactory;
  readonly timeoutMilliseconds?: number;
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
  readonly #operations: readonly McpOperationBinding[];
  readonly #credentialResolver: CredentialResolver;
  readonly #clientFactory: HttpMcpClientFactory;
  readonly #transportFactory: HttpMcpTransportFactory;
  readonly #timeoutMilliseconds: number;

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
    const timeoutMilliseconds = options.timeoutMilliseconds ?? DEFAULT_HTTP_TIMEOUT_MS;
    if (
      !Number.isSafeInteger(timeoutMilliseconds)
      || timeoutMilliseconds < 1
      || timeoutMilliseconds > MAX_HTTP_TIMEOUT_MS
    ) {
      throw new Error("provider_invalid:http_timeout");
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
    this.#operations = captureMcpOperationBindings(
      options.operations,
      options.server.componentDigest,
      this.#policy.version,
    );
    this.#credentialResolver = options.credentialResolver;
    this.#clientFactory = options.clientFactory ?? new SdkHttpMcpClientFactory();
    this.#transportFactory = options.transportFactory ?? new SdkHttpMcpTransportFactory();
    this.#timeoutMilliseconds = timeoutMilliseconds;
    Object.freeze(this);
  }

  describe(): ProviderDescriptor {
    return this.#descriptor;
  }

  async #resolveCredential(projectId: string, signal: AbortSignal): Promise<SecretValue | undefined> {
    let reference: CredentialReference | undefined;
    if (this.#server.bearerTokenReference !== undefined) {
      reference = Object.freeze({ kind: "bearer", reference: this.#server.bearerTokenReference });
    } else if (this.#server.oauthResource !== undefined) {
      reference = Object.freeze({ kind: "oauth", reference: this.#server.oauthResource });
    }
    if (reference === undefined) return undefined;
    assertCredentialRequest(reference, projectId);
    try {
      return await awaitWithSignal(
        this.#credentialResolver.resolve(reference, projectId, Object.freeze({ signal })),
        signal,
      );
    } catch {
      if (signal.aborted) throw new HttpInvocationTimeoutError();
      throw new Error("credential_missing");
    }
  }

  async invoke(request: ProviderRequest, context: ProviderContext): Promise<ProviderResult> {
    const captured = captureProviderInvocation(request, context);
    request = captured.request;
    const providerDenial = admissionReason(this.#parentLicense, "mcp_http", this.#policy);
    if (providerDenial !== undefined) throw new Error(providerDenial);
    const operation = validateMcpInvocation(this.#server.name, request, this.#operations);
    const capabilityDenial = admissionReason(this.#parentLicense, operation.capability, this.#policy);
    if (capabilityDenial !== undefined) throw new Error(capabilityDenial);

    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), this.#timeoutMilliseconds);
    let client: HttpMcpClient | undefined;
    let result: ProviderResult = Object.freeze({ status: "failed", reason: "mcp_http_failed" });
    try {
      const credential = await this.#resolveCredential(request.projectId, controller.signal);
      if (controller.signal.aborted) throw new HttpInvocationTimeoutError();
      client = this.#clientFactory.create(Object.freeze({ signal: controller.signal }));
      const streamable = this.#transportFactory.create({
        kind: "streamable-http",
        url: this.#url,
        ...(credential === undefined ? {} : { credential }),
        signal: controller.signal,
      });
      try {
        await awaitWithSignal(
          client.connect(streamable, Object.freeze({ signal: controller.signal })),
          controller.signal,
        );
      } catch (error: unknown) {
        if (!(error instanceof McpCompatibilityError)) throw error;
        if (controller.signal.aborted) throw new HttpInvocationTimeoutError();
        const sse = this.#transportFactory.create({
          kind: "sse",
          url: this.#url,
          ...(credential === undefined ? {} : { credential }),
          signal: controller.signal,
        });
        await awaitWithSignal(
          client.connect(sse, Object.freeze({ signal: controller.signal })),
          controller.signal,
        );
      }
      const output = await awaitWithSignal(
        client.callTool({
          name: operation.operationName,
          arguments: request.arguments,
        }, Object.freeze({ signal: controller.signal })),
        controller.signal,
      );
      result = Object.freeze({ status: "success", output });
    } catch (error: unknown) {
      result = Object.freeze({
        status: "failed",
        reason: error instanceof HttpInvocationTimeoutError || controller.signal.aborted
          ? "mcp_http_timed_out"
          : "mcp_http_failed",
      });
    } finally {
      clearTimeout(timer);
      if (client !== undefined) {
        const clientToClose = client;
        if (!(await boundedCleanup(
          (signal) => clientToClose.close(Object.freeze({ signal })),
        ))) {
          result = Object.freeze({ status: "failed", reason: "mcp_http_failed" });
        }
      }
    }
    return result;
  }
}
