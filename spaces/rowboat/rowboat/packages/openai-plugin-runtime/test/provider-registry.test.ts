import { describe, expect, it } from "vitest";
import {
  MAX_PROCESS_TIMEOUT_MS,
  ProviderRegistry,
  assertBinding,
  createTemporaryAdapterBindings,
  normalizeApp,
  normalizeMcpServer,
  pairAppAndMcp,
  type PluginProvider,
  type ProviderBinding,
  type ProviderDescriptor,
  type ProviderResult,
} from "../src/index.js";

const DIGEST_A = "a".repeat(64);
const DIGEST_B = "b".repeat(64);

function createProvider(id: string, kind: ProviderDescriptor["kind"] = "rowboat-native") {
  let calls = 0;
  const provider: PluginProvider = {
    id,
    describe: () => Object.freeze({ id, kind, temporaryAdapter: false }),
    invoke: async (request): Promise<ProviderResult> => {
      calls += 1;
      return Object.freeze({ status: "success", output: request.arguments });
    },
  };
  return { provider, get calls(): number { return calls; } };
}

function binding(id: string, providerKind: ProviderDescriptor["kind"] = "rowboat-native"): ProviderBinding {
  return Object.freeze({
    id,
    providerKind,
    componentDigest: DIGEST_A,
  });
}

describe("ProviderRegistry", () => {
  it("returns unavailable without invoking a fallback for an unknown exact binding", async () => {
    const nativeProvider = createProvider("native.search");
    const registry = new ProviderRegistry();
    registry.register(binding("binding.search"), nativeProvider.provider);

    const result = registry.resolve(binding("binding.unknown"));

    expect(result).toEqual({ status: "unavailable", reason: "provider_unavailable" });
    expect(nativeProvider.calls).toBe(0);
  });

  it("resolves only the exact registered binding ID and matching descriptor", () => {
    const admitted = createProvider("native.search");
    const registry = new ProviderRegistry();
    registry.register(binding("binding.search"), admitted.provider);

    const resolved = registry.resolve(binding("binding.search"));
    expect(resolved).toMatchObject({ status: "available" });
    if (resolved.status !== "available") throw new Error("expected available provider");
    expect(resolved.provider).not.toBe(admitted.provider);
    expect(resolved.provider.id).toBe("native.search");
    expect(registry.resolve({ ...binding("binding.search"), componentDigest: DIGEST_B }))
      .toEqual({ status: "unavailable", reason: "provider_unavailable" });
  });

  it("rejects duplicate binding IDs and invalid registrations", () => {
    const registry = new ProviderRegistry();
    registry.register(binding("binding.search"), createProvider("native.search").provider);

    expect(() => registry.register(binding("binding.search"), createProvider("other").provider))
      .toThrow("provider_duplicate:binding.search");
    expect(() => registry.register({ ...binding("binding.search"), id: " bad " }, createProvider("other").provider))
      .toThrow("provider_invalid:binding_id");
    expect(() => registry.register({ ...binding("binding.search"), componentDigest: "abc" }, createProvider("other").provider))
      .toThrow("provider_invalid:component_digest");
    expect(() => registry.register({ ...binding("binding.search"), providerKind: "invented" as "rowboat-native" }, createProvider("other").provider))
      .toThrow("provider_invalid:provider_kind");
  });

  it("snapshots an immutable provider facade when the caller mutates the provider boundary", async () => {
    const descriptor: { id: string; kind: ProviderDescriptor["kind"]; temporaryAdapter: boolean } = {
      id: "native.mutable",
      kind: "rowboat-native",
      temporaryAdapter: false,
    };
    let originalCalls = 0;
    let replacementCalls = 0;
    const provider = {
      id: "native.mutable",
      describe: (): ProviderDescriptor => descriptor,
      invoke: async (): Promise<ProviderResult> => {
        originalCalls += 1;
        return { status: "success", output: { source: "original" } };
      },
    };
    const registry = new ProviderRegistry();
    registry.register(binding("binding.mutable"), provider);

    provider.id = "mutated.id";
    descriptor.id = "mutated.descriptor";
    descriptor.kind = "mcp-process";
    descriptor.temporaryAdapter = true;
    provider.invoke = async (): Promise<ProviderResult> => {
      replacementCalls += 1;
      return { status: "failed", reason: "replacement" };
    };

    const resolution = registry.resolve(binding("binding.mutable"));
    if (resolution.status !== "available") throw new Error("expected available provider");
    expect(Object.isFrozen(resolution.provider)).toBe(true);
    expect(resolution.provider.id).toBe("native.mutable");
    expect(resolution.provider.describe()).toEqual({
      id: "native.mutable",
      kind: "rowboat-native",
      temporaryAdapter: false,
    });
    expect(Object.isFrozen(resolution.provider.describe())).toBe(true);
    expect(await resolution.provider.invoke({
      projectId: "project-1",
      pluginName: "search",
      componentName: "search",
      capability: "read",
      arguments: {},
    }, { requestId: "request-1" })).toEqual({ status: "success", output: { source: "original" } });
    expect(originalCalls).toBe(1);
    expect(replacementCalls).toBe(0);
  });

  it("captures the admitted descriptor exactly once during registration", () => {
    let describeCalls = 0;
    const provider: PluginProvider = {
      id: "native.once",
      describe: () => {
        describeCalls += 1;
        return describeCalls === 1
          ? { id: "native.once", kind: "rowboat-native", temporaryAdapter: false }
          : { id: "native.once", kind: "openai-connector-bridge", temporaryAdapter: true };
      },
      invoke: async () => ({ status: "success", output: null }),
    };
    const registry = new ProviderRegistry();

    registry.register(binding("binding.once"), provider);
    const resolution = registry.resolve(binding("binding.once"));

    if (resolution.status !== "available") throw new Error("expected available provider");
    expect(describeCalls).toBe(1);
    expect(resolution.provider.describe()).toEqual({
      id: "native.once",
      kind: "rowboat-native",
      temporaryAdapter: false,
    });
  });

  it("captures accessor-backed provider metadata exactly once", () => {
    let providerIdReads = 0;
    let descriptorIdReads = 0;
    let descriptorKindReads = 0;
    let temporaryAdapterReads = 0;
    const descriptor = Object.defineProperties({}, {
      id: {
        enumerable: true,
        get: () => {
          descriptorIdReads += 1;
          return descriptorIdReads <= 2 ? "native.accessor" : "mutated.id";
        },
      },
      kind: {
        enumerable: true,
        get: () => {
          descriptorKindReads += 1;
          return descriptorKindReads === 1 ? "rowboat-native" : "openai-connector-bridge";
        },
      },
      temporaryAdapter: {
        enumerable: true,
        get: () => {
          temporaryAdapterReads += 1;
          return temporaryAdapterReads === 1 ? false : true;
        },
      },
    }) as ProviderDescriptor;
    const provider = Object.defineProperties({
      describe: (): ProviderDescriptor => descriptor,
      invoke: async (): Promise<ProviderResult> => ({ status: "success", output: null }),
    }, {
      id: {
        enumerable: true,
        get: () => {
          providerIdReads += 1;
          return providerIdReads <= 2 ? "native.accessor" : "mutated.id";
        },
      },
    }) as unknown as PluginProvider;
    const registry = new ProviderRegistry();

    registry.register(binding("binding.accessor"), provider);
    const resolution = registry.resolve(binding("binding.accessor"));

    if (resolution.status !== "available") throw new Error("expected available provider");
    expect(resolution.provider.id).toBe("native.accessor");
    expect(resolution.provider.describe()).toEqual({
      id: "native.accessor",
      kind: "rowboat-native",
      temporaryAdapter: false,
    });
    expect(providerIdReads).toBe(1);
    expect(descriptorIdReads).toBe(1);
    expect(descriptorKindReads).toBe(1);
    expect(temporaryAdapterReads).toBe(1);
  });

  it.each([
    { digests: [] },
    { digests: [DIGEST_A] },
    { digests: [DIGEST_A, DIGEST_B, "c".repeat(64)] },
  ])("rejects a runtime paired-digest array with invalid length %#", ({ digests }) => {
    const malformed = {
      ...binding("binding.tuple"),
      pairedComponentDigests: digests,
    } as unknown as ProviderBinding;
    const registry = new ProviderRegistry();

    expect(() => assertBinding(malformed)).toThrow(/provider_invalid/);
    expect(() => registry.register(malformed, createProvider("native.search").provider))
      .toThrow(/provider_invalid/);
    expect(registry.resolve(malformed)).toEqual({
      status: "unavailable",
      reason: "provider_unavailable",
    });
  });

  it.each([
    { label: "numeric object", value: { 0: DIGEST_A, 1: DIGEST_B } },
    { label: "array-like object", value: { 0: DIGEST_A, 1: DIGEST_B, length: 2 } },
  ])("rejects a non-array paired-digest $label", ({ value }) => {
    const malformed = {
      ...binding("binding.array-like"),
      pairedComponentDigests: value,
    } as unknown as ProviderBinding;
    const registry = new ProviderRegistry();

    expect(() => assertBinding(malformed)).toThrow(/provider_invalid/);
    expect(() => registry.register(malformed, createProvider("native.search").provider))
      .toThrow(/provider_invalid/);
    expect(registry.resolve(malformed)).toEqual({
      status: "unavailable",
      reason: "provider_unavailable",
    });
  });
});

describe("component provider normalizers", () => {
  it("normalizes HTTP and process MCP servers to exact provider kinds", () => {
    expect(normalizeMcpServer("remote", {
      type: "http",
      url: "https://example.com/mcp",
      oauth_resource: "https://example.com/oauth",
    }, DIGEST_A)).toEqual({
      name: "remote",
      kind: "mcp-http",
      componentDigest: DIGEST_A,
      url: "https://example.com/mcp",
      oauthResource: "https://example.com/oauth",
    });

    expect(normalizeMcpServer("local", {
      command: "node",
      args: ["server.js"],
      env_vars: ["API_TOKEN"],
      tool_timeout_sec: 15,
    }, DIGEST_B)).toEqual({
      name: "local",
      kind: "mcp-process",
      componentDigest: DIGEST_B,
      command: "node",
      args: ["server.js"],
      environmentReferences: ["API_TOKEN"],
      timeoutMilliseconds: 15_000,
    });
    expect(MAX_PROCESS_TIMEOUT_MS).toBe(300_000);
    expect(normalizeMcpServer("bounded", {
      command: "node",
      tool_timeout_sec: 300,
    }, DIGEST_A)).toMatchObject({ timeoutMilliseconds: MAX_PROCESS_TIMEOUT_MS });
  });

  it("gives an HTTP MCP server with no credential declaration its own URL as the OAuth resource", () => {
    // Every executable HTTP MCP server in the pinned catalog is auth-gated at
    // the provider, so "declares nothing" must not mean "call unauthenticated":
    // it means the server is an OAuth-protected resource at its own URL, and
    // resolution fails closed unless the operator has provisioned that
    // reference. A genuinely open server needs an explicit declaration.
    expect(normalizeMcpServer("bare", {
      type: "http",
      url: "https://mcp.cloudflare.com/mcp",
    }, DIGEST_A)).toEqual({
      name: "bare",
      kind: "mcp-http",
      componentDigest: DIGEST_A,
      url: "https://mcp.cloudflare.com/mcp",
      oauthResource: "https://mcp.cloudflare.com/mcp",
    });
  });

  it("does not add the OAuth fallback when a bearer token reference is declared", () => {
    expect(normalizeMcpServer("declared", {
      type: "http",
      url: "https://api.example.com/mcp",
      bearer_token_env_var: "API_TOKEN",
    }, DIGEST_A)).toEqual({
      name: "declared",
      kind: "mcp-http",
      componentDigest: DIGEST_A,
      url: "https://api.example.com/mcp",
      bearerTokenReference: "API_TOKEN",
    });
  });

  it("normalizes an app only to its exact connector ID", () => {
    expect(normalizeApp("drive", { id: "connector_abcdef", capabilities: ["read"] }, DIGEST_A))
      .toEqual({
        name: "drive",
        kind: "app",
        connectorId: "connector_abcdef",
        capabilities: ["read"],
        componentDigest: DIGEST_A,
      });
  });

  it.each([
    () => normalizeApp("drive", { id: "drive" }, DIGEST_A),
    () => normalizeApp("drive", { id: "connector_abcdef" }, "not-a-digest"),
    () => normalizeMcpServer("remote", { type: "http", url: "file:///secret" }, DIGEST_A),
    () => normalizeMcpServer("local", { command: " " }, DIGEST_A),
    () => normalizeMcpServer("local", { command: "node", env: { TOKEN: "secret" } }, DIGEST_A),
    () => normalizeMcpServer("local", { command: "node", tool_timeout_sec: 0 }, DIGEST_A),
    () => normalizeMcpServer("local", { command: "node", tool_timeout_sec: -1 }, DIGEST_A),
    () => normalizeMcpServer("local", { command: "node", tool_timeout_sec: Number.POSITIVE_INFINITY }, DIGEST_A),
    () => normalizeMcpServer("local", { command: "node", tool_timeout_sec: 1.5 }, DIGEST_A),
    () => normalizeMcpServer("local", { command: "node", tool_timeout_sec: Number.MAX_VALUE }, DIGEST_A),
    () => normalizeMcpServer("local", { command: "node", tool_timeout_sec: Number.MAX_SAFE_INTEGER }, DIGEST_A),
    () => normalizeMcpServer("local", { command: "node", tool_timeout_sec: 301 }, DIGEST_A),
  ])("fails closed for invalid app or MCP input", (operation) => {
    expect(operation).toThrow(/component_invalid/);
  });

  it.each([
    "/absolute",
    "C:\\absolute",
    "C:drive-relative",
    "C:..\\outside",
    "\\\\server\\share",
    "\\rooted",
    "safe/../outside",
    "safe\\..\\outside",
  ])("rejects cross-platform process cwd escape %s", (cwd) => {
    expect(() => normalizeMcpServer("local", { command: "node", cwd }, DIGEST_A))
      .toThrow("component_invalid:process_cwd");
  });

  it("normalizes safe process cwd separators and dot segments", () => {
    expect(normalizeMcpServer("local", {
      command: "node",
      cwd: "safe\\nested/./child",
    }, DIGEST_A)).toMatchObject({ workingDirectory: "safe/nested/child" });
  });

  it("rejects HTTP query values without leaking literal secrets", () => {
    const unsafeUrls = [
      "https://example.com/mcp?api_key=super-secret-value",
      "https://user:super-secret-value@example.com/mcp",
      "https://example.com/mcp#super-secret-value",
    ];
    for (const url of unsafeUrls) {
      const operation = (): void => {
        normalizeMcpServer("remote", { type: "http", url }, DIGEST_A);
      };
      expect(operation).toThrow("component_invalid:mcp_url");
      try {
        operation();
      } catch (error: unknown) {
        const message = error instanceof Error ? error.message : String(error);
        expect(message).not.toContain("api_key");
        expect(message).not.toContain("super-secret-value");
      }
    }
  });

  it("pairs an app and MCP only through explicit policy carrying both digests", () => {
    const app = normalizeApp("search", { id: "connector_abcdef" }, DIGEST_A);
    const mcp = normalizeMcpServer("differently-named-gateway", { type: "http", url: "https://example.com/mcp" }, DIGEST_B);

    expect(() => pairAppAndMcp(app, mcp, undefined)).toThrow("provider_binding_required");
    expect(() => pairAppAndMcp(app, mcp, {
      id: "binding.search",
      providerKind: "mcp-http",
      componentDigest: DIGEST_A,
      pairedComponentDigests: [DIGEST_B, DIGEST_A],
    })).toThrow("provider_invalid:paired_component_digests");
    expect(pairAppAndMcp(app, mcp, {
      id: "binding.search",
      providerKind: "mcp-http",
      componentDigest: DIGEST_A,
      pairedComponentDigests: [DIGEST_A, DIGEST_B],
    })).toEqual({
      id: "binding.search",
      providerKind: "mcp-http",
      componentDigest: DIGEST_A,
      pairedComponentDigests: [DIGEST_A, DIGEST_B],
    });
  });
});

describe("temporary adapter bindings", () => {
  it("defines frozen exact bindings for every migration-only adapter", () => {
    const bindings = createTemporaryAdapterBindings({
      reddit: DIGEST_A,
      xTwitter: DIGEST_A,
      googleDrive: DIGEST_A,
      googleSheets: DIGEST_A,
      admittedSearch: DIGEST_A,
      mockTools: DIGEST_A,
    });

    expect(bindings.map((item) => item.id)).toEqual([
      "temporary.reddit",
      "temporary.x-twitter",
      "temporary.google-drive",
      "temporary.google-sheets",
      "temporary.admitted-search",
      "temporary.mock-tools",
    ]);
    expect(bindings.every((item) => item.temporaryAdapter === true)).toBe(true);
    expect(bindings.every((item) => item.providerKind === "legacy-composio-adapter" || item.providerKind === "rowboat-native"))
      .toBe(true);
    expect(Object.isFrozen(bindings)).toBe(true);
    expect(bindings.every(Object.isFrozen)).toBe(true);
  });

  it("represents the unavailable OpenAI connector bridge without exposing a fallback", () => {
    const registry = new ProviderRegistry();
    expect(() => registry.register(
      binding("openai-connector-bridge", "openai-connector-bridge"),
      createProvider("openai-connector-bridge", "openai-connector-bridge").provider,
    )).toThrow("provider_unavailable:openai-connector-bridge");
    expect(registry.resolve(binding("openai-connector-bridge", "openai-connector-bridge")))
      .toEqual({ status: "unavailable", reason: "provider_unavailable" });
  });
});
