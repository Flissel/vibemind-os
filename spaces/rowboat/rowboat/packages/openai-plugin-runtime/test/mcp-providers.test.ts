import { mkdir, readFile, symlink } from "node:fs/promises";
import { join } from "node:path";
import { afterEach, describe, expect, it } from "vitest";

import {
  DEFAULT_POLICY,
  HttpMcpProvider,
  McpCompatibilityError,
  ProcessMcpProvider,
  ProviderRegistry,
  createSecretValue,
  executeSafeProcess,
  type CredentialReference,
  type CredentialResolver,
  type HttpMcpClient,
  type HttpMcpTransportFactory,
  type PluginPolicy,
  type ProcessMcpClientFactory,
  type ProcessSpawner,
  type ProviderBinding,
  type ProviderRequest,
  type SpawnedProcess,
} from "../src/index.js";
import {
  cleanupRegisteredTestRoots,
  createOwnedTestRoot,
} from "./test-temp.js";

const HTTP_DIGEST = "a".repeat(64);
const PROCESS_DIGEST = "b".repeat(64);
async function createTempDirectory(): Promise<string> {
  return createOwnedTestRoot("mcp-providers");
}

afterEach(cleanupRegisteredTestRoots);

const request: ProviderRequest = Object.freeze({
  projectId: "project-1",
  pluginName: "example-plugin",
  componentName: "search",
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
    expect(input).toEqual({ name: "search", arguments: { query: "safe query" } });
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
  return new HttpMcpProvider({
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
  });
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
      policy: DEFAULT_POLICY,
      credentialResolver: new RecordingCredentialResolver(),
      clientFactory: { create: () => { throw new Error("factory super-secret-value"); } },
      transportFactory: { create: (input) => Object.freeze({ kind: input.kind }) },
    });

    const result = await provider.invoke(request, { requestId: "request-1" });
    expect(result).toEqual({ status: "failed", reason: "mcp_http_failed" });
    expect(JSON.stringify(result)).not.toContain("super-secret-value");
  });
});

class RecordingSpawner implements ProcessSpawner {
  readonly calls: Array<{
    command: string;
    args: readonly string[];
    options: { readonly shell: false; readonly cwd: string; readonly env: Readonly<Record<string, string>> };
  }> = [];
  next: SpawnedProcess = {
    stdout: (async function* () { yield Buffer.from("ok"); })(),
    stderr: (async function* () {})(),
    completion: Promise.resolve({ exitCode: 0, signal: null }),
    kill: () => undefined,
  };

  spawn(command: string, args: readonly string[], options: {
    readonly shell: false;
    readonly cwd: string;
    readonly env: Readonly<Record<string, string>>;
  }): SpawnedProcess {
    this.calls.push({ command, args, options });
    return this.next;
  }
}

const PROCESS_POLICY: PluginPolicy = Object.freeze({
  ...DEFAULT_POLICY,
  allowProcessMcp: true,
});

function processProvider(options: {
  readonly pluginRoot: string;
  readonly spawner: RecordingSpawner;
  readonly resolver?: CredentialResolver;
  readonly policy?: PluginPolicy;
  readonly workingDirectory?: string;
  readonly environmentReferences?: readonly string[];
  readonly timeoutMilliseconds?: number;
  readonly maxOutputBytes?: number;
  readonly clientCall?: () => Promise<unknown>;
  readonly clientFactory?: ProcessMcpClientFactory;
}): ProcessMcpProvider {
  return new ProcessMcpProvider({
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
    pluginRoot: options.pluginRoot,
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
  });
}

describe("process MCP provider", () => {
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
      pluginRoot,
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
      pluginRoot,
      parentLicense: "MIT",
      policy: PROCESS_POLICY,
      credentialResolver: new RecordingCredentialResolver(),
      spawner,
      clientFactory: {
        create: () => ({
          connect: async () => { connectCalls += 1; },
          callTool: async (input: unknown) => {
            callToolCalls += 1;
            expect(input).toEqual({ name: "search", arguments: { query: "safe query" } });
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
    const provider = processProvider({
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

  it("does not spawn when an exact credential reference is missing", async () => {
    const pluginRoot = await createTempDirectory();
    const spawner = new RecordingSpawner();
    const provider = processProvider({
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
    await symlink(outside, join(pluginRoot, "linked"), "junction");
    const spawner = new RecordingSpawner();
    const provider = processProvider({ pluginRoot, spawner, workingDirectory: "linked" });

    await expect(provider.invoke(request, { requestId: "request-1" }))
      .rejects.toThrow("path_escape");
    expect(spawner.calls).toHaveLength(0);
  });

  it("spawns exact command and args with shell false and only fixed baseline plus resolved env", async () => {
    const pluginRoot = await createTempDirectory();
    await mkdir(join(pluginRoot, "server"));
    const spawner = new RecordingSpawner();
    const resolver = new RecordingCredentialResolver({ API_TOKEN: "super-secret-value" });
    const provider = processProvider({
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
    const provider = processProvider({
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
    const provider = processProvider({
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
      new Promise<"test_timeout">((resolve) => setTimeout(() => resolve("test_timeout"), 100)),
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
    const provider = processProvider({
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
    const provider = processProvider({ pluginRoot, spawner });

    const result = await provider.invoke(request, { requestId: "request-1" });
    expect(result).toEqual({ status: "failed", reason: "process_failed" });
    expect(killed).toBe(1);
    expect(JSON.stringify(result)).not.toContain("super-secret-value");
  });

  it("bounds both stdout and stderr in the shared safe process boundary", async () => {
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
      spawnOptions: { shell: false, cwd: ".", env: {} },
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
    const provider = processProvider({ pluginRoot, spawner });

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
    const provider = processProvider({
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
