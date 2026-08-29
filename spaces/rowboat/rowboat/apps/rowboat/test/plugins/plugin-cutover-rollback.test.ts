import { describe, expect, it } from "vitest";
import { PINNED_OPENAI_PLUGINS_COMMIT, PINNED_PLUGIN_CATALOG_DIGEST, type PluginMigrationRecord, type PluginReceipt } from "@rowboat/openai-plugin-runtime";
import { PluginRuntimeState, parsePluginRuntimeState, Project, type PluginRuntimeStateValue } from "@/src/entities/models/project";
import { SetPluginRuntimeModeUseCase, type SetPluginRuntimeModeDependencies } from "@/src/application/use-cases/plugins/set-plugin-runtime-mode.use-case";
import type { PluginApiIdentity } from "@/src/application/policies/plugin-api-authorization.policy";
import { NextRequest } from "next/server";
import { createRuntimeModeRoute } from "@/src/interface-adapters/http/plugins/plugin-migration-routes";
import { SetPluginRuntimeModeController } from "@/src/interface-adapters/controllers/plugins/set-plugin-runtime-mode.controller";

const projectId = "11111111-1111-4111-8111-111111111111";
const migrationRecordId = "22222222-2222-4222-8222-222222222222";
const installationId = "33333333-3333-4333-8333-333333333333";
const parityReceiptId = `parity:${"c".repeat(64)}`;
const identity: PluginApiIdentity = Object.freeze({ kind: "user", userId: "user-1" });
const now = new Date("2026-08-27T10:00:00.000Z");

const migrationRecord = (overrides: Partial<PluginMigrationRecord> = {}): PluginMigrationRecord => Object.freeze({
  id: migrationRecordId,
  projectId,
  recipeId: "legacy-card:github-pr-to-slack:v1",
  recipeDigest: "d".repeat(64),
  sourceProjectRevision: 7,
  sourceDigest: "e".repeat(64),
  sourceInventoryDigest: "f".repeat(64),
  targetCatalogDigest: PINNED_PLUGIN_CATALOG_DIGEST,
  targetSourceCommit: PINNED_OPENAI_PLUGINS_COMMIT,
  targetPolicyVersion: "1",
  targetInstallationIds: Object.freeze([installationId]),
  rollbackSnapshotDigest: "a".repeat(64),
  status: "applied",
  blockers: Object.freeze([]),
  createdAt: "2026-08-26T10:00:00.000Z",
  ...overrides,
}) as PluginMigrationRecord;

const parityReceipt = (overrides: Partial<PluginReceipt> = {}): PluginReceipt => Object.freeze({
  type: "execution", receiptId: parityReceiptId, projectId, pluginName: "github",
  status: "success", redactions: Object.freeze([]), ...overrides,
}) as PluginReceipt;

interface Harness {
  readonly service: SetPluginRuntimeModeUseCase;
  readonly saved: PluginRuntimeStateValue[];
  readonly receipts: PluginReceipt[];
  readonly calls: string[];
  readonly expectedRevisions: number[];
}

function harness(options: Readonly<{
  state?: PluginRuntimeStateValue | null;
  record?: PluginMigrationRecord | null;
  receipt?: PluginReceipt | null;
  authorize?: () => Promise<void>;
  saveError?: Error;
}> = {}): Harness {
  const saved: PluginRuntimeStateValue[] = [];
  const receipts: PluginReceipt[] = [];
  const calls: string[] = [];
  const expectedRevisions: number[] = [];
  const dependencies: SetPluginRuntimeModeDependencies = {
    async authorizeProject(_actor: PluginApiIdentity, _project: string) { calls.push("authorize"); if (options.authorize !== undefined) await options.authorize(); },
    async loadRuntimeState(_project: string) { calls.push("loadRuntimeState"); return options.state === undefined ? { mode: "legacy" as const, revision: 0 } : options.state; },
    async loadMigrationRecord(_id: string) { calls.push("loadMigrationRecord"); return options.record === undefined ? migrationRecord() : options.record; },
    async loadReceipt(_id: string) { calls.push("loadReceipt"); return options.receipt === undefined ? parityReceipt() : options.receipt; },
    async saveRuntimeState(_project: string, expectedRevision: number, state: PluginRuntimeStateValue) {
      calls.push("saveRuntimeState");
      expectedRevisions.push(expectedRevision);
      if (options.saveError !== undefined) throw options.saveError;
      saved.push(state);
      return state;
    },
    async putReceipt(receipt: PluginReceipt) { calls.push("putReceipt"); receipts.push(receipt); },
    now: () => now,
  };
  return { service: new SetPluginRuntimeModeUseCase(dependencies), saved, receipts, calls, expectedRevisions };
}

const cutoverEvidence = Object.freeze({ catalogDigest: PINNED_PLUGIN_CATALOG_DIGEST, migrationRecordId, parityReceiptId });

function stateFor(mode: PluginRuntimeStateValue["mode"]): PluginRuntimeStateValue {
  if (mode === "legacy") return { mode, revision: 3 };
  if (mode === "shadow") return { mode, revision: 3, migrationRecordId, rollbackSnapshotDigest: "a".repeat(64) };
  return { mode, revision: 3, migrationRecordId, parityReceiptId, rollbackSnapshotDigest: "a".repeat(64), cutoverAt: "2026-08-26T12:00:00.000Z" };
}

describe("reversible plugin runtime cutover", () => {
  it.each([
    ["legacy", "shadow", true],
    ["shadow", "openai", true],
    ["openai", "legacy", true],
    ["legacy", "openai", false],
  ] as const)("permits %s -> %s: %s", async (from, to, allowed) => {
    const { service } = harness({ state: stateFor(from) });
    const operation = service.execute({ identity, projectId, mode: to, expectedRevision: 3, ...cutoverEvidence });
    if (allowed) await expect(operation).resolves.toMatchObject({ mode: to });
    else await expect(operation).rejects.toThrow("runtime_mode_transition_rejected");
  });

  it.each([
    ["shadow", "legacy"],
    ["openai", "shadow"],
    ["legacy", "legacy"],
  ] as const)("rejects the unspecified %s -> %s transition", async (from, to) => {
    const { service, saved } = harness({ state: stateFor(from) });
    await expect(service.execute({ identity, projectId, mode: to, expectedRevision: 3, ...cutoverEvidence })).rejects.toThrow("runtime_mode_transition_rejected");
    expect(saved).toEqual([]);
  });

  it("rejects cutover when parity or rollback evidence is missing", async () => {
    const withoutParity = harness({ state: stateFor("shadow"), receipt: null });
    await expect(withoutParity.service.execute({ identity, projectId, mode: "openai", expectedRevision: 3, ...cutoverEvidence }))
      .rejects.toThrow("cutover_evidence_required");
    const withoutRollback = harness({ state: stateFor("shadow"), record: migrationRecord({ rollbackSnapshotDigest: undefined as unknown as string }) });
    await expect(withoutRollback.service.execute({ identity, projectId, mode: "openai", expectedRevision: 3, ...cutoverEvidence }))
      .rejects.toThrow("cutover_evidence_required");
    expect(withoutParity.saved).toEqual([]);
    expect(withoutRollback.saved).toEqual([]);
  });

  it.each([
    ["a blocked migration record", { record: migrationRecord({ status: "blocked", blockers: [{ capabilityId: "slack", legacyActionId: "post", code: "provider_unavailable", pluginName: "slack", componentId: "slack:post" }], targetInstallationIds: [] }) }],
    ["unresolved blockers", { record: migrationRecord({ blockers: [{ capabilityId: "slack", legacyActionId: "post", code: "provider_unavailable", pluginName: "slack", componentId: "slack:post" }] }) }],
    ["a missing migration record", { record: null }],
    ["a migration record for another project", { record: migrationRecord({ projectId: "44444444-4444-4444-8444-444444444444" }) }],
    ["a stale catalog digest", { record: migrationRecord({ targetCatalogDigest: "b".repeat(64) }) }],
    ["a failed parity receipt", { receipt: parityReceipt({ status: "failed", reason: "parity_failed" }) }],
    ["a parity receipt for another project", { receipt: parityReceipt({ projectId: "44444444-4444-4444-8444-444444444444" }) }],
    ["a parity receipt that is not an execution receipt", { receipt: parityReceipt({ type: "install" }) }],
    ["no target installation", { record: migrationRecord({ targetInstallationIds: [] }) }],
  ])("refuses cutover with %s", async (_label, options) => {
    const { service, saved, receipts } = harness({ state: stateFor("shadow"), ...options });
    await expect(service.execute({ identity, projectId, mode: "openai", expectedRevision: 3, ...cutoverEvidence })).rejects.toThrow("cutover_evidence_required");
    expect(saved).toEqual([]);
    expect(receipts).toEqual([]);
  });

  it("refuses cutover when the request catalog digest is not the pinned catalog", async () => {
    const { service } = harness({ state: stateFor("shadow") });
    await expect(service.execute({ identity, projectId, mode: "openai", expectedRevision: 3, ...cutoverEvidence, catalogDigest: "b".repeat(64) }))
      .rejects.toThrow("catalog_digest_mismatch");
  });

  it("stores cutover evidence, bumps the revision by one, and records a receipt", async () => {
    const { service, saved, receipts } = harness({ state: stateFor("shadow") });
    const result = await service.execute({ identity, projectId, mode: "openai", expectedRevision: 3, ...cutoverEvidence });
    expect(saved).toHaveLength(1);
    expect(saved[0]).toMatchObject({
      mode: "openai", revision: 4, migrationRecordId, parityReceiptId,
      rollbackSnapshotDigest: "a".repeat(64), cutoverAt: now.toISOString(),
    });
    expect(() => PluginRuntimeState.parse(saved[0])).not.toThrow();
    expect(receipts).toHaveLength(1);
    expect(receipts[0]).toMatchObject({ type: "migration", projectId, status: "success" });
    expect(result).toMatchObject({ mode: "openai", revision: 4, projectId });
  });

  it("restores legacy authority on rollback without touching workflow fields", async () => {
    const { service, saved, receipts, calls } = harness({ state: stateFor("openai") });
    const result = await service.execute({ identity, projectId, mode: "legacy", expectedRevision: 3 });
    expect(saved[0]).toMatchObject({ mode: "legacy", revision: 4, rolledBackAt: now.toISOString(), rollbackSnapshotDigest: "a".repeat(64) });
    expect(saved[0]).not.toHaveProperty("cutoverAt");
    expect(receipts[0]).toMatchObject({ type: "migration", projectId, status: "success" });
    expect(result.rolledBack).toBe(true);
    expect(calls).not.toContain("updateDraftWorkflow");
    expect(calls).not.toContain("updateLiveWorkflow");
  });

  it("passes the caller expected revision to the compare-and-swap and surfaces a conflict", async () => {
    const conflict = harness({ state: stateFor("legacy"), saveError: new Error("plugin_runtime_state_conflict") });
    await expect(conflict.service.execute({ identity, projectId, mode: "shadow", expectedRevision: 3 })).rejects.toThrow("plugin_runtime_state_conflict");
    expect(conflict.expectedRevisions).toEqual([3]);
  });

  it("refuses a stale expected revision before writing", async () => {
    const { service, saved } = harness({ state: stateFor("legacy") });
    await expect(service.execute({ identity, projectId, mode: "shadow", expectedRevision: 2 })).rejects.toThrow("plugin_runtime_state_conflict");
    expect(saved).toEqual([]);
  });

  it("authorizes the project before reading or writing any state", async () => {
    const { service, calls, saved } = harness({ authorize: async () => { throw new Error("forbidden"); } });
    await expect(service.execute({ identity, projectId, mode: "shadow", expectedRevision: 0 })).rejects.toThrow("forbidden");
    expect(calls).toEqual(["authorize"]);
    expect(saved).toEqual([]);
  });

  it("reports a missing project instead of creating runtime state", async () => {
    const { service, saved } = harness({ state: null });
    await expect(service.execute({ identity, projectId, mode: "shadow", expectedRevision: 0 })).rejects.toThrow("project_not_found");
    expect(saved).toEqual([]);
  });

  it.each([
    ["an unknown target mode", { mode: "openai_beta" }],
    ["a negative expected revision", { expectedRevision: -1 }],
    ["a fractional expected revision", { expectedRevision: 1.5 }],
    ["an invalid project id", { projectId: "not a project id" }],
    ["an invalid migration record id", { migrationRecordId: "not a record" }],
  ])("rejects %s", async (_label, override) => {
    const { service, calls } = harness({ state: stateFor("shadow") });
    await expect(service.execute({ identity, projectId, mode: "openai", expectedRevision: 3, ...cutoverEvidence, ...override } as never))
      .rejects.toThrow("plugin_runtime_request_invalid");
    expect(calls).toEqual([]);
  });
});

describe("backward compatible plugin runtime project state", () => {
  const projectDocument = Object.freeze({
    id: projectId, name: "demo", createdAt: "2026-08-01T10:00:00.000Z", createdByUserId: "user-1", secret: "s",
    draftWorkflow: { agents: [], prompts: [], tools: [], pipelines: [], startAgent: "a", lastUpdatedAt: "2026-08-01T10:00:00.000Z" },
    liveWorkflow: { agents: [], prompts: [], tools: [], pipelines: [], startAgent: "a", lastUpdatedAt: "2026-08-01T10:00:00.000Z" },
  });

  it("parses an existing project without a plugin runtime field as legacy", () => {
    const parsed = Project.parse(projectDocument);
    expect(parsed.pluginRuntime).toBeUndefined();
    expect(parsePluginRuntimeState(parsed.pluginRuntime)).toEqual({ mode: "legacy", revision: 0 });
    expect(parsePluginRuntimeState(undefined)).toEqual({ mode: "legacy", revision: 0 });
  });

  it("keeps a stored runtime state verbatim and rejects an unknown mode or shape", () => {
    const stored = { mode: "openai" as const, revision: 2, migrationRecordId, parityReceiptId, rollbackSnapshotDigest: "a".repeat(64), cutoverAt: "2026-08-26T12:00:00.000Z" };
    expect(parsePluginRuntimeState(stored)).toEqual(stored);
    expect(Project.parse({ ...projectDocument, pluginRuntime: stored }).pluginRuntime).toEqual(stored);
    expect(() => parsePluginRuntimeState({ mode: "openai_beta", revision: 1 })).toThrow();
    expect(() => parsePluginRuntimeState({ mode: "openai", revision: -1 })).toThrow();
    expect(() => parsePluginRuntimeState({ mode: "openai", revision: 1, unexpected: true })).toThrow();
  });
});

describe("plugin runtime mode route and controller", () => {
  const context = { params: Promise.resolve({ projectId }) };
  const url = `https://rowboat.invalid/api/v1/projects/${projectId}/plugins/runtime-mode`;
  const body = Object.freeze({ mode: "openai", expectedRevision: 3, catalogDigest: PINNED_PLUGIN_CATALOG_DIGEST, migrationRecordId, parityReceiptId });
  const post = (payload: unknown, target = url) => new NextRequest(target, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(payload) });

  it("passes the exact strict body to the controller", async () => {
    const route = createRuntimeModeRoute(async () => ({ execute: async (_request: Request, input: Readonly<Record<string, unknown>>) => input }));
    const response = await route(post(body), context);
    expect(response.status).toBe(200);
    expect(await response.json()).toMatchObject({ projectId, mode: "openai", expectedRevision: 3, catalogDigest: PINNED_PLUGIN_CATALOG_DIGEST, migrationRecordId, parityReceiptId });
  });

  it("rejects a partial, extended, or duplicated body before controller execution", async () => {
    let calls = 0;
    const route = createRuntimeModeRoute(async () => ({ execute: async () => { calls += 1; return {}; } }));
    expect((await route(post({ mode: "shadow", expectedRevision: 0 }), context)).status).toBe(400);
    expect((await route(post({ ...body, extra: true }), context)).status).toBe(400);
    const duplicated = new NextRequest(url, { method: "POST", headers: { "content-type": "application/json" }, body: '{"mode":"shadow","mode":"openai","expectedRevision":0,"catalogDigest":null,"migrationRecordId":null,"parityReceiptId":null}' });
    expect((await route(duplicated, context)).status).toBe(400);
    expect(calls).toBe(0);
  });

  it("rejects a foreign path or a query string before controller resolution", async () => {
    let resolves = 0;
    const route = createRuntimeModeRoute(async () => { resolves += 1; return { execute: async () => ({}) }; });
    expect((await route(post(body, `${url}?force=1`), context)).status).toBe(400);
    expect((await route(post(body, `https://rowboat.invalid/api/v1/projects/${projectId}/plugins/runtime-mode/extra`), context)).status).toBe(400);
    expect(resolves).toBe(0);
  });

  it.each([
    ["runtime_mode_transition_rejected", 409],
    ["cutover_evidence_required", 409],
    ["plugin_runtime_state_conflict", 409],
    ["plugin_runtime_request_invalid", 400],
    ["forbidden", 403],
    ["project_not_found", 404],
  ] as const)("maps %s to HTTP %i", async (reason, status) => {
    const route = createRuntimeModeRoute(async () => ({ execute: async () => { throw new Error(reason); } }));
    const response = await route(post(body), context);
    expect(response.status).toBe(status);
    expect(await response.json()).toEqual({ error: reason });
  });

  it("authenticates before running the use case and drops null evidence references", async () => {
    const calls: string[] = [];
    const executed: unknown[] = [];
    const controller = new SetPluginRuntimeModeController({
      authorization: { authenticate: async () => { calls.push("authenticate"); return identity; } } as never,
      useCase: { execute: async (input: unknown) => { calls.push("execute"); executed.push(input); return { mode: "shadow" }; } } as never,
    });
    const result = await controller.execute(new NextRequest(url, { method: "POST" }), Object.freeze({
      projectId, mode: "shadow", expectedRevision: 0, catalogDigest: null, migrationRecordId: null, parityReceiptId: null,
      callerSignal: new AbortController().signal,
    }));
    expect(calls).toEqual(["authenticate", "execute"]);
    expect(result).toEqual({ mode: "shadow" });
    expect(executed[0]).toEqual({ identity, projectId, mode: "shadow", expectedRevision: 0 });
  });

  it("refuses an unknown mode, a non-numeric revision, and a non-string evidence reference", async () => {
    const controller = new SetPluginRuntimeModeController({
      authorization: { authenticate: async () => { throw new Error("unexpected"); } } as never,
      useCase: { execute: async () => { throw new Error("unexpected"); } } as never,
    });
    const base = { projectId, mode: "shadow", expectedRevision: 0, catalogDigest: null, migrationRecordId: null, parityReceiptId: null, callerSignal: new AbortController().signal };
    await expect(controller.execute(new NextRequest(url, { method: "POST" }), { ...base, mode: "openai_beta" })).rejects.toThrow("plugin_runtime_request_invalid");
    await expect(controller.execute(new NextRequest(url, { method: "POST" }), { ...base, expectedRevision: "0" })).rejects.toThrow("plugin_runtime_request_invalid");
    await expect(controller.execute(new NextRequest(url, { method: "POST" }), { ...base, migrationRecordId: 7 })).rejects.toThrow("plugin_runtime_request_invalid");
  });
});
