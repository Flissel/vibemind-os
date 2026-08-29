import { describe, expect, it } from "vitest";
import { PINNED_OPENAI_PLUGINS_COMMIT, PINNED_PLUGIN_CATALOG_DIGEST, type PluginMigrationRecord, type PluginReceipt } from "@rowboat/openai-plugin-runtime";
import type { PluginRuntimeStateValue } from "@/src/entities/models/project";
import { LegacyPluginRemovalGate, ROLLBACK_RETENTION_MS, type ProjectRuntimeStateEntry } from "@/src/application/services/legacy-plugin-removal-gate";

const migrationRecordId = "22222222-2222-4222-8222-222222222222";
const installationId = "33333333-3333-4333-8333-333333333333";
const parityReceiptId = `parity:${"c".repeat(64)}`;
const now = new Date("2026-09-30T10:00:00.000Z");
const longAgo = new Date(now.getTime() - ROLLBACK_RETENTION_MS - 1_000).toISOString();

const record = (overrides: Partial<PluginMigrationRecord> = {}): PluginMigrationRecord => Object.freeze({
  id: migrationRecordId, projectId: "11111111-1111-4111-8111-111111111111",
  recipeId: "legacy-card:github-pr-to-slack:v1", recipeDigest: "d".repeat(64),
  sourceProjectRevision: 7, sourceDigest: "e".repeat(64), sourceInventoryDigest: "f".repeat(64),
  targetCatalogDigest: PINNED_PLUGIN_CATALOG_DIGEST, targetSourceCommit: PINNED_OPENAI_PLUGINS_COMMIT, targetPolicyVersion: "1",
  targetInstallationIds: Object.freeze([installationId]), rollbackSnapshotDigest: "a".repeat(64),
  status: "verified", blockers: Object.freeze([]), createdAt: "2026-08-26T10:00:00.000Z", ...overrides,
}) as PluginMigrationRecord;

const receipt = (overrides: Partial<PluginReceipt> = {}): PluginReceipt => Object.freeze({
  type: "execution", receiptId: parityReceiptId, projectId: "11111111-1111-4111-8111-111111111111",
  pluginName: "github", status: "success", redactions: Object.freeze([]), ...overrides,
}) as PluginReceipt;

// Evidence is per project: each project references its own migration record
// and parity receipt, so a report can never satisfy one project with another
// project's evidence.
const recordIdFor = (projectId: string) => `2${projectId.slice(1)}`;
const receiptIdFor = (projectId: string) => `parity:${projectId.slice(0, 8)}${"c".repeat(56)}`;

function projectFixture(state: Partial<PluginRuntimeStateValue> & Readonly<{ mode: PluginRuntimeStateValue["mode"] }>, id = "11111111-1111-4111-8111-111111111111"): ProjectRuntimeStateEntry {
  const base = state.mode === "openai"
    ? { revision: 4, migrationRecordId: recordIdFor(id), parityReceiptId: receiptIdFor(id), rollbackSnapshotDigest: "a".repeat(64), cutoverAt: longAgo }
    : { revision: 1 };
  return Object.freeze({ projectId: id, state: Object.freeze({ ...base, ...state }) as PluginRuntimeStateValue });
}

function gateFor(projects: readonly ProjectRuntimeStateEntry[], options: Readonly<{ record?: PluginMigrationRecord | null; receipt?: PluginReceipt | null }> = {}): LegacyPluginRemovalGate {
  const byRecordId = new Map(projects.map(project => [recordIdFor(project.projectId), record({ id: recordIdFor(project.projectId), projectId: project.projectId })]));
  const byReceiptId = new Map(projects.map(project => [receiptIdFor(project.projectId), receipt({ receiptId: receiptIdFor(project.projectId), projectId: project.projectId })]));
  return new LegacyPluginRemovalGate({
    listProjectRuntimeStates: async () => projects,
    loadMigrationRecord: async (id: string) => options.record === undefined ? byRecordId.get(id) ?? null : options.record,
    loadReceipt: async (id: string) => options.receipt === undefined ? byReceiptId.get(id) ?? null : options.receipt,
    now: () => now,
  });
}

describe("legacy plugin removal gate", () => {
  it("blocks removal while any stored project is not verified on OpenAI runtime", async () => {
    const gate = gateFor([projectFixture({ mode: "legacy" }), projectFixture({ mode: "openai" }, "44444444-4444-4444-8444-444444444444")]);
    await expect(gate.assertReady()).rejects.toThrow("legacy_projects_remaining:1");
    const report = await gate.report();
    expect(report).toMatchObject({ ready: false, totalProjects: 2, counts: { legacy: 1, shadow: 0, openai: 1 } });
    expect(report.reasons).toContain("legacy_projects_remaining:1");
  });

  it("blocks removal when rollback retention has not elapsed", async () => {
    const gate = gateFor([projectFixture({ mode: "openai", cutoverAt: now.toISOString() })]);
    await expect(gate.assertReady()).rejects.toThrow("rollback_window_active");
    expect(await gate.report()).toMatchObject({ ready: false, activeRollbackWindows: 1 });
  });

  it("counts a shadow project as remaining work of its own", async () => {
    const gate = gateFor([projectFixture({ mode: "shadow" })]);
    await expect(gate.assertReady()).rejects.toThrow("shadow_projects_remaining:1");
    expect(await gate.report()).toMatchObject({ counts: { legacy: 0, shadow: 1, openai: 0 } });
  });

  it("counts blocked migrations and missing receipts instead of assuming success", async () => {
    const blocked = gateFor([projectFixture({ mode: "openai" })], {
      record: record({ status: "blocked", blockers: [{ capabilityId: "slack", legacyActionId: "post", code: "provider_unavailable", pluginName: "slack", componentId: "slack:post" }], targetInstallationIds: [] }),
    });
    expect(await blocked.report()).toMatchObject({ ready: false, blockedMigrations: 1 });
    await expect(blocked.assertReady()).rejects.toThrow("blocked_migrations:1");

    const unreadable = gateFor([projectFixture({ mode: "openai" })], { receipt: null });
    expect(await unreadable.report()).toMatchObject({ ready: false, missingReceipts: 1 });
    await expect(unreadable.assertReady()).rejects.toThrow("missing_receipts:1");

    const failedParity = gateFor([projectFixture({ mode: "openai" })], { receipt: receipt({ status: "failed", reason: "parity_failed" }) });
    expect(await failedParity.report()).toMatchObject({ missingReceipts: 1 });

    const missingRecord = gateFor([projectFixture({ mode: "openai" })], { record: null });
    expect(await missingRecord.report()).toMatchObject({ blockedMigrations: 1 });
  });

  it("treats an openai project without cutover evidence references as unverified", async () => {
    const gate = gateFor([{ projectId: "11111111-1111-4111-8111-111111111111", state: { mode: "openai", revision: 4 } }]);
    const report = await gate.report();
    expect(report).toMatchObject({ ready: false, blockedMigrations: 1, missingReceipts: 1 });
    await expect(gate.assertReady()).rejects.toThrow();
  });

  it("reports ready only when every project is verified and the rollback window elapsed", async () => {
    const gate = gateFor([projectFixture({ mode: "openai" }), projectFixture({ mode: "openai" }, "44444444-4444-4444-8444-444444444444")]);
    const report = await gate.report();
    expect(report).toMatchObject({ ready: true, totalProjects: 2, blockedMigrations: 0, missingReceipts: 0, activeRollbackWindows: 0 });
    expect(report.reasons).toEqual([]);
    await expect(gate.assertReady()).resolves.toBeUndefined();
  });

  it("refuses to declare an empty deployment ready", async () => {
    const gate = gateFor([]);
    expect(await gate.report()).toMatchObject({ ready: false, totalProjects: 0 });
    await expect(gate.assertReady()).rejects.toThrow("no_projects_observed");
  });

  it("produces a report with no secret, credential, or workflow content", async () => {
    const gate = gateFor([projectFixture({ mode: "openai" }), projectFixture({ mode: "legacy" }, "44444444-4444-4444-8444-444444444444")]);
    const serialized = JSON.stringify(await gate.report());
    expect(serialized).not.toContain("secret");
    expect(serialized).not.toContain("Workflow");
    expect(serialized).not.toContain("token");
    expect(serialized).toContain("generatedAt");
  });

  it("fails closed on an unreadable project runtime state", async () => {
    const gate = new LegacyPluginRemovalGate({
      listProjectRuntimeStates: async () => [{ projectId: "11111111-1111-4111-8111-111111111111", state: { mode: "openai_beta", revision: 1 } } as unknown as ProjectRuntimeStateEntry],
      loadMigrationRecord: async () => record(),
      loadReceipt: async () => receipt(),
      now: () => now,
    });
    await expect(gate.report()).rejects.toThrow("legacy_removal_gate_state_invalid");
  });

  it("never mutates or writes anything while reporting", async () => {
    const calls: string[] = [];
    const gate = new LegacyPluginRemovalGate({
      listProjectRuntimeStates: async () => { calls.push("list"); return [projectFixture({ mode: "openai" })]; },
      loadMigrationRecord: async () => { calls.push("record"); return record(); },
      loadReceipt: async () => { calls.push("receipt"); return receipt(); },
      now: () => now,
    });
    await gate.report();
    expect(calls).toEqual(["list", "record", "receipt"]);
  });
});
