import { describe, expect, it } from "vitest";

import {
  ConnectorBridgeProvider,
  DEFAULT_POLICY,
  ProviderRegistry,
  createSecretValue,
  deriveConnectorReference,
  type ConnectorBridgeProviderOptions,
  type CredentialReference,
  type CredentialResolver,
  type NormalizedApp,
  type PluginPolicy,
  type ProviderBinding,
  type ProviderRequest,
} from "../src/index.js";

const CONNECTOR_DIGEST = "c".repeat(64);
const WRITE_POLICY: PluginPolicy = Object.freeze({
  ...DEFAULT_POLICY,
  allowWriteCapabilities: true,
});

function binding(
  id: string,
  componentDigest: string,
): ProviderBinding {
  return Object.freeze({ id, providerKind: "openai-connector-bridge", componentDigest });
}

function canvaApp(overrides: Partial<NormalizedApp> = {}): NormalizedApp {
  return Object.freeze({
    name: "canva",
    kind: "app",
    connectorId: "connector_ab12",
    capabilities: Object.freeze(["write"] as const),
    componentDigest: CONNECTOR_DIGEST,
    ...overrides,
  });
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

// Never resolves on its own -- it only settles (by rejecting) once the
// signal it was called with fires "abort", the way a real network-backed
// resolver honors cancellation. Used to exercise the timeout/abort path
// through credential resolution without a real clock dependency.
class HangingCredentialResolver implements CredentialResolver {
  readonly calls: Array<{ reference: CredentialReference; projectId: string }> = [];

  async resolve(
    reference: CredentialReference,
    projectId: string,
    options?: Readonly<{ readonly signal: AbortSignal }>,
  ) {
    this.calls.push({ reference, projectId });
    return new Promise<never>((_resolve, reject) => {
      options?.signal.addEventListener("abort", () => reject(new Error("resolver_aborted")), { once: true });
    });
  }
}

class RecordingFetch {
  readonly calls: Array<{ url: string; init: RequestInit }> = [];
  #response: () => Response;
  constructor(response: () => Response) { this.#response = response; }
  readonly impl = (async (url: string | URL, init?: RequestInit) => {
    this.calls.push({ url: String(url), init: init ?? {} });
    return this.#response();
  }) as typeof fetch;
}

const ok = (body: unknown): Response => ({ ok: true, status: 200, json: async () => body } as Response);

const request: ProviderRequest = Object.freeze({
  projectId: "project-1",
  pluginName: "canva",
  componentName: "canva",
  operationName: "export_design",
  capability: "write",
  arguments: Object.freeze({ design_id: "d-1" }),
});

function baseOptions(resolver: CredentialResolver, fetchImpl: typeof fetch): ConnectorBridgeProviderOptions {
  return {
    id: "bridge.canva",
    binding: binding("binding.canva", CONNECTOR_DIGEST),
    app: canvaApp(),
    parentLicense: "MIT",
    policy: WRITE_POLICY,
    credentialResolver: resolver,
    fetchImpl,
  };
}

describe("deriveConnectorReference", () => {
  it("derives the CONNECTOR_ environment reference from an app name", () => {
    expect(deriveConnectorReference("canva")).toBe("CONNECTOR_CANVA");
    expect(deriveConnectorReference("monday-com")).toBe("CONNECTOR_MONDAY_COM");
  });
});

describe("ConnectorBridgeProvider", () => {
  it("invokes the Responses API with the pinned request contract and returns the mcp_call output", async () => {
    const resolver = new RecordingCredentialResolver({
      OPENAI_API_KEY: "sk-fake",
      CONNECTOR_CANVA: "tok-fake",
    });
    const fetchStub = new RecordingFetch(() => ok({
      output: [{ type: "mcp_call", name: "export_design", output: { url: "https://x" } }],
    }));
    const bridge = new ConnectorBridgeProvider(baseOptions(resolver, fetchStub.impl));

    const result = await bridge.invoke(request, { requestId: "request-1" });

    expect(result).toEqual({ status: "success", output: { url: "https://x" } });
    expect(fetchStub.calls).toHaveLength(1);
    const call = fetchStub.calls[0];
    if (call === undefined) throw new Error("expected one fetch call");
    expect(call.url).toBe("https://api.openai.com/v1/responses");
    const headers = call.init.headers as Record<string, string>;
    expect(headers.authorization).toBe("Bearer sk-fake");
    expect(headers["content-type"]).toBe("application/json");
    const body = JSON.parse(String(call.init.body)) as {
      model: string;
      store: boolean;
      tool_choice: string;
      max_output_tokens: number;
      tools: unknown[];
      input: string;
    };
    expect(body).toEqual({
      model: "gpt-5.6",
      store: false,
      tool_choice: "required",
      max_output_tokens: 1024,
      tools: [{
        type: "mcp",
        server_label: "canva",
        connector_id: "connector_ab12",
        authorization: "tok-fake",
        require_approval: "never",
        allowed_tools: ["export_design"],
      }],
      input: `Call the tool export_design exactly once with exactly these arguments, then stop: ${JSON.stringify({ design_id: "d-1" })}`,
    });
  });

  it("resolves the OpenAI API key before the connector token, both scoped to the project", async () => {
    const resolver = new RecordingCredentialResolver({
      OPENAI_API_KEY: "sk-fake",
      CONNECTOR_CANVA: "tok-fake",
    });
    const fetchStub = new RecordingFetch(() => ok({
      output: [{ type: "mcp_call", name: "export_design", output: null }],
    }));
    const bridge = new ConnectorBridgeProvider(baseOptions(resolver, fetchStub.impl));

    await bridge.invoke(request, { requestId: "request-1" });

    expect(resolver.calls).toEqual([
      { reference: { kind: "environment", reference: "OPENAI_API_KEY" }, projectId: "project-1" },
      { reference: { kind: "environment", reference: "CONNECTOR_CANVA" }, projectId: "project-1" },
    ]);
  });

  it.each([
    { label: "missing API key", values: { CONNECTOR_CANVA: "tok-fake" } },
    { label: "missing connector token", values: { OPENAI_API_KEY: "sk-fake" } },
  ])("reports credential_missing without any fetch when the $label is unavailable", async ({ values }) => {
    const resolver = new RecordingCredentialResolver(values);
    const fetchStub = new RecordingFetch(() => ok({ output: [] }));
    const bridge = new ConnectorBridgeProvider(baseOptions(resolver, fetchStub.impl));

    const result = await bridge.invoke(request, { requestId: "request-1" });

    expect(result).toEqual({ status: "failed", reason: "credential_missing" });
    expect(fetchStub.calls).toHaveLength(0);
  });

  it("reports provider_failed, not credential_missing, when the timeout aborts a hanging credential resolution", async () => {
    const resolver = new HangingCredentialResolver();
    const fetchStub = new RecordingFetch(() => ok({ output: [] }));
    const bridge = new ConnectorBridgeProvider({
      ...baseOptions(resolver, fetchStub.impl),
      timeoutMilliseconds: 5,
    });

    const result = await bridge.invoke(request, { requestId: "request-1" });

    expect(result).toEqual({ status: "failed", reason: "provider_failed" });
    expect(fetchStub.calls).toHaveLength(0);
  });

  it("reports provider_failed with zero fetches and no resolver-value leak when context.signal is pre-aborted", async () => {
    const resolver = new RecordingCredentialResolver({
      OPENAI_API_KEY: "sk-fake",
      CONNECTOR_CANVA: "tok-fake",
    });
    const fetchStub = new RecordingFetch(() => ok({ output: [] }));
    const bridge = new ConnectorBridgeProvider(baseOptions(resolver, fetchStub.impl));
    const abortedController = new AbortController();
    abortedController.abort();

    const result = await bridge.invoke(request, { requestId: "request-1", signal: abortedController.signal });

    expect(result).toEqual({ status: "failed", reason: "provider_failed" });
    expect(fetchStub.calls).toHaveLength(0);
    const serialized = JSON.stringify(result);
    expect(serialized).not.toContain("sk-fake");
    expect(serialized).not.toContain("tok-fake");
  });

  it("does not leak the upstream error body or either credential on a non-200 response", async () => {
    const resolver = new RecordingCredentialResolver({
      OPENAI_API_KEY: "sk-fake",
      CONNECTOR_CANVA: "tok-fake",
    });
    const fetchStub = new RecordingFetch(() => ({
      ok: false,
      status: 401,
      json: async () => ({ error: { message: "boom" } }),
    } as Response));
    const bridge = new ConnectorBridgeProvider(baseOptions(resolver, fetchStub.impl));

    const result = await bridge.invoke(request, { requestId: "request-1" });

    expect(result).toEqual({ status: "failed", reason: "provider_failed" });
    const serialized = JSON.stringify(result);
    expect(serialized).not.toContain("boom");
    expect(serialized).not.toContain("sk-fake");
    expect(serialized).not.toContain("tok-fake");
  });

  it.each([
    {
      label: "no matching mcp_call item",
      body: { output: [{ type: "mcp_call", name: "other_tool", output: {} }] },
    },
    {
      label: "an mcp_call item with an error",
      body: { output: [{ type: "mcp_call", name: "export_design", error: "tool exploded" }] },
    },
  ])("reports provider_failed and does not leak upstream detail for $label", async ({ body }) => {
    const resolver = new RecordingCredentialResolver({
      OPENAI_API_KEY: "sk-fake",
      CONNECTOR_CANVA: "tok-fake",
    });
    const fetchStub = new RecordingFetch(() => ok(body));
    const bridge = new ConnectorBridgeProvider(baseOptions(resolver, fetchStub.impl));

    const result = await bridge.invoke(request, { requestId: "request-1" });

    expect(result).toEqual({ status: "failed", reason: "provider_failed" });
    expect(JSON.stringify(result)).not.toContain("exploded");
  });

  it("fails closed on the write-capability gate before touching credentials or the network", async () => {
    const resolver = new RecordingCredentialResolver({
      OPENAI_API_KEY: "sk-fake",
      CONNECTOR_CANVA: "tok-fake",
    });
    const fetchStub = new RecordingFetch(() => ok({ output: [] }));
    const bridge = new ConnectorBridgeProvider({
      ...baseOptions(resolver, fetchStub.impl),
      policy: DEFAULT_POLICY,
    });

    const result = await bridge.invoke(request, { requestId: "request-1" });

    expect(result).toEqual({ status: "failed", reason: "write_review_required" });
    expect(fetchStub.calls).toHaveLength(0);
    expect(resolver.calls).toHaveLength(0);
  });

  it("refuses to construct for an app that is not a connector_ component", () => {
    const resolver = new RecordingCredentialResolver();
    const fetchStub = new RecordingFetch(() => ok({ output: [] }));

    expect(() => new ConnectorBridgeProvider({
      ...baseOptions(resolver, fetchStub.impl),
      app: canvaApp({ connectorId: "asdk_app_ab12" }),
    })).toThrow("provider_unavailable:not_a_connector");
  });

  it("throws provider_invalid:timeout for an out-of-bounds timeoutMilliseconds", () => {
    const resolver = new RecordingCredentialResolver();
    const fetchStub = new RecordingFetch(() => ok({ output: [] }));

    expect(() => new ConnectorBridgeProvider({
      ...baseOptions(resolver, fetchStub.impl),
      timeoutMilliseconds: 0,
    })).toThrow("provider_invalid:timeout");
    expect(() => new ConnectorBridgeProvider({
      ...baseOptions(resolver, fetchStub.impl),
      timeoutMilliseconds: 300_001,
    })).toThrow("provider_invalid:timeout");
  });

  it("throws component_invalid:base_url for a non-https, non-local baseUrl", () => {
    const resolver = new RecordingCredentialResolver();
    const fetchStub = new RecordingFetch(() => ok({ output: [] }));

    expect(() => new ConnectorBridgeProvider({
      ...baseOptions(resolver, fetchStub.impl),
      baseUrl: "http://example.com",
    })).toThrow("component_invalid:base_url");
    expect(() => new ConnectorBridgeProvider({
      ...baseOptions(resolver, fetchStub.impl),
      baseUrl: "not a url",
    })).toThrow("component_invalid:base_url");
  });

  it("throws component_invalid:base_url for a baseUrl carrying a query, a hash, or a non-root path", () => {
    const resolver = new RecordingCredentialResolver();
    const fetchStub = new RecordingFetch(() => ok({ output: [] }));

    expect(() => new ConnectorBridgeProvider({
      ...baseOptions(resolver, fetchStub.impl),
      baseUrl: "https://x/?a=b",
    })).toThrow("component_invalid:base_url");
    expect(() => new ConnectorBridgeProvider({
      ...baseOptions(resolver, fetchStub.impl),
      baseUrl: "https://x/#f",
    })).toThrow("component_invalid:base_url");
    expect(() => new ConnectorBridgeProvider({
      ...baseOptions(resolver, fetchStub.impl),
      baseUrl: "https://x/extra",
    })).toThrow("component_invalid:base_url");
  });

  it("accepts a root baseUrl with a trailing slash", () => {
    const resolver = new RecordingCredentialResolver();
    const fetchStub = new RecordingFetch(() => ok({ output: [] }));

    expect(() => new ConnectorBridgeProvider({
      ...baseOptions(resolver, fetchStub.impl),
      baseUrl: "https://x/",
    })).not.toThrow();
  });

  it("resolves failed without any fetch when context.signal is not a real AbortSignal", async () => {
    const resolver = new RecordingCredentialResolver({
      OPENAI_API_KEY: "sk-fake",
      CONNECTOR_CANVA: "tok-fake",
    });
    const fetchStub = new RecordingFetch(() => ok({ output: [] }));
    const bridge = new ConnectorBridgeProvider(baseOptions(resolver, fetchStub.impl));

    const result = await bridge.invoke(request, { requestId: "request-1", signal: {} as never });

    expect(result).toEqual({ status: "failed", reason: "provider_failed" });
    expect(fetchStub.calls).toHaveLength(0);
    expect(resolver.calls).toHaveLength(0);
  });

  it("resolves failed without any fetch when componentName does not match the bound app", async () => {
    const resolver = new RecordingCredentialResolver({
      OPENAI_API_KEY: "sk-fake",
      CONNECTOR_CANVA: "tok-fake",
    });
    const fetchStub = new RecordingFetch(() => ok({ output: [] }));
    const bridge = new ConnectorBridgeProvider(baseOptions(resolver, fetchStub.impl));

    const result = await bridge.invoke(
      { ...request, componentName: "not-canva" },
      { requestId: "request-1" },
    );

    expect(result).toEqual({ status: "failed", reason: "provider_failed" });
    expect(fetchStub.calls).toHaveLength(0);
    expect(resolver.calls).toHaveLength(0);
  });

  it("resolves failed without any fetch when arguments carry a prototype-risk key", async () => {
    const resolver = new RecordingCredentialResolver({
      OPENAI_API_KEY: "sk-fake",
      CONNECTOR_CANVA: "tok-fake",
    });
    const fetchStub = new RecordingFetch(() => ok({ output: [] }));
    const bridge = new ConnectorBridgeProvider(baseOptions(resolver, fetchStub.impl));
    const hostileArguments = JSON.parse('{"constructor":{"polluted":true}}') as Record<string, unknown>;

    const result = await bridge.invoke(
      { ...request, arguments: hostileArguments },
      { requestId: "request-1" },
    );

    expect(result).toEqual({ status: "failed", reason: "provider_failed" });
    expect(fetchStub.calls).toHaveLength(0);
    expect(resolver.calls).toHaveLength(0);
  });

  it("registers with the provider registry and delegates invoke through the resolved facade", async () => {
    const resolver = new RecordingCredentialResolver({
      OPENAI_API_KEY: "sk-fake",
      CONNECTOR_CANVA: "tok-fake",
    });
    const fetchStub = new RecordingFetch(() => ok({
      output: [{ type: "mcp_call", name: "export_design", output: { ok: true } }],
    }));
    const bridge = new ConnectorBridgeProvider(baseOptions(resolver, fetchStub.impl));
    const registry = new ProviderRegistry();
    const exactBinding = binding("binding.canva", CONNECTOR_DIGEST);

    registry.register(exactBinding, bridge);
    const resolution = registry.resolve(exactBinding);

    expect(resolution).toMatchObject({ status: "available" });
    if (resolution.status !== "available") throw new Error("expected available provider");
    await expect(resolution.provider.invoke(request, { requestId: "request-1" }))
      .resolves.toEqual({ status: "success", output: { ok: true } });
    expect(fetchStub.calls).toHaveLength(1);
  });

  it("describes itself exactly and refuses a binding whose digest does not match the app", () => {
    const resolver = new RecordingCredentialResolver();
    const fetchStub = new RecordingFetch(() => ok({ output: [] }));
    const bridge = new ConnectorBridgeProvider(baseOptions(resolver, fetchStub.impl));

    expect(bridge.describe()).toEqual({
      id: "bridge.canva",
      kind: "openai-connector-bridge",
      temporaryAdapter: false,
    });

    expect(() => new ConnectorBridgeProvider({
      ...baseOptions(resolver, fetchStub.impl),
      binding: binding("binding.canva", "d".repeat(64)),
    })).toThrow("provider_invalid:descriptor_mismatch");
  });
});
