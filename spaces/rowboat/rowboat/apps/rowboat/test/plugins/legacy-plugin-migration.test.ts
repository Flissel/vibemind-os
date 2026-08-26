import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import {
  PINNED_OPENAI_PLUGINS_COMMIT,
  PINNED_PLUGIN_CATALOG_DIGEST,
  ZPluginMigrationRecord,
  type PluginCatalogLock,
} from "@rowboat/openai-plugin-runtime";
import {
  LEGACY_CARD_IDS,
  LEGACY_PLUGIN_RECIPES,
  resolveLegacyRecipe,
} from "@/src/application/services/legacy-plugin-recipes";
import {
  LegacyPluginMigration,
  LegacyPluginMigrationError,
} from "@/src/application/services/legacy-plugin-migration";

const catalog = JSON.parse(readFileSync(new URL("../../../../config/openai-plugin-catalog.lock.json", import.meta.url), "utf8")) as PluginCatalogLock;
const cardsRoot = new URL("../../app/lib/prebuilt-cards/", import.meta.url);
const now = "2026-08-26T12:00:00.000Z";

function source(cardId: (typeof LEGACY_CARD_IDS)[number]) {
  return {
    projectId: "11111111-1111-4111-8111-111111111111",
    sourceProjectRevision: 7,
    legacyCardId: cardId,
    sourceConfiguration: JSON.parse(readFileSync(new URL(`${cardId}.json`, cardsRoot), "utf8")) as unknown,
  };
}

describe("legacy plugin migration recipes", () => {
  it("defines exactly the ten checked-in cards from their real tool configuration", () => {
    expect(LEGACY_CARD_IDS).toEqual([
      "customer-support",
      "eisenhower-email-organizer",
      "github-data-to-spreadsheet",
      "github-issue-to-slack",
      "github-pr-to-slack",
      "interview-scheduler",
      "meeting-prep-assistant",
      "reddit-on-slack",
      "tweet-assistant",
      "twitter-sentiment",
    ]);
    expect(Object.keys(LEGACY_PLUGIN_RECIPES)).toEqual(LEGACY_CARD_IDS);
    expect(LEGACY_PLUGIN_RECIPES["github-issue-to-slack"].capabilities.map(item => item.legacyToolSlug)).toEqual(["SLACK_SEND_MESSAGE"]);
    expect(LEGACY_PLUGIN_RECIPES["github-pr-to-slack"].capabilities.map(item => item.legacyToolSlug)).toEqual(["SLACK_SEND_MESSAGE"]);
    expect(LEGACY_PLUGIN_RECIPES["github-data-to-spreadsheet"].capabilities.map(item => item.legacyToolSlug)).toEqual([
      "GITHUB_GET_PAGE_VIEWS",
      "GOOGLESHEETS_SPREADSHEETS_VALUES_APPEND",
      "GITHUB_GET_REPOSITORY_CLONES",
      "SLACK_SENDS_A_MESSAGE_TO_A_SLACK_CHANNEL",
    ]);
    expect(Object.isFrozen(LEGACY_PLUGIN_RECIPES)).toBe(true);
    expect(Object.isFrozen(LEGACY_PLUGIN_RECIPES["github-data-to-spreadsheet"].capabilities)).toBe(true);
  });

  it.each(LEGACY_CARD_IDS)("accounts for every real legacy tool in %s without invented capabilities", cardId => {
    const card = JSON.parse(readFileSync(new URL(`${cardId}.json`, cardsRoot), "utf8")) as {
      readonly tools: readonly {
        readonly name: string;
        readonly mockTool?: boolean;
        readonly composioData?: { readonly slug?: string };
      }[];
    };
    const actualToolIdentities = card.tools.map(tool => tool.mockTool === true
      ? `MOCK:${tool.name}`
      : tool.composioData?.slug).filter((value): value is string => value !== undefined);
    expect(LEGACY_PLUGIN_RECIPES[cardId].capabilities.map(item => item.legacyToolSlug)).toEqual(actualToolIdentities);
  });

  it.each(LEGACY_CARD_IDS)("builds a byte-stable, read-only preview for %s", cardId => {
    const mutation = new LegacyPluginMigration({ now: () => now });
    const input = source(cardId);
    const before = structuredClone(input);
    const first = mutation.preview(input, catalog);
    const second = mutation.preview(input, catalog);
    expect(JSON.stringify(first)).toBe(JSON.stringify(second));
    expect(first).toMatchObject({
      recipeId: `legacy-card:${cardId}:v1`,
      sourceProjectRevision: 7,
      targetCatalogDigest: PINNED_PLUGIN_CATALOG_DIGEST,
      targetSourceCommit: PINNED_OPENAI_PLUGINS_COMMIT,
      mutationsApplied: false,
      createdAt: now,
    });
    const { mutationsApplied: _mutationsApplied, installations: _installations, ...record } = first;
    expect(ZPluginMigrationRecord.parse(record)).toEqual(expect.objectContaining({ id: first.id }));
    expect(input).toEqual(before);
    expect(Object.isFrozen(first)).toBe(true);
    expect(Object.isFrozen(first.blockers)).toBe(true);
  });

  it("recognizes the exact admitted GitHub MCP but does not fabricate a missing provider binding", () => {
    const result = new LegacyPluginMigration({ now: () => now }).preview(source("github-data-to-spreadsheet"), catalog);
    expect(result.status).toBe("blocked");
    expect(result.installations).toEqual([]);
    expect(result.blockers).toEqual(expect.arrayContaining([
      expect.objectContaining({ capabilityId: "github-page-views", componentId: "mcp:.mcp.json#github", code: "provider_unavailable" }),
      expect.objectContaining({ capabilityId: "github-repository-clones", componentId: "mcp:.mcp.json#github", code: "provider_unavailable" }),
      expect.objectContaining({ capabilityId: "google-sheets-append", code: "admission_review_required" }),
      expect.objectContaining({ capabilityId: "slack-send-message", code: "admission_review_required" }),
    ]));
  });

  it.each([
    ["customer-support", "mock-delivery-status"],
    ["reddit-on-slack", "reddit-search"],
    ["tweet-assistant", "x-create-post"],
    ["meeting-prep-assistant", "web-search"],
  ] as const)("blocks %s when exact provider %s is absent", (cardId, capabilityId) => {
    const result = new LegacyPluginMigration({ now: () => now }).preview(source(cardId), catalog);
    expect(result.blockers).toContainEqual(expect.objectContaining({ capabilityId, code: "provider_unavailable" }));
  });

  it("distinguishes missing, ambiguous, provider, license, admission, and availability failures", () => {
    const base = catalog.entries.find(entry => entry.name === "github")!;
    const exact = base.components.find(item => item.component.id === "mcp:.mcp.json#github")!;
    const capability = LEGACY_PLUGIN_RECIPES["github-data-to-spreadsheet"].capabilities[0]!;
    expect(resolveLegacyRecipe(capability, [])).toEqual(expect.objectContaining({ code: "provider_unavailable" }));
    expect(resolveLegacyRecipe(capability, [{ ...base, components: [] }])).toEqual(expect.objectContaining({ code: "component_missing" }));
    expect(resolveLegacyRecipe(capability, [base, structuredClone(base)])).toEqual(expect.objectContaining({ code: "component_ambiguous" }));
    expect(resolveLegacyRecipe(capability, [{ ...base, admission: { status: "review_required", reason: "license_review_required", policyVersion: base.policyVersion } }])).toEqual(expect.objectContaining({ code: "license_review_required" }));
    expect(resolveLegacyRecipe(capability, [{ ...base, admission: { status: "rejected", reason: "license_rejected", policyVersion: base.policyVersion } }])).toEqual(expect.objectContaining({ code: "license_rejected" }));
    expect(resolveLegacyRecipe(capability, [{ ...base, components: [{ ...exact, admission: { status: "review_required", reason: "write_review_required", policyVersion: base.policyVersion } }] }])).toEqual(expect.objectContaining({ code: "admission_review_required" }));
    expect(resolveLegacyRecipe(capability, [{ ...base, components: [{ ...exact, component: { ...exact.component, status: "unavailable", reason: "provider_unavailable" } }] }])).toEqual(expect.objectContaining({ code: "provider_unavailable" }));
  });

  it("rejects catalog drift before creating a preview", () => {
    const drifted = structuredClone(catalog);
    (drifted as { sourceCommit: string }).sourceCommit = "f".repeat(40);
    expect(() => new LegacyPluginMigration({ now: () => now }).preview(source("customer-support"), drifted)).toThrowError("catalog_drift");
  });

  it("rejects an applied claim when the pinned catalog still has blockers", () => {
    const mutation = new LegacyPluginMigration({ now: () => now });
    const input = source("github-data-to-spreadsheet");
    const preview = mutation.preview(input, catalog);
    const { mutationsApplied: _mutationsApplied, installations: _installations, ...previewRecord } = preview;
    const applied = { ...previewRecord, status: "applied" as const };
    expect(() => mutation.preview(input, catalog, applied)).toThrowError("migration_record_invalid");
    expect(() => mutation.preview(input, catalog, { ...applied, targetInstallationIds: ["spoofed"] })).toThrowError("migration_record_invalid");
    expect(() => mutation.preview({ ...input, sourceProjectRevision: 8 }, catalog, applied)).toThrowError("migration_record_invalid");
  });

  it("rejects unknown fields, proxies, accessors, polluted prototypes, and unknown cards without touching dependencies", () => {
    const calls = { repository: 0, provider: 0, credential: 0, network: 0 };
    const mutation = new LegacyPluginMigration({ now: () => now, sideEffectGuards: {
      repository: () => { calls.repository += 1; },
      provider: () => { calls.provider += 1; },
      credential: () => { calls.credential += 1; },
      network: () => { calls.network += 1; },
    } });
    const valid = source("customer-support");
    const accessor = { ...valid };
    Object.defineProperty(accessor, "legacyCardId", { enumerable: true, get: () => "customer-support" });
    const polluted = Object.assign(Object.create({ inherited: true }), valid);
    const attempts: unknown[] = [
      { ...valid, extra: true },
      new Proxy(valid, {}),
      accessor,
      polluted,
      { ...valid, legacyCardId: "unknown-card" },
      { ...valid, sourceConfiguration: { __proto__: { polluted: true } } },
    ];
    for (const attempt of attempts) expect(() => mutation.preview(attempt, catalog)).toThrow(LegacyPluginMigrationError);
    expect(calls).toEqual({ repository: 0, provider: 0, credential: 0, network: 0 });
  });

  it("never serializes credentials, raw tool configuration, or secret-shaped values", () => {
    const input = source("customer-support");
    input.sourceConfiguration = {
      nested: { apiKey: "sensitive-api-key-value", authorization: "sensitive-authorization-value" },
      tool: { rawConfig: "sensitive-database-configuration" },
    };
    const result = new LegacyPluginMigration({ now: () => now }).preview(input, catalog);
    const serialized = JSON.stringify(result);
    expect(serialized).not.toContain("sensitive-api-key-value");
    expect(serialized).not.toContain("sensitive-authorization-value");
    expect(serialized).not.toContain("sensitive-database-configuration");
    expect(serialized).not.toContain("rawConfig");
    expect(result.sourceDigest).toMatch(/^[a-f0-9]{64}$/);
  });

  it("uses stable canonical sorting and domain-separated ids and digests", () => {
    const firstInput = source("customer-support");
    const secondInput = source("customer-support");
    firstInput.sourceConfiguration = { b: 2, a: { d: 4, c: 3 } };
    secondInput.sourceConfiguration = { a: { c: 3, d: 4 }, b: 2 };
    const mutation = new LegacyPluginMigration({ now: () => now });
    const first = mutation.preview(firstInput, catalog);
    const second = mutation.preview(secondInput, catalog);
    expect(first.sourceDigest).toBe(second.sourceDigest);
    expect(first.id).toBe(second.id);
    expect(first.rollbackSnapshotDigest).not.toBe(first.sourceDigest);
    expect(first.blockers).toEqual([...first.blockers].sort((left, right) => `${left.capabilityId}\0${left.code}`.localeCompare(`${right.capabilityId}\0${right.code}`)));
  });
});
