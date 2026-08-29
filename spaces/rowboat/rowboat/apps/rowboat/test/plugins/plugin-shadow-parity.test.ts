import { describe, expect, it } from "vitest";
import { compareParityRepresentations, PARITY_DIMENSIONS, PluginParityError, type ParityRepresentationInput } from "@/src/application/services/plugin-parity-report";
import { parity, planShadowToolConfig, type ShadowInvocable } from "@/src/application/services/plugin-shadow-parity";

const projectId = "11111111-1111-4111-8111-111111111111";
const request = Object.freeze({ projectId, pluginName: "github", operationName: "create_issue", input: { title: "hello", token: "sk-live-should-never-be-persisted" } });

interface Spy extends ShadowInvocable {
  calls: number;
}

function providerSpy(options: Readonly<{ effect?: unknown; throwOnCall?: boolean; result?: unknown; representation?: ParityRepresentationInput }>): Spy {
  const spy: Spy = {
    calls: 0,
    effect: options.effect,
    ...(options.representation === undefined ? {} : { representation: options.representation }),
    async invoke(input: unknown): Promise<unknown> {
      spy.calls += 1;
      if (options.throwOnCall === true) throw new Error("shadow_invoked");
      return options.result === undefined ? { ok: true, echo: input } : options.result;
    },
  };
  return spy;
}

const legacyFixture: ParityRepresentationInput = {
  agents: [
    { id: "support", model: "gpt-4.1", tools: ["create_issue", "search_issues"] },
    { id: "triage", model: "gpt-4.1", tools: [] },
  ],
  prompts: [{ id: "greeting", role: "system", body: "welcome" }],
  tools: [{ id: "create_issue", capability: "write" }],
  providers: [{ id: "github", capability: "write", transport: "process" }],
};

const pluginFixture: ParityRepresentationInput = {
  agents: [
    { id: "triage", model: "gpt-4.1", tools: [] },
    { id: "support", model: "gpt-4.1", tools: ["search_issues", "create_issue"] },
  ],
  prompts: [{ id: "greeting", role: "system", body: "welcome" }],
  tools: [{ id: "create_issue", capability: "write" }],
  providers: [{ id: "github", capability: "write", transport: "process" }],
};

describe("write-safe plugin shadow parity", () => {
  it("compares write-capable descriptors without executing the shadow provider", async () => {
    const active = providerSpy({ effect: "write" });
    const shadow = providerSpy({ effect: "write", throwOnCall: true });
    const report = await parity.compareAndRun({ active, shadow, request });
    expect(active.calls).toBe(1);
    expect(shadow.calls).toBe(0);
    expect(report.shadow.execution).toBe("descriptor_only");
  });

  it("normalizes agent, prompt, tool, and provider descriptors", async () => {
    const report = await parity.compareOnly(legacyFixture, pluginFixture);
    expect(report.dimensions).toEqual(["agent", "prompt", "tool", "provider"]);
    expect(report.differences).toEqual([]);
    expect(report.matched).toBe(true);
    expect(report.legacyDigest).toBe(report.pluginDigest);
    expect(report.shadow.execution).toBe("not_requested");
    expect(report.shadow.comparedOutput).toBe(false);
  });

  it("treats an unknown or absent effect classification as write-capable", async () => {
    for (const effect of [undefined, null, "READ", "read-only", 1, {}]) {
      const active = providerSpy({ effect: "read" });
      const shadow = providerSpy({ effect, throwOnCall: true });
      const report = await parity.compareAndRun({ active, shadow, request });
      expect(shadow.calls).toBe(0);
      expect(report.shadow.execution).toBe("descriptor_only");
      expect(report.shadow.effect).toBe("write");
    }
  });

  it("invokes a read-classified shadow exactly once and compares outputs directly", async () => {
    const active = providerSpy({ effect: "write", result: { issues: 2 } });
    const shadow = providerSpy({ effect: "read", result: { issues: 2 } });
    const report = await parity.compareAndRun({ active, shadow, request });
    expect(active.calls).toBe(1);
    expect(shadow.calls).toBe(1);
    expect(report.shadow.execution).toBe("read_only");
    expect(report.shadow.comparedOutput).toBe(true);
    expect(report.shadow.outputMatched).toBe(true);
    expect(report.shadow.activeOutputDigest).toBe(report.shadow.shadowOutputDigest);
  });

  it("never claims output parity for a descriptor-only write shadow", async () => {
    const active = providerSpy({ effect: "write", result: { issues: 2 } });
    const shadow = providerSpy({ effect: "write", result: { issues: 2 } });
    const report = await parity.compareAndRun({ active, shadow, request });
    expect(report.shadow.comparedOutput).toBe(false);
    expect(report.shadow.outputMatched).toBeNull();
    expect(report.shadow.shadowOutputDigest).toBeNull();
    expect(shadow.calls).toBe(0);
  });

  it("records a failing read-only shadow without failing the active result", async () => {
    const active = providerSpy({ effect: "read", result: { issues: 2 } });
    const shadow = providerSpy({ effect: "read", throwOnCall: true });
    const report = await parity.compareAndRun({ active, shadow, request });
    expect(report.activeResult).toEqual({ issues: 2 });
    expect(report.shadow.execution).toBe("read_only");
    expect(report.shadow.failed).toBe(true);
    expect(report.shadow.outputMatched).toBe(false);
    expect(report.matched).toBe(false);
  });

  it("passes an immutable capture of the caller input and never shares it between paths", async () => {
    const input = { title: "hello", nested: { list: [1, 2] } };
    const seen: unknown[] = [];
    const active: ShadowInvocable = { effect: "read", async invoke(value: unknown) { seen.push(value); return { ok: true }; } };
    const shadow: ShadowInvocable = { effect: "read", async invoke(value: unknown) { seen.push(value); return { ok: true }; } };
    await parity.compareAndRun({ active, shadow, request: { ...request, input } });
    expect(input).toEqual({ title: "hello", nested: { list: [1, 2] } });
    expect(seen).toHaveLength(2);
    expect(seen[0]).not.toBe(seen[1]);
    expect(Object.isFrozen(seen[0])).toBe(true);
    expect(seen[1]).toEqual({ title: "hello", nested: { list: [1, 2] } });
  });

  it("invokes the active path exactly once even when the comparison reports differences", async () => {
    const active = providerSpy({ effect: "write", representation: legacyFixture });
    const shadow = providerSpy({ effect: "write", representation: { ...pluginFixture, tools: [] } });
    const report = await parity.compareAndRun({ active, shadow, request });
    expect(active.calls).toBe(1);
    expect(report.matched).toBe(false);
    expect(report.differences).toEqual([{ dimension: "tool", code: "missing_in_plugin", id: "create_issue" }]);
  });

  it("reports identity-only differences per dimension and never descriptor values", async () => {
    const report = await parity.compareOnly(legacyFixture, {
      ...pluginFixture,
      agents: [{ id: "support", model: "gpt-4.1-mini", tools: ["create_issue", "search_issues"] }, { id: "copilot", model: "gpt-4.1", tools: [] }],
    });
    expect(report.differences).toEqual([
      { dimension: "agent", code: "descriptor_mismatch", id: "support" },
      { dimension: "agent", code: "missing_in_legacy", id: "copilot" },
      { dimension: "agent", code: "missing_in_plugin", id: "triage" },
    ]);
    expect(JSON.stringify(report.differences)).not.toContain("gpt-4.1-mini");
  });

  it("treats identifier arrays as order-insensitive sets and rejects duplicates", () => {
    const shuffled = compareParityRepresentations(legacyFixture, pluginFixture);
    expect(shuffled.matched).toBe(true);
    expect(() => compareParityRepresentations(legacyFixture, { ...pluginFixture, agents: [{ id: "support", model: "gpt-4.1", tools: ["create_issue", "create_issue", "search_issues"] }, { id: "triage", model: "gpt-4.1", tools: [] }] }))
      .toThrow("parity_source_invalid");
    expect(() => compareParityRepresentations(legacyFixture, { ...pluginFixture, tools: [{ id: "create_issue", capability: "write" }, { id: "create_issue", capability: "read" }] }))
      .toThrow("parity_source_invalid");
  });

  it("fails closed on getters, proxies, and unknown dimensions", () => {
    const withGetter = { id: "support", get model(): string { return "gpt-4.1"; } };
    expect(() => compareParityRepresentations(legacyFixture, { ...pluginFixture, agents: [withGetter] })).toThrow(PluginParityError);
    expect(() => compareParityRepresentations(legacyFixture, new Proxy(pluginFixture, {}) as ParityRepresentationInput)).toThrow("parity_source_invalid");
    expect(() => compareParityRepresentations(legacyFixture, { ...pluginFixture, extra: [] } as unknown as ParityRepresentationInput)).toThrow("parity_source_invalid");
    expect(() => compareParityRepresentations(legacyFixture, { agents: [], prompts: [], tools: [] } as unknown as ParityRepresentationInput)).toThrow("parity_source_invalid");
    expect(PARITY_DIMENSIONS).toEqual(["agent", "prompt", "tool", "provider"]);
  });

  it("persists redacted receipt evidence that carries no argument or result values", async () => {
    const receipts: unknown[] = [];
    const active = providerSpy({ effect: "write", result: { issueUrl: "https://example.invalid/1", token: "sk-live-should-never-be-persisted" }, representation: legacyFixture });
    const shadow = providerSpy({ effect: "write", representation: { ...pluginFixture, tools: [] } });
    const report = await parity.compareAndRun({ active, shadow, request, receipts: { async putReceipt(receipt: unknown) { receipts.push(receipt); } } });
    expect(receipts).toHaveLength(1);
    const serialized = JSON.stringify(receipts[0]);
    expect(serialized).not.toContain("sk-live-should-never-be-persisted");
    expect(serialized).not.toContain("example.invalid");
    expect(serialized).not.toContain("hello");
    expect(receipts[0]).toMatchObject({ type: "execution", projectId, pluginName: "github", status: "failed", reason: "parity_failed" });
    expect(report.receipt).toEqual(receipts[0]);
    expect(report.receipt.redactions.length).toBeGreaterThan(0);
    expect(report.reportDigest).toMatch(/^[a-f0-9]{64}$/);
  });

  it("records a success receipt with a stable digest for a matching comparison", async () => {
    const receipts: unknown[] = [];
    const first = await parity.compareAndRun({
      active: providerSpy({ effect: "read", representation: legacyFixture, result: { ok: true } }),
      shadow: providerSpy({ effect: "read", representation: pluginFixture, result: { ok: true } }),
      request,
      receipts: { async putReceipt(receipt: unknown) { receipts.push(receipt); } },
    });
    const second = await parity.compareAndRun({
      active: providerSpy({ effect: "read", representation: legacyFixture, result: { ok: true } }),
      shadow: providerSpy({ effect: "read", representation: pluginFixture, result: { ok: true } }),
      request,
    });
    expect(first.reportDigest).toBe(second.reportDigest);
    expect(receipts[0]).toMatchObject({ type: "execution", status: "success" });
    expect(first.matched).toBe(true);
  });

  it("rejects an invalid request identity and a non-callable provider before invoking anything", async () => {
    const active = providerSpy({ effect: "read" });
    await expect(parity.compareAndRun({ active, shadow: providerSpy({ effect: "read" }), request: { ...request, projectId: "not a project id" } })).rejects.toThrow("parity_request_invalid");
    await expect(parity.compareAndRun({ active, shadow: { effect: "read", invoke: undefined as unknown as ShadowInvocable["invoke"] }, request })).rejects.toThrow("parity_request_invalid");
    expect(active.calls).toBe(0);
  });
});

describe("shadow runtime mode tool planning", () => {
  const readBinding = Object.freeze({ installationId: "22222222-2222-4222-8222-222222222222", pluginName: "github", componentDigest: "a".repeat(64), providerBindingId: "github.read", capability: "read" as const });
  const writeBinding = Object.freeze({ ...readBinding, providerBindingId: "github.write", capability: "write" as const });
  const toolConfig = (binding: unknown) => ({ search: { name: "search", description: "", parameters: { type: "object", properties: {} }, pluginBinding: binding } });

  it("keeps plugin bindings executable only on the openai runtime mode", () => {
    const planned = planShadowToolConfig("openai", toolConfig(writeBinding));
    expect(planned.toolConfig.search.pluginBinding).toEqual(writeBinding);
    expect(planned.shadow.execution).toBe("not_requested");
    expect(planned.active).toBe("plugin");
  });

  it("keeps legacy authoritative and strips plugin bindings in legacy mode", () => {
    const planned = planShadowToolConfig("legacy", toolConfig(readBinding));
    expect(planned.toolConfig.search.pluginBinding).toBeUndefined();
    expect(planned.active).toBe("legacy");
    expect(planned.shadow.execution).toBe("not_requested");
    expect(planned.shadow.bindings).toEqual([]);
  });

  it("keeps legacy active in shadow mode and permits read-only shadow execution only when every binding is read-classified", () => {
    const readOnly = planShadowToolConfig("shadow", toolConfig(readBinding));
    expect(readOnly.active).toBe("legacy");
    expect(readOnly.toolConfig.search.pluginBinding).toBeUndefined();
    expect(readOnly.shadow.execution).toBe("read_only");
    expect(readOnly.shadow.bindings).toEqual([readBinding]);

    const mixed = planShadowToolConfig("shadow", { ...toolConfig(readBinding), write: { name: "write", description: "", parameters: { type: "object", properties: {} }, pluginBinding: writeBinding } });
    expect(mixed.shadow.execution).toBe("descriptor_only");
    expect(mixed.toolConfig.write.pluginBinding).toBeUndefined();
  });

  it("classifies an unparseable binding as write-capable and fails closed on an unknown mode", () => {
    const planned = planShadowToolConfig("shadow", toolConfig({ ...readBinding, capability: "readonly" }));
    expect(planned.shadow.execution).toBe("descriptor_only");
    expect(planned.toolConfig.search.pluginBinding).toBeUndefined();
    expect(() => planShadowToolConfig("openai_beta" as never, toolConfig(readBinding))).toThrow("plugin_runtime_mode_invalid");
  });

  it("never mutates the caller tool configuration", () => {
    const original = toolConfig(writeBinding);
    const snapshot = structuredClone(original);
    planShadowToolConfig("shadow", original);
    expect(original).toEqual(snapshot);
  });
});
