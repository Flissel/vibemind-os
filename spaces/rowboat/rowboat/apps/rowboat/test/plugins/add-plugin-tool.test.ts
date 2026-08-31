import { describe, expect, it } from "vitest";
import type { PluginApiIdentity } from "@/src/application/policies/plugin-api-authorization.policy";
import type { PluginCatalogEntry, PluginInstallation } from "@/src/application/repositories/plugins.repository.interface";
import { AddPluginToolUseCase, pluginToolName, type AddPluginToolDependencies } from "@/src/application/use-cases/plugins/add-plugin-tool.use-case";
import { planShadowToolConfig } from "@/src/application/services/plugin-shadow-parity";

const projectId = "11111111-1111-4111-8111-111111111111";
const installationId = "33333333-3333-4333-8333-333333333333";
const componentId = "mcp:.mcp.json#github";
const componentDigest = "a".repeat(64);
const identity: PluginApiIdentity = Object.freeze({ kind: "user", userId: "user-1" });

const installation = (overrides: Partial<PluginInstallation> = {}): PluginInstallation => Object.freeze({
  id: installationId, projectId, pluginName: "github", pluginVersion: "1.0.0", sourceCommit: "1".repeat(40),
  manifestDigest: "b".repeat(64), treeDigest: "c".repeat(64), policyVersion: "rowboat-plugin-policy-v1",
  enabled: true, revision: 1,
  providerBindings: [{ componentId, binding: { id: "mcp.github", providerKind: "mcp-http", componentDigest } }],
  ...overrides,
}) as PluginInstallation;

const entry = (overrides: Record<string, unknown> = {}): PluginCatalogEntry => Object.freeze({
  name: "github", pluginName: "github", pluginVersion: "1.0.0", catalogDigest: "d".repeat(64),
  sourceCommit: "1".repeat(40), manifestDigest: "b".repeat(64), treeDigest: "c".repeat(64),
  policyVersion: "rowboat-plugin-policy-v1",
  admission: { status: "admitted", policyVersion: "rowboat-plugin-policy-v1" },
  components: [{
    component: { id: componentId, name: "github", kind: "mcp", status: "available", metadata: { digest: "e".repeat(64), bindingDigest: componentDigest, transport: "http" } },
    admission: { status: "admitted", policyVersion: "rowboat-plugin-policy-v1" },
  }],
  ...overrides,
}) as unknown as PluginCatalogEntry;

const workflow = () => ({ agents: [], prompts: [], pipelines: [], startAgent: "a", lastUpdatedAt: "2026-08-01T10:00:00.000Z", tools: [{ name: "existing" }] });

interface Harness {
  readonly service: AddPluginToolUseCase;
  readonly saved: unknown[];
  readonly calls: string[];
}

function harness(options: Readonly<{
  installation?: PluginInstallation | null;
  entry?: PluginCatalogEntry | null;
  workflow?: unknown;
  authorize?: () => Promise<void>;
}> = {}): Harness {
  const saved: unknown[] = [];
  const calls: string[] = [];
  const dependencies: AddPluginToolDependencies = {
    async authorizeProject() { calls.push("authorize"); if (options.authorize !== undefined) await options.authorize(); },
    async loadInstallation() { calls.push("loadInstallation"); return options.installation === undefined ? installation() : options.installation; },
    async loadCatalogEntry() { calls.push("loadCatalogEntry"); return options.entry === undefined ? entry() : options.entry; },
    async loadDraftWorkflow() { calls.push("loadDraftWorkflow"); return options.workflow === undefined ? workflow() : options.workflow; },
    async saveDraftWorkflow(_projectId: string, value: unknown) { calls.push("saveDraftWorkflow"); saved.push(value); },
  };
  return { service: new AddPluginToolUseCase(dependencies), saved, calls };
}

const request = Object.freeze({ identity, projectId, pluginName: "github", componentDigest });

describe("adding an installed plugin component as a workflow tool", () => {
  it("appends a native binding that the runtime mode gate does not strip", async () => {
    const { service, saved } = harness();
    const result = await service.execute(request);
    expect(result).toMatchObject({ added: true, toolName: "plugin_github_github" });

    const tools = (saved[0] as { tools: Record<string, unknown>[] }).tools;
    expect(tools).toHaveLength(2);
    expect(tools[0]).toEqual({ name: "existing" });
    expect(tools[1]).toMatchObject({
      name: "plugin_github_github",
      pluginBinding: { installationId, pluginName: "github", componentDigest, providerBindingId: "mcp.github", capability: "write", origin: "native" },
    });

    // The point of the native origin: the tool stays executable in every mode.
    const planned = planShadowToolConfig("legacy", { [String(tools[1]!.name)]: tools[1]! });
    expect(planned.toolConfig.plugin_github_github!.pluginBinding).toEqual(tools[1]!.pluginBinding);
  });

  it("is idempotent for the same component and refuses to overwrite a different tool", async () => {
    const bound = harness();
    const first = await bound.service.execute(request);
    const existing = (bound.saved[0] as { tools: unknown[] }).tools;

    const repeated = harness({ workflow: { ...workflow(), tools: existing } });
    const second = await repeated.service.execute(request);
    expect(second).toMatchObject({ added: false, toolName: first.toolName });
    expect(repeated.calls).not.toContain("saveDraftWorkflow");

    const foreign = harness({ workflow: { ...workflow(), tools: [{ name: first.toolName, description: "someone else" }] } });
    await expect(foreign.service.execute(request)).rejects.toThrow("installation_conflict");
  });

  it("refuses a component the catalog does not admit", async () => {
    const review = harness({ entry: entry({ components: [{
      component: { id: componentId, name: "github", kind: "mcp", status: "available", metadata: { digest: "e".repeat(64), bindingDigest: componentDigest } },
      admission: { status: "review_required", reason: "write_review_required", policyVersion: "rowboat-plugin-policy-v1" },
    }] }) });
    await expect(review.service.execute(request)).rejects.toThrow("component_not_admitted");
    expect(review.saved).toEqual([]);

    const unavailable = harness({ entry: entry({ components: [{
      component: { id: componentId, name: "github", kind: "mcp", status: "unavailable", reason: "provider_unavailable", metadata: { digest: "e".repeat(64), bindingDigest: componentDigest } },
      admission: { status: "admitted", policyVersion: "rowboat-plugin-policy-v1" },
    }] }) });
    await expect(unavailable.service.execute(request)).rejects.toThrow("provider_unavailable");
  });

  it("refuses without an enabled installation that binds the exact component", async () => {
    await expect(harness({ installation: null }).service.execute(request)).rejects.toThrow("installation_not_found");
    await expect(harness({ installation: installation({ enabled: false }) }).service.execute(request)).rejects.toThrow("installation_not_found");
    await expect(harness({ installation: installation({ providerBindings: [] }) }).service.execute(request)).rejects.toThrow("provider_unavailable");
    // A binding for another component digest is simply not this component.
    await expect(harness({ installation: installation({ providerBindings: [{ componentId, binding: { id: "mcp.github", providerKind: "mcp-http", componentDigest: "f".repeat(64) } }] }) }).service.execute(request))
      .rejects.toThrow("provider_unavailable");
    // The digest matches but it belongs to a different component in the catalog:
    // the two identities must agree before anything is added.
    await expect(harness({ installation: installation({ providerBindings: [{ componentId: "mcp:.mcp.json#other", binding: { id: "mcp.github", providerKind: "mcp-http", componentDigest } }] }) }).service.execute(request))
      .rejects.toThrow("digest_mismatch");
  });

  it("authorizes before reading anything and validates the request first", async () => {
    const denied = harness({ authorize: async () => { throw new Error("forbidden"); } });
    await expect(denied.service.execute(request)).rejects.toThrow("forbidden");
    expect(denied.calls).toEqual(["authorize"]);

    const invalid = harness();
    for (const override of [{ projectId: "nope" }, { pluginName: "in valid" }, { componentDigest: "nope" }]) {
      await expect(invalid.service.execute({ ...request, ...override } as never)).rejects.toThrow("request_invalid");
    }
    expect(invalid.calls).toEqual([]);
  });

  it("namespaces the tool name and falls back to the digest for an unnameable component", () => {
    expect(pluginToolName("google-drive", "Google Sheets", componentDigest)).toBe("plugin_google_drive_google_sheets");
    expect(pluginToolName("github", "***", componentDigest)).toBe(`plugin_github_${componentDigest.slice(0, 12)}`);
    expect(pluginToolName("x", "y", componentDigest)).toMatch(/^[a-z0-9_]+$/);
  });
});
