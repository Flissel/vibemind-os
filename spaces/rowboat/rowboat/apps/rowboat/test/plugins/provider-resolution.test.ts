import { describe, expect, it } from "vitest";
import { DEFAULT_POLICY, type PluginPolicy, type ProviderBinding } from "@rowboat/openai-plugin-runtime";
import { resolvePluginProvider, UnreleasedCredentialResolver } from "@/src/infrastructure/plugins/provider-resolution";

const componentDigest = "a".repeat(64);
const credentialResolver = new UnreleasedCredentialResolver();

const httpBinding: ProviderBinding = Object.freeze({ id: "mcp.github", providerKind: "mcp-http", componentDigest });
const component = (overrides: Record<string, unknown> = {}) => Object.freeze({
  id: "mcp:.mcp.json#github", name: "github", kind: "mcp",
  metadata: Object.freeze({ digest: "b".repeat(64), bindingDigest: componentDigest, transport: "http", mcpServer: { type: "http", url: "https://api.githubcopilot.com/mcp/" } }),
  ...overrides,
});
const entry = Object.freeze({ licenseDeclaration: "MIT" });

describe("plugin provider resolution", () => {
  it("resolves an HTTP MCP component from the pinned catalog record alone", () => {
    const resolution = resolvePluginProvider({ component: component(), entry, binding: httpBinding }, { credentialResolver });
    expect(resolution.status).toBe("available");
    if (resolution.status !== "available") return;
    expect(resolution.provider.describe()).toMatchObject({ id: "mcp.github", kind: "mcp-http" });
  });

  it("refuses a component kind that no provider here can execute", () => {
    // A process MCP server needs a verified execution root from the content
    // store, which the web runtime does not mount.
    expect(resolvePluginProvider({
      component: component({ metadata: { digest: "b".repeat(64), bindingDigest: componentDigest, transport: "process", mcpServer: { type: "process", command: "node", args: ["server.js"] } } }),
      entry, binding: { ...httpBinding, providerKind: "mcp-process" },
    }, { credentialResolver })).toMatchObject({ status: "unavailable", reason: "provider_unavailable" });

    // The registry itself refuses the connector bridge; no implementation exists.
    expect(resolvePluginProvider({
      component: component({ kind: "app", id: "app:.app.json#github" }), entry,
      binding: { ...httpBinding, providerKind: "openai-connector-bridge" },
    }, { credentialResolver })).toMatchObject({ status: "unavailable" });
  });

  it("refuses a missing, malformed, or insecure declaration", () => {
    for (const metadata of [
      { digest: "b".repeat(64), bindingDigest: componentDigest, transport: "http" },
      { digest: "b".repeat(64), bindingDigest: componentDigest, transport: "http", mcpServer: "nope" },
      { digest: "b".repeat(64), bindingDigest: componentDigest, transport: "http", mcpServer: { type: "http" } },
      { digest: "b".repeat(64), bindingDigest: componentDigest, transport: "http", mcpServer: { type: "http", url: "http://insecure.invalid/mcp" } },
    ]) {
      expect(resolvePluginProvider({ component: component({ metadata }), entry, binding: httpBinding }, { credentialResolver }))
        .toMatchObject({ status: "unavailable", reason: "provider_unavailable" });
    }
  });

  it("refuses a binding whose digest does not match the component it names", () => {
    expect(resolvePluginProvider({ component: component(), entry, binding: { ...httpBinding, componentDigest: "c".repeat(64) } }, { credentialResolver }))
      .toMatchObject({ status: "unavailable" });
  });

  it("hands out no credential until one is released", async () => {
    await expect(new UnreleasedCredentialResolver().resolve({ name: "GITHUB_TOKEN" } as never, "project-1"))
      .rejects.toThrow("credential_missing");
  });

  it("carries a policy passed in dependencies into the constructed provider, not the kernel default", async () => {
    // 127.0.0.1:1 refuses the connection immediately (nothing listens there),
    // so this stays fast and needs no network access -- it only has to prove
    // which policy the provider was built with, not complete a real call.
    const localComponent = component({
      metadata: {
        digest: "b".repeat(64), bindingDigest: componentDigest, transport: "http",
        mcpServer: { type: "http", url: "https://127.0.0.1:1/mcp" },
      },
    });
    const invocationRequest = Object.freeze({
      projectId: "project-1", pluginName: "github", componentName: "github",
      operationName: "search", capability: "write" as const, arguments: {},
    });

    const defaultResolution = resolvePluginProvider({ component: localComponent, entry, binding: httpBinding }, { credentialResolver });
    expect(defaultResolution.status).toBe("available");
    if (defaultResolution.status !== "available") return;
    // DEFAULT_POLICY refuses every write outright, before any network attempt.
    await expect(defaultResolution.provider.invoke(invocationRequest, { requestId: "req-1" }))
      .rejects.toThrow("write_review_required");

    const elevatedPolicy: PluginPolicy = Object.freeze({ ...DEFAULT_POLICY, allowWriteCapabilities: true });
    const elevatedResolution = resolvePluginProvider(
      { component: localComponent, entry, binding: httpBinding },
      { credentialResolver, policy: elevatedPolicy, timeoutMilliseconds: 2_000 },
    );
    expect(elevatedResolution.status).toBe("available");
    if (elevatedResolution.status !== "available") return;
    const elevatedResult = await elevatedResolution.provider.invoke(invocationRequest, { requestId: "req-2" });
    // The elevated policy clears the same admission check and lets the call
    // reach the network attempt instead, which then fails for an unrelated,
    // local reason -- never for write_review_required again.
    expect(elevatedResult).toMatchObject({ status: "failed" });
    expect((elevatedResult as { status: "failed"; reason: string }).reason).not.toBe("write_review_required");
  });
});
