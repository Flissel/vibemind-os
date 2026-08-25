import { describe, expect, it } from "vitest";
import {
  ProviderRegistry,
  createTemporaryAdapterBindings,
  normalizeApp,
  normalizeMcpServer,
  pairAppAndMcp,
  type PluginProvider,
  type ProviderBinding,
  type ProviderDescriptor,
  type ProviderRequest,
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

    expect(registry.resolve(binding("binding.search"))).toMatchObject({
      status: "available",
      provider: admitted.provider,
    });
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

  it("freezes provider requests and results at the contract boundary", async () => {
    const argumentsValue = Object.freeze({ query: "safe" });
    const request: ProviderRequest = Object.freeze({
      projectId: "project-1",
      pluginName: "search",
      componentName: "search",
      capability: "read",
      arguments: argumentsValue,
    });
    const provider = createProvider("native.search").provider;

    expect(Object.isFrozen(request)).toBe(true);
    expect(Object.isFrozen(await provider.invoke(request, Object.freeze({ requestId: "request-1" })))).toBe(true);
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
      timeoutSeconds: 15,
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
    () => normalizeMcpServer("local", { command: "node", tool_timeout_sec: Number.POSITIVE_INFINITY }, DIGEST_A),
  ])("fails closed for invalid app or MCP input", (operation) => {
    expect(operation).toThrow(/component_invalid/);
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
    expect(registry.resolve(binding("openai-connector-bridge", "openai-connector-bridge")))
      .toEqual({ status: "unavailable", reason: "provider_unavailable" });
  });
});
