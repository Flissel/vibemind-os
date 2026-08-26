import { describe, expect, it, vi } from "vitest";
import catalogFixture from "../../../../config/openai-plugin-catalog.lock.json";
import {
  PINNED_PLUGIN_CATALOG_DIGEST,
  type PluginCatalogLock,
  type PluginProvider,
  type ProviderBinding,
} from "@rowboat/openai-plugin-runtime";
import type {
  IPluginsRepository,
  PluginComponentAdmission,
  PluginCredentialSlot,
  PluginInstallation,
  PluginReceipt,
} from "@/src/application/repositories/plugins.repository.interface";
import {
  PluginToolRuntime,
  PluginToolRuntimeError,
  type PluginProviderResolutionInput,
  type PluginToolRuntimeDependencies,
} from "@/src/application/services/plugin-tool-runtime";
import { WorkflowTool } from "@/app/lib/types/workflow_types";

vi.mock("@/src/application/lib/composio/composio", () => ({
  composio: Object.freeze({ tools: Object.freeze({ execute: () => { throw new Error("legacy_forbidden"); } }) }),
}));
vi.mock("@/di/container", () => ({
  container: Object.freeze({ resolve: () => { throw new Error("legacy_forbidden"); } }),
}));
vi.mock("@/app/lib/qdrant", () => ({
  qdrantClient: Object.freeze({ query: () => { throw new Error("legacy_forbidden"); } }),
}));

const catalog = catalogFixture as unknown as PluginCatalogLock;
const entry = catalog.entries.find((candidate) => candidate.name === "actively")!;
const selected = entry.components.find(({ component }) => component.kind === "app")!;
const componentDigest = selected.component.metadata.bindingDigest;
const providerBinding: ProviderBinding = Object.freeze({
  id: "actively-provider",
  providerKind: "rowboat-native",
  componentDigest,
});
const installation: PluginInstallation = Object.freeze({
  id: "15bc7a6e-a676-4f39-8644-29a9bac00421",
  projectId: "project-1",
  pluginName: entry.name,
  pluginVersion: entry.pluginVersion,
  sourceCommit: entry.sourceCommit,
  manifestDigest: entry.manifestDigest,
  treeDigest: entry.treeDigest,
  policyVersion: entry.policyVersion,
  enabled: true,
  revision: 7,
  providerBindings: Object.freeze([Object.freeze({ componentId: selected.component.id, binding: providerBinding })]),
});
const admission: PluginComponentAdmission = Object.freeze({
  installationId: installation.id,
  componentDigest,
  componentKind: "app",
  componentName: selected.component.name,
  status: "admitted",
  policyVersion: catalog.policyVersion,
});
const credentialSlot: PluginCredentialSlot = Object.freeze({
  id: "slot-1",
  projectId: installation.projectId,
  installationId: installation.id,
  name: "API_TOKEN",
  reference: Object.freeze({ kind: "environment", reference: "ACTIVELY_API_TOKEN" }),
});
const binding = Object.freeze({
  installationId: installation.id,
  pluginName: installation.pluginName,
  componentDigest,
  providerBindingId: providerBinding.id,
  capability: "read" as const,
});

class FakeRepository implements IPluginsRepository {
  currentInstallation: PluginInstallation | null = installation;
  currentAdmissions: readonly PluginComponentAdmission[] = [admission];
  currentSlots: readonly PluginCredentialSlot[] = [credentialSlot];
  receipts: PluginReceipt[] = [];
  credentialReads = 0;
  receiptFailure = false;
  async getCatalog(digest: string): Promise<PluginCatalogLock | null> { return digest === catalog.catalogDigest ? catalog : null; }
  async getInstallation(projectId: string, pluginName: string): Promise<PluginInstallation | null> {
    return projectId === installation.projectId && pluginName === installation.pluginName ? this.currentInstallation : null;
  }
  async listAdmissions(): Promise<readonly PluginComponentAdmission[]> { return this.currentAdmissions; }
  async listCredentialSlots(): Promise<readonly PluginCredentialSlot[]> { this.credentialReads += 1; return this.currentSlots; }
  async putReceipt(receipt: PluginReceipt): Promise<void> {
    if (this.receiptFailure) throw new Error("database internals secret-value");
    this.receipts.push(receipt);
  }
  async putCatalog(): Promise<void> {}
  async putCatalogSnapshot(): Promise<void> {}
  async getCatalogSnapshot(): Promise<null> { return null; }
  async listCatalogEntries(): Promise<readonly never[]> { return []; }
  async putCatalogEntries(): Promise<void> {}
  async putInstallation(): Promise<void> {}
  async listInstallations(): Promise<readonly PluginInstallation[]> { return []; }
  async setInstallationEnabled(): Promise<PluginInstallation> { throw new Error("unused"); }
  async putAdmissions(): Promise<void> {}
  async putCredentialSlot(): Promise<void> {}
  async putMigrationRecord(): Promise<void> {}
  async getIdempotentReceipt(): Promise<null> { return null; }
  async installIdempotently(): Promise<never> { throw new Error("unused"); }
  async setInstallationEnabledIdempotently(): Promise<never> { throw new Error("unused"); }
}

function setup(options: {
  readonly operationCapability?: "read" | "write";
  readonly providerResult?: "success" | "failed" | "hang";
  readonly providerAvailable?: boolean;
  readonly resolverHangs?: boolean;
  readonly credentialFailure?: boolean;
  readonly resolverProxyTrap?: () => void;
  readonly descriptorProxyTrap?: () => void;
  readonly directProvider?: boolean;
  readonly authorize?: () => Promise<void>;
} = {}) {
  const repository = new FakeRepository();
  const counters = { authorize: 0, resolve: 0, provider: 0, cancel: 0, legacy: 0 };
  let observedReference = "";
  const provider: PluginProvider = Object.freeze({
    id: providerBinding.id,
    describe: () => options.descriptorProxyTrap === undefined
      ? Object.freeze({ id: providerBinding.id, kind: providerBinding.providerKind, temporaryAdapter: false })
      : new Proxy(Object.freeze({ id: providerBinding.id, kind: providerBinding.providerKind, temporaryAdapter: false }), {
        get: (target, key, receiver) => { options.descriptorProxyTrap?.(); return Reflect.get(target, key, receiver); },
        ownKeys: () => { options.descriptorProxyTrap?.(); return []; },
      }),
    invoke: async (): Promise<Awaited<ReturnType<PluginProvider["invoke"]>>> => {
      counters.provider += 1;
      if (options.providerResult === "failed") return Object.freeze({ status: "failed" as const, reason: "remote secret-value failure" });
      if (options.providerResult === "hang") return new Promise(() => undefined);
      return Object.freeze({ status: "success" as const, output: Object.freeze({ ok: true }) });
    },
  });
  const dependencies: PluginToolRuntimeDependencies = Object.freeze({
    pluginsRepository: repository,
    authorizeProject: async () => { counters.authorize += 1; await options.authorize?.(); },
    classifyOperation: () => options.operationCapability ?? "read",
    resolveProvider: async (input: PluginProviderResolutionInput) => {
      counters.resolve += 1;
      observedReference = input.credentialSlots[0]?.reference.reference ?? "";
      if (options.resolverHangs === true) return new Promise<never>(() => undefined);
      if (options.credentialFailure === true) throw new Error("credential_missing");
      if (options.resolverProxyTrap !== undefined) {
        return new Proxy(Object.freeze({ status: "available" as const, provider }), {
          has: () => { options.resolverProxyTrap?.(); return true; },
          ownKeys: () => { options.resolverProxyTrap?.(); return []; },
        });
      }
      if (options.directProvider === true) return provider as unknown as { readonly status: "unavailable"; readonly reason: "provider_unavailable" };
      if (options.providerAvailable === false) return Object.freeze({ status: "unavailable" as const, reason: "provider_unavailable" as const });
      return Object.freeze({ status: "available" as const, provider });
    },
    timeoutMilliseconds: 20,
    createRequestId: () => "request-1",
    onCancel: () => { counters.cancel += 1; },
  });
  return { runtime: new PluginToolRuntime(dependencies), repository, counters, get observedReference() { return observedReference; } };
}

describe("PluginToolRuntime", () => {
  it("accepts only the strict immutable workflow plugin binding shape", () => {
    const parsed = WorkflowTool.parse({
      name: "actively_lookup", description: "lookup", parameters: { type: "object", properties: {} },
      pluginBinding: binding, isComposio: true,
    });
    expect(parsed.pluginBinding).toEqual(binding);
    expect(Object.isFrozen(parsed.pluginBinding)).toBe(true);
    expect(() => WorkflowTool.parse({
      name: "actively_lookup", description: "lookup", parameters: { type: "object", properties: {} },
      pluginBinding: { ...binding, extra: true },
    })).toThrow();
  });

  it("builds a plugin tool that delegates exclusively to the plugin runtime", async () => {
    const { createPluginTool } = await import("@/src/application/lib/agents-runtime/agent-tools");
    let calls = 0;
    const created = createPluginTool(WorkflowTool.parse({
      name: "actively_lookup", description: "lookup", parameters: { type: "object", properties: {} },
      pluginBinding: binding, mockTool: true, isMcp: true, isComposio: true, isWebhook: true,
      mcpServerName: "legacy", composioData: { slug: "LEGACY", noAuth: true, toolkitName: "Legacy", toolkitSlug: "legacy", logo: "legacy" },
    }), "project-1", async () => ({
      invoke: async (receivedBinding, args, context) => {
        calls += 1;
        expect(receivedBinding).toEqual(binding);
        expect(args).toEqual({ query: "safe" });
        expect(context).toEqual({ projectId: "project-1", operationName: "actively_lookup" });
        return { status: "success" as const, output: { routed: true } };
      },
    }));
    const callable = created as unknown as { invoke: (runContext: unknown, input: string) => Promise<string> };
    expect(await callable.invoke({}, JSON.stringify({ query: "safe" }))).toContain("routed");
    expect(calls).toBe(1);
  });

  it("routes the exact current admitted binding and writes only a redacted provenance receipt", async () => {
    const state = setup();
    const result = await state.runtime.invoke(binding, { query: "safe" }, { projectId: "project-1", operationName: "lookup" });
    expect(result).toEqual({ status: "success", output: { ok: true } });
    expect(state.counters).toMatchObject({ authorize: 1, resolve: 1, provider: 1, legacy: 0 });
    expect(state.repository.receipts).toHaveLength(1);
    expect(state.repository.receipts[0]).toMatchObject({ type: "execution", projectId: "project-1", pluginName: "actively", status: "success" });
    expect(JSON.stringify(state.repository.receipts[0])).not.toContain("query");
    expect(Object.isFrozen(state.repository.receipts[0])).toBe(true);
  });

  it("loads current credential references at every call, after authorization and policy gates", async () => {
    const state = setup();
    await state.runtime.invoke(binding, {}, { projectId: "project-1", operationName: "lookup" });
    state.repository.currentSlots = [Object.freeze({ ...credentialSlot, reference: { kind: "environment" as const, reference: "ROTATED_TOKEN" } })];
    await state.runtime.invoke(binding, {}, { projectId: "project-1", operationName: "lookup" });
    expect(state.repository.credentialReads).toBe(2);
    expect(state.observedReference).toBe("ROTATED_TOKEN");

    const denied = setup({ authorize: async () => { throw new Error("forbidden"); } });
    await expect(denied.runtime.invoke(binding, {}, { projectId: "project-1", operationName: "lookup" })).rejects.toThrow("forbidden");
    expect(denied.repository.credentialReads).toBe(0);
    expect(denied.counters.resolve).toBe(0);
  });

  it.each([
    ["wrong project", { context: { projectId: "project-2", operationName: "lookup" } }],
    ["stale installation id", { binding: { ...binding, installationId: "7d6c37cf-b78c-45bf-a6dd-3dcba4e4de31" } }],
    ["component digest mismatch", { binding: { ...binding, componentDigest: "f".repeat(64) } }],
    ["provider mismatch", { binding: { ...binding, providerBindingId: "other-provider" } }],
  ])("fails closed for %s before credentials/provider", async (_name, mutation) => {
    const state = setup();
    const candidateBinding = "binding" in mutation ? mutation.binding : binding;
    const context = "context" in mutation ? mutation.context : { projectId: "project-1", operationName: "lookup" };
    await expect(state.runtime.invoke(candidateBinding, {}, context)).rejects.toBeInstanceOf(PluginToolRuntimeError);
    expect(state.repository.credentialReads).toBe(0);
    expect(state.counters.resolve).toBe(0);
    expect(state.counters.provider).toBe(0);
    expect(state.counters.legacy).toBe(0);
  });

  it("rejects caller read downgrade when trusted operation classification is write", async () => {
    const state = setup({ operationCapability: "write" });
    await expect(state.runtime.invoke(binding, {}, { projectId: "project-1", operationName: "mutate" })).rejects.toThrow("capability_mismatch");
    expect(state.repository.credentialReads).toBe(0);
    expect(state.counters.provider).toBe(0);
  });

  it("rejects an invalid current installation revision before admissions or credentials", async () => {
    const state = setup();
    state.repository.currentInstallation = Object.freeze({ ...installation, revision: -1 });
    await expect(state.runtime.invoke(binding, {}, { projectId: "project-1", operationName: "lookup" })).rejects.toThrow("installation_mismatch");
    expect(state.repository.credentialReads).toBe(0);
    expect(state.counters.provider).toBe(0);
  });

  it("rejects current admission before credentials and unavailable providers without fallback", async () => {
    const denied = setup();
    denied.repository.currentAdmissions = [Object.freeze({ ...admission, status: "rejected" as const, reason: "component_unsupported" as const })];
    await expect(denied.runtime.invoke(binding, {}, { projectId: "project-1", operationName: "lookup" })).rejects.toThrow("admission_denied");
    expect(denied.repository.credentialReads).toBe(0);
    expect(denied.counters.provider).toBe(0);

    const unavailable = setup({ providerAvailable: false });
    await expect(unavailable.runtime.invoke(binding, {}, { projectId: "project-1", operationName: "lookup" })).rejects.toThrow("provider_unavailable");
    expect(unavailable.counters).toMatchObject({ provider: 0, legacy: 0 });
  });

  it("never falls back and redacts provider failures", async () => {
    const state = setup({ providerResult: "failed" });
    await expect(state.runtime.invoke(binding, { token: "secret-value" }, { projectId: "project-1", operationName: "lookup" })).rejects.toThrow("provider_failed");
    expect(state.counters).toMatchObject({ provider: 1, legacy: 0 });
    expect(JSON.stringify(state.repository.receipts)).not.toContain("secret-value");
  });

  it("fails with a typed error when the redacted receipt cannot be persisted", async () => {
    const state = setup();
    state.repository.receiptFailure = true;
    await expect(state.runtime.invoke(binding, {}, { projectId: "project-1", operationName: "lookup" })).rejects.toThrow("receipt_unavailable");
    expect(state.counters).toMatchObject({ provider: 1, legacy: 0 });
  });

  it("times out hanging providers, signals cancellation once, and has no fallback", async () => {
    const state = setup({ providerResult: "hang" });
    await expect(state.runtime.invoke(binding, {}, { projectId: "project-1", operationName: "lookup" })).rejects.toThrow("provider_timed_out");
    expect(state.counters).toMatchObject({ provider: 1, cancel: 1, legacy: 0 });
    expect(state.repository.receipts[0]).toMatchObject({ status: "timed_out" });
  });

  it("applies the same deadline while resolving credentials/provider", async () => {
    const state = setup({ resolverHangs: true });
    await expect(state.runtime.invoke(binding, {}, { projectId: "project-1", operationName: "lookup" })).rejects.toThrow("provider_timed_out");
    expect(state.counters).toMatchObject({ resolve: 1, provider: 0, cancel: 1, legacy: 0 });
  });

  it("applies the whole-call deadline while project authorization is pending", async () => {
    const state = setup({ authorize: () => new Promise<never>(() => undefined) });
    await expect(state.runtime.invoke(binding, {}, { projectId: "project-1", operationName: "lookup" })).rejects.toThrow("provider_timed_out");
    expect(state.repository.credentialReads).toBe(0);
    expect(state.counters).toMatchObject({ authorize: 1, provider: 0, cancel: 1, legacy: 0 });
  });

  it("classifies credential failure without exposing reference values or falling back", async () => {
    const state = setup({ credentialFailure: true });
    await expect(state.runtime.invoke(binding, {}, { projectId: "project-1", operationName: "lookup" })).rejects.toThrow("credential_missing");
    expect(state.counters).toMatchObject({ resolve: 1, provider: 0, legacy: 0 });
    expect(JSON.stringify(state.repository.receipts)).not.toContain("ACTIVELY_API_TOKEN");
    expect(state.repository.receipts[0]).toMatchObject({ status: "failed", reason: "credential_missing" });
  });

  it("honors caller abort, cancels once, and prevents provider dispatch", async () => {
    const state = setup({ providerResult: "hang" });
    const controller = new AbortController();
    const pending = state.runtime.invoke(binding, {}, { projectId: "project-1", operationName: "lookup", signal: controller.signal });
    controller.abort();
    await expect(pending).rejects.toThrow("request_aborted");
    expect(state.counters).toMatchObject({ provider: 0, cancel: 1, legacy: 0 });
  });

  it("rejects proxies, accessors and unknown binding fields without invoking them", async () => {
    const state = setup();
    let traps = 0;
    const proxy = new Proxy(binding, { ownKeys: () => { traps += 1; return []; } });
    await expect(state.runtime.invoke(proxy, {}, { projectId: "project-1", operationName: "lookup" })).rejects.toThrow("binding_invalid");
    expect(traps).toBe(0);
    const accessor = { ...binding };
    Object.defineProperty(accessor, "pluginName", { enumerable: true, get: () => { traps += 1; return "actively"; } });
    await expect(state.runtime.invoke(accessor, {}, { projectId: "project-1", operationName: "lookup" })).rejects.toThrow("binding_invalid");
    expect(traps).toBe(0);
    await expect(state.runtime.invoke({ ...binding, unexpected: true }, {}, { projectId: "project-1", operationName: "lookup" })).rejects.toThrow("binding_invalid");
  });

  it("rejects a proxied provider resolution without invoking its traps", async () => {
    let traps = 0;
    const state = setup({ resolverProxyTrap: () => { traps += 1; } });
    await expect(state.runtime.invoke(binding, {}, { projectId: "project-1", operationName: "lookup" })).rejects.toThrow("provider_unavailable");
    expect(traps).toBe(0);
    expect(state.counters.provider).toBe(0);
  });

  it("rejects a proxied provider descriptor without invoking its traps", async () => {
    let traps = 0;
    const state = setup({ descriptorProxyTrap: () => { traps += 1; } });
    await expect(state.runtime.invoke(binding, {}, { projectId: "project-1", operationName: "lookup" })).rejects.toThrow("provider_unavailable");
    expect(traps).toBe(0);
    expect(state.counters.provider).toBe(0);
  });

  it("rejects a provider returned outside the hardened resolution envelope", async () => {
    const state = setup({ directProvider: true });
    await expect(state.runtime.invoke(binding, {}, { projectId: "project-1", operationName: "lookup" })).rejects.toThrow("provider_unavailable");
    expect(state.counters.provider).toBe(0);
  });

  it("is pinned to the complete catalog digest", async () => {
    const state = setup();
    expect(PINNED_PLUGIN_CATALOG_DIGEST).toBe(catalog.catalogDigest);
    state.repository.getCatalog = async () => null;
    await expect(state.runtime.invoke(binding, {}, { projectId: "project-1", operationName: "lookup" })).rejects.toThrow("catalog_unavailable");
    expect(state.repository.credentialReads).toBe(0);
  });
});
