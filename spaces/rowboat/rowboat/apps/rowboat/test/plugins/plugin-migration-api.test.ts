import { createHash } from "node:crypto";
import { describe, expect, it } from "vitest";
import { NextRequest } from "next/server";
import { PreviewPluginMigrationUseCase } from "@/src/application/use-cases/plugins/preview-plugin-migration.use-case";
import { ApplyPluginMigrationUseCase } from "@/src/application/use-cases/plugins/apply-plugin-migration.use-case";
import { createMigrationPreviewRoute, createMigrationApplyRoute } from "@/src/interface-adapters/http/plugins/plugin-migration-routes";
import { signMigrationConfirmation, verifyMigrationConfirmation, type MigrationConfirmationClaims } from "@/src/application/use-cases/plugins/plugin-migration.shared";

const digest = (value: string) => createHash("sha256").update(value).digest("hex");
const actor = Object.freeze({ kind: "user" as const, userId: "admin-1" });
const catalog = Object.freeze({ catalogDigest: digest("catalog"), sourceCommit: "1".repeat(40), policyVersion: "openai-plugins-v1" });
const readyPreview = (projectId = "11111111-1111-4111-8111-111111111111") => Object.freeze({
  id: "22222222-2222-4222-8222-222222222222", projectId, recipeId: "legacy-card:test:v1",
  recipeDigest: digest("recipe"), sourceProjectRevision: 7, sourceDigest: digest("source"),
  sourceInventoryDigest: digest("inventory"), targetCatalogDigest: catalog.catalogDigest,
  targetSourceCommit: catalog.sourceCommit, targetPolicyVersion: catalog.policyVersion,
  targetInstallationIds: Object.freeze(["33333333-3333-4333-8333-333333333333"]),
  rollbackSnapshotDigest: digest("rollback"), status: "previewed" as const, blockers: Object.freeze([]),
  createdAt: "2026-08-26T10:00:00.000Z", mutationsApplied: false as const,
  installations: Object.freeze([{ id: "33333333-3333-4333-8333-333333333333" }]),
});

function previewFixture() {
  let reads = 0;
  let writes = 0;
  const projects = ["33333333-3333-4333-8333-333333333333", "11111111-1111-4111-8111-111111111111"];
  const useCase = new PreviewPluginMigrationUseCase({
    authorizeProject: async () => undefined,
    authorizeAll: async identity => { if (identity.kind !== "user") throw new Error("forbidden"); },
    getPinnedCatalog: async () => { reads += 1; return catalog; },
    listProjectsPage: async (_identity, cursor) => {
      reads += 1;
      return cursor === undefined
        ? { projects: [Object.freeze({ projectId: projects[0], sourceProjectRevision: 2 })], nextCursor: "next", snapshotToken: "snapshot-1" }
        : { projects: [Object.freeze({ projectId: projects[1], sourceProjectRevision: 7 })], nextCursor: null, snapshotToken: "snapshot-1" };
    },
    getProjectSource: async projectId => { reads += 1; return Object.freeze({ projectId, sourceProjectRevision: projectId === projects[0] ? 2 : 7 }); },
    previewProject: source => readyPreview(source.projectId),
    issueConfirmation: input => `token:${input.projectId}`,
    now: () => new Date("2026-08-26T10:00:00.000Z"),
  });
  return { useCase, counts: () => ({ reads, writes }), write: () => { writes += 1; } };
}

describe("plugin migration preview", () => {
  it("authenticates all-scope before reads, paginates one snapshot, sorts projects, and never writes", async () => {
    const fixture = previewFixture();
    const report = await fixture.useCase.execute({ actor, scope: "all" });
    expect(report.projects.map(project => project.projectId)).toEqual([
      "11111111-1111-4111-8111-111111111111", "33333333-3333-4333-8333-333333333333",
    ]);
    expect(report).toMatchObject({ scope: "all", projectCount: 2, blockerCount: 0, mutationCount: 0, receiptIds: [], mutationsApplied: false, snapshotToken: "snapshot-1" });
    expect(report.projects.every(project => "confirmationToken" in project && typeof project.confirmationToken === "string")).toBe(true);
    expect(fixture.counts()).toEqual({ reads: 5, writes: 0 });
  });

  it("rejects all-scope project credentials before every repository read", async () => {
    const fixture = previewFixture();
    await expect(fixture.useCase.execute({ actor: { kind: "project_api_key", projectId: "p" }, scope: "all" })).rejects.toThrow("forbidden");
    expect(fixture.counts()).toEqual({ reads: 0, writes: 0 });
  });

  it("authorizes a project before catalog or project reads and issues no token for blocked previews", async () => {
    let reads = 0;
    let issued = 0;
    const useCase = new PreviewPluginMigrationUseCase({
      authorizeProject: async () => undefined, authorizeAll: async () => { throw new Error("unexpected"); },
      getPinnedCatalog: async () => { reads += 1; return catalog; }, listProjectsPage: async () => { throw new Error("unexpected"); },
      getProjectSource: async projectId => { reads += 1; return { projectId, sourceProjectRevision: 1 }; },
      previewProject: source => ({ ...readyPreview(source.projectId), status: "blocked" as const, blockers: [{ code: "provider_unavailable" }], installations: [], targetInstallationIds: [] }),
      issueConfirmation: () => { issued += 1; return "never"; }, now: () => new Date("2026-08-26T10:00:00.000Z"),
    });
    const report = await useCase.execute({ actor, scope: "project", projectId: "11111111-1111-4111-8111-111111111111" });
    expect(report.projects[0]).not.toHaveProperty("confirmationToken");
    expect(report.blockerReasons).toEqual({ provider_unavailable: 1 });
    expect({ reads, issued }).toEqual({ reads: 2, issued: 0 });
  });

  it("rejects accessor-backed repository captures without invoking the accessor", async () => {
    let getterCalls = 0;
    const source = { sourceProjectRevision: 1 } as Record<string, unknown>;
    Object.defineProperty(source, "projectId", { enumerable: true, get: () => { getterCalls += 1; return readyPreview().projectId; } });
    const useCase = new PreviewPluginMigrationUseCase({ authorizeProject: async () => undefined, authorizeAll: async () => undefined,
      getPinnedCatalog: async () => catalog, listProjectsPage: async () => { throw new Error("unexpected"); }, getProjectSource: async () => source as never,
      previewProject: () => { throw new Error("unexpected"); }, issueConfirmation: () => "never", now: () => new Date() });
    await expect(useCase.execute({ actor, scope: "project", projectId: readyPreview().projectId })).rejects.toThrow("migration_project_invalid");
    expect(getterCalls).toBe(0);
  });
});

describe("plugin migration apply", () => {
  it("rejects a missing confirmation before reads or writes", async () => {
    let reads = 0; let writes = 0;
    const useCase = new ApplyPluginMigrationUseCase({
      authorizeProject: async () => undefined, verifyConfirmation: () => { reads += 1; throw new Error("unexpected"); },
      previewProject: async () => { reads += 1; return readyPreview(); },
      runAtomically: async operation => operation({}), writeRollbackSnapshot: async () => { writes += 1; },
      writeInstallationsAndAdmissions: async () => { writes += 1; }, writeMigrationRecord: async () => { writes += 1; return { receiptIds: [] }; },
      compareAndSetProjectPointer: async () => { writes += 1; },
    });
    await expect(useCase.execute({ actor, projectId: readyPreview().projectId, confirmationToken: undefined, idempotencyKey: "apply-1" })).rejects.toThrow("migration_confirmation_required");
    expect({ reads, writes }).toEqual({ reads: 0, writes: 0 });
  });

  it("reauthorizes, verifies the actor-bound token, repreviews, then applies atomically in exact order", async () => {
    const order: string[] = [];
    const preview = readyPreview();
    const useCase = new ApplyPluginMigrationUseCase({
      authorizeProject: async () => { order.push("authorize"); },
      verifyConfirmation: () => { order.push("verify"); return { actorKind: "user", actorId: "admin-1", projectId: preview.projectId, previewDigest: digest("preview") }; },
      previewProject: async () => { order.push("repreview"); return preview; },
      digestPreview: () => digest("preview"),
      runAtomically: async operation => { order.push("transaction"); return operation({}); },
      writeRollbackSnapshot: async () => { order.push("rollback"); },
      writeInstallationsAndAdmissions: async () => { order.push("installations"); },
      writeMigrationRecord: async () => { order.push("record"); return { receiptIds: ["migration-receipt"] }; },
      compareAndSetProjectPointer: async () => { order.push("pointer"); },
    });
    const result = await useCase.execute({ actor, projectId: preview.projectId, confirmationToken: "signed-token", idempotencyKey: "apply-1" });
    expect(order).toEqual(["authorize", "verify", "repreview", "transaction", "rollback", "installations", "record", "pointer"]);
    expect(result).toEqual({ projectId: preview.projectId, mutationsApplied: true, mutationCount: 4, receiptIds: ["migration-receipt"] });
  });

  it.each(["cross_actor", "cross_project", "stale_preview", "replay"])("fails closed on %s before writes", async failure => {
    let writes = 0;
    const preview = readyPreview();
    const useCase = new ApplyPluginMigrationUseCase({
      authorizeProject: async () => undefined,
      verifyConfirmation: () => failure === "replay" ? (() => { throw new Error("migration_confirmation_replayed"); })() : ({
        actorKind: "user", actorId: failure === "cross_actor" ? "other" : "admin-1",
        projectId: failure === "cross_project" ? "44444444-4444-4444-8444-444444444444" : preview.projectId,
        previewDigest: failure === "stale_preview" ? digest("old") : digest("preview"),
      }),
      previewProject: async () => preview, digestPreview: () => digest("preview"),
      runAtomically: async operation => operation({}), writeRollbackSnapshot: async () => { writes += 1; },
      writeInstallationsAndAdmissions: async () => { writes += 1; }, writeMigrationRecord: async () => { writes += 1; return { receiptIds: [] }; },
      compareAndSetProjectPointer: async () => { writes += 1; },
    });
    await expect(useCase.execute({ actor, projectId: preview.projectId, confirmationToken: "signed-token", idempotencyKey: "apply-1" })).rejects.toThrow(/migration_confirmation_(invalid|replayed)|migration_preview_stale/);
    expect(writes).toBe(0);
  });

  it("leaves no committed writes when a step before the pointer fails", async () => {
    const preview = readyPreview(); const committed: string[] = [];
    const useCase = new ApplyPluginMigrationUseCase({ authorizeProject: async () => undefined,
      verifyConfirmation: () => ({ actorKind: "user", actorId: "admin-1", projectId: preview.projectId, previewDigest: digest("preview") }),
      previewProject: async () => preview, digestPreview: () => digest("preview"),
      runAtomically: async operation => { const staged: string[] = []; try { const output = await operation(staged); committed.push(...staged); return output; } catch (error) { throw error; } },
      writeRollbackSnapshot: async transaction => { (transaction as string[]).push("rollback"); },
      writeInstallationsAndAdmissions: async transaction => { (transaction as string[]).push("installations"); },
      writeMigrationRecord: async () => { throw new Error("repository_transaction_failed"); },
      compareAndSetProjectPointer: async transaction => { (transaction as string[]).push("pointer"); },
    });
    await expect(useCase.execute({ actor, projectId: preview.projectId, confirmationToken: "signed-token", idempotencyKey: "apply-1" })).rejects.toThrow("repository_transaction_failed");
    expect(committed).toEqual([]);
  });
});

describe("migration confirmation envelope", () => {
  const claims: MigrationConfirmationClaims = Object.freeze({ version: 1, operation: "apply_plugin_migration", actorKind: "user", actorId: "admin-1",
    projectId: readyPreview().projectId, sourceProjectRevision: 7, sourceDigest: digest("source"), sourceInventoryDigest: digest("inventory"),
    recipeDigest: digest("recipe"), rollbackSnapshotDigest: digest("rollback"), targetCatalogDigest: catalog.catalogDigest,
    targetInstallationIds: readyPreview().targetInstallationIds, previewDigest: digest("preview"), reportDigest: digest("report"),
    issuedAt: "2026-08-26T10:00:00.000Z", expiresAt: "2026-08-26T10:02:00.000Z", nonce: "nonce-1", idempotencyKey: "apply-1" });
  const secret = "s".repeat(32);
  it("round-trips a canonical actor/project/catalog-bound short-lived token", () => {
    expect(verifyMigrationConfirmation(signMigrationConfirmation(claims, secret), secret, new Date("2026-08-26T10:01:00.000Z"))).toEqual(claims);
  });
  it.each(["", "short", `safe\n${"x".repeat(32)}`, "\ud800" as string])("rejects an invalid required secret", value => {
    expect(() => signMigrationConfirmation(claims, value)).toThrow("migration_confirmation_secret_invalid");
  });
  it("rejects tamper and expiry", () => {
    const token = signMigrationConfirmation(claims, secret);
    expect(() => verifyMigrationConfirmation(`${token.slice(0, -1)}x`, secret, new Date("2026-08-26T10:01:00.000Z"))).toThrow("migration_confirmation_invalid");
    expect(() => verifyMigrationConfirmation(token, secret, new Date("2026-08-26T10:03:00.000Z"))).toThrow("migration_confirmation_expired");
  });
});

it("rejects accessor actor identities without invocation", async () => {
  let calls = 0;
  const accessor = {} as Record<string, unknown>;
  Object.defineProperty(accessor, "kind", { enumerable: true, get: () => { calls += 1; return "user"; } });
  Object.defineProperty(accessor, "userId", { enumerable: true, value: "admin-1" });
  const fixture = previewFixture();
  await expect(fixture.useCase.execute({ actor: accessor as never, scope: "all" })).rejects.toThrow("authorization_invalid");
  expect(calls).toBe(0); expect(fixture.counts()).toEqual({ reads: 0, writes: 0 });
});

describe("plugin migration routes", () => {
  const context = { params: Promise.resolve({ projectId: readyPreview().projectId }) };
  it("rejects non-exact NextRequest and raw URL drift before controller resolution", async () => {
    let resolves = 0;
    const route = createMigrationPreviewRoute(async () => { resolves += 1; return { execute: async () => ({}) }; });
    expect((await route(new Request(`https://rowboat.invalid/api/v1/projects/${readyPreview().projectId}/plugins/migration/preview`), context)).status).toBe(400);
    expect((await route(new NextRequest(`https://rowboat.invalid/api/v1/projects/${readyPreview().projectId}/plugins/migration/preview?scope=project`), context)).status).toBe(400);
    expect(resolves).toBe(0);
  });
  it("dispatches exact preview and apply requests with strict input", async () => {
    const previewRoute = createMigrationPreviewRoute(async () => ({ execute: async (_request, input) => ({ projectId: input.projectId }) }));
    const previewResponse = await previewRoute(new NextRequest(`https://rowboat.invalid/api/v1/projects/${readyPreview().projectId}/plugins/migration/preview`), context);
    expect(await previewResponse.json()).toEqual({ projectId: readyPreview().projectId });
    const applyRoute = createMigrationApplyRoute(async () => ({ execute: async (_request, input) => input }));
    const response = await applyRoute(new NextRequest(`https://rowboat.invalid/api/v1/projects/${readyPreview().projectId}/plugins/migration/apply`, {
      method: "POST", headers: { "content-type": "application/json", "idempotency-key": "apply-1" }, body: JSON.stringify({ confirmationToken: "signed-token" }),
    }), context);
    expect(await response.json()).toEqual({ projectId: readyPreview().projectId, confirmationToken: "signed-token", idempotencyKey: "apply-1" });
  });
  it("rejects duplicate JSON keys before controller execution", async () => {
    let calls = 0;
    const route = createMigrationApplyRoute(async () => ({ execute: async () => { calls += 1; return {}; } }));
    const response = await route(new NextRequest(`https://rowboat.invalid/api/v1/projects/${readyPreview().projectId}/plugins/migration/apply`, {
      method: "POST", headers: { "content-type": "application/json", "idempotency-key": "apply-1" }, body: '{"confirmationToken":"a","confirmationToken":"b"}',
    }), context);
    expect(response.status).toBe(400); expect(calls).toBe(0);
  });
});
