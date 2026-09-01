import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { toPluginCardView, type PluginCatalogCardItem } from "@/app/projects/[projectId]/plugins/components/plugin-card";
import { pluginToolSummary } from "@/app/projects/[projectId]/entities/plugin-tool-summary";
import { createPluginActionRuntime } from "@/src/interface-adapters/actions/plugin-action-runtime";
import { pluginCatalogPath } from "@/app/projects/[projectId]/plugins/components/plugin-catalog";
import { Modal } from "@heroui/react";
import { PluginInstallDialogFrame, toPluginInstallDialogView } from "@/app/projects/[projectId]/plugins/components/plugin-install-dialog";
import { LEGACY_TOOLS_LABEL } from "@/app/projects/[projectId]/tools/components/legacy-tools-label";

const digest = "a".repeat(64);
const sourceCommit = "b".repeat(40);

function card(
  status: PluginCatalogCardItem["status"],
  reason?: PluginCatalogCardItem["reason"],
  componentStatus = "available",
): PluginCatalogCardItem {
  return Object.freeze({
    pluginName: "github",
    pluginVersion: "1.0.0",
    catalogDigest: digest,
    sourceCommit,
    status,
    ...(reason === undefined ? {} : { reason }),
    components: Object.freeze([Object.freeze({ componentDigest: digest, name: "github", kind: "mcp" as const, status: componentStatus })]),
  });
}

describe("plugin catalog view state", () => {
  // Installation is component-scoped, so a plugin is offered whenever at least
  // one of its components is available and nothing is installed yet - which is
  // what makes a `partially_available` plugin such as `github` installable.
  it.each([
    ["available", "available", "Available", true],
    ["review_required", "review_required", "Review required", false],
    ["installed", "available", "Installed", false],
    ["partially_available", "available", "Partially available", true],
    ["unavailable", "unavailable", "Unavailable", false],
    ["migration_required", "migration_required", "Migration required", false],
    ["error", "error", "Error", false],
  ] as const)("renders the server-owned %s state", (status, componentStatus, badge, canInstall) => {
    expect(toPluginCardView(card(status, undefined, componentStatus))).toEqual(expect.objectContaining({ badge, canInstall }));
  });

  it("does not label a partial plugin installed and preserves the policy reason", () => {
    expect(toPluginCardView(card("partially_available", "provider_unavailable"))).toMatchObject({
      badge: "Partially available",
      canInstall: true,
      reason: "provider_unavailable",
    });
    expect(toPluginCardView(card("review_required", "license_review_required", "review_required")).reason).toBe("license_review_required");
  });

  it("does not offer a plugin that has no components at all", () => {
    expect(toPluginCardView({ ...card("available"), components: Object.freeze([]) }).canInstall).toBe(false);
  });

  it("returns deeply immutable hydration-safe view data", () => {
    const view = toPluginCardView(card("available"));
    expect(Object.isFrozen(view)).toBe(true);
    expect(Object.isFrozen(view.components)).toBe(true);
  });

  it("uses an encoded canonical project path and keeps the legacy label explicit", () => {
    expect(pluginCatalogPath("project safe/one")).toBe("/projects/project%20safe%2Fone/plugins");
    expect(LEGACY_TOOLS_LABEL).toBe("Legacy — migration pending");
  });

  it("shows only component decisions and credential slot requirements in the dialog", () => {
    const view = toPluginInstallDialogView(Object.freeze({
      ...card("available"), previewToken: "signed-preview-token",
      components: Object.freeze([Object.freeze({ componentDigest: digest, name: "github-mcp", kind: "mcp" as const, status: "available", reason: "write_review_required" as const })]),
      credentialSlots: Object.freeze([Object.freeze({ name: "GITHUB_TOKEN", configured: false })]),
    }));
    expect(view.components).toEqual([{ componentDigest: digest, name: "github-mcp", kind: "mcp", status: "available", reason: "write_review_required", selectable: true }]);
    expect(view.credentialSlots).toEqual([{ name: "GITHUB_TOKEN", configured: false }]);
    expect(view.canInstall).toBe(true);
    expect(JSON.stringify(view)).not.toContain("secret");
    expect(Object.isFrozen(view)).toBe(true);
  });

  it("uses the established focus-managed dialog primitive with labelled semantics", () => {
    const onClose = () => undefined;
    const frame = PluginInstallDialogFrame({ onClose, children: "content" });
    expect(frame.type).toBe(Modal);
    expect(frame.props).toMatchObject({
      isOpen: true,
      onClose,
      isDismissable: true,
      isKeyboardDismissDisabled: false,
    });
    expect(frame.props).not.toHaveProperty("aria-labelledby");
    expect(frame.props).not.toHaveProperty("aria-describedby");
    const modalSource = readFileSync(resolve(process.cwd(), "node_modules/@heroui/modal/dist/use-modal.js"), "utf8");
    const headerSource = readFileSync(resolve(process.cwd(), "node_modules/@heroui/modal/dist/modal-header.js"), "utf8");
    const bodySource = readFileSync(resolve(process.cwd(), "node_modules/@heroui/modal/dist/modal-body.js"), "utf8");
    const dialogSource = readFileSync(resolve(process.cwd(), "app/projects/[projectId]/plugins/components/plugin-install-dialog.tsx"), "utf8");
    expect(modalSource).toContain('"aria-labelledby": headerMounted ? headerId : void 0');
    expect(modalSource).toContain('"aria-describedby": bodyMounted ? bodyId : void 0');
    expect(headerSource).toContain("id: headerId");
    expect(bodySource).toContain("id: bodyId");
    expect(dialogSource).not.toMatch(/aria-(?:labelledby|describedby)|plugin-install-(?:title|description)/);
    expect(PluginInstallDialogFrame({ onClose, children: "content" }).props).toMatchObject({
      isDismissable: true,
      isKeyboardDismissDisabled: false,
    });
  });
});

describe("plugin server action boundary", () => {
  function setup() {
    const events: string[] = [];
    let installs = 0;
    const catalogItem = Object.freeze({
      name: "github", pluginName: "github", pluginVersion: "1.0.0", catalogDigest: digest, sourceCommit,
      policyVersion: "openai-plugin-policy-v1", license: Object.freeze({ declaration: "MIT", decision: "admitted" }),
      admission: "admitted", components: Object.freeze([]),
    });
    const preview = Object.freeze({
      pluginName: "github", catalogDigest: digest, sourceCommit, policyVersion: "openai-plugin-policy-v1",
      license: Object.freeze({ declaration: "MIT", decision: "admitted" }), admission: "admitted",
      components: Object.freeze([]), credentialSlots: Object.freeze([Object.freeze({ name: "GITHUB_TOKEN", configured: false })]),
    });
    const controllers = Object.freeze({
      authenticate: async () => Object.freeze({ kind: "user" as const, userId: "user-1" }),
      findInstallReplay: async () => null,
      catalog: Object.freeze({ execute: async () => { events.push("catalog-auth-read"); return [catalogItem]; } }),
      installation: Object.freeze({
        list: async (_request: Request, input: Readonly<Record<string, unknown>>) => {
          events.push("project-auth-read");
          if (input.projectId !== "project-1") throw new Error("forbidden");
          return [];
        },
        preview: async (_request: Request, input: Readonly<Record<string, unknown>>) => {
          events.push("preview-auth-read");
          expect(input).toEqual({ projectId: "project-1", pluginName: "github", catalogDigest: digest });
          return preview;
        },
        install: async (_request: Request, input: Readonly<Record<string, unknown>>) => {
          events.push("install-auth-mutation"); installs += 1;
          return Object.freeze({ type: "install", receiptId: "receipt-1", projectId: "project-1", pluginName: "github", status: "success", redactions: Object.freeze([]) });
        },
      }),
    });
    const runtime = createPluginActionRuntime({
      resolveControllers: async () => controllers,
      createRequest: () => new Request("https://rowboat.invalid/internal/plugin-action"),
      createIdempotencyKey: () => "server-key-1",
      previewSecret: undefined,
      pinnedCatalogDigest: digest,
    });
    return { runtime, events, get installs() { return installs; } };
  }

  it("loads the authorized catalog and project installations without accepting status authority", async () => {
    const state = setup();
    const result = await state.runtime.list({ projectId: "project-1", catalogDigest: digest });
    expect(result.items[0]).toMatchObject({ pluginName: "github", status: "available" });
    expect(state.events).toEqual(["project-auth-read", "catalog-auth-read"]);
    await expect(state.runtime.list({ projectId: "project-1", catalogDigest: digest, status: "installed" })).rejects.toThrow("request_invalid");
    expect(state.events).toEqual(["project-auth-read", "catalog-auth-read"]);
    await expect(state.runtime.list({ projectId: "project-2", catalogDigest: digest })).rejects.toThrow("forbidden");
    expect(state.events).toEqual(["project-auth-read", "catalog-auth-read", "project-auth-read"]);
  });

  it.each([
    ["rejected", [], "unavailable"],
    ["admitted", [{ name: "mcp", kind: "mcp", admission: { status: "admitted", policyVersion: "p1" }, availability: { status: "migration_required", reason: "migration_conflict" }, componentDigest: digest }], "migration_required"],
    ["admitted", [{ name: "mcp", kind: "mcp", admission: { status: "admitted", policyVersion: "p1" }, availability: { status: "error", reason: "provider_unavailable" }, componentDigest: digest }], "error"],
    ["admitted", [{ name: "mcp", kind: "mcp", admission: { status: "admitted", policyVersion: "p1" }, availability: { status: "unavailable", reason: "provider_unavailable" }, componentDigest: digest }], "unavailable"],
  ] as const)("normalizes server catalog state %s to %s without inventing installed", async (admission, components, expected) => {
    const catalogItem = {
      name: "github", pluginName: "github", pluginVersion: "1.0.0", catalogDigest: digest, sourceCommit,
      policyVersion: "p1", license: admission === "rejected"
        ? { declaration: "MIT", decision: "rejected", reason: "license_rejected" }
        : { declaration: "MIT", decision: "admitted" },
      admission, ...(admission === "rejected" ? { reason: "license_rejected" } : {}), components,
    };
    const runtime = createPluginActionRuntime({
      resolveControllers: async () => ({
        authenticate: async () => Object.freeze({ kind: "user" as const, userId: "user-1" }),
        findInstallReplay: async () => null,
        catalog: { execute: async () => [catalogItem] },
        installation: { list: async () => [], preview: async () => { throw new Error("unused"); }, install: async () => { throw new Error("unused"); } },
      }),
      createRequest: () => new Request("https://rowboat.invalid/internal/plugin-action"),
      createIdempotencyKey: () => "server-key-1",
      previewSecret: undefined,
      pinnedCatalogDigest: digest,
    });
    expect((await runtime.list({ projectId: "project-1", catalogDigest: digest })).items[0]?.status).toBe(expected);
  });

  it("rejects stale, cross-project, forged, proxy and accessor arguments before controllers", async () => {
    const state = setup();
    const invalid: unknown[] = [
      { projectId: "project-2", pluginName: "github", catalogDigest: digest, expectedRevision: 0, idempotencyKey: "server-key-1", status: "installed" },
      { projectId: "project-1", pluginName: "github", catalogDigest: "f".repeat(64), expectedRevision: -1, idempotencyKey: "server-key-1" },
      { projectId: "project-1", pluginName: "github", catalogDigest: digest, expectedRevision: 0, idempotencyKey: "bad,key" },
    ];
    let traps = 0;
    invalid.push(new Proxy(Object.freeze({ projectId: "project-1" }), { ownKeys: () => { traps += 1; return []; } }));
    const accessor = { pluginName: "github", catalogDigest: digest, expectedRevision: 0, idempotencyKey: "server-key-1" } as Record<string, unknown>;
    Object.defineProperty(accessor, "projectId", { enumerable: true, get: () => { traps += 1; return "project-1"; } });
    invalid.push(accessor);
    for (const input of invalid) await expect(state.runtime.install(input)).rejects.toThrow("request_invalid");
    expect(traps).toBe(0);
    expect(state.events).toEqual([]);
  });

  it("rejects credential values and mismatched preview provenance before mutation", async () => {
    const state = setup();
    const poisoned = createPluginActionRuntime({
      resolveControllers: async () => Object.freeze({
        authenticate: async () => Object.freeze({ kind: "user" as const, userId: "user-1" }),
        findInstallReplay: async () => null,
        catalog: Object.freeze({ execute: async () => [] }),
        installation: Object.freeze({
          list: async () => [],
          preview: async () => ({
            pluginName: "other", catalogDigest: digest, sourceCommit, policyVersion: "openai-plugin-policy-v1",
            license: { declaration: "MIT", decision: "admitted" }, admission: "admitted", components: [],
            credentialSlots: [{ name: "TOKEN", configured: false, value: "secret-value" }],
          }),
          install: async () => { throw new Error("mutation_must_not_run"); },
        }),
      }),
      createRequest: () => new Request("https://rowboat.invalid/internal/plugin-action"),
      createIdempotencyKey: () => "server-key-1",
      previewSecret: "s".repeat(64),
      pinnedCatalogDigest: digest,
      now: () => 1_700_000_000_000,
    });
    await expect(poisoned.preview({ projectId: "project-1", pluginName: "github", catalogDigest: digest })).rejects.toThrow("response_invalid");
    expect(state.installs).toBe(0);
  });

  it("fails closed when a fresh preview is not installable", async () => {
    let mutations = 0;
    const runtime = createPluginActionRuntime({
      resolveControllers: async () => ({
        authenticate: async () => Object.freeze({ kind: "user" as const, userId: "user-1" }),
        findInstallReplay: async () => null,
        catalog: { execute: async () => [] },
        installation: {
          list: async () => [],
          preview: async () => ({
            pluginName: "github", catalogDigest: digest, sourceCommit, policyVersion: "p1",
            license: { declaration: "MIT", decision: "review_required", reason: "license_review_required" },
            admission: "review_required", reason: "license_review_required", components: [],
            credentialSlots: [],
          }),
          install: async () => { mutations += 1; return {}; },
        },
      }),
      createRequest: () => new Request("https://rowboat.invalid/internal/plugin-action"),
      createIdempotencyKey: () => "server-key-1",
      previewSecret: "s".repeat(64),
      pinnedCatalogDigest: digest,
      now: () => 1_700_000_000_000,
    });
    const preview = await runtime.preview({ projectId: "project-1", pluginName: "github", catalogDigest: digest });
    await expect(runtime.install({ previewToken: preview.previewToken })).rejects.toThrow("license_review_required");
    expect(mutations).toBe(0);
  });
});

describe("signed plugin preview authority", () => {
  type TestActor = Readonly<{ kind: "user"; userId: string }> | Readonly<{ kind: "project_api_key"; projectId: string }>;

  function secureSetup(previewSecret = "s".repeat(64)) {
    let now = 1_700_000_000_000;
    let actor: TestActor = Object.freeze({ kind: "user", userId: "user-1" });
    let mutations = 0;
    let previews = 0;
    let lists = 0;
    let currentInstallations: readonly unknown[] = [];
    let replayLookups = 0;
    let lastReplayInput: Readonly<Record<string, unknown>> | null = null;
    let currentPreview: Readonly<Record<string, unknown>> = Object.freeze({
      pluginName: "github", catalogDigest: digest, sourceCommit, policyVersion: "openai-plugin-policy-v1",
      license: Object.freeze({ declaration: "MIT", decision: "admitted" }), admission: "admitted",
      components: Object.freeze([]), credentialSlots: Object.freeze([Object.freeze({ name: "GITHUB_TOKEN", configured: false })]),
    });
    const receipt = Object.freeze({ type: "install", receiptId: "receipt-1", projectId: "project-1", pluginName: "github", status: "success", redactions: Object.freeze([]) });
    let replayReceipt: typeof receipt | null = null;
    const runtime = createPluginActionRuntime({
      resolveControllers: async () => Object.freeze({
        authenticate: async () => actor,
        findInstallReplay: async (_request: Request, input: Readonly<Record<string, unknown>>) => {
          replayLookups += 1; lastReplayInput = input; return replayReceipt;
        },
        catalog: Object.freeze({ execute: async () => [] }),
        installation: Object.freeze({
          list: async () => { lists += 1; return currentInstallations; },
          preview: async () => { previews += 1; return currentPreview; },
          install: async (_request: Request, input: Readonly<Record<string, unknown>>) => {
            mutations += 1;
            expect(input).toEqual({ projectId: "project-1", pluginName: "github", catalogDigest: digest, expectedRevision: 0, idempotencyKey: "server-key-1" });
            currentInstallations = [Object.freeze({
              pluginName: "github", pluginVersion: "1.0.0", catalogDigest: digest, policyVersion: "openai-plugin-policy-v1",
              license: Object.freeze({ declaration: "MIT", decision: "admitted" }), admission: "admitted", components: Object.freeze([]),
              enabled: true, revision: 0,
            })];
            replayReceipt = receipt;
            return receipt;
          },
        }),
      }),
      createRequest: () => new Request("https://rowboat.invalid/internal/plugin-action"),
      createIdempotencyKey: () => "server-key-1",
      previewSecret,
      pinnedCatalogDigest: digest,
      now: () => now,
    });
    return {
      runtime,
      set now(value: number) { now = value; },
      set actor(value: TestActor) { actor = Object.freeze(value); },
      set preview(value: Readonly<Record<string, unknown>>) { currentPreview = value; },
      set installations(value: readonly unknown[]) { currentInstallations = value; },
      get mutations() { return mutations; }, get previews() { return previews; }, get lists() { return lists; },
      get replayLookups() { return replayLookups; },
      get lastReplayInput() { return lastReplayInput; },
    };
  }

  function secretBoundarySetup(previewSecret: string) {
    let resolves = 0;
    let authentications = 0;
    let reads = 0;
    const runtime = createPluginActionRuntime({
      resolveControllers: async () => {
        resolves += 1;
        return Object.freeze({
          authenticate: async () => { authentications += 1; return Object.freeze({ kind: "user" as const, userId: "user-1" }); },
          findInstallReplay: async () => { reads += 1; return null; },
          catalog: Object.freeze({ execute: async () => { reads += 1; return []; } }),
          installation: Object.freeze({
            list: async () => { reads += 1; return []; },
            preview: async () => { reads += 1; return []; },
            install: async () => { reads += 1; return {}; },
          }),
        });
      },
      createRequest: () => new Request("https://rowboat.invalid/internal/plugin-action"),
      createIdempotencyKey: () => "server-key-1",
      previewSecret,
      pinnedCatalogDigest: digest,
      now: () => 1_700_000_000_000,
    });
    return Object.freeze({
      runtime,
      counters: () => Object.freeze({ resolves, authentications, reads }),
    });
  }

  it("accepts only a signed server preview envelope at install", async () => {
    const state = secureSetup();
    const preview = await state.runtime.preview({ projectId: "project-1", pluginName: "github", catalogDigest: digest });
    expect(preview).toMatchObject({ pluginName: "github", credentialSlots: [{ name: "GITHUB_TOKEN", configured: false }] });
    expect(Object.keys(preview).sort()).not.toContain("expectedRevision");
    expect(Object.keys(preview).sort()).not.toContain("idempotencyKey");
    expect(typeof (preview as unknown as { previewToken: unknown }).previewToken).toBe("string");
    const result = await state.runtime.install({ previewToken: (preview as unknown as { previewToken: string }).previewToken });
    expect(result).toEqual({ ...result, receiptId: "receipt-1" });
    expect(state.mutations).toBe(1);
    await expect(state.runtime.install({
      previewToken: (preview as unknown as { previewToken: string }).previewToken,
      expectedRevision: 99, idempotencyKey: "forged-key", projectId: "project-2",
    })).rejects.toThrow("request_invalid");
    expect(state.mutations).toBe(1);
  });

  it("rejects tamper, cross-actor use and expiry before mutation", async () => {
    const state = secureSetup();
    const preview = await state.runtime.preview({ projectId: "project-1", pluginName: "github", catalogDigest: digest }) as unknown as { previewToken: string };
    const last = preview.previewToken.endsWith("A") ? "B" : "A";
    await expect(state.runtime.install({ previewToken: `${preview.previewToken.slice(0, -1)}${last}` })).rejects.toThrow("preview_invalid");
    state.actor = { kind: "user", userId: "user-2" };
    await expect(state.runtime.install({ previewToken: preview.previewToken })).rejects.toThrow("preview_invalid");
    state.actor = { kind: "project_api_key", projectId: "project-1" };
    await expect(state.runtime.install({ previewToken: preview.previewToken })).rejects.toThrow("preview_invalid");
    state.actor = { kind: "user", userId: "user-1" };
    state.now = 1_700_000_300_000;
    await expect(state.runtime.install({ previewToken: preview.previewToken })).rejects.toThrow("preview_expired");
    expect(state.mutations).toBe(0);
  });

  it.each([
    ["projectId", "project-2"],
    ["pluginName", "other"],
    ["catalogDigest", "f".repeat(64)],
    ["expectedRevision", 99],
    ["idempotencyKey", "forged-key"],
  ] as const)("rejects a signed-payload %s substitution", async (field, value) => {
    const state = secureSetup();
    const preview = await state.runtime.preview({ projectId: "project-1", pluginName: "github", catalogDigest: digest }) as unknown as { previewToken: string };
    const [payload, signature] = preview.previewToken.split(".") as [string, string];
    const decoded = JSON.parse(Buffer.from(payload, "base64url").toString("utf8")) as Record<string, unknown>;
    decoded[field] = value;
    const substituted = `${Buffer.from(JSON.stringify(decoded), "utf8").toString("base64url")}.${signature}`;
    await expect(state.runtime.install({ previewToken: substituted })).rejects.toThrow("preview_invalid");
    expect(state.mutations).toBe(0);
  });

  it("returns stale_preview when current decisions change and preserves deterministic replay", async () => {
    const state = secureSetup();
    const preview = await state.runtime.preview({ projectId: "project-1", pluginName: "github", catalogDigest: digest }) as unknown as { previewToken: string };
    const first = await state.runtime.install({ previewToken: preview.previewToken });
    const replay = await state.runtime.install({ previewToken: preview.previewToken });
    expect(replay).toEqual(first);
    expect(state.mutations).toBe(1);

    const changed = secureSetup();
    const stale = await changed.runtime.preview({ projectId: "project-1", pluginName: "github", catalogDigest: digest }) as unknown as { previewToken: string };
    changed.preview = Object.freeze({
      pluginName: "github", catalogDigest: digest, sourceCommit, policyVersion: "openai-plugin-policy-v1",
      license: Object.freeze({ declaration: "MIT", decision: "admitted" }), admission: "admitted",
      components: Object.freeze([Object.freeze({
        componentDigest: digest, name: "github-mcp", kind: "mcp",
        admission: Object.freeze({ status: "admitted", policyVersion: "openai-plugin-policy-v1" }),
        availability: Object.freeze({ status: "unavailable", reason: "provider_unavailable" }),
      })]), credentialSlots: Object.freeze([]),
    });
    await expect(changed.runtime.install({ previewToken: stale.previewToken })).rejects.toThrow("stale_preview");
    expect(changed.mutations).toBe(0);

    const concurrentlyInstalled = secureSetup();
    const absentToken = await concurrentlyInstalled.runtime.preview({ projectId: "project-1", pluginName: "github", catalogDigest: digest }) as unknown as { previewToken: string };
    concurrentlyInstalled.installations = [Object.freeze({
      pluginName: "github", pluginVersion: "1.0.0", catalogDigest: digest, policyVersion: "openai-plugin-policy-v1",
      license: Object.freeze({ declaration: "MIT", decision: "admitted" }), admission: "admitted", components: Object.freeze([]),
      enabled: true, revision: 0,
    })];
    await expect(concurrentlyInstalled.runtime.install({ previewToken: absentToken.previewToken })).rejects.toThrow("stale_preview");
    expect(concurrentlyInstalled.mutations).toBe(0);

    const revised = secureSetup();
    const revisionToken = await revised.runtime.preview({ projectId: "project-1", pluginName: "github", catalogDigest: digest }) as unknown as { previewToken: string };
    revised.installations = [Object.freeze({
      pluginName: "github", pluginVersion: "1.0.0", catalogDigest: digest, policyVersion: "openai-plugin-policy-v1",
      license: Object.freeze({ declaration: "MIT", decision: "admitted" }), admission: "admitted", components: Object.freeze([]),
      enabled: true, revision: 1,
    })];
    await expect(revised.runtime.install({ previewToken: revisionToken.previewToken })).rejects.toThrow("stale_preview");
    expect(revised.mutations).toBe(0);
  });

  it("returns the exact receipt before credential, catalog or installation state drift checks", async () => {
    const state = secureSetup();
    const preview = await state.runtime.preview({ projectId: "project-1", pluginName: "github", catalogDigest: digest }) as unknown as { previewToken: string };
    const first = await state.runtime.install({ previewToken: preview.previewToken });
    const readsAfterInstall = { previews: state.previews, lists: state.lists };
    state.preview = Object.freeze({
      pluginName: "github", catalogDigest: digest, sourceCommit: "c".repeat(40), policyVersion: "changed-policy",
      license: Object.freeze({ declaration: "MIT", decision: "admitted" }), admission: "admitted",
      components: Object.freeze([]), credentialSlots: Object.freeze([Object.freeze({ name: "GITHUB_TOKEN", configured: true })]),
    });
    state.installations = [Object.freeze({
      pluginName: "github", pluginVersion: "2.0.0", catalogDigest: "f".repeat(64), policyVersion: "changed-policy",
      license: Object.freeze({ declaration: "MIT", decision: "admitted" }), admission: "admitted", components: Object.freeze([]),
      enabled: true, revision: 7,
    })];
    expect(await state.runtime.install({ previewToken: preview.previewToken })).toEqual(first);
    expect(state.mutations).toBe(1);
    expect(state.replayLookups).toBe(2);
    expect(state.lastReplayInput).toMatchObject({
      version: "rowboat_plugin_preview_v1", actorType: "user", actorId: "user-1",
      projectId: "project-1", pluginName: "github", catalogDigest: digest, sourceCommit,
      installationPresent: false, expectedRevision: 0, componentDecisionsDigest: expect.stringMatching(/^[a-f0-9]{64}$/),
      credentialSlotsDigest: expect.stringMatching(/^[a-f0-9]{64}$/), idempotencyKey: "server-key-1",
      operation: "install", issuedAt: 1_700_000_000_000, expiresAt: 1_700_000_300_000,
    });
    expect({ previews: state.previews, lists: state.lists }).toEqual(readsAfterInstall);
  });

  it("fails closed without a configured signing secret before preview reads", async () => {
    let resolves = 0;
    const runtime = createPluginActionRuntime({
      resolveControllers: async () => { resolves += 1; throw new Error("must_not_resolve"); },
      createRequest: () => new Request("https://rowboat.invalid/internal/plugin-action"),
      createIdempotencyKey: () => "server-key-1",
      previewSecret: undefined,
      pinnedCatalogDigest: digest,
      now: () => 1_700_000_000_000,
    });
    await expect(runtime.preview({ projectId: "project-1", pluginName: "github", catalogDigest: digest })).rejects.toThrow("preview_configuration_invalid");
    expect(resolves).toBe(0);
  });

  it.each([
    ["4097 ASCII bytes", "s".repeat(4097)],
    ["4098 multibyte UTF-8 bytes", "é".repeat(2049)],
  ])("rejects %s before resolving preview or install controllers", async (_case, previewSecret) => {
    const state = secretBoundarySetup(previewSecret);
    await expect(state.runtime.preview({ projectId: "project-1", pluginName: "github", catalogDigest: digest })).rejects.toThrow("preview_configuration_invalid");
    await expect(state.runtime.install({ previewToken: "not-a-token" })).rejects.toThrow("preview_configuration_invalid");
    expect(state.counters()).toEqual({ resolves: 0, authentications: 0, reads: 0 });
  });

  it("rejects every ASCII control character before resolving either action", async () => {
    for (const codePoint of [...Array.from({ length: 0x20 }, (_unused, index) => index), 0x7f]) {
      const state = secretBoundarySetup(`${"s".repeat(32)}${String.fromCharCode(codePoint)}`);
      await expect(state.runtime.preview({ projectId: "project-1", pluginName: "github", catalogDigest: digest })).rejects.toThrow("preview_configuration_invalid");
      await expect(state.runtime.install({ previewToken: "not-a-token" })).rejects.toThrow("preview_configuration_invalid");
      expect(state.counters(), `U+${codePoint.toString(16).padStart(4, "0")}`).toEqual({ resolves: 0, authentications: 0, reads: 0 });
    }
  });

  it.each([
    ["lone high surrogate", `${"s".repeat(32)}${String.fromCharCode(0xd800)}`],
    ["lone low surrogate", `${"s".repeat(32)}${String.fromCharCode(0xdc00)}`],
    ["high surrogate followed by text and low surrogate", `${"s".repeat(32)}${String.fromCharCode(0xd800)}x${String.fromCharCode(0xdc00)}`],
    ["reversed surrogate pair", `${"s".repeat(32)}${String.fromCharCode(0xdc00)}${String.fromCharCode(0xd800)}`],
  ])("rejects a %s before resolving either action", async (_case, previewSecret) => {
    const state = secretBoundarySetup(previewSecret);
    await expect(state.runtime.preview({ projectId: "project-1", pluginName: "github", catalogDigest: digest })).rejects.toThrow("preview_configuration_invalid");
    await expect(state.runtime.install({ previewToken: "not-a-token" })).rejects.toThrow("preview_configuration_invalid");
    expect(state.counters()).toEqual({ resolves: 0, authentications: 0, reads: 0 });
  });

  it("allows a valid paired non-BMP secret and counts its actual UTF-8 bytes", async () => {
    const previewSecret = `${"s".repeat(28)}😀`;
    expect(Buffer.byteLength(previewSecret, "utf8")).toBe(32);
    const state = secureSetup(previewSecret);
    const preview = await state.runtime.preview({ projectId: "project-1", pluginName: "github", catalogDigest: digest });
    await expect(state.runtime.install({ previewToken: preview.previewToken })).resolves.toMatchObject({ status: "success" });
    expect(state.mutations).toBe(1);
  });
});

describe("plugin preview secret operator configuration", () => {
  it("passes the secret through compose without a committed value and documents lifecycle effects", () => {
    const compose = readFileSync(resolve(process.cwd(), "../../docker-compose.yml"), "utf8");
    const readme = readFileSync(resolve(process.cwd(), "../../README.md"), "utf8");
    expect(compose).toContain("PLUGIN_UI_PREVIEW_SECRET=${PLUGIN_UI_PREVIEW_SECRET:-}");
    expect(compose).not.toMatch(/PLUGIN_UI_PREVIEW_SECRET=(?!\$\{PLUGIN_UI_PREVIEW_SECRET:-\})[^\r\n]+/);
    expect(readme).toContain("PLUGIN_UI_PREVIEW_SECRET");
    expect(readme).toContain("openssl rand -base64 48");
    expect(readme).toContain("32 to 4096 UTF-8 bytes");
    expect(readme).toContain("any ASCII control character (U+0000–U+001F or U+007F)");
    expect(readme).toContain("any unpaired UTF-16 surrogate");
    expect(readme).toContain("Valid paired non-BMP characters are allowed");
    expect(readme).toContain("limits are counted after UTF-8 encoding");
    expect(readme).toContain("preview_configuration_invalid");
    expect(readme).toContain("Rotation invalidates outstanding plugin previews");
  });
});

describe("plugin card tool offers", () => {
  const component = (overrides: Record<string, unknown> = {}) => ({ name: "github", kind: "mcp", status: "available", componentDigest: "a".repeat(64), ...overrides });
  const item = (overrides: Record<string, unknown> = {}) => ({
    pluginName: "github", pluginVersion: "1.0.0", catalogDigest: "d".repeat(64), sourceCommit: "1".repeat(40),
    status: "installed", components: [component()], ...overrides,
  }) as Parameters<typeof toPluginCardView>[0];

  it("offers only executable, available components that carry a pinned digest", () => {
    const view = toPluginCardView(item({ components: [
      component(),
      component({ name: "slack", kind: "app" }),
      component({ name: "review", kind: "skill" }),
      component({ name: "broken", status: "unavailable" }),
      component({ name: "unpinned", componentDigest: undefined }),
    ] }));
    expect(view.addableComponents.map(entry => entry.name)).toEqual(["github", "slack"]);
    expect(view.addableComponents.every(entry => entry.canAdd)).toBe(true);
  });

  it("does not offer to add a tool before the plugin is installed", () => {
    for (const status of ["available", "review_required", "partially_available", "unavailable", "error"] as const) {
      const view = toPluginCardView(item({ status }));
      expect(view.addableComponents.every(entry => !entry.canAdd)).toBe(true);
    }
    expect(toPluginCardView(item()).addableComponents[0]!.canAdd).toBe(true);
  });
});

describe("plugin tool panel summary", () => {
  const binding = Object.freeze({
    installationId: "33333333-3333-4333-8333-333333333333", pluginName: "actively",
    componentDigest: "e5461d911ee3b869576d8c781ab5401cf38f8c4756a1b6b757f3d0e1ab880392",
    providerBindingId: "app.actively", capability: "write" as const, origin: "native" as const,
  });

  it("describes a plugin tool by its real provenance", () => {
    expect(pluginToolSummary({ name: "plugin_actively_actively", pluginBinding: binding })).toEqual({
      pluginName: "actively", providerBindingId: "app.actively", componentDigestShort: "e5461d911ee3",
      capability: "write", origin: "native",
      originLabel: "Added from the plugin catalog", capabilityLabel: "Write-capable",
    });
  });

  it("reads a migrated or unmarked binding as migrated", () => {
    const { origin: dropped, ...unmarked } = binding;
    void dropped;
    expect(pluginToolSummary({ pluginBinding: unmarked })).toMatchObject({ origin: "migration", originLabel: "Migrated from a legacy tool" });
    expect(pluginToolSummary({ pluginBinding: { ...binding, origin: "handmade" } })).toMatchObject({ origin: "migration" });
    expect(pluginToolSummary({ pluginBinding: { ...binding, capability: "read" } })).toMatchObject({ capability: "read", capabilityLabel: "Read-only" });
  });

  it("says nothing about a tool without a structurally valid binding", () => {
    expect(pluginToolSummary({ name: "webhook_tool" })).toBeNull();
    expect(pluginToolSummary(null)).toBeNull();
    expect(pluginToolSummary({ pluginBinding: [] })).toBeNull();
    for (const broken of [
      { ...binding, pluginName: "not valid" },
      { ...binding, componentDigest: "short" },
      { ...binding, providerBindingId: "" },
      { ...binding, capability: "maybe" },
    ]) {
      expect(pluginToolSummary({ pluginBinding: broken })).toBeNull();
    }
  });
});

// A partially available plugin - `github` in the pinned catalog - is installed
// by naming the components the caller wants. The selection is bound into the
// signed envelope, so a token issued for one selection cannot install another.

describe("component-scoped installation through the server action", () => {
  const mcpDigest = "1".repeat(64);
  const appDigest = "2".repeat(64);
  const skillDigest = "3".repeat(64);
  const policyVersion = "openai-plugin-policy-v1";
  const admitted = Object.freeze({ status: "admitted", policyVersion });

  const partialPreview = Object.freeze({
    pluginName: "github", catalogDigest: digest, sourceCommit, policyVersion,
    license: Object.freeze({ declaration: "MIT", decision: "admitted" }), admission: "admitted",
    components: Object.freeze([
      Object.freeze({ componentDigest: mcpDigest, name: "github", kind: "mcp", admission: admitted, availability: Object.freeze({ status: "available" }) }),
      Object.freeze({ componentDigest: appDigest, name: "github", kind: "app", admission: admitted, availability: Object.freeze({ status: "available" }) }),
      Object.freeze({
        componentDigest: skillDigest, name: "github", kind: "skill",
        admission: Object.freeze({ status: "review_required", reason: "write_review_required", policyVersion }),
        availability: Object.freeze({ status: "available" }),
      }),
    ]),
    credentialSlots: Object.freeze([]),
  });

  function setup() {
    let mutations = 0;
    let installInput: Readonly<Record<string, unknown>> | null = null;
    let lastReplayInput: Readonly<Record<string, unknown>> | null = null;
    let currentPreview: Readonly<Record<string, unknown>> = partialPreview;
    const runtime = createPluginActionRuntime({
      resolveControllers: async () => Object.freeze({
        authenticate: async () => Object.freeze({ kind: "user" as const, userId: "user-1" }),
        findInstallReplay: async (_request: Request, input: Readonly<Record<string, unknown>>) => { lastReplayInput = input; return null; },
        catalog: Object.freeze({ execute: async () => [] }),
        installation: Object.freeze({
          list: async () => [],
          preview: async () => currentPreview,
          install: async (_request: Request, input: Readonly<Record<string, unknown>>) => {
            mutations += 1; installInput = input;
            return Object.freeze({ type: "install", receiptId: "receipt-1", projectId: "project-1", pluginName: "github", status: "success", redactions: Object.freeze([]) });
          },
        }),
      }),
      createRequest: () => new Request("https://rowboat.invalid/internal/plugin-action"),
      createIdempotencyKey: () => "server-key-1",
      previewSecret: "s".repeat(64),
      pinnedCatalogDigest: digest,
      now: () => 1_700_000_000_000,
    });
    const previewFor = async (componentDigests?: readonly string[], priorPreviewToken?: string) => (await runtime.preview({
      projectId: "project-1", pluginName: "github", catalogDigest: digest,
      ...(componentDigests === undefined ? {} : { componentDigests }),
      ...(priorPreviewToken === undefined ? {} : { priorPreviewToken }),
    })) as unknown as { previewToken: string };
    return {
      runtime, previewFor,
      set preview(value: Readonly<Record<string, unknown>>) { currentPreview = value; },
      get mutations() { return mutations; },
      get installInput() { return installInput; },
      get lastReplayInput() { return lastReplayInput; },
    };
  }

  it("installs a partially available plugin when only its admitted components are selected", async () => {
    const state = setup();
    const preview = await state.previewFor([mcpDigest]);
    await expect(state.runtime.install({ previewToken: preview.previewToken, componentDigests: [mcpDigest] }))
      .resolves.toMatchObject({ receiptId: "receipt-1" });
    expect(state.mutations).toBe(1);
    expect(state.installInput).toEqual({
      projectId: "project-1", pluginName: "github", catalogDigest: digest,
      expectedRevision: 0, idempotencyKey: "server-key-1", componentDigests: [mcpDigest],
    });
  });

  it("binds the selection into the signed envelope and rejects the same token with another selection", async () => {
    const state = setup();
    const preview = await state.previewFor([mcpDigest]);
    const payload = JSON.parse(Buffer.from(preview.previewToken.split(".")[0]!, "base64url").toString("utf8")) as Record<string, unknown>;
    expect(payload.componentSelectionDigest).toMatch(/^[a-f0-9]{64}$/);
    for (const componentDigests of [[appDigest], [mcpDigest, appDigest], undefined]) {
      await expect(state.runtime.install({
        previewToken: preview.previewToken, ...(componentDigests === undefined ? {} : { componentDigests }),
      })).rejects.toThrow("preview_invalid");
    }
    expect(state.mutations).toBe(0);
  });

  it("accepts a selection the caller supplied in another order and hands the replay lookup the bound digest", async () => {
    const state = setup();
    const preview = await state.previewFor([appDigest, mcpDigest]);
    await expect(state.runtime.install({ previewToken: preview.previewToken, componentDigests: [mcpDigest, appDigest] }))
      .resolves.toMatchObject({ receiptId: "receipt-1" });
    expect(state.installInput).toMatchObject({ componentDigests: [mcpDigest, appDigest].sort() });
    expect(state.lastReplayInput).toMatchObject({ componentSelectionDigest: expect.stringMatching(/^[a-f0-9]{64}$/) });
  });

  it("refuses a selection that names a component the catalog does not admit, before signing anything", async () => {
    const state = setup();
    await expect(state.previewFor([mcpDigest, skillDigest])).rejects.toThrow("write_review_required");
    await expect(state.previewFor(["4".repeat(64)])).rejects.toThrow("request_invalid");
    await expect(state.previewFor([])).rejects.toThrow("request_invalid");
    await expect(state.previewFor([mcpDigest, mcpDigest])).rejects.toThrow("request_invalid");
    expect(state.mutations).toBe(0);
  });

  it("still refuses to install a partially available plugin whole", async () => {
    const state = setup();
    const preview = await state.previewFor();
    await expect(state.runtime.install({ previewToken: preview.previewToken })).rejects.toThrow("write_review_required");
    expect(state.mutations).toBe(0);
  });

  // The dialog re-previews at click time to authorize the selection the operator
  // ended up with. Handing back the token the dialog was opened with makes that
  // re-signing refuse whenever a decision drifted while the dialog was open, so
  // staleness still spans the whole review rather than the last few milliseconds.
  it("refuses to re-sign a preview when a decision drifted since the prior envelope", async () => {
    const state = setup();
    const opened = await state.previewFor([mcpDigest]);
    state.preview = Object.freeze({
      ...partialPreview,
      components: Object.freeze([
        Object.freeze({ componentDigest: mcpDigest, name: "github", kind: "mcp", admission: admitted, availability: Object.freeze({ status: "available" }) }),
        Object.freeze({ componentDigest: appDigest, name: "github", kind: "app", admission: admitted, availability: Object.freeze({ status: "unavailable", reason: "provider_unavailable" }) }),
        Object.freeze({
          componentDigest: skillDigest, name: "github", kind: "skill",
          admission: Object.freeze({ status: "review_required", reason: "write_review_required", policyVersion }),
          availability: Object.freeze({ status: "available" }),
        }),
      ]),
    });
    await expect(state.previewFor([mcpDigest], opened.previewToken)).rejects.toThrow("stale_preview");
    expect(state.mutations).toBe(0);
  });

  it("re-signs against an unchanged prior envelope and installs with the fresh token", async () => {
    const state = setup();
    const opened = await state.previewFor([mcpDigest]);
    const authorized = await state.previewFor([mcpDigest, appDigest], opened.previewToken);
    expect(typeof authorized.previewToken).toBe("string");
    await expect(state.runtime.install({ previewToken: authorized.previewToken, componentDigests: [mcpDigest, appDigest] }))
      .resolves.toMatchObject({ receiptId: "receipt-1" });
    expect(state.mutations).toBe(1);
  });

  it("refuses a prior envelope that was issued for another plugin, project or secret", async () => {
    const state = setup();
    const opened = await state.previewFor([mcpDigest]);
    const last = opened.previewToken.endsWith("A") ? "B" : "A";
    for (const input of [
      { projectId: "project-2", pluginName: "github", catalogDigest: digest, priorPreviewToken: opened.previewToken },
      { projectId: "project-1", pluginName: "other", catalogDigest: digest, priorPreviewToken: opened.previewToken },
      { projectId: "project-1", pluginName: "github", catalogDigest: digest, priorPreviewToken: `${opened.previewToken.slice(0, -1)}${last}` },
    ]) {
      await expect(state.runtime.preview(input)).rejects.toThrow("preview_invalid");
    }
    expect(state.mutations).toBe(0);
  });
});

describe("component selection in the catalog card and install dialog", () => {
  const component = (overrides: Record<string, unknown> = {}) => ({ componentDigest: "a".repeat(64), name: "github", kind: "mcp", status: "available", ...overrides });
  const previewOf = (components: readonly Record<string, unknown>[]) => Object.freeze({
    pluginName: "github", pluginVersion: "1.0.0", catalogDigest: digest, sourceCommit,
    status: "partially_available", components, previewToken: "signed-preview-token",
    credentialSlots: Object.freeze([]),
  }) as unknown as Parameters<typeof toPluginInstallDialogView>[0];

  it("offers a partially available plugin for installation and an installed one never", () => {
    const item = (overrides: Record<string, unknown>) => ({
      pluginName: "github", pluginVersion: "1.0.0", catalogDigest: digest, sourceCommit, ...overrides,
    }) as PluginCatalogCardItem;
    const components = [component(), component({ componentDigest: "b".repeat(64), status: "review_required", reason: "write_review_required" })];
    expect(toPluginCardView(item({ status: "partially_available", components })).canInstall).toBe(true);
    expect(toPluginCardView(item({ status: "partially_available", components, revision: 3 })).canInstall).toBe(false);
    expect(toPluginCardView(item({ status: "installed", components })).canInstall).toBe(false);
    expect(toPluginCardView(item({ status: "unavailable", components: [component({ status: "unavailable", reason: "provider_unavailable" })] })).canInstall).toBe(false);
  });

  it("preselects every available component and keeps the rest visible but unselectable", () => {
    const view = toPluginInstallDialogView(previewOf([
      component({ componentDigest: "b".repeat(64) }),
      component(),
      component({ componentDigest: "c".repeat(64), kind: "skill", status: "review_required", reason: "write_review_required" }),
      component({ componentDigest: undefined, kind: "asset" }),
    ]));
    expect(view.defaultSelection).toEqual(["a".repeat(64), "b".repeat(64)]);
    expect(view.components.map((entry) => entry.selectable)).toEqual([true, true, false, false]);
    expect(view.components[2]).toMatchObject({ status: "review_required", reason: "write_review_required", selectable: false });
    expect(view.canInstall).toBe(true);
    expect(Object.isFrozen(view.defaultSelection)).toBe(true);
  });

  it("offers no installation when the plugin has no available component", () => {
    const view = toPluginInstallDialogView(previewOf([component({ status: "review_required", reason: "write_review_required" })]));
    expect(view.defaultSelection).toEqual([]);
    expect(view.canInstall).toBe(false);
  });
});
