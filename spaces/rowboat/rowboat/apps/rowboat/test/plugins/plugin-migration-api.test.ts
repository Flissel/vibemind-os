import { createHash } from "node:crypto";
import { describe, expect, it } from "vitest";
import { NextRequest } from "next/server";
import { PreviewPluginMigrationUseCase, type PreparedMigrationPreview } from "@/src/application/use-cases/plugins/preview-plugin-migration.use-case";
import { ApplyPluginMigrationUseCase, type PreparedMigrationInvocation } from "@/src/application/use-cases/plugins/apply-plugin-migration.use-case";
import { createMigrationPreviewRoute, createMigrationApplyRoute } from "@/src/interface-adapters/http/plugins/plugin-migration-routes";
import { pluginErrorResponse } from "@/app/api/v1/projects/[projectId]/plugins/_responses";
import { signMigrationConfirmation, verifyMigrationConfirmation, type MigrationConfirmationClaims } from "@/src/application/use-cases/plugins/plugin-migration.shared";
import { createMigrationDeadline } from "@/src/application/services/plugin-migration-keyset-snapshot";
import { runGuardedMigrationTransaction } from "@/src/application/services/plugin-migration-transaction";

const digest = (value: string) => createHash("sha256").update(value).digest("hex");
const actor = Object.freeze({ kind: "user" as const, userId: "admin-1" });
const catalog = Object.freeze({ catalogDigest: digest("catalog"), sourceCommit: "1".repeat(40), policyVersion: "openai-plugins-v1" });
const readyPreview = (suffix = "a"): PreparedMigrationInvocation["preview"] => Object.freeze({
  id: "22222222-2222-4222-8222-222222222222", projectId: "11111111-1111-4111-8111-111111111111", recipeId: "legacy-card:test:v1", recipeDigest: digest(`recipe-${suffix}`),
  sourceProjectRevision: suffix === "a" ? 1787738400000 : 1787738401000, sourceDigest: digest(`source-${suffix}`), sourceInventoryDigest: digest(`inventory-${suffix}`),
  targetCatalogDigest: catalog.catalogDigest, targetSourceCommit: catalog.sourceCommit, targetPolicyVersion: catalog.policyVersion,
  targetInstallationIds: Object.freeze([suffix === "a" ? "33333333-3333-4333-8333-333333333333" : "44444444-4444-4444-8444-444444444444"]),
  rollbackSnapshotDigest: digest(`rollback-${suffix}`), status: "previewed" as const, blockers: Object.freeze([]),
});
const prepared = (suffix = "a"): PreparedMigrationInvocation => { const preview = readyPreview(suffix); return Object.freeze({ preview, context: Object.freeze({ suffix, preview }) }); };
const claimsFor = (preview = readyPreview(), overrides: Partial<MigrationConfirmationClaims> = {}): MigrationConfirmationClaims => Object.freeze({
  version: 1, operation: "apply_plugin_migration", actorKind: "user", actorId: "admin-1", projectId: preview.projectId,
  sourceProjectRevision: preview.sourceProjectRevision, sourceDigest: preview.sourceDigest, sourceInventoryDigest: preview.sourceInventoryDigest,
  recipeDigest: preview.recipeDigest, rollbackSnapshotDigest: preview.rollbackSnapshotDigest, targetCatalogDigest: preview.targetCatalogDigest,
  targetInstallationIds: preview.targetInstallationIds, previewDigest: digest(`preview-${preview.sourceDigest}`), reportDigest: digest("report"),
  issuedAt: "2026-08-26T10:00:00.000Z", expiresAt: "2026-08-26T10:02:00.000Z", nonce: `nonce-${preview.sourceProjectRevision}`, idempotencyKey: `migration-${preview.id}`, ...overrides,
});
const publicPrepared = (suffix = "a"): PreparedMigrationPreview => Object.freeze({ preview: readyPreview(suffix) });

describe("plugin migration preview", () => {
  it("authorizes before a snapshot scan, sorts, and never reports writes", async () => {
    const events: string[] = []; let issued = 0;
    const useCase = new PreviewPluginMigrationUseCase({ authorizeProject: async () => { throw new Error("unexpected"); }, authorizeAll: async () => { events.push("authorize"); },
      prepareProject: async () => { throw new Error("unexpected"); }, scanAllProjects: async (_actor, visit) => { events.push("scan"); await visit(publicPrepared("b")); await visit(publicPrepared("a")); return { catalog, snapshotToken: digest("snapshot") }; },
      issueConfirmation: ({ preview }) => { issued += 1; return { token: `token-${preview.sourceDigest}`, idempotencyKey: `migration-${preview.id}` }; }, now: () => new Date("2026-08-26T10:00:00.000Z") });
    const report = await useCase.execute({ actor, scope: "all" });
    expect(events).toEqual(["authorize", "scan"]); expect(report.mutationCount).toBe(0); expect(report.mutationsApplied).toBe(false); expect(report.projects).toHaveLength(2);
    expect(issued).toBe(0); expect(report.projects.every(project => !("confirmationToken" in project) && !("catalog" in project))).toBe(true);
    expect(report).toMatchObject({ catalogDigest: catalog.catalogDigest, sourceCommit: catalog.sourceCommit, policyVersion: catalog.policyVersion });
  });

  it("issues a short-lived confirmation only for one project-scoped ready preview", async () => {
    let issued = 0; const useCase = new PreviewPluginMigrationUseCase({ authorizeProject: async () => undefined, authorizeAll: async () => { throw new Error("unexpected"); },
      prepareProject: async () => ({ prepared: publicPrepared(), catalog }), scanAllProjects: async () => { throw new Error("unexpected"); },
      issueConfirmation: () => { issued += 1; return { token: "project-token", idempotencyKey: "signed-idempotency" }; }, now: () => new Date("2026-08-26T10:00:00.000Z") });
    const report = await useCase.execute({ actor, scope: "project", projectId: readyPreview().projectId });
    expect(issued).toBe(1); expect(report.projects[0]).toMatchObject({ confirmationToken: "project-token", confirmationIdempotencyKey: "signed-idempotency" });
  });

  it("rejects all authority before reads and issues no new token for applied history", async () => {
    let scans = 0; let issued = 0;
    const denied = new PreviewPluginMigrationUseCase({ authorizeProject: async () => undefined, authorizeAll: async () => { throw new Error("forbidden"); }, prepareProject: async () => ({ prepared: publicPrepared(), catalog }),
      scanAllProjects: async () => { scans += 1; return { catalog, snapshotToken: digest("snapshot") }; }, issueConfirmation: () => { issued += 1; return { token: "never", idempotencyKey: "never" }; }, now: () => new Date() });
    await expect(denied.execute({ actor: { kind: "project_api_key", projectId: "p" }, scope: "all" })).rejects.toThrow("forbidden"); expect(scans).toBe(0);
    const applied = Object.freeze({ ...readyPreview(), status: "applied" as const });
    const history = new PreviewPluginMigrationUseCase({ authorizeProject: async () => undefined, authorizeAll: async () => undefined, prepareProject: async () => ({ prepared: { preview: applied }, catalog }),
      scanAllProjects: async () => { throw new Error("unexpected"); }, issueConfirmation: () => { issued += 1; return { token: "never", idempotencyKey: "never" }; }, now: () => new Date() });
    const report = await history.execute({ actor, scope: "project", projectId: applied.projectId }); expect(report.projects[0]).not.toHaveProperty("confirmationToken"); expect(issued).toBe(0);
  });

  it("keeps an invalid all-scope project as a typed blocker without aborting valid peers", async () => {
    const blocked = Object.freeze({ ...readyPreview("b"), status: "blocked" as const, targetInstallationIds: Object.freeze([]), blockers: Object.freeze([{ code: "migration_project_invalid" }]) });
    const useCase = new PreviewPluginMigrationUseCase({ authorizeProject: async () => undefined, authorizeAll: async () => undefined, prepareProject: async () => { throw new Error("unexpected"); },
      scanAllProjects: async (_actor, visit) => { await visit({ preview: blocked }); await visit(publicPrepared()); return { catalog, snapshotToken: digest("manifest") }; },
      issueConfirmation: () => { throw new Error("unexpected_token"); }, now: () => new Date("2026-08-26T10:00:00.000Z") });
    const report = await useCase.execute({ actor, scope: "all" }); expect(report.projectCount).toBe(2); expect(report.blockerReasons).toEqual({ migration_project_invalid: 1 }); expect(report.projects.every(project => !("confirmationToken" in project))).toBe(true);
  });

  it.each(["project", "all"] as const)("passes the exact caller signal through %s preview preparation", async scope => {
    const caller = new AbortController(); let captured: AbortSignal | undefined;
    const useCase = new PreviewPluginMigrationUseCase({ authorizeProject: async () => undefined, authorizeAll: async () => undefined,
      prepareProject: async (_projectId, signal) => { if (signal === undefined) throw new Error("missing_signal"); captured = signal; return { prepared: publicPrepared(), catalog }; },
      scanAllProjects: async (_actor, _visit, signal) => { if (signal === undefined) throw new Error("missing_signal"); captured = signal; return { catalog, snapshotToken: digest("snapshot") }; },
      issueConfirmation: () => ({ token: "token", idempotencyKey: "key" }), now: () => new Date("2026-08-26T10:00:00.000Z") });
    await useCase.execute({ actor, scope, ...(scope === "project" ? { projectId: readyPreview().projectId } : {}), callerSignal: caller.signal });
    expect(captured).toBe(caller.signal);
  });
});

describe("plugin migration apply", () => {
  it("rejects missing confirmation before authorization, reads, or writes", async () => {
    let calls = 0; const useCase = new ApplyPluginMigrationUseCase({ authorizeProject: async () => { calls += 1; }, verifyConfirmation: () => { calls += 1; return claimsFor(); },
      prepareProject: async () => { calls += 1; return prepared(); }, digestPreview: preview => digest(`preview-${preview.sourceDigest}`), applyAtomically: async () => { calls += 1; return { receiptIds: [], replayed: false, mutationCount: 0, generatedAt: new Date().toISOString() }; } });
    await expect(useCase.execute({ actor, projectId: readyPreview().projectId })).rejects.toThrow("migration_confirmation_required"); expect(calls).toBe(0);
  });

  it("passes each exact frozen invocation context directly into one atomic call under concurrency", async () => {
    const invocations = [prepared("a"), prepared("b")]; let index = 0; let waiting = 0; let release: (() => void) | undefined;
    const barrier = new Promise<void>(resolve => { release = resolve; }); const pairs: string[] = []; const claims = new Map([["token-a", claimsFor(invocations[0].preview)], ["token-b", claimsFor(invocations[1].preview)]]);
    const useCase = new ApplyPluginMigrationUseCase({ authorizeProject: async () => undefined, verifyConfirmation: token => claims.get(token)!, prepareProject: async () => { const selected = invocations[index++]!; waiting += 1; if (waiting === 2) release!(); await barrier; return selected; },
      digestPreview: preview => digest(`preview-${preview.sourceDigest}`), applyAtomically: async (context, claim) => { expect(Object.isFrozen(context)).toBe(true); pairs.push(`${(context as Readonly<{ suffix: string }>).suffix}:${claim.sourceDigest}`); return { receiptIds: [claim.projectId], replayed: false, mutationCount: 4, generatedAt: "2026-08-26T10:01:00.000Z" }; } });
    await Promise.all([useCase.execute({ actor, projectId: readyPreview().projectId, confirmationToken: "token-a" }), useCase.execute({ actor, projectId: readyPreview().projectId, confirmationToken: "token-b" })]);
    expect(pairs.sort()).toEqual([`a:${invocations[0].preview.sourceDigest}`, `b:${invocations[1].preview.sourceDigest}`]);
  });

  it.each(["actor", "project", "source", "catalog", "installation"])("rejects stale or cross-bound %s claims before atomic writes", async mismatch => {
    let writes = 0; const selected = readyPreview();
    const overrides: Partial<MigrationConfirmationClaims> = mismatch === "actor" ? { actorId: "other" } : mismatch === "project" ? { projectId: "other" } : mismatch === "source" ? { sourceDigest: digest("old") } : mismatch === "catalog" ? { targetCatalogDigest: digest("old-catalog") } : { targetInstallationIds: ["different"] };
    const useCase = new ApplyPluginMigrationUseCase({ authorizeProject: async () => undefined, verifyConfirmation: () => claimsFor(selected, overrides), prepareProject: async () => prepared(), digestPreview: preview => digest(`preview-${preview.sourceDigest}`), applyAtomically: async () => { writes += 1; throw new Error("unexpected"); } });
    await expect(useCase.execute({ actor, projectId: selected.projectId, confirmationToken: "token" })).rejects.toThrow(/migration_confirmation_invalid|migration_preview_stale/); expect(writes).toBe(0);
  });

  it.each([[false, 4], [true, 0]] as const)("returns the strict machine report and exact replay provenance (replayed=%s)", async (replayed, mutationCount) => {
    const selected = readyPreview(); const claims = claimsFor(selected);
    const useCase = new ApplyPluginMigrationUseCase({ authorizeProject: async () => undefined, verifyConfirmation: () => claims, prepareProject: async () => prepared(),
      digestPreview: preview => digest(`preview-${preview.sourceDigest}`), applyAtomically: async () => ({ receiptIds: [selected.id], replayed, mutationCount, generatedAt: "2026-08-26T10:01:00.000Z" }) });
    const report = await useCase.execute({ actor, projectId: selected.projectId, confirmationToken: "token" });
    expect(report).toMatchObject({ version: 1, catalogDigest: catalog.catalogDigest, sourceCommit: catalog.sourceCommit, policyVersion: catalog.policyVersion,
      generatedAt: "2026-08-26T10:01:00.000Z", snapshotToken: claims.previewDigest, scope: "project", projectCount: 1, blockerCount: 0,
      mutationCount, receiptIds: [selected.id], mutationsApplied: !replayed, reportDigest: claims.reportDigest,
      confirmation: { idempotencyKey: claims.idempotencyKey, replayed }, projects: [{ projectId: selected.projectId, status: "applied", previewDigest: claims.previewDigest }] });
    expect(JSON.stringify(report)).not.toMatch(/confirmationToken|secret|credential|mongodb|path/i);
  });

  it("passes one exact caller signal through prepare and atomic apply", async () => {
    const caller = new AbortController(); const signals: AbortSignal[] = []; const selected = readyPreview();
    const useCase = new ApplyPluginMigrationUseCase({ authorizeProject: async () => undefined, verifyConfirmation: () => claimsFor(selected),
      prepareProject: async (_projectId, signal) => { if (signal === undefined) throw new Error("missing_signal"); signals.push(signal); return prepared(); }, digestPreview: preview => digest(`preview-${preview.sourceDigest}`),
      applyAtomically: async (_context, _claims, signal) => { if (signal === undefined) throw new Error("missing_signal"); signals.push(signal); return { receiptIds: [selected.id], replayed: false, mutationCount: 4, generatedAt: "2026-08-26T10:01:00.000Z" }; } });
    await useCase.execute({ actor, projectId: selected.projectId, confirmationToken: "token", callerSignal: caller.signal });
    expect(signals).toEqual([caller.signal, caller.signal]);
  });
});

describe("migration confirmation envelope", () => {
  const claims = claimsFor(); const secret = "s".repeat(32);
  it("round-trips canonical short-lived claims", () => { expect(verifyMigrationConfirmation(signMigrationConfirmation(claims, secret), secret, new Date("2026-08-26T10:01:00.000Z"))).toEqual(claims); });
  it.each(["", "short", `safe\n${"x".repeat(32)}`, "\ud800" as string])("rejects an invalid required secret", value => { expect(() => signMigrationConfirmation(claims, value)).toThrow("migration_confirmation_secret_invalid"); });
  it("rejects tamper and expiry", () => { const token = signMigrationConfirmation(claims, secret); expect(() => verifyMigrationConfirmation(`${token.slice(0, -1)}x`, secret, new Date("2026-08-26T10:01:00.000Z"))).toThrow("migration_confirmation_invalid"); expect(() => verifyMigrationConfirmation(token, secret, new Date("2026-08-26T10:03:00.000Z"))).toThrow("migration_confirmation_expired"); });
});

describe("plugin migration routes", () => {
  const projectId = readyPreview().projectId; const context = { params: Promise.resolve({ projectId }) };
  it("rejects non-exact request boundaries before controller resolution", async () => { let resolves = 0; const route = createMigrationPreviewRoute(async () => { resolves += 1; return { execute: async () => ({}) }; }); expect((await route(new Request(`https://rowboat.invalid/api/v1/projects/${projectId}/plugins/migration/preview`), context)).status).toBe(400); expect((await route(new NextRequest(`https://rowboat.invalid/api/v1/projects/${projectId}/plugins/migration/preview?scope=x`), context)).status).toBe(400); expect(resolves).toBe(0); });
  it("rejects an own accessor signal without invoking it or resolving a controller", async () => {
    let accessors = 0; let resolves = 0; const request = new NextRequest(`https://rowboat.invalid/api/v1/projects/${projectId}/plugins/migration/preview`);
    Object.defineProperty(request, "signal", { configurable: true, get: () => { accessors += 1; return new AbortController().signal; } });
    const response = await createMigrationPreviewRoute(async () => { resolves += 1; return { execute: async () => ({}) }; })(request, context);
    expect(response.status).toBe(400); expect(accessors).toBe(0); expect(resolves).toBe(0);
  });
  it.each(["preview", "apply"] as const)("passes only the intrinsic caller signal through the exported %s route", async mode => {
    const caller = new AbortController(); let captured: unknown;
    const controller = { execute: async (_request: Request, input: Readonly<Record<string, unknown>>) => { captured = input.callerSignal; return {}; } };
    const route = mode === "preview" ? createMigrationPreviewRoute(controller) : createMigrationApplyRoute(controller);
    const url = `https://rowboat.invalid/api/v1/projects/${projectId}/plugins/migration/${mode}`;
    const request = new NextRequest(url, mode === "preview" ? { signal: caller.signal } : {
      method: "POST", signal: caller.signal, headers: { "content-type": "application/json" }, body: JSON.stringify({ confirmationToken: "signed-token" }),
    });
    expect((await route(request, context)).status).toBe(200); expect(captured).toBe(request.signal); expect((captured as AbortSignal).aborted).toBe(false);
    caller.abort(); expect((captured as AbortSignal).aborted).toBe(true);
  });
  it("maps a caller abort after controller start to the exact safe 400 response", async () => {
    const caller = new AbortController(); let markStarted: (() => void) | undefined; const started = new Promise<void>(resolve => { markStarted = resolve; });
    const route = createMigrationPreviewRoute({ execute: async (_request, input) => {
      markStarted!(); const signal = input.callerSignal as AbortSignal;
      return new Promise((_resolve, reject) => signal.addEventListener("abort", () => reject(new Error("request_aborted")), { once: true }));
    } });
    const pending = route(new NextRequest(`https://rowboat.invalid/api/v1/projects/${projectId}/plugins/migration/preview`, { signal: caller.signal }), context);
    await started; caller.abort(); const response = await pending;
    expect(response.status).toBe(400); expect(await response.json()).toEqual({ error: "request_aborted" });
    expect(response.headers.get("cache-control")).toBe("no-store");
  });
  it("rolls back an apply transaction aborted through the exported route after auth and read", async () => {
    const caller = new AbortController(); const counters = { auth: 0, reads: 0, starts: 0, aborts: 0, ends: 0, pointers: 0 };
    let active = false; let transactionStarted: (() => void) | undefined; const started = new Promise<void>(resolve => { transactionStarted = resolve; });
    const route = createMigrationApplyRoute({ execute: async (_request, input) => {
      counters.auth += 1; counters.reads += 1; const signal = input.callerSignal as AbortSignal;
      const deadline = createMigrationDeadline(() => Date.now(), 100, signal);
      return runGuardedMigrationTransaction({ deadline, transaction: { start: () => { counters.starts += 1; active = true; transactionStarted!(); }, inTransaction: () => active,
        commit: async () => undefined, abort: async () => { counters.aborts += 1; active = false; }, end: async () => { counters.ends += 1; } },
      work: async () => { await new Promise<never>((_resolve, reject) => { if (deadline.signal.aborted) reject(new Error("cancelled"));
        else deadline.signal.addEventListener("abort", () => reject(new Error("cancelled")), { once: true }); });
        counters.pointers += 1; return {}; }, recover: async () => null });
    } });
    const pending = route(new NextRequest(`https://rowboat.invalid/api/v1/projects/${projectId}/plugins/migration/apply`, { method: "POST", signal: caller.signal,
      headers: { "content-type": "application/json" }, body: JSON.stringify({ confirmationToken: "signed-token" }) }), context);
    await started; caller.abort(); const response = await pending;
    expect(response.status).toBe(400); expect(counters).toEqual({ auth: 1, reads: 1, starts: 1, aborts: 1, ends: 1, pointers: 0 });
  });
  it("derives apply idempotency only from the signed token", async () => { const route = createMigrationApplyRoute(async () => ({ execute: async (_request, input) => input })); const response = await route(new NextRequest(`https://rowboat.invalid/api/v1/projects/${projectId}/plugins/migration/apply`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ confirmationToken: "signed-token" }) }), context); const body = await response.json(); expect(body).toMatchObject({ projectId, confirmationToken: "signed-token" }); expect(body.callerSignal).toEqual({}); });
  it("rejects duplicate JSON keys before controller execution", async () => { let calls = 0; const route = createMigrationApplyRoute(async () => ({ execute: async () => { calls += 1; return {}; } })); const response = await route(new NextRequest(`https://rowboat.invalid/api/v1/projects/${projectId}/plugins/migration/apply`, { method: "POST", headers: { "content-type": "application/json" }, body: '{"confirmationToken":"a","confirmationToken":"b"}' }), context); expect(response.status).toBe(400); expect(calls).toBe(0); });

  it.each([
    ["project_not_found", 404], ["unauthenticated", 401], ["user_authentication_required", 401], ["forbidden", 403],
    ["migration_pointer_invalid", 409], ["migration_pointer_conflict", 409], ["migration_installation_conflict", 409],
    ["migration_admission_conflict", 409], ["migration_record_conflict", 409], ["migration_rollback_conflict", 409],
    ["migration_idempotency_conflict", 409], ["migration_confirmation_replayed", 409], ["migration_preview_stale", 409],
    ["migration_preview_timeout", 408], ["migration_commit_uncertain", 409], ["migration_repository_failed", 500], ["migration_system_failed", 500],
  ] as const)("maps safe migration error %s to exact HTTP %s without raw details", async (code, status) => {
    const response = pluginErrorResponse(new Error(code)); expect(response.status).toBe(status); expect(await response.json()).toEqual({ error: code });
  });

  it("maps unallowlisted migration details to one safe internal error", async () => {
    const response = pluginErrorResponse(new Error("migration_raw_driver_E11000 secret")); expect(response.status).toBe(500); expect(await response.json()).toEqual({ error: "internal_error" });
  });
});
