import { describe, expect, it } from "vitest";
import type { ProviderBinding } from "@rowboat/openai-plugin-runtime";
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
});
