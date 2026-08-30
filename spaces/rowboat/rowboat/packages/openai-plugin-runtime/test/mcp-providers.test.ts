import { mkdir, readFile, rename, symlink, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  DEFAULT_POLICY,
  ContentStore,
  HttpMcpProvider,
  McpCompatibilityError,
  NodeProcessSpawner,
  ProcessMcpProvider,
  ProviderRegistry,
  assertSafeSpawnIdentity,
  createSecretValue,
  digestTree,
  executeSafeProcess,
  terminateSpawnedProcess,
  verifyProcessExecutionRoot,
  type CredentialReference,
  type CredentialResolver,
  type HttpMcpClient,
  type HttpMcpTransportFactory,
  type PluginPolicy,
  type ProcessMcpClientFactory,
  type ProcessSpawner,
  type ProviderBinding,
  type ProviderContext,
  type ProviderRequest,
  type SafeSpawnOptions,
  type SpawnedProcess,
  type VerifiedProcessExecutionRoot,
} from "../src/index.js";
import {
  cleanupRegisteredTestRoots,
  createOwnedTestRoot,
} from "./test-temp.js";
import {
  snapshotDirectoryIdentity,
} from "../src/import/directory-identity.js";
import { inspectDigestTree } from "../src/import/digest-service.js";

const HTTP_DIGEST = "a".repeat(64);
const PROCESS_DIGEST = "b".repeat(64);
const WRITE_HTTP_POLICY: PluginPolicy = Object.freeze({
  ...DEFAULT_POLICY,
  allowWriteCapabilities: true,
});
async function createTempDirectory(): Promise<string> {
  return createOwnedTestRoot("mcp-providers");
}

async function createVerifiedProcessRootDetails(pluginRoot: string) {
  const digest = await digestTree(pluginRoot);
  const storeContainer = await createOwnedTestRoot("mcp-provider-store");
  const storeRoot = join(storeContainer, "store");
  const content = await new ContentStore({
    repositoryRoot: pluginRoot,
    storeRoot,
  }).put(pluginRoot, digest);
  const executionRoot = await verifyProcessExecutionRoot(
    content,
    PROCESS_DIGEST,
  );
  return Object.freeze({ executionRoot, path: content.path });
}

async function createVerifiedProcessRoot(pluginRoot: string) {
  return (await createVerifiedProcessRootDetails(pluginRoot)).executionRoot;
}

afterEach(cleanupRegisteredTestRoots);

const request: ProviderRequest = Object.freeze({
  projectId: "project-1",
  pluginName: "example-plugin",
  componentName: "search",
  operationName: "query",
  capability: "write",
  arguments: Object.freeze({ query: "safe query" }),
});

function binding(
  id: string,
  providerKind: "mcp-http" | "mcp-process",
  componentDigest: string,
): ProviderBinding {
  return Object.freeze({ id, providerKind, componentDigest });
}

function invalidInvocationCases(): ReadonlyArray<Readonly<{
  label: string;
  request: unknown;
  context: unknown;
  accessorCalls?: () => number;
}>> {
  const cyclic: Record<string, unknown> = {};
  cyclic.self = cyclic;
  let deep: Record<string, unknown> = {};
  for (let index = 0; index < 20; index += 1) deep = { child: deep };
  let accessorCalls = 0;
  const accessorRequest = { ...request } as Record<string, unknown>;
  Object.defineProperty(accessorRequest, "projectId", {
    enumerable: true,
    get() {
      accessorCalls += 1;
      return "project-1";
    },
  });
  class RequestSubclass {
    projectId = request.projectId;
    pluginName = request.pluginName;
    componentName = request.componentName;
    operationName = request.operationName;
    capability = request.capability;
    arguments = request.arguments;
  }
  return [
    { label: "request accessor", request: accessorRequest, context: { requestId: "request-1" }, accessorCalls: () => accessorCalls },
    { label: "non-plain request", request: new RequestSubclass(), context: { requestId: "request-1" } },
    { label: "non-plain context", request, context: new (class { requestId = "request-1"; })() },
    { label: "empty project id", request: { ...request, projectId: "" }, context: { requestId: "request-1" } },
    { label: "invalid plugin id", request: { ...request, pluginName: "../plugin" }, context: { requestId: "request-1" } },
    { label: "empty component id", request: { ...request, componentName: "" }, context: { requestId: "request-1" } },
    { label: "invalid request id", request, context: { requestId: "request id" } },
    { label: "invalid capability", request: { ...request, capability: "admin" }, context: { requestId: "request-1" } },
    { label: "null arguments", request: { ...request, arguments: null }, context: { requestId: "request-1" } },
    { label: "array arguments", request: { ...request, arguments: [] }, context: { requestId: "request-1" } },
    { label: "prototype-risk key", request: { ...request, arguments: JSON.parse('{"constructor":{"polluted":true}}') }, context: { requestId: "request-1" } },
    { label: "cyclic arguments", request: { ...request, arguments: cyclic }, context: { requestId: "request-1" } },
    { label: "undefined argument", request: { ...request, arguments: { value: undefined } }, context: { requestId: "request-1" } },
    { label: "non-finite number", request: { ...request, arguments: { value: Number.NaN } }, context: { requestId: "request-1" } },
    { label: "oversize arguments", request: { ...request, arguments: { value: "x".repeat(70_000) } }, context: { requestId: "request-1" } },
    { label: "over-depth arguments", request: { ...request, arguments: deep }, context: { requestId: "request-1" } },
    { label: "over-node arguments", request: { ...request, arguments: { values: Array.from({ length: 1_100 }, () => null) } }, context: { requestId: "request-1" } },
  ];
}

class RecordingCredentialResolver implements CredentialResolver {
  readonly calls: Array<{ reference: CredentialReference; projectId: string }> = [];
  readonly #values: Readonly<Record<string, string>>;

  constructor(values: Readonly<Record<string, string>> = {}) {
    this.#values = values;
  }

  async resolve(reference: CredentialReference, projectId: string) {
    this.calls.push({ reference, projectId });
    const value = this.#values[reference.reference];
    if (value === undefined) throw new Error(`unsafe upstream detail ${reference.reference}`);
    return createSecretValue(value);
  }
}

class RecordingHttpClient implements HttpMcpClient {
  readonly connectTransports: string[] = [];
  closeCalls = 0;
  callToolCalls = 0;
  streamableError: unknown;
  result: unknown = { content: [{ type: "text", text: "ok" }] };

  async connect(transport: { readonly kind: "streamable-http" | "sse" }): Promise<void> {
    this.connectTransports.push(transport.kind);
    if (transport.kind === "streamable-http" && this.streamableError !== undefined) {
      throw this.streamableError;
    }
  }

  async callTool(input: {
    readonly name: string;
    readonly arguments: Readonly<Record<string, unknown>>;
  }): Promise<unknown> {
    this.callToolCalls += 1;
    expect(input).toEqual({ name: "query", arguments: { query: "safe query" } });
    return this.result;
  }

  async close(): Promise<void> {
    this.closeCalls += 1;
  }
}

function httpProvider(
  client: RecordingHttpClient,
  resolver = new RecordingCredentialResolver(),
  transportCalls: Array<{ kind: string; credential?: unknown }> = [],
): HttpMcpProvider {
  const transportFactory: HttpMcpTransportFactory = {
    create(input) {
      transportCalls.push({
        kind: input.kind,
        ...(input.credential === undefined ? {} : { credential: input.credential }),
      });
      return Object.freeze({ kind: input.kind });
    },
  };
  const options = {
    id: "mcp.http.search",
    binding: binding("binding.http.search", "mcp-http", HTTP_DIGEST),
    server: Object.freeze({
      name: "search",
      kind: "mcp-http",
      componentDigest: HTTP_DIGEST,
      url: "https://example.com/mcp",
    }),
    parentLicense: "MIT",
    policy: WRITE_HTTP_POLICY,
    credentialResolver: resolver,
    clientFactory: { create: () => client },
    transportFactory,
  };
  return new HttpMcpProvider(options);
}

function deadlineHttpProvider(options: {
  readonly client: HttpMcpClient;
  readonly resolver: CredentialResolver;
  readonly timeoutMilliseconds?: number;
  readonly factorySignal?: (signal: AbortSignal | undefined) => void;
  readonly transportSignal?: (signal: AbortSignal | undefined) => void;
}): HttpMcpProvider {
  const providerOptions = {
    id: "mcp.http.search",
    binding: binding("binding.http.search", "mcp-http", HTTP_DIGEST),
    server: Object.freeze({
      name: "search",
      kind: "mcp-http" as const,
      componentDigest: HTTP_DIGEST,
      url: "https://example.com/mcp",
      bearerTokenReference: "API_TOKEN",
    }),
    parentLicense: "MIT",
    policy: WRITE_HTTP_POLICY,
    credentialResolver: options.resolver,
    clientFactory: {
      create(input?: { readonly signal: AbortSignal }) {
        options.factorySignal?.(input?.signal);
        return options.client;
      },
    },
    transportFactory: {
      create(input: { readonly kind: "streamable-http" | "sse" }) {
        const signal = Reflect.get(input, "signal");
        options.transportSignal?.(signal instanceof AbortSignal ? signal : undefined);
        return Object.freeze({ kind: input.kind });
      },
    },
    timeoutMilliseconds: options.timeoutMilliseconds ?? 10,
  };
  return new HttpMcpProvider(providerOptions);
}

describe("HTTP MCP provider", () => {
  it("pins the patched MCP SDK runtime exactly", async () => {
    const packageJson = JSON.parse(await readFile(new URL("../package.json", import.meta.url), "utf8")) as unknown;
    if (typeof packageJson !== "object" || packageJson === null || !("dependencies" in packageJson)) {
      throw new Error("invalid package manifest");
    }
    const dependencies = packageJson.dependencies;
    if (typeof dependencies !== "object" || dependencies === null) throw new Error("invalid dependencies");
    expect(Reflect.get(dependencies, "@modelcontextprotocol/sdk")).toBe("1.26.0");
  });

  it("integrates with the exact provider registry binding, uses Streamable HTTP, and closes", async () => {
    const client = new RecordingHttpClient();
    const provider = httpProvider(client);
    const registry = new ProviderRegistry();
    const exactBinding = binding("binding.http.search", "mcp-http", HTTP_DIGEST);
    registry.register(exactBinding, provider);

    const resolution = registry.resolve(exactBinding);
    if (resolution.status !== "available") throw new Error("expected provider");
    await expect(resolution.provider.invoke(request, { requestId: "request-1" }))
      .resolves.toMatchObject({ status: "success" });
    expect(client.connectTransports).toEqual(["streamable-http"]);
    expect(client.callToolCalls).toBe(1);
    expect(client.closeCalls).toBe(1);
  });

  it("separates a bound multi-tool server from its selected operation", async () => {
    const client = new RecordingHttpClient();
    const provider = new HttpMcpProvider({
      id: "mcp.http.search",
      binding: binding("binding.http.search", "mcp-http", HTTP_DIGEST),
      server: Object.freeze({
        name: "search-server",
        kind: "mcp-http",
        componentDigest: HTTP_DIGEST,
        url: "https://example.com/mcp",
      }),
      parentLicense: "MIT",
      policy: WRITE_HTTP_POLICY,
      credentialResolver: new RecordingCredentialResolver(),
      clientFactory: { create: () => client },
      transportFactory: { create: (input) => Object.freeze({ kind: input.kind }) },
    });

    await expect(provider.invoke({
      ...request,
      componentName: "search-server",
      operationName: "query",
    }, { requestId: "request-1" })).resolves.toMatchObject({ status: "success" });
    expect(client.callToolCalls).toBe(1);
  });

  it("does not authorize an unbound operation from a caller-supplied read label", async () => {
    const client = new RecordingHttpClient();
    const resolver = new RecordingCredentialResolver({ API_TOKEN: "super-secret-value" });
    const options = {
      id: "mcp.http.search",
      binding: binding("binding.http.search", "mcp-http", HTTP_DIGEST),
      server: Object.freeze({
        name: "search",
        kind: "mcp-http" as const,
        componentDigest: HTTP_DIGEST,
        url: "https://example.com/mcp",
        bearerTokenReference: "API_TOKEN",
      }),
      parentLicense: "MIT",
      policy: DEFAULT_POLICY,
      credentialResolver: resolver,
      clientFactory: { create: () => client },
      transportFactory: { create: (input: { readonly kind: "streamable-http" | "sse" }) => Object.freeze({ kind: input.kind }) },
    };
    const provider = new HttpMcpProvider(options);

    await expect(provider.invoke({
      ...request,
      operationName: "admin_delete",
      capability: "read",
    }, { requestId: "request-1" })).rejects.toThrow("provider_invalid:capability_mismatch");
    expect(resolver.calls).toHaveLength(0);
    expect(client.connectTransports).toHaveLength(0);
  });

  it("does not authorize admin_delete through a forged caller read claim", async () => {
    const client = new RecordingHttpClient();
    const resolver = new RecordingCredentialResolver({ API_TOKEN: "super-secret-value" });
    const provider = new HttpMcpProvider({
      id: "mcp.http.search",
      binding: binding("binding.http.search", "mcp-http", HTTP_DIGEST),
      server: Object.freeze({
        name: "search",
        kind: "mcp-http",
        componentDigest: HTTP_DIGEST,
        url: "https://example.com/mcp",
        bearerTokenReference: "API_TOKEN",
      }),
      parentLicense: "MIT",
      policy: DEFAULT_POLICY,
      credentialResolver: resolver,
      clientFactory: { create: () => client },
      transportFactory: { create: (input) => Object.freeze({ kind: input.kind }) },
    });

    await expect(provider.invoke({
      ...request,
      operationName: "admin_delete",
      capability: "read",
    }, { requestId: "request-1" })).rejects.toThrow("provider_invalid:capability_mismatch");
    expect(resolver.calls).toHaveLength(0);
    expect(client.connectTransports).toHaveLength(0);
  });

  it("allows admin_delete only through explicit write policy and a write request", async () => {
    let invokedName: string | undefined;
    const client: HttpMcpClient = {
      connect: async () => undefined,
      callTool: async (input) => {
        invokedName = input.name;
        return { content: [] };
      },
      close: async () => undefined,
    };
    const provider = new HttpMcpProvider({
      id: "mcp.http.search",
      binding: binding("binding.http.search", "mcp-http", HTTP_DIGEST),
      server: Object.freeze({
        name: "search",
        kind: "mcp-http",
        componentDigest: HTTP_DIGEST,
        url: "https://example.com/mcp",
      }),
      parentLicense: "MIT",
      policy: WRITE_HTTP_POLICY,
      credentialResolver: new RecordingCredentialResolver(),
      clientFactory: { create: () => client },
      transportFactory: { create: (input) => Object.freeze({ kind: input.kind }) },
    });

    await expect(provider.invoke({
      ...request,
      operationName: "admin_delete",
      capability: "write",
    }, { requestId: "request-1" })).resolves.toMatchObject({ status: "success" });
    expect(invokedName).toBe("admin_delete");
  });

  it("rejects a caller capability that differs from the admitted HTTP operation", async () => {
    const client = new RecordingHttpClient();
    const policy = Object.freeze({ ...DEFAULT_POLICY, allowWriteCapabilities: true });
    const options = {
      id: "mcp.http.search",
      binding: binding("binding.http.search", "mcp-http", HTTP_DIGEST),
      server: Object.freeze({
        name: "search",
        kind: "mcp-http" as const,
        componentDigest: HTTP_DIGEST,
        url: "https://example.com/mcp",
      }),
      parentLicense: "MIT",
      policy,
      credentialResolver: new RecordingCredentialResolver(),
      clientFactory: { create: () => client },
      transportFactory: { create: (input: { readonly kind: "streamable-http" | "sse" }) => Object.freeze({ kind: input.kind }) },
    };
    const provider = new HttpMcpProvider(options);

    await expect(provider.invoke({ ...request, capability: "read" }, { requestId: "request-1" }))
      .rejects.toThrow("provider_invalid:capability_mismatch");
    expect(client.connectTransports).toHaveLength(0);
  });

  it.each([
    { label: "missing operation", request: { ...request, operationName: undefined } },
    { label: "invalid operation", request: { ...request, operationName: "../admin" } },
    { label: "component escape", request: { ...request, componentName: "admin_delete", operationName: "admin_delete" } },
  ])("rejects $label before HTTP credentials or transport", async ({ request: unsafeRequest }) => {
    const client = new RecordingHttpClient();
    const resolver = new RecordingCredentialResolver({ API_TOKEN: "super-secret-value" });
    const provider = new HttpMcpProvider({
      id: "mcp.http.search",
      binding: binding("binding.http.search", "mcp-http", HTTP_DIGEST),
      server: Object.freeze({
        name: "search",
        kind: "mcp-http",
        componentDigest: HTTP_DIGEST,
        url: "https://example.com/mcp",
        bearerTokenReference: "API_TOKEN",
      }),
      parentLicense: "MIT",
      policy: DEFAULT_POLICY,
      credentialResolver: resolver,
      clientFactory: { create: () => client },
      transportFactory: { create: (input) => Object.freeze({ kind: input.kind }) },
    });

    await expect(provider.invoke(unsafeRequest as ProviderRequest, { requestId: "request-1" }))
      .rejects.toThrow(/provider_invalid/);
    expect(resolver.calls).toHaveLength(0);
    expect(client.connectTransports).toHaveLength(0);
  });

  it("uses SSE only after a typed compatibility failure", async () => {
    const client = new RecordingHttpClient();
    client.streamableError = new McpCompatibilityError("unsupported_transport");

    await expect(httpProvider(client).invoke(request, { requestId: "request-1" }))
      .resolves.toMatchObject({ status: "success" });
    expect(client.connectTransports).toEqual(["streamable-http", "sse"]);
    expect(client.closeCalls).toBe(1);
  });

  it.each([
    ["authentication", new Error("401 token=super-secret-value")],
    ["network", new TypeError("network super-secret-value")],
    ["general", new Error("boom super-secret-value")],
  ])("does not use SSE after a non-compatibility %s failure", async (_label, error) => {
    const client = new RecordingHttpClient();
    client.streamableError = error;

    await expect(httpProvider(client).invoke(request, { requestId: "request-1" }))
      .resolves.toEqual({ status: "failed", reason: "mcp_http_failed" });
    expect(client.connectTransports).toEqual(["streamable-http"]);
    expect(client.closeCalls).toBe(1);
    expect(JSON.stringify(await httpProvider(client).invoke(request, { requestId: "request-2" })))
      .not.toContain("super-secret-value");
  });

  it("resolves an exact project-scoped bearer reference without stringifying its value", async () => {
    const client = new RecordingHttpClient();
    const resolver = new RecordingCredentialResolver({ API_TOKEN: "super-secret-value" });
    const transportCalls: Array<{ kind: string; credential?: unknown }> = [];
    const provider = new HttpMcpProvider({
      id: "mcp.http.search",
      binding: binding("binding.http.search", "mcp-http", HTTP_DIGEST),
      server: Object.freeze({
        name: "search",
        kind: "mcp-http",
        componentDigest: HTTP_DIGEST,
        url: "https://example.com/mcp",
        bearerTokenReference: "API_TOKEN",
      }),
      parentLicense: "MIT",
      policy: WRITE_HTTP_POLICY,
      credentialResolver: resolver,
      clientFactory: { create: () => client },
      transportFactory: {
        create(input) {
          transportCalls.push({ kind: input.kind, credential: input.credential });
          return Object.freeze({ kind: input.kind });
        },
      },
    });

    await provider.invoke(request, { requestId: "request-1" });
    expect(resolver.calls).toEqual([{
      reference: { kind: "bearer", reference: "API_TOKEN" },
      projectId: "project-1",
    }]);
    expect(JSON.stringify(transportCalls)).not.toContain("super-secret-value");
    expect(Object.isFrozen(transportCalls[0]?.credential)).toBe(true);
  });

  it("resolves an exact project-scoped OAuth resource reference", async () => {
    const client = new RecordingHttpClient();
    const resolver = new RecordingCredentialResolver({
      "https://example.com/oauth": "oauth-secret-value",
    });
    const provider = new HttpMcpProvider({
      id: "mcp.http.search",
      binding: binding("binding.http.search", "mcp-http", HTTP_DIGEST),
      server: Object.freeze({
        name: "search",
        kind: "mcp-http",
        componentDigest: HTTP_DIGEST,
        url: "https://example.com/mcp",
        oauthResource: "https://example.com/oauth",
      }),
      parentLicense: "MIT",
      policy: WRITE_HTTP_POLICY,
      credentialResolver: resolver,
      clientFactory: { create: () => client },
      transportFactory: { create: (input) => Object.freeze({ kind: input.kind }) },
    });

    await provider.invoke(request, { requestId: "request-1" });
    expect(resolver.calls).toEqual([{
      reference: { kind: "oauth", reference: "https://example.com/oauth" },
      projectId: "project-1",
    }]);
  });

  it("rejects query-bearing URLs before credential or transport calls", async () => {
    const client = new RecordingHttpClient();
    const resolver = new RecordingCredentialResolver({ API_TOKEN: "secret" });
    expect(() => new HttpMcpProvider({
      id: "mcp.http.search",
      binding: binding("binding.http.search", "mcp-http", HTTP_DIGEST),
      server: {
        name: "search",
        kind: "mcp-http",
        componentDigest: HTTP_DIGEST,
        url: "https://example.com/mcp?token=super-secret-value",
        bearerTokenReference: "API_TOKEN",
      },
      parentLicense: "MIT",
      policy: DEFAULT_POLICY,
      credentialResolver: resolver,
      clientFactory: { create: () => client },
      transportFactory: { create: (input) => Object.freeze({ kind: input.kind }) },
    })).toThrow("component_invalid:mcp_url");
    expect(resolver.calls).toHaveLength(0);
    expect(client.connectTransports).toHaveLength(0);
  });

  it("snapshots policy so caller mutation cannot upgrade a denied HTTP provider", async () => {
    const mutablePolicy = {
      ...DEFAULT_POLICY,
      allowHttpMcp: false,
    };
    const client = new RecordingHttpClient();
    const provider = new HttpMcpProvider({
      id: "mcp.http.search",
      binding: binding("binding.http.search", "mcp-http", HTTP_DIGEST),
      server: Object.freeze({
        name: "search",
        kind: "mcp-http",
        componentDigest: HTTP_DIGEST,
        url: "https://example.com/mcp",
      }),
      parentLicense: "MIT",
      policy: mutablePolicy,
      credentialResolver: new RecordingCredentialResolver(),
      clientFactory: { create: () => client },
      transportFactory: { create: (input) => Object.freeze({ kind: input.kind }) },
    });
    mutablePolicy.allowHttpMcp = true;

    await expect(provider.invoke(request, { requestId: "request-1" }))
      .rejects.toThrow("http_mcp_not_admitted");
    expect(client.connectTransports).toHaveLength(0);
  });

  it("sanitizes an HTTP client factory failure", async () => {
    const provider = new HttpMcpProvider({
      id: "mcp.http.search",
      binding: binding("binding.http.search", "mcp-http", HTTP_DIGEST),
      server: Object.freeze({
        name: "search",
        kind: "mcp-http",
        componentDigest: HTTP_DIGEST,
        url: "https://example.com/mcp",
      }),
      parentLicense: "MIT",
      policy: WRITE_HTTP_POLICY,
      credentialResolver: new RecordingCredentialResolver(),
      clientFactory: { create: () => { throw new Error("factory super-secret-value"); } },
      transportFactory: { create: (input) => Object.freeze({ kind: input.kind }) },
    });

    const result = await provider.invoke(request, { requestId: "request-1" });
    expect(result).toEqual({ status: "failed", reason: "mcp_http_failed" });
    expect(JSON.stringify(result)).not.toContain("super-secret-value");
  });

  it("reports a missing credential as itself, not the generic HTTP failure", async () => {
    const client = new RecordingHttpClient();
    // No value configured for API_TOKEN, so the resolver rejects exactly as a
    // real credential-store miss would.
    const resolver = new RecordingCredentialResolver();
    const provider = new HttpMcpProvider({
      id: "mcp.http.search",
      binding: binding("binding.http.search", "mcp-http", HTTP_DIGEST),
      server: Object.freeze({
        name: "search",
        kind: "mcp-http",
        componentDigest: HTTP_DIGEST,
        url: "https://example.com/mcp",
        bearerTokenReference: "API_TOKEN",
      }),
      parentLicense: "MIT",
      policy: WRITE_HTTP_POLICY,
      credentialResolver: resolver,
      clientFactory: { create: () => client },
      transportFactory: { create: (input) => Object.freeze({ kind: input.kind }) },
    });

    const result = await provider.invoke(request, { requestId: "request-1" });
    expect(result).toEqual({ status: "failed", reason: "credential_missing" });
    expect(resolver.calls).toHaveLength(1);
    // Never reached the transport: the credential gate is what stopped it.
    expect(client.connectTransports).toHaveLength(0);
  });

  it("still reports the generic HTTP failure for a non-credential transport error", async () => {
    // Same shape of provider as the credential test above (a bearer reference
    // configured, so the credential gate runs first) but this time the
    // resolver succeeds and the transport itself is what fails -- proving the
    // new credential_missing branch does not swallow unrelated failures.
    const client = new RecordingHttpClient();
    client.streamableError = new Error("connect refused, token=super-secret-value");
    const resolver = new RecordingCredentialResolver({ API_TOKEN: "super-secret-value" });
    const provider = new HttpMcpProvider({
      id: "mcp.http.search",
      binding: binding("binding.http.search", "mcp-http", HTTP_DIGEST),
      server: Object.freeze({
        name: "search",
        kind: "mcp-http",
        componentDigest: HTTP_DIGEST,
        url: "https://example.com/mcp",
        bearerTokenReference: "API_TOKEN",
      }),
      parentLicense: "MIT",
      policy: WRITE_HTTP_POLICY,
      credentialResolver: resolver,
      clientFactory: { create: () => client },
      transportFactory: { create: (input) => Object.freeze({ kind: input.kind }) },
    });

    const result = await provider.invoke(request, { requestId: "request-1" });
    expect(result).toEqual({ status: "failed", reason: "mcp_http_failed" });
    expect(resolver.calls).toHaveLength(1);
    expect(JSON.stringify(result)).not.toContain("super-secret-value");
  });

  it("captures and deeply freezes HTTP arguments before awaiting credentials", async () => {
    let releaseCredential: ((value: ReturnType<typeof createSecretValue>) => void) | undefined;
    const resolver: CredentialResolver = {
      resolve: () => new Promise((resolve) => { releaseCredential = resolve; }),
    };
    let receivedArguments: Readonly<Record<string, unknown>> | undefined;
    const client: HttpMcpClient = {
      connect: async () => undefined,
      callTool: async (input) => {
        receivedArguments = input.arguments;
        return { content: [] };
      },
      close: async () => undefined,
    };
    const options = {
      id: "mcp.http.search",
      binding: binding("binding.http.search", "mcp-http", HTTP_DIGEST),
      server: Object.freeze({
        name: "search",
        kind: "mcp-http" as const,
        componentDigest: HTTP_DIGEST,
        url: "https://example.com/mcp",
        bearerTokenReference: "API_TOKEN",
      }),
      parentLicense: "MIT",
      policy: WRITE_HTTP_POLICY,
      credentialResolver: resolver,
      clientFactory: { create: () => client },
      transportFactory: { create: (input: { readonly kind: "streamable-http" | "sse" }) => Object.freeze({ kind: input.kind }) },
    };
    const provider = new HttpMcpProvider(options);
    const mutableArguments = { nested: { value: "before" } };

    const invocation = provider.invoke({ ...request, arguments: mutableArguments }, { requestId: "request-1" });
    mutableArguments.nested.value = "after";
    releaseCredential?.(createSecretValue("super-secret-value"));
    await invocation;

    expect(receivedArguments).toEqual({ nested: { value: "before" } });
    expect(Object.isFrozen(receivedArguments)).toBe(true);
    expect(Object.isFrozen(receivedArguments?.nested)).toBe(true);
  });

  it("bounds a hanging HTTP credential resolution with the invocation deadline", async () => {
    let resolverSignal: AbortSignal | undefined;
    let factoryCalls = 0;
    const resolver: CredentialResolver = {
      resolve(_reference, _projectId, options?: { readonly signal: AbortSignal }) {
        resolverSignal = options?.signal;
        return new Promise(() => undefined);
      },
    };
    const client = new RecordingHttpClient();
    const provider = deadlineHttpProvider({
      client,
      resolver,
      factorySignal: () => { factoryCalls += 1; },
    });

    const outcome = await Promise.race([
      provider.invoke(request, { requestId: "request-1" }),
      new Promise<"test_timeout">((resolve) => setTimeout(() => resolve("test_timeout"), 100)),
    ]);
    expect(outcome).toEqual({ status: "failed", reason: "mcp_http_timed_out" });
    expect(resolverSignal?.aborted).toBe(true);
    expect(factoryCalls).toBe(0);
  });

  it.each(["connect", "call"] as const)("bounds a hanging HTTP %s stage and closes the client", async (stage) => {
    let stageSignal: AbortSignal | undefined;
    let closeCalls = 0;
    const client: HttpMcpClient = {
      connect: async (_transport, options?: { readonly signal: AbortSignal }) => {
        stageSignal = options?.signal;
        if (stage === "connect") await new Promise(() => undefined);
      },
      callTool: async (_input, options?: { readonly signal: AbortSignal }) => {
        stageSignal = options?.signal;
        if (stage === "call") await new Promise(() => undefined);
        return { content: [] };
      },
      close: async () => { closeCalls += 1; },
    };
    const provider = deadlineHttpProvider({
      client,
      resolver: new RecordingCredentialResolver({ API_TOKEN: "super-secret-value" }),
    });

    const outcome = await Promise.race([
      provider.invoke(request, { requestId: "request-1" }),
      new Promise<"test_timeout">((resolve) => setTimeout(() => resolve("test_timeout"), 100)),
    ]);
    expect(outcome).toEqual({ status: "failed", reason: "mcp_http_timed_out" });
    expect(stageSignal?.aborted).toBe(true);
    expect(closeCalls).toBe(1);
  });

  it("keeps a hanging compatibility fallback inside the same HTTP deadline", async () => {
    const transports: string[] = [];
    let closeCalls = 0;
    const client: HttpMcpClient = {
      connect: async (transport) => {
        transports.push(transport.kind);
        if (transport.kind === "streamable-http") throw new McpCompatibilityError("unsupported_transport");
        await new Promise(() => undefined);
      },
      callTool: async () => ({ content: [] }),
      close: async () => { closeCalls += 1; },
    };
    const provider = deadlineHttpProvider({
      client,
      resolver: new RecordingCredentialResolver({ API_TOKEN: "super-secret-value" }),
    });

    const outcome = await Promise.race([
      provider.invoke(request, { requestId: "request-1" }),
      new Promise<"test_timeout">((resolve) => setTimeout(() => resolve("test_timeout"), 100)),
    ]);
    expect(outcome).toEqual({ status: "failed", reason: "mcp_http_timed_out" });
    expect(transports).toEqual(["streamable-http", "sse"]);
    expect(closeCalls).toBe(1);
  });

  it("aborts and closes a hanging unauthenticated production SSE connection", async () => {
    const originalFetch = globalThis.fetch;
    let sseSignal: AbortSignal | undefined;
    globalThis.fetch = (async (_input: string | URL | Request, init?: RequestInit) => {
      if (init?.method === "POST") return new Response("", { status: 404 });
      sseSignal = init?.signal instanceof AbortSignal ? init.signal : undefined;
      return new Promise<Response>((_resolve, reject) => {
        sseSignal?.addEventListener(
          "abort",
          () => reject(new DOMException("aborted", "AbortError")),
          { once: true },
        );
      });
    }) as typeof fetch;
    try {
      const provider = new HttpMcpProvider({
        id: "mcp.http.search",
        binding: binding("binding.http.search", "mcp-http", HTTP_DIGEST),
        server: Object.freeze({
          name: "search",
          kind: "mcp-http",
          componentDigest: HTTP_DIGEST,
          url: "https://example.com/mcp",
        }),
        parentLicense: "MIT",
        policy: WRITE_HTTP_POLICY,
        credentialResolver: new RecordingCredentialResolver(),
        timeoutMilliseconds: 25,
      });

      const outcome = await Promise.race([
        provider.invoke(request, { requestId: "request-1" }),
        new Promise<"test_timeout">((resolve) => setTimeout(() => resolve("test_timeout"), 200)),
      ]);
      expect(outcome).toEqual({ status: "failed", reason: "mcp_http_timed_out" });
      expect(sseSignal?.aborted).toBe(true);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("bounds hanging HTTP close cleanup after a completed call", async () => {
    let closeCalls = 0;
    let cleanupSignal: AbortSignal | undefined;
    const client: HttpMcpClient = {
      connect: async () => undefined,
      callTool: async () => ({ content: [] }),
      close: async (options?: { readonly signal: AbortSignal }) => {
        closeCalls += 1;
        cleanupSignal = options?.signal;
        await new Promise(() => undefined);
      },
    };
    const provider = deadlineHttpProvider({
      client,
      resolver: new RecordingCredentialResolver({ API_TOKEN: "super-secret-value" }),
      timeoutMilliseconds: 50,
    });

    const outcome = await Promise.race([
      provider.invoke(request, { requestId: "request-1" }),
      new Promise<"test_timeout">((resolve) => setTimeout(() => resolve("test_timeout"), 100)),
    ]);
    expect(outcome).toEqual({ status: "failed", reason: "mcp_http_failed" });
    expect(closeCalls).toBe(1);
    expect(cleanupSignal?.aborted).toBe(true);
  });

  it("propagates one HTTP invocation signal through resolver, factories, connect, and call", async () => {
    const signals: AbortSignal[] = [];
    const resolver: CredentialResolver = {
      async resolve(_reference, _projectId, options?: { readonly signal: AbortSignal }) {
        if (options !== undefined) signals.push(options.signal);
        return createSecretValue("super-secret-value");
      },
    };
    const client: HttpMcpClient = {
      connect: async (_transport, options?: { readonly signal: AbortSignal }) => {
        if (options !== undefined) signals.push(options.signal);
      },
      callTool: async (_input, options?: { readonly signal: AbortSignal }) => {
        if (options !== undefined) signals.push(options.signal);
        return { content: [] };
      },
      close: async () => undefined,
    };
    const provider = deadlineHttpProvider({
      client,
      resolver,
      factorySignal: (signal) => { if (signal !== undefined) signals.push(signal); },
      transportSignal: (signal) => { if (signal !== undefined) signals.push(signal); },
      timeoutMilliseconds: 50,
    });

    await expect(provider.invoke(request, { requestId: "request-1" }))
      .resolves.toMatchObject({ status: "success" });
    expect(signals).toHaveLength(5);
    expect(new Set(signals).size).toBe(1);
    expect(signals[0]?.aborted).toBe(false);
  });

  it("composes a caller abort into an in-flight HTTP call and settles cleanup", async () => {
    const caller = new AbortController();
    let markStarted: (() => void) | undefined;
    const started = new Promise<void>((resolve) => { markStarted = resolve; });
    let callSettled = false;
    let closeCalls = 0;
    const client: HttpMcpClient = {
      connect: async () => undefined,
      callTool: async (_input, options?: { readonly signal: AbortSignal }) => {
        markStarted?.();
        const signal = options?.signal;
        await new Promise<void>((resolve) => {
          if (signal?.aborted === true) resolve();
          else signal?.addEventListener("abort", () => resolve(), { once: true });
        });
        callSettled = true;
        throw new DOMException("aborted", "AbortError");
      },
      close: async () => { closeCalls += 1; },
    };
    const provider = deadlineHttpProvider({
      client,
      resolver: new RecordingCredentialResolver({ API_TOKEN: "super-secret-value" }),
      timeoutMilliseconds: 1_000,
    });
    const context = Object.freeze({ requestId: "request-1", signal: caller.signal }) as ProviderContext;
    const invocation = provider.invoke(request, context);
    const stage = await Promise.race([
      started.then(() => "started" as const),
      invocation.then(() => "ended" as const, () => "ended" as const),
    ]);
    expect(stage).toBe("started");
    caller.abort();
    await expect(invocation).resolves.toEqual({ status: "failed", reason: "mcp_http_timed_out" });
    expect(callSettled).toBe(true);
    expect(closeCalls).toBe(1);
  });
});

class RecordingSpawner implements ProcessSpawner {
  readonly calls: Array<{
    command: string;
    args: readonly string[];
    options: SafeSpawnOptions;
  }> = [];
  next: SpawnedProcess = {
    stdout: (async function* () { yield Buffer.from("ok"); })(),
    stderr: (async function* () {})(),
    completion: Promise.resolve({ exitCode: 0, signal: null }),
    kill: () => undefined,
  };

  async spawn(command: string, args: readonly string[], options: SafeSpawnOptions): Promise<SpawnedProcess> {
    await assertSafeSpawnIdentity(options);
    this.calls.push({ command, args, options });
    return this.next;
  }
}

const PROCESS_POLICY: PluginPolicy = Object.freeze({
  ...DEFAULT_POLICY,
  allowProcessMcp: true,
  allowWriteCapabilities: true,
});

async function processProvider(options: {
  readonly pluginRoot: string;
  readonly spawner: ProcessSpawner;
  readonly resolver?: CredentialResolver;
  readonly policy?: PluginPolicy;
  readonly workingDirectory?: string;
  readonly environmentReferences?: readonly string[];
  readonly timeoutMilliseconds?: number;
  readonly maxOutputBytes?: number;
  readonly clientCall?: (input: {
    readonly name: string;
    readonly arguments: Readonly<Record<string, unknown>>;
  }) => Promise<unknown>;
  readonly clientFactory?: ProcessMcpClientFactory;
  readonly executionRoot?: VerifiedProcessExecutionRoot;
}): Promise<ProcessMcpProvider> {
  const executionRoot = options.executionRoot ?? await createVerifiedProcessRoot(options.pluginRoot);
  const providerOptions = {
    id: "mcp.process.search",
    binding: binding("binding.process.search", "mcp-process", PROCESS_DIGEST),
    server: Object.freeze({
      name: "search",
      kind: "mcp-process",
      componentDigest: PROCESS_DIGEST,
      command: "node",
      args: Object.freeze(["server.js", "--stdio"]),
      ...(options.workingDirectory === undefined ? {} : { workingDirectory: options.workingDirectory }),
      environmentReferences: Object.freeze([...(options.environmentReferences ?? [])]),
      ...(options.timeoutMilliseconds === undefined ? {} : { timeoutMilliseconds: options.timeoutMilliseconds }),
    }),
    executionRoot,
    parentLicense: "MIT",
    policy: options.policy ?? PROCESS_POLICY,
    credentialResolver: options.resolver ?? new RecordingCredentialResolver(),
    spawner: options.spawner,
    safeBaselineEnvironment: Object.freeze({ NO_COLOR: "1" }),
    ...(options.maxOutputBytes === undefined ? {} : { maxOutputBytes: options.maxOutputBytes }),
    clientFactory: options.clientFactory ?? {
      create: () => ({
        connect: async () => undefined,
        callTool: options.clientCall ?? (async () => ({ content: [{ type: "text", text: "ok" }] })),
        close: async () => undefined,
      }),
    },
  };
  return new ProcessMcpProvider(providerOptions);
}

describe("process MCP provider", () => {
  it("rejects accessor-backed execution-root provenance without invoking accessors", async () => {
    const pluginRoot = await createTempDirectory();
    const digest = await digestTree(pluginRoot);
    let accessorCalls = 0;
    const content = {
      get path(): string {
        accessorCalls += 1;
        return pluginRoot;
      },
      digest,
    };

    await expect(verifyProcessExecutionRoot(content, PROCESS_DIGEST))
      .rejects.toThrow("provider_invalid:execution_root");
    expect(accessorCalls).toBe(0);
  });

  it("rejects a structurally forged stored-content token", async () => {
    const pluginRoot = await createTempDirectory();
    const forged = Object.freeze({ path: pluginRoot, digest: await digestTree(pluginRoot) });

    await expect(verifyProcessExecutionRoot(forged, PROCESS_DIGEST))
      .rejects.toThrow("provider_invalid:execution_root");
  });

  it.each(invalidInvocationCases())(
    "rejects invalid $label at the shared boundary before HTTP or process side effects",
    async ({ request: invalidRequest, context: invalidContext, accessorCalls }) => {
      const httpResolver = new RecordingCredentialResolver({ API_TOKEN: "super-secret-value" });
      let httpFactoryCalls = 0;
      let httpTransportCalls = 0;
      const httpOptions = {
        id: "mcp.http.search",
        binding: binding("binding.http.search", "mcp-http", HTTP_DIGEST),
        server: Object.freeze({
          name: "search",
          kind: "mcp-http" as const,
          componentDigest: HTTP_DIGEST,
          url: "https://example.com/mcp",
          bearerTokenReference: "API_TOKEN",
        }),
        parentLicense: "MIT",
        policy: DEFAULT_POLICY,
        credentialResolver: httpResolver,
        clientFactory: {
          create: () => {
            httpFactoryCalls += 1;
            return new RecordingHttpClient();
          },
        },
        transportFactory: {
          create: (input: { readonly kind: "streamable-http" | "sse" }) => {
            httpTransportCalls += 1;
            return Object.freeze({ kind: input.kind });
          },
        },
      };
      const http = new HttpMcpProvider(httpOptions);
      await expect(http.invoke(
        invalidRequest as ProviderRequest,
        invalidContext as ProviderContext,
      )).rejects.toThrow("provider_invalid:request");
      expect(httpResolver.calls).toHaveLength(0);
      expect(httpFactoryCalls).toBe(0);
      expect(httpTransportCalls).toBe(0);

      const pluginRoot = await createTempDirectory();
      const processResolver = new RecordingCredentialResolver({ API_TOKEN: "super-secret-value" });
      const spawner = new RecordingSpawner();
      const process = await processProvider({
        pluginRoot,
        spawner,
        resolver: processResolver,
        workingDirectory: "missing-directory",
        environmentReferences: ["API_TOKEN"],
      });
      await expect(process.invoke(
        invalidRequest as ProviderRequest,
        invalidContext as ProviderContext,
      )).rejects.toThrow("provider_invalid:request");
      expect(processResolver.calls).toHaveLength(0);
      expect(spawner.calls).toHaveLength(0);
      expect(accessorCalls?.() ?? 0).toBe(0);
    },
  );

  it("captures process arguments before awaiting credentials", async () => {
    const pluginRoot = await createTempDirectory();
    const spawner = new RecordingSpawner();
    let releaseCredential: ((value: ReturnType<typeof createSecretValue>) => void) | undefined;
    let markResolverEntered: (() => void) | undefined;
    const resolverEntered = new Promise<void>((resolve) => { markResolverEntered = resolve; });
    const resolver: CredentialResolver = {
      resolve: () => {
        markResolverEntered?.();
        return new Promise((resolve) => { releaseCredential = resolve; });
      },
    };
    let receivedArguments: Readonly<Record<string, unknown>> | undefined;
    const provider = await processProvider({
      pluginRoot,
      spawner,
      resolver,
      environmentReferences: ["API_TOKEN"],
      clientCall: async (input) => {
        receivedArguments = input.arguments;
        return { content: [] };
      },
    });
    const mutableArguments = { nested: { value: "before" } };

    const invocation = provider.invoke({ ...request, arguments: mutableArguments }, { requestId: "request-1" });
    await resolverEntered;
    mutableArguments.nested.value = "after";
    releaseCredential?.(createSecretValue("super-secret-value"));
    await invocation;

    expect(receivedArguments).toEqual({ nested: { value: "before" } });
    expect(Object.isFrozen(receivedArguments)).toBe(true);
    expect(Object.isFrozen(receivedArguments?.nested)).toBe(true);
  });

  it.each(["root", "cwd", "content"] as const)(
    "revalidates the bound process %s identity after credential resolution",
    async (swapTarget) => {
      const base = await createTempDirectory();
      const pluginRoot = join(base, "plugin");
      await mkdir(join(pluginRoot, "server"), { recursive: true });
      const verified = await createVerifiedProcessRootDetails(pluginRoot);
      const workingDirectory = join(verified.path, "server");
      const spawner = new RecordingSpawner();
      const resolver: CredentialResolver = {
        async resolve() {
          if (swapTarget === "root") {
            await rename(verified.path, `${verified.path}-old`);
            await mkdir(workingDirectory, { recursive: true });
          } else if (swapTarget === "cwd") {
            await rename(workingDirectory, `${workingDirectory}-old`);
            await mkdir(workingDirectory);
          } else {
            await writeFile(join(verified.path, "server.js"), "changed after verification", "utf8");
          }
          return createSecretValue("super-secret-value");
        },
      };
      const provider = await processProvider({
        pluginRoot,
        spawner,
        resolver,
        workingDirectory: "server",
        environmentReferences: ["API_TOKEN"],
        executionRoot: verified.executionRoot,
      });

      if (swapTarget === "content") {
        await expect(provider.invoke(request, { requestId: "request-1" }))
          .resolves.toEqual({ status: "failed", reason: "process_failed" });
      } else {
        await expect(provider.invoke(request, { requestId: "request-1" }))
          .rejects.toThrow("path_escape");
      }
      expect(spawner.calls).toHaveLength(0);
    },
  );

  it.each(["trusted-parent", "root", "cwd", "content"] as const)(
    "revalidates %s inside the spawn boundary before an actual spawn",
    async (swapTarget) => {
    const base = await createTempDirectory();
    const pluginRoot = join(base, "plugin");
    await mkdir(join(pluginRoot, "server"), { recursive: true });
    await writeFile(join(pluginRoot, "server.js"), "original", "utf8");
    const verified = await createVerifiedProcessRootDetails(pluginRoot);
    const workingDirectory = join(verified.path, "server");
    const trustedStore = dirname(verified.path);
    let actualSpawnCalls = 0;
    const boundarySpawner = {
      async spawn(_command: string, _args: readonly string[], options: unknown): Promise<SpawnedProcess> {
        if (swapTarget === "trusted-parent") {
          await rename(trustedStore, `${trustedStore}-old`);
          await mkdir(trustedStore);
        } else if (swapTarget === "root") {
          await rename(verified.path, `${verified.path}-old`);
          await mkdir(verified.path);
        } else if (swapTarget === "cwd") {
          await rename(workingDirectory, `${workingDirectory}-old`);
          await mkdir(workingDirectory);
        } else {
          await writeFile(join(verified.path, "server.js"), "changed", "utf8");
        }
        await assertSafeSpawnIdentity(options as SafeSpawnOptions);
        actualSpawnCalls += 1;
        return {
          stdout: (async function* () {})(),
          stderr: (async function* () {})(),
          completion: Promise.resolve({ exitCode: 0, signal: null }),
          kill: () => undefined,
        };
      },
    } as unknown as ProcessSpawner;
    const provider = await processProvider({
      pluginRoot,
      executionRoot: verified.executionRoot,
      spawner: boundarySpawner,
      workingDirectory: "server",
    });

    await expect(provider.invoke(request, { requestId: "request-1" }))
      .resolves.toEqual({ status: "failed", reason: "process_failed" });
    await new Promise((resolve) => setTimeout(resolve, 25));
    expect(actualSpawnCalls).toBe(0);
    },
  );

  it.each(["oversized", "deep", "many-file"] as const)(
    "rejects %s execution-root inventory drift at the trusted spawn boundary",
    async (drift) => {
      const base = await createTempDirectory();
      const pluginRoot = join(base, "plugin");
      await mkdir(join(pluginRoot, "server"), { recursive: true });
      await writeFile(join(pluginRoot, "server.js"), "original", "utf8");
      const verified = await createVerifiedProcessRootDetails(pluginRoot);
      if (drift === "oversized") {
        await writeFile(join(verified.path, "server.js"), "x".repeat(128), "utf8");
      } else if (drift === "deep") {
        await mkdir(join(verified.path, "server", "deeper"), { recursive: true });
      } else {
        await writeFile(join(verified.path, "added.js"), "added", "utf8");
      }
      let actualSpawnCalls = 0;
      const boundarySpawner: ProcessSpawner = {
        async spawn(_command, _args, options) {
          await assertSafeSpawnIdentity(options);
          actualSpawnCalls += 1;
          throw new Error("unexpected actual spawn");
        },
      };
      const provider = await processProvider({
        pluginRoot,
        executionRoot: verified.executionRoot,
        spawner: boundarySpawner,
      });

      const outcome = await Promise.race([
        provider.invoke(request, { requestId: "request-1" }),
        new Promise<"test_timeout">((resolve) => setTimeout(() => resolve("test_timeout"), 500)),
      ]);
      expect(outcome).not.toBe("test_timeout");
      expect(outcome).toEqual({ status: "failed", reason: "process_failed" });
      expect(actualSpawnCalls).toBe(0);
    },
  );

  it("uses the pinned SDK protocol over an injected stdio process", async () => {
    const pluginRoot = await createTempDirectory();
    const spawner = new RecordingSpawner();
    const queued: Buffer[] = [];
    const waiters: Array<() => void> = [];
    const methods: string[] = [];
    let finished = false;
    let killed = 0;
    let complete: ((value: { exitCode: number | null; signal: string | null }) => void) | undefined;
    const enqueue = (value: unknown): void => {
      queued.push(Buffer.from(`${JSON.stringify(value)}\n`));
      waiters.shift()?.();
    };
    spawner.next = {
      stdout: (async function* () {
        while (!finished || queued.length > 0) {
          const next = queued.shift();
          if (next !== undefined) {
            yield next;
          } else {
            await new Promise<void>((resolve) => waiters.push(resolve));
          }
        }
      })(),
      stderr: (async function* () {})(),
      completion: new Promise((resolve) => { complete = resolve; }),
      async writeStdin(chunk: string): Promise<void> {
        for (const line of chunk.trim().split("\n")) {
          const message = JSON.parse(line) as unknown;
          if (typeof message !== "object" || message === null) throw new Error("bad request");
          const method = Reflect.get(message, "method");
          if (typeof method === "string") methods.push(method);
          const id = Reflect.get(message, "id");
          if (method === "initialize") {
            const params = Reflect.get(message, "params");
            const protocolVersion = typeof params === "object" && params !== null
              ? Reflect.get(params, "protocolVersion")
              : undefined;
            enqueue({
              jsonrpc: "2.0",
              id,
              result: {
                protocolVersion,
                capabilities: { tools: {} },
                serverInfo: { name: "fake-mcp", version: "1.0.0" },
              },
            });
          } else if (method === "tools/call") {
            enqueue({
              jsonrpc: "2.0",
              id,
              result: { content: [{ type: "text", text: "sdk-ok" }] },
            });
          }
        }
      },
      async closeStdin(): Promise<void> {},
      kill: () => {
        killed += 1;
        finished = true;
        waiters.shift()?.();
        complete?.({ exitCode: null, signal: "SIGTERM" });
      },
    };
    const provider = new ProcessMcpProvider({
      id: "mcp.process.search",
      binding: binding("binding.process.search", "mcp-process", PROCESS_DIGEST),
      server: Object.freeze({
        name: "search",
        kind: "mcp-process",
        componentDigest: PROCESS_DIGEST,
        command: "node",
        args: Object.freeze(["server.js"]),
        environmentReferences: Object.freeze([]),
        timeoutMilliseconds: 500,
      }),
      executionRoot: await createVerifiedProcessRoot(pluginRoot),
      parentLicense: "MIT",
      policy: PROCESS_POLICY,
      credentialResolver: new RecordingCredentialResolver(),
      spawner,
    });

    const result = await provider.invoke(request, { requestId: "request-1" });
    expect(result).toMatchObject({
      status: "success",
      output: { result: { content: [{ type: "text", text: "sdk-ok" }] } },
    });
    expect(methods).toEqual(["initialize", "notifications/initialized", "tools/call"]);
    expect(killed).toBe(1);
  });

  it("passes the composed abort signal through the production SDK process adapter", async () => {
    const pluginRoot = await createTempDirectory();
    const caller = new AbortController();
    const spawner = new RecordingSpawner();
    let killed = 0;
    let complete: ((value: { exitCode: number | null; signal: string | null }) => void) | undefined;
    spawner.next = {
      stdout: (async function* () {})(),
      stderr: (async function* () {})(),
      completion: new Promise((resolve) => { complete = resolve; }),
      async writeStdin(): Promise<void> {},
      async closeStdin(): Promise<void> {},
      kill: () => {
        killed += 1;
        complete?.({ exitCode: null, signal: "SIGTERM" });
      },
    };
    let connectSignal: AbortSignal | undefined;
    let callSignal: AbortSignal | undefined;
    let callSettled = false;
    const closeArguments: unknown[][] = [];
    let markStarted: (() => void) | undefined;
    const started = new Promise<void>((resolve) => { markStarted = resolve; });
    const sdkClientFactory = {
      create: () => ({
        connect: async (_transport: unknown, options?: Readonly<{ readonly signal?: AbortSignal }>) => {
          connectSignal = options?.signal;
        },
        callTool: async (
          input: Readonly<{ readonly name: string; readonly arguments: Readonly<Record<string, unknown>> }>,
          resultSchema?: unknown,
          options?: Readonly<{ readonly signal?: AbortSignal }>,
        ): Promise<unknown> => {
          expect(input).toEqual({ name: "query", arguments: { query: "safe query" } });
          expect(resultSchema).toBeUndefined();
          callSignal = options?.signal;
          markStarted?.();
          await new Promise<void>((resolve) => {
            if (callSignal?.aborted === true) resolve();
            else callSignal?.addEventListener("abort", () => resolve(), { once: true });
          });
          callSettled = true;
          throw new DOMException("aborted", "AbortError");
        },
        close: async (...args: unknown[]) => { closeArguments.push(args); },
      }),
    };
    const providerOptions = {
      id: "mcp.process.search",
      binding: binding("binding.process.search", "mcp-process", PROCESS_DIGEST),
      server: Object.freeze({
        name: "search",
        kind: "mcp-process" as const,
        componentDigest: PROCESS_DIGEST,
        command: "node",
        args: Object.freeze(["server.js"]),
        environmentReferences: Object.freeze([]),
        timeoutMilliseconds: 1_000,
      }),
      executionRoot: await createVerifiedProcessRoot(pluginRoot),
      parentLicense: "MIT",
      policy: PROCESS_POLICY,
      credentialResolver: new RecordingCredentialResolver(),
      spawner,
      sdkClientFactory,
    };
    const provider = new ProcessMcpProvider(providerOptions);

    const unhandled: unknown[] = [];
    const captureUnhandled = (reason: unknown): void => { unhandled.push(reason); };
    process.on("unhandledRejection", captureUnhandled);
    try {
      const invocation = provider.invoke(
        request,
        Object.freeze({ requestId: "request-1", signal: caller.signal }),
      );
      const stage = await Promise.race([
        started.then(() => "started" as const),
        invocation.then(() => "ended" as const, () => "ended" as const),
      ]);
      expect(stage).toBe("started");
      caller.abort();

      await expect(invocation).resolves.toEqual({ status: "failed", reason: "process_timed_out" });
      await new Promise((resolve) => setTimeout(resolve, 25));
      expect(connectSignal).toBe(callSignal);
      expect(callSignal?.aborted).toBe(true);
      expect(callSettled).toBe(true);
      expect(closeArguments).toEqual([[]]);
      expect(killed).toBe(1);
      expect(unhandled).toEqual([]);
    } finally {
      process.off("unhandledRejection", captureUnhandled);
    }
  });

  it("fails closed when individually valid protocol frames exceed the cumulative stdout budget", async () => {
    const pluginRoot = await createTempDirectory();
    const spawner = new RecordingSpawner();
    const queued: Buffer[] = [];
    const waiters: Array<() => void> = [];
    let finished = false;
    let killed = 0;
    let complete: ((value: { exitCode: number | null; signal: string | null }) => void) | undefined;
    const enqueue = (value: unknown): void => {
      queued.push(Buffer.from(`${JSON.stringify(value)}\n`));
      waiters.shift()?.();
    };
    spawner.next = {
      stdout: (async function* () {
        while (!finished || queued.length > 0) {
          const next = queued.shift();
          if (next !== undefined) yield next;
          else await new Promise<void>((resolve) => waiters.push(resolve));
        }
      })(),
      stderr: (async function* () {})(),
      completion: new Promise((resolve) => { complete = resolve; }),
      async writeStdin(chunk: string): Promise<void> {
        for (const line of chunk.trim().split("\n")) {
          const message = JSON.parse(line) as unknown;
          if (typeof message !== "object" || message === null) throw new Error("bad request");
          const method = Reflect.get(message, "method");
          const id = Reflect.get(message, "id");
          if (method === "initialize") {
            const params = Reflect.get(message, "params");
            const protocolVersion = typeof params === "object" && params !== null
              ? Reflect.get(params, "protocolVersion")
              : undefined;
            enqueue({
              jsonrpc: "2.0",
              id,
              result: {
                protocolVersion,
                capabilities: { logging: {}, tools: {} },
                serverInfo: { name: "budget-mcp", version: "1.0.0" },
              },
            });
            for (let index = 0; index < 12; index += 1) {
              enqueue({
                jsonrpc: "2.0",
                method: "notifications/message",
                params: { level: "info", data: `bounded-frame-${index}` },
              });
            }
          } else if (method === "tools/call") {
            enqueue({ jsonrpc: "2.0", id, result: { content: [{ type: "text", text: "unexpected" }] } });
          }
        }
      },
      async closeStdin(): Promise<void> {},
      kill: () => {
        killed += 1;
        finished = true;
        waiters.shift()?.();
        complete?.({ exitCode: null, signal: "SIGTERM" });
      },
    };
    const provider = new ProcessMcpProvider({
      id: "mcp.process.search",
      binding: binding("binding.process.search", "mcp-process", PROCESS_DIGEST),
      server: Object.freeze({
        name: "search",
        kind: "mcp-process",
        componentDigest: PROCESS_DIGEST,
        command: "node",
        args: Object.freeze(["server.js"]),
        environmentReferences: Object.freeze([]),
        timeoutMilliseconds: 500,
      }),
      executionRoot: await createVerifiedProcessRoot(pluginRoot),
      parentLicense: "MIT",
      policy: PROCESS_POLICY,
      credentialResolver: new RecordingCredentialResolver(),
      spawner,
      maxProtocolFrameBytes: 256,
      maxProtocolBytes: 512,
    });

    const outcome = await Promise.race([
      provider.invoke(request, { requestId: "request-1" }),
      new Promise<"test_timeout">((resolve) => setTimeout(() => resolve("test_timeout"), 100)),
    ]);
    expect(outcome).not.toBe("test_timeout");
    expect(outcome).toEqual({ status: "failed", reason: "process_failed" });
    expect(killed).toBe(1);
  });

  it("invokes the MCP tool through the client without waiting for the server process to exit", async () => {
    const pluginRoot = await createTempDirectory();
    const spawner = new RecordingSpawner();
    let complete: ((value: { exitCode: number | null; signal: string | null }) => void) | undefined;
    spawner.next = {
      stdout: (async function* () {})(),
      stderr: (async function* () {})(),
      completion: new Promise((resolve) => { complete = resolve; }),
      kill: () => complete?.({ exitCode: null, signal: "SIGTERM" }),
    };
    let connectCalls = 0;
    let callToolCalls = 0;
    let closeCalls = 0;
    const providerOptions = {
      id: "mcp.process.search",
      binding: binding("binding.process.search", "mcp-process", PROCESS_DIGEST),
      server: Object.freeze({
        name: "search",
        kind: "mcp-process" as const,
        componentDigest: PROCESS_DIGEST,
        command: "node",
        args: Object.freeze(["server.js"]),
        environmentReferences: Object.freeze([]),
        timeoutMilliseconds: 2_000,
      }),
      executionRoot: await createVerifiedProcessRoot(pluginRoot),
      parentLicense: "MIT",
      policy: PROCESS_POLICY,
      credentialResolver: new RecordingCredentialResolver(),
      spawner,
      clientFactory: {
        create: () => ({
          connect: async () => { connectCalls += 1; },
          callTool: async (input: unknown) => {
            callToolCalls += 1;
            expect(input).toEqual({ name: "query", arguments: { query: "safe query" } });
            return { content: [{ type: "text", text: "ok" }] };
          },
          close: async () => { closeCalls += 1; },
        }),
      },
    };
    const provider = new ProcessMcpProvider(providerOptions);

    await expect(provider.invoke(request, { requestId: "request-1" }))
      .resolves.toMatchObject({ status: "success" });
    expect(connectCalls).toBe(1);
    expect(callToolCalls).toBe(1);
    expect(closeCalls).toBe(1);
  });

  it("denies policy-rejected execution before credentials, cwd, or spawn", async () => {
    const pluginRoot = await createTempDirectory();
    const spawner = new RecordingSpawner();
    const resolver = new RecordingCredentialResolver({ API_TOKEN: "secret" });
    const provider = await processProvider({
      pluginRoot,
      spawner,
      resolver,
      policy: DEFAULT_POLICY,
      workingDirectory: "missing-directory",
      environmentReferences: ["API_TOKEN"],
    });

    await expect(provider.invoke(request, { requestId: "request-1" }))
      .rejects.toThrow("process_not_admitted");
    expect(resolver.calls).toHaveLength(0);
    expect(spawner.calls).toHaveLength(0);
  });

  it.each([
    { label: "missing operation", request: { ...request, operationName: undefined } },
    { label: "invalid operation", request: { ...request, operationName: "../admin" } },
    { label: "component escape", request: { ...request, componentName: "admin_delete", operationName: "admin_delete" } },
  ])("rejects $label before process credentials, cwd, or spawn", async ({ request: unsafeRequest }) => {
    const pluginRoot = await createTempDirectory();
    const spawner = new RecordingSpawner();
    const resolver = new RecordingCredentialResolver({ API_TOKEN: "super-secret-value" });
    const provider = await processProvider({
      pluginRoot,
      spawner,
      resolver,
      workingDirectory: "missing-directory",
      environmentReferences: ["API_TOKEN"],
    });

    await expect(provider.invoke(unsafeRequest as ProviderRequest, { requestId: "request-1" }))
      .rejects.toThrow(/provider_invalid/);
    expect(resolver.calls).toHaveLength(0);
    expect(spawner.calls).toHaveLength(0);
  });

  it("denies admin_delete even when a caller forges a read capability claim", async () => {
    const pluginRoot = await createTempDirectory();
    const spawner = new RecordingSpawner();
    const provider = await processProvider({
      pluginRoot,
      spawner,
    });

    await expect(provider.invoke({
      ...request,
      operationName: "admin_delete",
      capability: "read",
    }, { requestId: "request-1" })).rejects.toThrow("provider_invalid:capability_mismatch");
    expect(spawner.calls).toHaveLength(0);
  });

  it("allows a process operation only through explicit write policy and a write request", async () => {
    const pluginRoot = await createTempDirectory();
    const spawner = new RecordingSpawner();
    let invokedName: string | undefined;
    const writePolicy = Object.freeze({ ...PROCESS_POLICY, allowWriteCapabilities: true });
    const provider = await processProvider({
      pluginRoot,
      spawner,
      policy: writePolicy,
      clientCall: async (input) => {
        invokedName = input.name;
        return { content: [] };
      },
    });

    await expect(provider.invoke({
      ...request,
      operationName: "admin_delete",
      capability: "write",
    }, { requestId: "request-1" })).resolves.toMatchObject({ status: "success" });
    expect(invokedName).toBe("admin_delete");
    expect(spawner.calls).toHaveLength(1);
  });

  it("does not spawn when an exact credential reference is missing", async () => {
    const pluginRoot = await createTempDirectory();
    const spawner = new RecordingSpawner();
    const provider = await processProvider({
      pluginRoot,
      spawner,
      resolver: new RecordingCredentialResolver(),
      environmentReferences: ["API_TOKEN"],
    });

    await expect(provider.invoke(request, { requestId: "request-1" }))
      .rejects.toThrow("credential_missing");
    expect(spawner.calls).toHaveLength(0);
  });

  it("bounds a hanging process credential resolver with the single invocation deadline", async () => {
    const pluginRoot = await createTempDirectory();
    const spawner = new RecordingSpawner();
    let resolverSignal: AbortSignal | undefined;
    const resolver: CredentialResolver = {
      resolve: (_reference, _projectId, options) => {
        resolverSignal = options?.signal;
        return new Promise(() => undefined);
      },
    };
    const provider = await processProvider({
      pluginRoot,
      spawner,
      resolver,
      environmentReferences: ["API_TOKEN"],
      timeoutMilliseconds: 15,
    });

    const outcome = await Promise.race([
      provider.invoke(request, { requestId: "request-1" }),
      new Promise<"test_timeout">((resolve) => setTimeout(() => resolve("test_timeout"), 200)),
    ]);
    expect(outcome).toEqual({ status: "failed", reason: "process_timed_out" });
    expect(resolverSignal?.aborted).toBe(true);
    expect(spawner.calls).toHaveLength(0);
  });

  it("rejects a segment-wise symlink cwd escape without spawning", async () => {
    const base = await createTempDirectory();
    const pluginRoot = join(base, "plugin");
    const outside = join(base, "outside");
    await mkdir(pluginRoot);
    await mkdir(outside);
    const executionRoot = await createVerifiedProcessRoot(pluginRoot);
    await symlink(outside, join(pluginRoot, "linked"), "junction");
    const spawner = new RecordingSpawner();
    const provider = await processProvider({
      pluginRoot,
      executionRoot,
      spawner,
      workingDirectory: "linked",
    });

    await expect(provider.invoke(request, { requestId: "request-1" }))
      .rejects.toThrow("path_escape");
    expect(spawner.calls).toHaveLength(0);
  });

  it("spawns exact command and args with shell false and only fixed baseline plus resolved env", async () => {
    const pluginRoot = await createTempDirectory();
    await mkdir(join(pluginRoot, "server"));
    const spawner = new RecordingSpawner();
    const resolver = new RecordingCredentialResolver({ API_TOKEN: "super-secret-value" });
    const provider = await processProvider({
      pluginRoot,
      spawner,
      resolver,
      workingDirectory: "server",
      environmentReferences: ["API_TOKEN"],
    });

    await expect(provider.invoke(request, { requestId: "request-1" }))
      .resolves.toMatchObject({ status: "success" });
    expect(spawner.calls).toHaveLength(1);
    expect(spawner.calls[0]).toMatchObject({
      command: "node",
      args: ["server.js", "--stdio"],
      options: {
        shell: false,
        env: { NO_COLOR: "1", API_TOKEN: "super-secret-value" },
      },
    });
    expect(spawner.calls[0]?.options.env).not.toHaveProperty("PATH");
    expect(resolver.calls).toEqual([{
      reference: { kind: "environment", reference: "API_TOKEN" },
      projectId: "project-1",
    }]);
  });

  it("enforces timeout, kills the process, and returns no raw error output", async () => {
    const pluginRoot = await createTempDirectory();
    const spawner = new RecordingSpawner();
    let killed = 0;
    let complete: ((value: { exitCode: number | null; signal: string | null }) => void) | undefined;
    spawner.next = {
      stdout: (async function* () { yield Buffer.from("partial super-secret-value"); })(),
      stderr: (async function* () { yield Buffer.from("bad super-secret-value"); })(),
      completion: new Promise((resolve) => { complete = resolve; }),
      kill: () => {
        killed += 1;
        complete?.({ exitCode: null, signal: "SIGTERM" });
      },
    };
    const provider = await processProvider({
      pluginRoot,
      spawner,
      timeoutMilliseconds: 100,
      resolver: new RecordingCredentialResolver({ API_TOKEN: "super-secret-value" }),
      environmentReferences: ["API_TOKEN"],
      clientCall: () => new Promise(() => undefined),
    });

    const result = await provider.invoke(request, { requestId: "request-1" });
    expect(result).toEqual({ status: "failed", reason: "process_timed_out" });
    expect(killed).toBe(1);
    expect(JSON.stringify(result)).not.toContain("super-secret-value");
  });

  it("composes a caller abort into an in-flight process call and terminates the child", async () => {
    const pluginRoot = await createTempDirectory();
    const caller = new AbortController();
    const spawner = new RecordingSpawner();
    let killed = 0;
    let complete: ((value: { exitCode: number | null; signal: string | null }) => void) | undefined;
    spawner.next = {
      stdout: (async function* () {})(),
      stderr: (async function* () {})(),
      completion: new Promise((resolve) => { complete = resolve; }),
      kill: () => {
        killed += 1;
        complete?.({ exitCode: null, signal: "SIGTERM" });
      },
    };
    let markStarted: (() => void) | undefined;
    const started = new Promise<void>((resolve) => { markStarted = resolve; });
    let callSettled = false;
    const provider = await processProvider({
      pluginRoot,
      spawner,
      timeoutMilliseconds: 1_000,
      clientFactory: {
        create: () => ({
          connect: async () => undefined,
          callTool: async (_input, options?: { readonly signal: AbortSignal }) => {
            markStarted?.();
            const signal = options?.signal;
            await new Promise<void>((resolve) => {
              if (signal?.aborted === true) resolve();
              else signal?.addEventListener("abort", () => resolve(), { once: true });
            });
            callSettled = true;
            throw new DOMException("aborted", "AbortError");
          },
          close: async () => undefined,
        }),
      },
    });
    const context = Object.freeze({ requestId: "request-1", signal: caller.signal }) as ProviderContext;
    const invocation = provider.invoke(request, context);
    const stage = await Promise.race([
      started.then(() => "started" as const),
      invocation.then(() => "ended" as const, () => "ended" as const),
    ]);
    expect(stage).toBe("started");
    caller.abort();
    await expect(invocation).resolves.toEqual({ status: "failed", reason: "process_timed_out" });
    expect(callSettled).toBe(true);
    expect(killed).toBe(1);
  });

  it("terminates a child returned by a spawn promise after the invocation deadline", async () => {
    const pluginRoot = await createTempDirectory();
    let markSpawnEntered: (() => void) | undefined;
    const spawnEntered = new Promise<void>((resolve) => { markSpawnEntered = resolve; });
    let releaseSpawn: ((process: SpawnedProcess) => void) | undefined;
    const delayedSpawn = new Promise<SpawnedProcess>((resolve) => { releaseSpawn = resolve; });
    let killed = 0;
    let complete: ((value: { exitCode: number | null; signal: string | null }) => void) | undefined;
    const child: SpawnedProcess = {
      stdout: (async function* () {})(),
      stderr: (async function* () {})(),
      completion: new Promise((resolve) => { complete = resolve; }),
      kill: () => {
        killed += 1;
        complete?.({ exitCode: null, signal: "SIGTERM" });
      },
    };
    const spawner: ProcessSpawner = {
      async spawn(_command, _args, options) {
        await assertSafeSpawnIdentity(options);
        markSpawnEntered?.();
        return delayedSpawn;
      },
    };
    const provider = await processProvider({
      pluginRoot,
      spawner,
      timeoutMilliseconds: 250,
    });

    const invocation = provider.invoke(request, { requestId: "request-1" });
    await spawnEntered;
    await expect(invocation).resolves.toEqual({ status: "failed", reason: "process_timed_out" });
    expect(killed).toBe(0);
    releaseSpawn?.(child);
    await new Promise((resolve) => setTimeout(resolve, 25));
    expect(killed).toBe(1);
  });

  it("does not let hanging client and stderr cleanup defeat the process timeout", async () => {
    const pluginRoot = await createTempDirectory();
    const spawner = new RecordingSpawner();
    let killed = 0;
    let complete: ((value: { exitCode: number | null; signal: string | null }) => void) | undefined;
    spawner.next = {
      stdout: (async function* () {})(),
      stderr: (async function* () { await new Promise(() => undefined); })(),
      completion: new Promise((resolve) => { complete = resolve; }),
      kill: () => {
        killed += 1;
        complete?.({ exitCode: null, signal: "SIGTERM" });
      },
    };
    const provider = await processProvider({
      pluginRoot,
      spawner,
      timeoutMilliseconds: 100,
      clientFactory: {
        create: () => ({
          connect: async () => undefined,
          callTool: async () => new Promise(() => undefined),
          close: async () => new Promise(() => undefined),
        }),
      },
    });

    const outcome = await Promise.race([
      provider.invoke(request, { requestId: "request-1" }),
      new Promise<"test_timeout">((resolve) => setTimeout(() => resolve("test_timeout"), 500)),
    ]);
    expect(outcome).not.toBe("test_timeout");
    expect(outcome).toEqual({ status: "failed", reason: "process_timed_out" });
    expect(killed).toBe(1);
  });

  it("bounds and redacts output while retaining full-output digests", async () => {
    const pluginRoot = await createTempDirectory();
    const spawner = new RecordingSpawner();
    spawner.next = {
      stdout: (async function* () { yield Buffer.from("0123456789-super-secret-value"); })(),
      stderr: (async function* () { yield Buffer.from("error-super-secret-value"); })(),
      completion: Promise.resolve({ exitCode: 0, signal: null }),
      kill: () => undefined,
    };
    const provider = await processProvider({
      pluginRoot,
      spawner,
      resolver: new RecordingCredentialResolver({ API_TOKEN: "super-secret-value" }),
      environmentReferences: ["API_TOKEN"],
      maxOutputBytes: 12,
    });

    const result = await provider.invoke(request, { requestId: "request-1" });
    expect(result.status).toBe("success");
    expect(JSON.stringify(result)).not.toContain("super-secret-value");
    if (result.status !== "success") throw new Error("expected success");
    expect(result.output).toMatchObject({ stderr: { truncated: true } });
    expect(JSON.stringify(result.output)).toMatch(/[a-f0-9]{64}/);
    expect(JSON.stringify(result.output)).not.toContain("error-");
  });

  it("kills and sanitizes when stream collection fails", async () => {
    const pluginRoot = await createTempDirectory();
    const spawner = new RecordingSpawner();
    let killed = 0;
    spawner.next = {
      stdout: (async function* () {})(),
      stderr: (async function* () { throw new Error("stream super-secret-value"); })(),
      completion: Promise.resolve({ exitCode: 1, signal: null }),
      kill: () => { killed += 1; },
    };
    const provider = await processProvider({ pluginRoot, spawner });

    const result = await provider.invoke(request, { requestId: "request-1" });
    expect(result).toEqual({ status: "failed", reason: "process_failed" });
    expect(killed).toBe(1);
    expect(JSON.stringify(result)).not.toContain("super-secret-value");
  });

  it("bounds both stdout and stderr in the shared safe process boundary", async () => {
    const trustedStore = await createTempDirectory();
    const executionRoot = join(trustedStore, "content");
    await mkdir(executionRoot);
    const trustedStoreIdentity = await snapshotDirectoryIdentity(trustedStore);
    const identity = await snapshotDirectoryIdentity(executionRoot);
    const inspection = await inspectDigestTree(executionRoot);
    const spawner = new RecordingSpawner();
    spawner.next = {
      stdout: (async function* () { yield Buffer.from("stdout-over-limit"); })(),
      stderr: (async function* () { yield Buffer.from("stderr-over-limit"); })(),
      completion: Promise.resolve({ exitCode: 0, signal: null }),
      kill: () => undefined,
    };

    const result = await executeSafeProcess({
      spawner,
      command: "node",
      args: [],
      spawnOptions: {
        shell: false,
        cwd: executionRoot,
        env: {},
        executionRootIdentity: identity,
        trustedStoreIdentity,
        workingDirectoryIdentity: identity,
        componentDigest: PROCESS_DIGEST,
        executionRootDigest: inspection.digest,
        executionRootInventory: inspection.inventory,
        signal: new AbortController().signal,
      },
      timeoutMilliseconds: 100,
      maxOutputBytes: 6,
    });
    expect(result).toMatchObject({
      status: "success",
      stdout: { text: "stdout", truncated: true },
      stderr: { text: "stderr", truncated: true },
    });
    expect(JSON.stringify(result)).toMatch(/[a-f0-9]{64}/);
  });

  it("does not call the OS spawn primitive when abort arrives after final async verification", async () => {
    const trustedStore = await createTempDirectory();
    const executionRoot = join(trustedStore, "content");
    await mkdir(executionRoot);
    const trustedStoreIdentity = await snapshotDirectoryIdentity(trustedStore);
    const identity = await snapshotDirectoryIdentity(executionRoot);
    const inspection = await inspectDigestTree(executionRoot);
    const controller = new AbortController();
    const spawnOptions: SafeSpawnOptions = {
      shell: false,
      cwd: executionRoot,
      env: {},
      executionRootIdentity: identity,
      trustedStoreIdentity,
      workingDirectoryIdentity: identity,
      componentDigest: PROCESS_DIGEST,
      executionRootDigest: inspection.digest,
      executionRootInventory: inspection.inventory,
      signal: controller.signal,
    };
    const abortedDescriptor = Object.getOwnPropertyDescriptor(AbortSignal.prototype, "aborted");
    if (abortedDescriptor?.get === undefined) throw new Error("AbortSignal getter unavailable");
    const originalAborted = abortedDescriptor.get;
    let reads = 0;
    const abortedSpy = vi.spyOn(AbortSignal.prototype, "aborted", "get")
      .mockImplementation(function(this: AbortSignal): boolean {
        reads += 1;
        return Reflect.apply(originalAborted, this, []) as boolean;
      });
    try {
      await assertSafeSpawnIdentity(spawnOptions);
      const verificationReads = reads;
      reads = 0;
      abortedSpy.mockImplementation(function(this: AbortSignal): boolean {
        reads += 1;
        if (reads === verificationReads + 1) controller.abort();
        return Reflect.apply(originalAborted, this, []) as boolean;
      });
      let operatingSystemSpawnCalls = 0;
      type SpawnPrimitive = NonNullable<ConstructorParameters<typeof NodeProcessSpawner>[0]>;
      const fakeOperatingSystemSpawn = (() => {
        operatingSystemSpawnCalls += 1;
        throw new Error("unexpected OS spawn");
      }) as unknown as SpawnPrimitive;
      const spawner = new NodeProcessSpawner(fakeOperatingSystemSpawn);

      await expect(spawner.spawn("node", [], spawnOptions))
        .rejects.toThrow("process_spawn_aborted");
      expect(operatingSystemSpawnCalls).toBe(0);
      expect(reads).toBe(verificationReads + 1);
    } finally {
      abortedSpy.mockRestore();
    }
  });

  it("observes a rejecting child completion before the first kill can throw", async () => {
    let rejectCompletion: ((reason: Error) => void) | undefined;
    const completion = new Promise<{ exitCode: number | null; signal: string | null }>((_resolve, reject) => {
      rejectCompletion = reject;
    });
    const child: SpawnedProcess = {
      stdout: (async function* () {})(),
      stderr: (async function* () {})(),
      completion,
      kill: () => { throw new Error("kill failed"); },
    };
    const unhandled: unknown[] = [];
    const captureUnhandled = (reason: unknown): void => { unhandled.push(reason); };
    process.on("unhandledRejection", captureUnhandled);
    try {
      const termination = Promise.race([
        terminateSpawnedProcess(child),
        new Promise<"test_timeout">((resolve) => {
          const timer = setTimeout(() => resolve("test_timeout"), 100);
          timer.unref?.();
        }),
      ]);
      await expect(termination).resolves.toBeUndefined();
      rejectCompletion?.(new Error("completion failed"));
      await new Promise((resolve) => setTimeout(resolve, 25));
      expect(unhandled).toEqual([]);
    } finally {
      process.off("unhandledRejection", captureUnhandled);
      void completion.catch(() => undefined);
    }
  });

  it("sanitizes a synchronous spawner failure", async () => {
    const pluginRoot = await createTempDirectory();
    const spawner = new RecordingSpawner();
    spawner.spawn = () => {
      throw new Error("spawn super-secret-value");
    };
    const provider = await processProvider({ pluginRoot, spawner });

    const result = await provider.invoke(request, { requestId: "request-1" });
    expect(result).toEqual({ status: "failed", reason: "process_failed" });
    expect(JSON.stringify(result)).not.toContain("super-secret-value");
  });

  it("kills the child and sanitizes a process client factory failure", async () => {
    const pluginRoot = await createTempDirectory();
    const spawner = new RecordingSpawner();
    let killed = 0;
    spawner.next = {
      stdout: (async function* () {})(),
      stderr: (async function* () {})(),
      completion: Promise.resolve({ exitCode: 1, signal: null }),
      kill: () => { killed += 1; },
    };
    const provider = await processProvider({
      pluginRoot,
      spawner,
      clientFactory: { create: () => { throw new Error("factory super-secret-value"); } },
    });

    const result = await provider.invoke(request, { requestId: "request-1" });
    expect(result).toEqual({ status: "failed", reason: "process_failed" });
    expect(killed).toBe(1);
    expect(JSON.stringify(result)).not.toContain("super-secret-value");
  });
});
