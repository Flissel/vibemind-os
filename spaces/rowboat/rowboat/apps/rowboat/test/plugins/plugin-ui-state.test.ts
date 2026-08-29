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

function card(status: PluginCatalogCardItem["status"], reason?: PluginCatalogCardItem["reason"]): PluginCatalogCardItem {
  return Object.freeze({
    pluginName: "github",
    pluginVersion: "1.0.0",
    catalogDigest: digest,
    sourceCommit,
    status,
    ...(reason === undefined ? {} : { reason }),
    components: Object.freeze([]),
  });
}

describe("plugin catalog view state", () => {
  it.each([
    ["available", "Available", true],
    ["review_required", "Review required", false],
    ["installed", "Installed", false],
    ["partially_available", "Partially available", false],
    ["unavailable", "Unavailable", false],
    ["migration_required", "Migration required", false],
    ["error", "Error", false],
  ] as const)("renders the server-owned %s state", (status, badge, canInstall) => {
    expect(toPluginCardView(card(status))).toEqual(expect.objectContaining({ badge, canInstall }));
  });

  it("does not label a partial plugin installed and preserves the policy reason", () => {
    expect(toPluginCardView(card("partially_available", "provider_unavailable"))).toMatchObject({
      badge: "Partially available",
      canInstall: false,
      reason: "provider_unavailable",
    });
    expect(toPluginCardView(card("review_required", "license_review_required")).reason).toBe("license_review_required");
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
      components: Object.freeze([Object.freeze({ name: "github-mcp", kind: "mcp" as const, status: "available", reason: "write_review_required" as const })]),
      credentialSlots: Object.freeze([Object.freeze({ name: "GITHUB_TOKEN", configured: false })]),
    }));
    expect(view.components).toEqual([{ name: "github-mcp", kind: "mcp", status: "available", reason: "write_review_required" }]);
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
