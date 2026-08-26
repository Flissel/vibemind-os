import { readFileSync } from "node:fs";
import { describe, expect, it, vi } from "vitest";
import {
  PINNED_OPENAI_PLUGINS_COMMIT,
  PINNED_PLUGIN_CATALOG_DIGEST,
  ZPluginMigrationRecord,
  type PluginCatalogLock,
} from "@rowboat/openai-plugin-runtime";
import {
  LEGACY_CARD_IDS,
  LEGACY_PLUGIN_RECIPES,
  computeLegacyRecipeDigest,
  resolveLegacyRecipe,
} from "@/src/application/services/legacy-plugin-recipes";
import * as legacyRecipeModule from "@/src/application/services/legacy-plugin-recipes";
import {
  LegacyPluginMigration,
  LegacyPluginMigrationError,
} from "@/src/application/services/legacy-plugin-migration";

const catalog = JSON.parse(readFileSync(new URL("../../../../config/openai-plugin-catalog.lock.json", import.meta.url), "utf8")) as PluginCatalogLock;
const cardsRoot = new URL("../../app/lib/prebuilt-cards/", import.meta.url);

function card(cardId: (typeof LEGACY_CARD_IDS)[number]): Record<string, unknown> {
  return JSON.parse(readFileSync(new URL(`${cardId}.json`, cardsRoot), "utf8")) as Record<string, unknown>;
}

function source(cardId: (typeof LEGACY_CARD_IDS)[number]) {
  const sourceConfiguration = card(cardId);
  return {
    projectId: "11111111-1111-4111-8111-111111111111",
    sourceProjectRevision: 7,
    sourceUpdatedAt: sourceConfiguration.lastUpdatedAt ?? null,
    legacyCardId: cardId,
    sourceConfiguration,
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
    expect(LEGACY_PLUGIN_RECIPES["github-issue-to-slack"].capabilities.map(item => item.legacyAction.identity)).toEqual(["composio:SLACK_SEND_MESSAGE"]);
    expect(LEGACY_PLUGIN_RECIPES["github-pr-to-slack"].capabilities.map(item => item.legacyAction.identity)).toEqual(["composio:SLACK_SEND_MESSAGE"]);
    expect(LEGACY_PLUGIN_RECIPES["github-data-to-spreadsheet"].capabilities.map(item => item.legacyAction.identity)).toEqual([
      "composio:GITHUB_GET_PAGE_VIEWS",
      "composio:GOOGLESHEETS_SPREADSHEETS_VALUES_APPEND",
      "composio:GITHUB_GET_REPOSITORY_CLONES",
      "composio:SLACK_SENDS_A_MESSAGE_TO_A_SLACK_CHANNEL",
    ]);
    expect(Object.values(LEGACY_PLUGIN_RECIPES).reduce((sum, recipe) => sum + recipe.inventory.counts.actions, 0)).toBe(19);
    expect(Object.values(LEGACY_PLUGIN_RECIPES).reduce((sum, recipe) => sum + recipe.inventory.counts.agents, 0)).toBe(32);
    expect(Object.values(LEGACY_PLUGIN_RECIPES).reduce((sum, recipe) => sum + recipe.inventory.counts.prompts, 0)).toBe(26);
    expect(Object.values(LEGACY_PLUGIN_RECIPES).reduce((sum, recipe) => sum + recipe.inventory.counts.pipelines, 0)).toBe(6);
    for (const recipe of Object.values(LEGACY_PLUGIN_RECIPES)) expect(recipe.recipeDigest).toMatch(/^[a-f0-9]{64}$/);
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
      ? `mock:${tool.name}`
      : `composio:${tool.composioData?.slug}`);
    expect(LEGACY_PLUGIN_RECIPES[cardId].capabilities.map(item => item.legacyAction.identity)).toEqual(actualToolIdentities);
  });

  it("binds the recipe digest to ordered actions and exact or null target mappings", () => {
    const recipe = LEGACY_PLUGIN_RECIPES["customer-support"];
    expect(recipe.capabilities[0]!.target).toBeNull();
    expect(computeLegacyRecipeDigest(recipe)).toBe(recipe.recipeDigest);
    expect(computeLegacyRecipeDigest({
      ...recipe,
      capabilities: [{ ...recipe.capabilities[0]!, target: {
        pluginName: "github",
        componentId: "mcp:.mcp.json#github",
        providerKind: "mcp-http",
      } }],
    })).not.toBe(recipe.recipeDigest);
  });

  it.each([
    ["missing", (configuration: Record<string, unknown>) => { (configuration.tools as unknown[]).pop(); }],
    ["duplicate", (configuration: Record<string, unknown>) => { (configuration.tools as unknown[]).push(structuredClone((configuration.tools as unknown[])[0])); }],
    ["altered", (configuration: Record<string, unknown>) => { ((configuration.tools as Record<string, unknown>[])[0]!).description = "altered action"; }],
    ["extra", (configuration: Record<string, unknown>) => { (configuration.tools as unknown[]).push({ name: "Unexpected action", mockTool: true, parameters: { type: "object" } }); }],
  ] as const)("blocks %s executable source drift before target resolution", (_caseName, mutate) => {
    const input = source("github-data-to-spreadsheet");
    mutate(input.sourceConfiguration);
    const result = new LegacyPluginMigration().preview(input, catalog);
    expect(result.status).toBe("blocked");
    expect(result.targetInstallationIds).toEqual([]);
    expect(result.installations).toEqual([]);
    expect(result.blockers.length).toBeGreaterThan(0);
    expect(result.blockers.every(blocker => blocker.code === "source_configuration_drift" || blocker.code === "legacy_action_unmapped")).toBe(true);
    expect(result.blockers.every(blocker => blocker.pluginName === null && blocker.componentId === null)).toBe(true);
  });

  it.each(["agents", "prompts", "pipelines", "startAgent"] as const)("binds %s into executable source drift detection", field => {
    const input = source("interview-scheduler");
    if (field === "startAgent") input.sourceConfiguration.startAgent = "altered-entrypoint";
    else ((input.sourceConfiguration[field] as Record<string, unknown>[])[0]!).name = "altered-descriptor";
    const result = new LegacyPluginMigration().preview(input, catalog);
    expect(result.status).toBe("blocked");
    expect(result.blockers).toEqual([expect.objectContaining({ code: "source_configuration_drift", pluginName: null, componentId: null })]);
    expect(result.installations).toEqual([]);
  });

  it("blocks custom projects with one target-free unmapped blocker per executable action", () => {
    const input = {
      projectId: "22222222-2222-4222-8222-222222222222",
      sourceProjectRevision: 4,
      sourceUpdatedAt: "2026-08-20T10:11:12.000Z",
      legacyCardId: "custom-project",
      sourceConfiguration: {
        tools: [
          { name: "Private action", mockTool: true, parameters: { type: "object" } },
          { name: "Custom MCP action", isMcp: true, parameters: { type: "object" } },
        ],
        agents: [], prompts: [], pipelines: [],
      },
    };
    const result = new LegacyPluginMigration().preview(input, catalog);
    expect(result.status).toBe("blocked");
    expect(result.blockers).toHaveLength(2);
    expect(result.blockers.every(blocker => blocker.code === "legacy_action_unmapped" && blocker.pluginName === null && blocker.componentId === null)).toBe(true);
    expect(result.targetInstallationIds).toEqual([]);
  });

  it.each(LEGACY_CARD_IDS)("builds a byte-stable, read-only preview for %s", cardId => {
    const mutation = new LegacyPluginMigration();
    const input = source(cardId);
    const before = structuredClone(input);
    const first = mutation.preview(input, catalog);
    const second = new LegacyPluginMigration().preview(input, catalog);
    expect(JSON.stringify(first)).toBe(JSON.stringify(second));
    expect(first).toMatchObject({
      recipeId: `legacy-card:${cardId}:v1`,
      sourceProjectRevision: 7,
      targetCatalogDigest: PINNED_PLUGIN_CATALOG_DIGEST,
      targetSourceCommit: PINNED_OPENAI_PLUGINS_COMMIT,
      mutationsApplied: false,
      createdAt: input.sourceUpdatedAt ?? catalog.importedAt,
      recipeDigest: LEGACY_PLUGIN_RECIPES[cardId].recipeDigest,
      sourceInventoryDigest: LEGACY_PLUGIN_RECIPES[cardId].inventory.digest,
      status: "blocked",
      targetInstallationIds: [],
      installations: [],
    });
    const { mutationsApplied: _mutationsApplied, installations: _installations, ...record } = first;
    expect(ZPluginMigrationRecord.parse(record)).toEqual(expect.objectContaining({ id: first.id }));
    expect(input).toEqual(before);
    expect(Object.isFrozen(first)).toBe(true);
    expect(Object.isFrozen(first.blockers)).toBe(true);
    if (input.sourceUpdatedAt === null) expect(first.createdAt).toBe(catalog.importedAt);
  });

  it.each(LEGACY_CARD_IDS)("normalizes supported raw, assistant-load, lazy-seed, and project-import model defaults for %s", cardId => {
    const raw = source(cardId);
    const rawPreview = new LegacyPluginMigration().preview(raw, catalog);
    for (const model of ["gpt-4.1", "gpt-4o", "configured-provider-default"]) {
      const transformed = source(cardId);
      for (const agent of transformed.sourceConfiguration.agents as Record<string, unknown>[]) {
        if (agent.model === "") agent.model = model;
      }
      const preview = new LegacyPluginMigration().preview(transformed, catalog);
      expect(preview.sourceInventoryDigest).toBe(rawPreview.sourceInventoryDigest);
      expect(preview.blockers).not.toContainEqual(expect.objectContaining({ code: "source_configuration_drift" }));
      expect(preview.sourceDigest).not.toBe(rawPreview.sourceDigest);
    }
  });

  it.each([
    ["missing", (agent: Record<PropertyKey, unknown>) => { delete agent.model; }],
    ["inherited", (agent: Record<PropertyKey, unknown>) => {
      delete agent.model;
      Object.setPrototypeOf(agent, { model: "gpt-4.1" });
    }],
    ["non-enumerable", (agent: Record<PropertyKey, unknown>) => {
      Object.defineProperty(agent, "model", { configurable: true, enumerable: false, value: "gpt-4.1", writable: true });
    }],
    ["non-configurable", (agent: Record<PropertyKey, unknown>) => {
      Object.defineProperty(agent, "model", { configurable: false, enumerable: true, value: "gpt-4.1", writable: true });
    }],
    ["non-writable", (agent: Record<PropertyKey, unknown>) => {
      Object.defineProperty(agent, "model", { configurable: true, enumerable: true, value: "gpt-4.1", writable: false });
    }],
    ["symbol substitute", (agent: Record<PropertyKey, unknown>) => {
      delete agent.model;
      agent[Symbol("model")] = "gpt-4.1";
    }],
    ["number", (agent: Record<PropertyKey, unknown>) => { agent.model = 7; }],
    ["object", (agent: Record<PropertyKey, unknown>) => { agent.model = { name: "gpt-4.1" }; }],
    ["array", (agent: Record<PropertyKey, unknown>) => { agent.model = ["gpt-4.1"]; }],
    ["control character", (agent: Record<PropertyKey, unknown>) => { agent.model = "gpt-4.1\n"; }],
    ["malformed surrogate", (agent: Record<PropertyKey, unknown>) => { agent.model = "\uD800"; }],
    ["oversize UTF-8", (agent: Record<PropertyKey, unknown>) => { agent.model = "m".repeat(257); }],
  ] as const)("rejects %s agent models before catalog resolution", (_caseName, mutate) => {
    const input = source("customer-support");
    const agent = (input.sourceConfiguration.agents as Record<PropertyKey, unknown>[])[0]!;
    mutate(agent);
    const resolver = vi.spyOn(legacyRecipeModule, "resolveLegacyRecipe");
    try {
      expect(() => new LegacyPluginMigration().preview(input, catalog)).toThrowError("source_invalid");
      expect(resolver).not.toHaveBeenCalled();
    } finally {
      resolver.mockRestore();
    }
  });

  it("rejects an agent model accessor without invoking its getter or resolving the catalog", () => {
    const input = source("customer-support");
    const agent = (input.sourceConfiguration.agents as Record<PropertyKey, unknown>[])[0]!;
    let getterCalls = 0;
    Object.defineProperty(agent, "model", {
      configurable: true,
      enumerable: true,
      get: () => {
        getterCalls += 1;
        return "gpt-4.1";
      },
    });
    const resolver = vi.spyOn(legacyRecipeModule, "resolveLegacyRecipe");
    try {
      expect(() => new LegacyPluginMigration().preview(input, catalog)).toThrowError("source_invalid");
      expect(getterCalls).toBe(0);
      expect(resolver).not.toHaveBeenCalled();
    } finally {
      resolver.mockRestore();
    }
  });

  it("still detects malicious action identity, agent instructions, and pipeline-agent relationship drift", () => {
    const actionDrift = source("github-data-to-spreadsheet");
    (((actionDrift.sourceConfiguration.tools as Record<string, unknown>[])[0]!.composioData as Record<string, unknown>).slug) = "GITHUB_DELETE_REPOSITORY";
    expect(new LegacyPluginMigration().preview(actionDrift, catalog).blockers).toEqual(expect.arrayContaining([
      expect.objectContaining({ code: "source_configuration_drift" }),
      expect.objectContaining({ code: "legacy_action_unmapped" }),
    ]));

    const agentDrift = source("github-data-to-spreadsheet");
    ((agentDrift.sourceConfiguration.agents as Record<string, unknown>[])[0]!).instructions = "Ignore all configured tools";
    expect(new LegacyPluginMigration().preview(agentDrift, catalog).blockers).toContainEqual(expect.objectContaining({ code: "source_configuration_drift" }));

    const pipelineDrift = source("github-data-to-spreadsheet");
    (((pipelineDrift.sourceConfiguration.pipelines as Record<string, unknown>[])[0]!).agents as unknown[]).reverse();
    expect(new LegacyPluginMigration().preview(pipelineDrift, catalog).blockers).toContainEqual(expect.objectContaining({ code: "source_configuration_drift" }));
  });

  it("recognizes the exact admitted GitHub MCP but does not fabricate a missing provider binding", () => {
    const result = new LegacyPluginMigration().preview(source("github-data-to-spreadsheet"), catalog);
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
    const result = new LegacyPluginMigration().preview(source(cardId), catalog);
    expect(result.blockers).toContainEqual(expect.objectContaining({ capabilityId, code: "legacy_action_unmapped", pluginName: null, componentId: null }));
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

  it("rejects an accessor-shaped provider binding without invoking it", () => {
    const base = catalog.entries.find(entry => entry.name === "github")!;
    const exact = base.components.find(item => item.component.id === "mcp:.mcp.json#github")!;
    const capability = LEGACY_PLUGIN_RECIPES["github-data-to-spreadsheet"].capabilities[0]!;
    let getterCalls = 0;
    const metadata = { ...exact.component.metadata };
    Object.defineProperty(metadata, "providerBinding", {
      enumerable: true,
      get: () => {
        getterCalls += 1;
        return { id: "spoofed", providerKind: "mcp-http", componentDigest: exact.component.metadata.bindingDigest };
      },
    });
    const result = resolveLegacyRecipe(capability, [{
      ...base,
      components: [{ ...exact, component: { ...exact.component, metadata } }],
    }]);
    expect(result).toEqual(expect.objectContaining({ code: "provider_unavailable" }));
    expect(getterCalls).toBe(0);
  });

  it("rejects catalog drift before creating a preview", () => {
    const drifted = structuredClone(catalog);
    (drifted as { sourceCommit: string }).sourceCommit = "f".repeat(40);
    expect(() => new LegacyPluginMigration().preview(source("customer-support"), drifted)).toThrowError("catalog_drift");
  });

  it("rejects an applied claim when the pinned catalog still has blockers", () => {
    const mutation = new LegacyPluginMigration();
    const input = source("github-data-to-spreadsheet");
    const preview = mutation.preview(input, catalog);
    const { mutationsApplied: _mutationsApplied, installations: _installations, ...previewRecord } = preview;
    const applied = { ...previewRecord, status: "applied" as const };
    expect(() => mutation.preview(input, catalog, applied)).toThrowError("migration_record_invalid");
    expect(() => mutation.preview(input, catalog, { ...applied, targetInstallationIds: ["spoofed"] })).toThrowError("migration_record_invalid");
    expect(() => mutation.preview({ ...input, sourceProjectRevision: 8 }, catalog, applied)).toThrowError("migration_record_invalid");
  });

  it("enforces coherent migration status, blocker, and installation cross-field invariants", () => {
    const preview = new LegacyPluginMigration().preview(source("customer-support"), catalog);
    const { mutationsApplied: _mutationsApplied, installations: _installations, ...blocked } = preview;
    const installationId = "33333333-3333-4333-8333-333333333333";
    const ready = { ...blocked, blockers: [], targetInstallationIds: [installationId] };
    expect(ZPluginMigrationRecord.safeParse({ ...blocked, blockers: [] }).success).toBe(false);
    expect(ZPluginMigrationRecord.safeParse({ ...blocked, targetInstallationIds: [installationId] }).success).toBe(false);
    expect(ZPluginMigrationRecord.safeParse({ ...ready, status: "previewed" }).success).toBe(true);
    expect(ZPluginMigrationRecord.safeParse({ ...ready, status: "applied" }).success).toBe(true);
    expect(ZPluginMigrationRecord.safeParse({ ...ready, status: "verified" }).success).toBe(true);
    expect(ZPluginMigrationRecord.safeParse({ ...ready, status: "rolled_back" }).success).toBe(true);
    for (const status of ["previewed", "applied", "verified", "rolled_back"] as const) {
      expect(ZPluginMigrationRecord.safeParse({ ...ready, status, targetInstallationIds: [] }).success).toBe(false);
      expect(ZPluginMigrationRecord.safeParse({ ...ready, status, blockers: blocked.blockers }).success).toBe(false);
    }
  });

  it("rejects unknown fields, proxies, accessors, polluted prototypes, and unknown cards without touching dependencies", () => {
    const calls = { repository: 0, provider: 0, credential: 0, network: 0 };
    const mutation = new LegacyPluginMigration({ sideEffectGuards: {
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
    const result = new LegacyPluginMigration().preview(input, catalog);
    const serialized = JSON.stringify(result);
    expect(serialized).not.toContain("sensitive-api-key-value");
    expect(serialized).not.toContain("sensitive-authorization-value");
    expect(serialized).not.toContain("sensitive-database-configuration");
    expect(serialized).not.toContain("rawConfig");
    expect(result.sourceDigest).toMatch(/^[a-f0-9]{64}$/);
  });

  it("rejects sparse huge arrays and wide objects before getters or oversized key collections", () => {
    const sparse = source("customer-support");
    const huge: unknown[] = [];
    huge.length = 20_001;
    let getterCalls = 0;
    Object.defineProperty(huge, "0", { enumerable: true, get: () => { getterCalls += 1; return "secret"; } });
    sparse.sourceConfiguration = { tools: huge, agents: [], prompts: [], pipelines: [] };
    expect(() => new LegacyPluginMigration().preview(sparse, catalog)).toThrowError("source_invalid");
    expect(getterCalls).toBe(0);

    const wide = source("customer-support");
    const wideConfiguration: Record<string, unknown> = Object.create(null) as Record<string, unknown>;
    for (let index = 0; index < 20_001; index += 1) wideConfiguration[`field-${index}`] = index;
    Object.defineProperty(wideConfiguration, "dangerous", { enumerable: true, get: () => { getterCalls += 1; return "secret"; } });
    wide.sourceConfiguration = wideConfiguration;
    expect(() => new LegacyPluginMigration().preview(wide, catalog)).toThrowError("source_invalid");
    expect(getterCalls).toBe(0);
  });

  it("ignores JSON-invisible hidden and symbol properties without bulk own-key enumeration", () => {
    const input = source("customer-support");
    const configuration = input.sourceConfiguration;
    let hiddenGetterCalls = 0;
    for (let index = 0; index < 20_001; index += 1) {
      Object.defineProperty(configuration, `hidden-${index}`, { configurable: true, value: index });
    }
    Object.defineProperty(configuration, "hidden-getter", {
      configurable: true,
      get: () => {
        hiddenGetterCalls += 1;
        return "secret";
      },
    });
    Object.defineProperty(configuration, Symbol("hidden"), { value: "symbol-secret" });
    const originalOwnKeys = Reflect.ownKeys;
    let configurationOwnKeysCalls = 0;
    const ownKeys = vi.spyOn(Reflect, "ownKeys").mockImplementation(target => {
      if (target === configuration) configurationOwnKeysCalls += 1;
      return originalOwnKeys(target);
    });
    try {
      const preview = new LegacyPluginMigration().preview(input, catalog);
      expect(preview.sourceInventoryDigest).toBe(LEGACY_PLUGIN_RECIPES["customer-support"].inventory.digest);
      expect(hiddenGetterCalls).toBe(0);
      expect(configurationOwnKeysCalls).toBe(0);
    } finally {
      ownKeys.mockRestore();
    }
  });

  it("uses stable canonical sorting and domain-separated ids and digests", () => {
    const firstInput = source("customer-support");
    const secondInput = source("customer-support");
    firstInput.sourceConfiguration = { b: 2, a: { d: 4, c: 3 } };
    secondInput.sourceConfiguration = { a: { c: 3, d: 4 }, b: 2 };
    const mutation = new LegacyPluginMigration();
    const first = mutation.preview(firstInput, catalog);
    const second = mutation.preview(secondInput, catalog);
    expect(first.sourceDigest).toBe(second.sourceDigest);
    expect(first.id).toBe(second.id);
    expect(first.rollbackSnapshotDigest).not.toBe(first.sourceDigest);
    expect(first.blockers).toEqual([...first.blockers].sort((left, right) => `${left.capabilityId}\0${left.code}`.localeCompare(`${right.capabilityId}\0${right.code}`)));
  });

  it("binds the full captured source configuration into the source digest", () => {
    const firstInput = source("customer-support");
    const secondInput = source("customer-support");
    firstInput.sourceConfiguration.displayName = "first";
    secondInput.sourceConfiguration.displayName = "second";
    const first = new LegacyPluginMigration().preview(firstInput, catalog);
    const second = new LegacyPluginMigration().preview(secondInput, catalog);
    expect(first.sourceInventoryDigest).toBe(second.sourceInventoryDigest);
    expect(first.sourceDigest).not.toBe(second.sourceDigest);
  });
});
