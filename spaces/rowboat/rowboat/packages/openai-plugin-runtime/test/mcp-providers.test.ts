import { mkdir, readFile, rename, symlink, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { afterEach, describe, expect, it } from "vitest";

import {
  DEFAULT_POLICY,
  HttpMcpProvider,
  McpCompatibilityError,
  ProcessMcpProvider,
  ProviderRegistry,
  assertSafeSpawnIdentity,
  createSecretValue,
  digestTree,
  executeSafeProcess,
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

const HTTP_DIGEST = "a".repeat(64);
const PROCESS_DIGEST = "b".repeat(64);
const READ_HTTP_OPERATIONS = Object.freeze([Object.freeze({
  operationName: "query",
  capability: "read" as const,
  componentDigest: HTTP_DIGEST,
  policyVersion: DEFAULT_POLICY.version,
})]);
const READ_PROCESS_OPERATIONS = Object.freeze([Object.freeze({
  operationName: "query",
  capability: "read" as const,
  componentDigest: PROCESS_DIGEST,
  policyVersion: DEFAULT_POLICY.version,
})]);
async function createTempDirectory(): Promise<string> {
  return createOwnedTestRoot("mcp-providers");
}

async function createVerifiedProcessRoot(pluginRoot: string) {
  return verifyProcessExecutionRoot(
    Object.freeze({ path: pluginRoot, digest: await digestTree(pluginRoot) }),
    PROCESS_DIGEST,
  );
}

afterEach(cleanupRegisteredTestRoots);

const request: ProviderRequest = Object.freeze({
  projectId: "project-1",
  pluginName: "example-plugin",
  componentName: "search",
  operationName: "query",
  capability: "read",
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
    policy: DEFAULT_POLICY,
    credentialResolver: resolver,
    clientFactory: { create: () => client },
    transportFactory,
    operations: READ_HTTP_OPERATIONS,
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
    policy: DEFAULT_POLICY,
    operations: READ_HTTP_OPERATIONS,
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
      policy: DEFAULT_POLICY,
      operations: READ_HTTP_OPERATIONS,
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
      operations: READ_HTTP_OPERATIONS,
      credentialResolver: resolver,
      clientFactory: { create: () => client },
      transportFactory: { create: (input: { readonly kind: "streamable-http" | "sse" }) => Object.freeze({ kind: input.kind }) },
    };
    const provider = new HttpMcpProvider(options);

    await expect(provider.invoke({
      ...request,
      operationName: "admin_delete",
      capability: "read",
    }, { requestId: "request-1" })).rejects.toThrow("provider_invalid:operation_not_admitted");
    expect(resolver.calls).toHaveLength(0);
    expect(client.connectTransports).toHaveLength(0);
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
      operations: READ_HTTP_OPERATIONS,
    };
    const provider = new HttpMcpProvider(options);

    await expect(provider.invoke({ ...request, capability: "write" }, { requestId: "request-1" }))
      .rejects.toThrow("provider_invalid:capability_mismatch");
    expect(client.connectTransports).toHaveLength(0);
  });

  it("rejects accessor-backed operation provenance without invoking the accessor", () => {
    let accessorCalls = 0;
    const accessorBinding = {
      get operationName(): string {
        accessorCalls += 1;
        return "query";
      },
      capability: "read" as const,
      componentDigest: HTTP_DIGEST,
      policyVersion: DEFAULT_POLICY.version,
    };
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
      policy: DEFAULT_POLICY,
      operations: [accessorBinding],
      credentialResolver: new RecordingCredentialResolver(),
    };

    expect(() => new HttpMcpProvider(options)).toThrow("provider_invalid:operation_bindings");
    expect(accessorCalls).toBe(0);
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
      operations: READ_HTTP_OPERATIONS,
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
      policy: DEFAULT_POLICY,
      operations: READ_HTTP_OPERATIONS,
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
      policy: DEFAULT_POLICY,
      operations: READ_HTTP_OPERATIONS,
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
      operations: READ_HTTP_OPERATIONS,
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
      operations: READ_HTTP_OPERATIONS,
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
      policy: DEFAULT_POLICY,
      operations: READ_HTTP_OPERATIONS,
      credentialResolver: new RecordingCredentialResolver(),
      clientFactory: { create: () => { throw new Error("factory super-secret-value"); } },
      transportFactory: { create: (input) => Object.freeze({ kind: input.kind }) },
    });

    const result = await provider.invoke(request, { requestId: "request-1" });
    expect(result).toEqual({ status: "failed", reason: "mcp_http_failed" });
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
      policy: DEFAULT_POLICY,
      operations: READ_HTTP_OPERATIONS,
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
  readonly operations?: readonly Readonly<{
    readonly operationName: string;
    readonly capability: "read" | "write";
    readonly componentDigest: string;
    readonly policyVersion: string;
  }>[];
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
    operations: options.operations ?? READ_PROCESS_OPERATIONS,
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
        operations: READ_HTTP_OPERATIONS,
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
      const workingDirectory = join(pluginRoot, "server");
      await mkdir(workingDirectory, { recursive: true });
      const spawner = new RecordingSpawner();
      const resolver: CredentialResolver = {
        async resolve() {
          if (swapTarget === "root") {
            await rename(pluginRoot, join(base, "plugin-old"));
            await mkdir(workingDirectory, { recursive: true });
          } else if (swapTarget === "cwd") {
            await rename(workingDirectory, join(base, "server-old"));
            await mkdir(workingDirectory);
          } else {
            await writeFile(join(pluginRoot, "server.js"), "changed after verification", "utf8");
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
      });

      await expect(provider.invoke(request, { requestId: "request-1" }))
        .rejects.toThrow(swapTarget === "content" ? "provider_invalid:execution_root_changed" : "path_escape");
      expect(spawner.calls).toHaveLength(0);
    },
  );

  it("revalidates cwd identity inside the spawn boundary before an actual spawn", async () => {
    const base = await createTempDirectory();
    const pluginRoot = join(base, "plugin");
    const workingDirectory = join(pluginRoot, "server");
    await mkdir(workingDirectory, { recursive: true });
    let actualSpawnCalls = 0;
    const boundarySpawner = {
      async spawn(_command: string, _args: readonly string[], options: unknown): Promise<SpawnedProcess> {
        await rename(workingDirectory, join(base, "server-old"));
        await mkdir(workingDirectory);
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
    const provider = await processProvider({ pluginRoot, spawner: boundarySpawner, workingDirectory: "server" });

    await expect(provider.invoke(request, { requestId: "request-1" }))
      .resolves.toEqual({ status: "failed", reason: "process_failed" });
    await new Promise((resolve) => setTimeout(resolve, 25));
    expect(actualSpawnCalls).toBe(0);
  });

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
      operations: READ_PROCESS_OPERATIONS,
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
      operations: READ_PROCESS_OPERATIONS,
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
        timeoutMilliseconds: 20,
      }),
      executionRoot: await createVerifiedProcessRoot(pluginRoot),
      parentLicense: "MIT",
      policy: PROCESS_POLICY,
      operations: READ_PROCESS_OPERATIONS,
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

  it("denies an unbound admin operation even when the caller labels it read", async () => {
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
    }, { requestId: "request-1" })).rejects.toThrow("provider_invalid:operation_not_admitted");
    expect(spawner.calls).toHaveLength(0);
  });

  it("allows a write process operation only with an exact binding and write policy", async () => {
    const pluginRoot = await createTempDirectory();
    const spawner = new RecordingSpawner();
    let invokedName: string | undefined;
    const writePolicy = Object.freeze({ ...PROCESS_POLICY, allowWriteCapabilities: true });
    const provider = await processProvider({
      pluginRoot,
      spawner,
      policy: writePolicy,
      operations: Object.freeze([Object.freeze({
        operationName: "admin_delete",
        capability: "write" as const,
        componentDigest: PROCESS_DIGEST,
        policyVersion: writePolicy.version,
      })]),
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
      timeoutMilliseconds: 5,
      resolver: new RecordingCredentialResolver({ API_TOKEN: "super-secret-value" }),
      environmentReferences: ["API_TOKEN"],
      clientCall: () => new Promise(() => undefined),
    });

    const result = await provider.invoke(request, { requestId: "request-1" });
    expect(result).toEqual({ status: "failed", reason: "process_timed_out" });
    expect(killed).toBe(1);
    expect(JSON.stringify(result)).not.toContain("super-secret-value");
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
      timeoutMilliseconds: 5,
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
      new Promise<"test_timeout">((resolve) => setTimeout(() => resolve("test_timeout"), 250)),
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
    const executionRoot = await createTempDirectory();
    const identity = await snapshotDirectoryIdentity(executionRoot);
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
        workingDirectoryIdentity: identity,
        componentDigest: PROCESS_DIGEST,
        executionRootDigest: await digestTree(executionRoot),
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
