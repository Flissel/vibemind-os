import { describe, expect, it, vi } from "vitest";
import catalogFixture from "../../../../config/openai-plugin-catalog.lock.json";
import {
  PINNED_PLUGIN_CATALOG_DIGEST,
  type PluginCatalogLock,
  type PluginProvider,
  type ProviderContext,
  type ProviderBinding,
} from "@rowboat/openai-plugin-runtime";
import type {
  IPluginsRepository,
  PluginComponentAdmission,
  PluginCredentialSlot,
  PluginExecutionDispatchClaim,
  PluginInstallation,
  PluginReceipt,
} from "@/src/application/repositories/plugins.repository.interface";
import {
  PluginToolRuntime,
  PluginToolRuntimeError,
  type PluginProviderResolutionInput,
  type PluginToolAuthorizationContext,
  type PluginToolRuntimeDependencies,
} from "@/src/application/services/plugin-tool-runtime";
import { ProjectActionAuthorizationPolicy } from "@/src/application/policies/project-action-authorization.policy";
import type { IProjectMembersRepository } from "@/src/application/repositories/project-members.repository.interface";
import type { IApiKeysRepository } from "@/src/application/repositories/api-keys.repository.interface";
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
  receiptHangs = false;
  installationReads = 0;
  admissionReads = 0;
  disableAfterFirstAdmissionRead = false;
  dispatchClaims: Readonly<Record<string, unknown>>[] = [];
  claimCalls = 0;
  async getCatalog(digest: string): Promise<PluginCatalogLock | null> { return digest === catalog.catalogDigest ? catalog : null; }
  async getInstallation(projectId: string, pluginName: string): Promise<PluginInstallation | null> {
    this.installationReads += 1;
    return projectId === installation.projectId && pluginName === installation.pluginName ? this.currentInstallation : null;
  }
  async listAdmissions(): Promise<readonly PluginComponentAdmission[]> {
    this.admissionReads += 1;
    if (this.disableAfterFirstAdmissionRead && this.admissionReads === 1) {
      this.currentInstallation = Object.freeze({ ...installation, enabled: false, revision: 8 });
    }
    return this.currentAdmissions;
  }
  async listCredentialSlots(): Promise<readonly PluginCredentialSlot[]> { this.credentialReads += 1; return this.currentSlots; }
  async putReceipt(receipt: PluginReceipt): Promise<void> {
    if (this.receiptHangs) return new Promise<never>(() => undefined);
    if (this.receiptFailure) throw new Error("database internals secret-value");
    this.receipts.push(receipt);
  }
  async getReceipt(receiptId: string): Promise<PluginReceipt | null> {
    return this.receipts.find(candidate => candidate.receiptId === receiptId) ?? null;
  }
  async claimExecutionDispatch(input: PluginExecutionDispatchClaim): Promise<void> {
    this.claimCalls += 1;
    const current = this.currentInstallation;
    const currentAdmission = this.currentAdmissions.filter((item) => item.componentDigest === input.componentDigest);
    if (
      current === null || !current.enabled || current.id !== input.installationId || current.projectId !== input.projectId
      || current.pluginName !== input.pluginName || current.revision !== input.installationRevision
      || input.catalogDigest !== catalog.catalogDigest
      || currentAdmission.length !== 1 || currentAdmission[0]?.status !== "admitted"
      || currentAdmission[0]?.componentKind !== input.componentKind || currentAdmission[0]?.componentName !== input.componentName
      || currentAdmission[0]?.policyVersion !== input.admissionPolicyVersion
      || JSON.stringify(this.currentSlots) !== JSON.stringify(input.credentialSlots)
    ) throw new Error("execution_claim_conflict");
    this.dispatchClaims.push(Object.freeze({ requestId: input.requestId, componentDigest: input.componentDigest, providerBindingId: input.providerBindingId }));
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
  async getMigrationRecord(): Promise<null> { return null; }
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
  readonly providerOutput?: unknown;
  readonly resolverMutation?: "disable" | "admission" | "credential";
  readonly providerMutation?: "disable" | "admission" | "credential";
  readonly providerWaitsForAbort?: boolean;
  readonly timeoutMilliseconds?: number;
  readonly authorizationContext?: PluginToolAuthorizationContext | false;
  readonly authorize?: (authorization: PluginToolAuthorizationContext, projectId: string) => Promise<void>;
} = {}) {
  const repository = new FakeRepository();
  const counters = { authorize: 0, resolve: 0, provider: 0, cancel: 0, legacy: 0 };
  let observedReference = "";
  let providerSettled = false;
  const mutateExecutionState = (mutation: "disable" | "admission" | "credential"): void => {
    if (mutation === "disable") repository.currentInstallation = Object.freeze({ ...installation, enabled: false, revision: 8 });
    if (mutation === "admission") repository.currentAdmissions = [Object.freeze({ ...admission, status: "rejected", reason: "component_unsupported" })];
    if (mutation === "credential") repository.currentSlots = [Object.freeze({ ...credentialSlot, reference: Object.freeze({ kind: "environment", reference: "ROTATED_SECRET_REFERENCE" }) })];
  };
  const provider: PluginProvider = Object.freeze({
    id: providerBinding.id,
    describe: () => options.descriptorProxyTrap === undefined
      ? Object.freeze({ id: providerBinding.id, kind: providerBinding.providerKind, temporaryAdapter: false })
      : new Proxy(Object.freeze({ id: providerBinding.id, kind: providerBinding.providerKind, temporaryAdapter: false }), {
        get: (target, key, receiver) => { options.descriptorProxyTrap?.(); return Reflect.get(target, key, receiver); },
        ownKeys: () => { options.descriptorProxyTrap?.(); return []; },
      }),
    invoke: async (_request: Parameters<PluginProvider["invoke"]>[0], providerContext: ProviderContext): Promise<Awaited<ReturnType<PluginProvider["invoke"]>>> => {
      counters.provider += 1;
      if (options.providerWaitsForAbort === true) {
        const signal = (providerContext as ProviderContext & { readonly signal?: AbortSignal }).signal;
        if (signal === undefined) return new Promise(() => undefined);
        await new Promise<void>((resolve) => {
          if (signal.aborted) resolve();
          else signal.addEventListener("abort", () => resolve(), { once: true });
        });
        providerSettled = true;
        return Object.freeze({ status: "failed" as const, reason: "aborted" });
      }
      if (options.providerMutation !== undefined) mutateExecutionState(options.providerMutation);
      if (options.providerResult === "failed") return Object.freeze({ status: "failed" as const, reason: "remote secret-value failure" });
      if (options.providerResult === "hang") return new Promise(() => undefined);
      return Object.freeze({ status: "success" as const, output: options.providerOutput ?? Object.freeze({ ok: true }) });
    },
  });
  const dependencies: PluginToolRuntimeDependencies = Object.freeze({
    pluginsRepository: repository,
    authorizationContext: options.authorizationContext === false
      ? undefined
      : options.authorizationContext ?? Object.freeze({ caller: "user" as const, userId: "user-1" }),
    authorizeProject: async (authorization: PluginToolAuthorizationContext, projectId: string) => {
      counters.authorize += 1;
      await options.authorize?.(authorization, projectId);
    },
    classifyOperation: () => options.operationCapability ?? "read",
    resolveProvider: async (input: PluginProviderResolutionInput) => {
      counters.resolve += 1;
      observedReference = input.credentialSlots[0]?.reference.reference ?? "";
      if (options.resolverMutation !== undefined) mutateExecutionState(options.resolverMutation);
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
    timeoutMilliseconds: options.timeoutMilliseconds ?? 20,
    receiptTimeoutMilliseconds: 15,
    createRequestId: () => "request-1",
    onCancel: () => { counters.cancel += 1; },
  });
  return {
    runtime: new PluginToolRuntime(dependencies), repository, counters,
    get observedReference() { return observedReference; },
    get providerSettled() { return providerSettled; },
  };
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
    }), "project-1", async (authorizationContext) => {
      expect(authorizationContext).toEqual({ caller: "user", userId: "user-1" });
      return ({
      invoke: async (receivedBinding, args, context) => {
        calls += 1;
        expect(receivedBinding).toEqual(binding);
        expect(args).toEqual({ query: "safe" });
        expect(context).toEqual({ projectId: "project-1", operationName: "actively_lookup" });
        return { status: "success" as const, output: { routed: true } };
      },
      });
    }, Object.freeze({ caller: "user", userId: "user-1" }));
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
    expect(state.repository.credentialReads).toBe(6);
    expect(state.observedReference).toBe("ROTATED_TOKEN");

    const denied = setup({ authorize: async () => { throw new Error("forbidden secret-policy-detail"); } });
    await expect(denied.runtime.invoke(binding, {}, { projectId: "project-1", operationName: "lookup" })).rejects.toThrow("authorization_denied");
    expect(denied.repository.installationReads).toBe(0);
    expect(denied.repository.credentialReads).toBe(0);
    expect(denied.counters.resolve).toBe(0);
  });

  it("fails closed without a trusted actor authorization context before all reads", async () => {
    const state = setup({ authorizationContext: false });
    await expect(state.runtime.invoke(binding, {}, { projectId: "project-1", operationName: "lookup" })).rejects.toThrow("authorization_context_missing");
    expect(state.repository.installationReads).toBe(0);
    expect(state.repository.credentialReads).toBe(0);
    expect(state.counters.provider).toBe(0);
  });

  it.each([
    Object.freeze({ caller: "user" as const, userId: "user-without-membership" }),
    Object.freeze({ caller: "api" as const, apiKey: "invalid-api-key" }),
  ])("rejects an existing project without trusted membership or API-key authority", async (authorizationContext) => {
    const policy = new ProjectActionAuthorizationPolicy({
      projectMembersRepository: { exists: async () => false } as unknown as IProjectMembersRepository,
      apiKeysRepository: { checkAndConsumeKey: async () => false } as unknown as IApiKeysRepository,
    });
    const state = setup({
      authorizationContext,
      authorize: async (authorization, projectId) => policy.authorize({ ...authorization, projectId }),
    });
    await expect(state.runtime.invoke(binding, {}, { projectId: "project-1", operationName: "lookup" })).rejects.toThrow("authorization_denied");
    expect(state.repository.installationReads).toBe(0);
    expect(state.repository.credentialReads).toBe(0);
    expect(state.counters.resolve).toBe(0);
    expect(state.counters.provider).toBe(0);
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
    await expect(state.runtime.invoke(binding, {}, { projectId: "project-1", operationName: "lookup" })).rejects.toThrow("execution_state_invalid");
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

  it("revalidates installation revision and enabled state before provider dispatch", async () => {
    const state = setup();
    state.repository.disableAfterFirstAdmissionRead = true;
    await expect(state.runtime.invoke(binding, {}, { projectId: "project-1", operationName: "lookup" })).rejects.toThrow("execution_state_changed");
    expect(state.repository.installationReads).toBeGreaterThanOrEqual(2);
    expect(state.counters.provider).toBe(0);
    expect(state.repository.receipts.some((receipt) => receipt.status === "success")).toBe(false);
  });

  it.each(["disable", "admission", "credential"] as const)(
    "atomically rejects resolver-induced %s revocation before provider dispatch",
    async (resolverMutation) => {
      const state = setup({ resolverMutation });
      await expect(state.runtime.invoke(binding, {}, { projectId: "project-1", operationName: "lookup" })).rejects.toThrow("execution_state_changed");
      expect(state.repository.claimCalls).toBe(1);
      expect(state.counters.provider).toBe(0);
      expect(state.repository.receipts).toHaveLength(0);
    },
  );

  it.each(["disable", "admission", "credential"] as const)(
    "records exactly one redacted failure after provider side effect and concurrent %s",
    async (providerMutation) => {
      const state = setup({ providerMutation, providerOutput: Object.freeze({ secret: "raw-provider-secret" }) });
      await expect(state.runtime.invoke(binding, {}, { projectId: "project-1", operationName: "lookup" })).rejects.toThrow("execution_state_changed");
      expect(state.counters.provider).toBe(1);
      expect(state.repository.receipts).toHaveLength(1);
      expect(state.repository.receipts[0]).toMatchObject({ status: "failed", reason: "execution_state_changed" });
      expect(JSON.stringify(state.repository.receipts)).not.toContain("raw-provider-secret");
      expect(JSON.stringify(state.repository.receipts)).not.toContain("ROTATED_SECRET_REFERENCE");
    },
  );

  it("propagates caller abort after dispatch and settles underlying provider work", async () => {
    const abort = new AbortController();
    const state = setup({ providerWaitsForAbort: true, timeoutMilliseconds: 200 });
    const invocation = state.runtime.invoke(binding, {}, { projectId: "project-1", operationName: "lookup", signal: abort.signal });
    const rejection = expect(invocation).rejects.toThrow("request_aborted");
    await vi.waitFor(() => expect(state.counters.provider).toBe(1));
    abort.abort();
    await rejection;
    expect(state.providerSettled).toBe(true);
    expect(state.counters.cancel).toBe(1);
    expect(state.repository.receipts).toHaveLength(1);
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

  it("bounds a hanging timeout receipt without replacing the original timeout", async () => {
    const state = setup({ providerResult: "hang" });
    state.repository.receiptHangs = true;
    const watchdog = new Promise<"watchdog">((resolve) => setTimeout(() => resolve("watchdog"), 150));
    const outcome = await Promise.race([
      state.runtime.invoke(binding, {}, { projectId: "project-1", operationName: "lookup" }).then(() => "resolved", (error: unknown) => error),
      watchdog,
    ]);
    expect(outcome).toBeInstanceOf(PluginToolRuntimeError);
    expect((outcome as PluginToolRuntimeError).code).toBe("provider_timed_out");
    expect(state.counters).toMatchObject({ cancel: 1, provider: 1, legacy: 0 });
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

  it("rejects proxied repository state before forwarding credentials", async () => {
    let traps = 0;
    const state = setup();
    state.repository.currentInstallation = new Proxy(installation, {
      get: (target, key, receiver) => { traps += 1; return Reflect.get(target, key, receiver); },
      ownKeys: () => { traps += 1; return []; },
    });
    await expect(state.runtime.invoke(binding, {}, { projectId: "project-1", operationName: "lookup" })).rejects.toThrow("execution_state_invalid");
    expect(traps).toBeGreaterThan(0); // Promise resolution probes `then`; service still rejects before field use.
    expect(state.repository.credentialReads).toBe(0);
    expect(state.counters.provider).toBe(0);
  });

  it("rejects repository accessors without invoking them", async () => {
    let getterCalls = 0;
    const state = setup();
    const malicious = { ...installation };
    Object.defineProperty(malicious, "enabled", { enumerable: true, get: () => { getterCalls += 1; return true; } });
    state.repository.currentInstallation = malicious as PluginInstallation;
    await expect(state.runtime.invoke(binding, {}, { projectId: "project-1", operationName: "lookup" })).rejects.toThrow("execution_state_invalid");
    expect(getterCalls).toBe(0);
    expect(state.counters.provider).toBe(0);
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

  it("rejects provider result accessors without invoking them or writing success", async () => {
    let getterCalls = 0;
    const output = Object.create(null) as Record<string, unknown>;
    Object.defineProperty(output, "secret", { enumerable: true, get: () => { getterCalls += 1; return "secret-value"; } });
    const state = setup({ providerOutput: output });
    await expect(state.runtime.invoke(binding, {}, { projectId: "project-1", operationName: "lookup" })).rejects.toThrow("provider_result_invalid");
    expect(getterCalls).toBe(0);
    expect(state.repository.receipts.some((receipt) => receipt.status === "success")).toBe(false);
    expect(JSON.stringify(state.repository.receipts)).not.toContain("secret-value");
  });

  it("rejects cyclic and oversized provider results with typed redacted failures", async () => {
    const cyclic: Record<string, unknown> = {};
    cyclic.self = cyclic;
    for (const output of [cyclic, { value: "x".repeat(70 * 1024) }, { ["k".repeat(70 * 1024)]: true }]) {
      const state = setup({ providerOutput: output });
      await expect(state.runtime.invoke(binding, {}, { projectId: "project-1", operationName: "lookup" })).rejects.toThrow("provider_result_invalid");
      expect(state.repository.receipts.some((receipt) => receipt.status === "success")).toBe(false);
    }
  });

  it("is pinned to the complete catalog digest", async () => {
    const state = setup();
    expect(PINNED_PLUGIN_CATALOG_DIGEST).toBe(catalog.catalogDigest);
    state.repository.getCatalog = async () => null;
    await expect(state.runtime.invoke(binding, {}, { projectId: "project-1", operationName: "lookup" })).rejects.toThrow("catalog_unavailable");
    expect(state.repository.credentialReads).toBe(0);
  });
});
