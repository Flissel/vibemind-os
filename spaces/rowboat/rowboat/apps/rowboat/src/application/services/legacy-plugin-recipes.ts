import { createHash } from "node:crypto";
import { isProxy } from "node:util/types";
import type { PluginCatalogEntry, PluginMigrationBlocker, ProviderBinding, ProviderKind } from "@rowboat/openai-plugin-runtime";
import customerSupport from "@/app/lib/prebuilt-cards/customer-support.json";
import eisenhowerEmailOrganizer from "@/app/lib/prebuilt-cards/eisenhower-email-organizer.json";
import githubDataToSpreadsheet from "@/app/lib/prebuilt-cards/github-data-to-spreadsheet.json";
import githubIssueToSlack from "@/app/lib/prebuilt-cards/github-issue-to-slack.json";
import githubPrToSlack from "@/app/lib/prebuilt-cards/github-pr-to-slack.json";
import interviewScheduler from "@/app/lib/prebuilt-cards/interview-scheduler.json";
import meetingPrepAssistant from "@/app/lib/prebuilt-cards/meeting-prep-assistant.json";
import redditOnSlack from "@/app/lib/prebuilt-cards/reddit-on-slack.json";
import tweetAssistant from "@/app/lib/prebuilt-cards/tweet-assistant.json";
import twitterSentiment from "@/app/lib/prebuilt-cards/twitter-sentiment.json";

export const LEGACY_CARD_IDS = Object.freeze([
  "customer-support", "eisenhower-email-organizer", "github-data-to-spreadsheet", "github-issue-to-slack",
  "github-pr-to-slack", "interview-scheduler", "meeting-prep-assistant", "reddit-on-slack", "tweet-assistant",
  "twitter-sentiment",
] as const);
export type LegacyCardId = (typeof LEGACY_CARD_IDS)[number];

export interface LegacyInventoryItem { readonly ordinal: number; readonly identity: string; readonly descriptorDigest: string; }
export interface LegacyExecutableInventory {
  readonly actions: readonly LegacyInventoryItem[];
  readonly agents: readonly LegacyInventoryItem[];
  readonly prompts: readonly LegacyInventoryItem[];
  readonly pipelines: readonly LegacyInventoryItem[];
  readonly entrypointDigest: string;
  readonly counts: Readonly<{ readonly actions: number; readonly agents: number; readonly prompts: number; readonly pipelines: number }>;
  readonly digest: string;
}
export interface LegacyCatalogTarget { readonly pluginName: string; readonly componentId: string; readonly providerKind: ProviderKind; }
export interface LegacyCapabilityRecipe { readonly capabilityId: string; readonly legacyAction: LegacyInventoryItem; readonly target: LegacyCatalogTarget | null; }
export interface LegacyPluginRecipe {
  readonly recipeId: string; readonly recipeVersion: 1; readonly cardId: LegacyCardId;
  readonly inventory: LegacyExecutableInventory; readonly capabilities: readonly LegacyCapabilityRecipe[]; readonly recipeDigest: string;
}
export interface ResolvedLegacyCapability {
  readonly capability: LegacyCapabilityRecipe; readonly entry: PluginCatalogEntry;
  readonly component: PluginCatalogEntry["components"][number]["component"];
  readonly providerBinding: ProviderBinding;
}

function canonical(value: unknown): string {
  if (value === null || typeof value === "boolean" || typeof value === "string" || typeof value === "number") return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  const selected = value as Readonly<Record<string, unknown>>;
  return `{${Object.keys(selected).sort().map(key => `${JSON.stringify(key)}:${canonical(selected[key])}`).join(",")}}`;
}
function digest(domain: string, value: unknown): string { return createHash("sha256").update(domain).update("\0").update(canonical(value)).digest("hex"); }
function record(value: unknown): Readonly<Record<string, unknown>> {
  if (value === null || typeof value !== "object" || Array.isArray(value)) throw new Error("legacy_inventory_invalid");
  return value as Readonly<Record<string, unknown>>;
}
function items(configuration: Readonly<Record<string, unknown>>, key: string): readonly unknown[] {
  const value = configuration[key];
  if (value === undefined) return Object.freeze([]);
  if (!Array.isArray(value) || value.length > 256) throw new Error("legacy_inventory_invalid");
  return value;
}
function text(value: unknown): string {
  if (typeof value !== "string" || value.length === 0 || value.length > 256) throw new Error("legacy_inventory_invalid");
  return value;
}
function inventoryItem(kind: "action" | "agent" | "prompt" | "pipeline", value: unknown, ordinal: number): LegacyInventoryItem {
  const selected = record(value);
  const descriptorDigest = digest(`rowboat:legacy-migration:${kind}-descriptor:v1`, selected);
  let identity: string;
  if (kind === "action") {
    const name = text(selected.name);
    const composio = selected.composioData === undefined ? undefined : record(selected.composioData);
    identity = selected.mockTool === true ? `mock:${name}` : typeof composio?.slug === "string" ? `composio:${composio.slug}` : `unmapped:${descriptorDigest.slice(0, 24)}`;
  } else if (kind === "agent") identity = `agent:${text(selected.type)}:${text(selected.name)}`;
  else if (kind === "prompt") identity = `prompt:${text(selected.type)}:${text(selected.name)}`;
  else identity = `pipeline:${text(selected.name)}`;
  return Object.freeze({ ordinal, identity, descriptorDigest });
}

export function buildLegacyExecutableInventory(configurationInput: unknown): LegacyExecutableInventory {
  const configuration = record(configurationInput);
  const actions = Object.freeze(items(configuration, "tools").map((value, index) => inventoryItem("action", value, index)));
  const agents = Object.freeze(items(configuration, "agents").map((value, index) => inventoryItem("agent", value, index)));
  const prompts = Object.freeze(items(configuration, "prompts").map((value, index) => inventoryItem("prompt", value, index)));
  const pipelines = Object.freeze(items(configuration, "pipelines").map((value, index) => inventoryItem("pipeline", value, index)));
  const entrypointDigest = digest("rowboat:legacy-migration:entrypoint:v1", configuration.startAgent ?? null);
  const counts = Object.freeze({ actions: actions.length, agents: agents.length, prompts: prompts.length, pipelines: pipelines.length });
  return Object.freeze({ actions, agents, prompts, pipelines, entrypointDigest, counts, digest: digest("rowboat:legacy-migration:executable-inventory:v1", { actions, agents, prompts, pipelines, entrypointDigest }) });
}

const target = (pluginName: string, componentId: string, providerKind: ProviderKind): LegacyCatalogTarget => Object.freeze({ pluginName, componentId, providerKind });
const APP = "openai-connector-bridge" as const;
const GITHUB = target("github", "mcp:.mcp.json#github", "mcp-http");
const GMAIL = target("gmail", "app:.app.json#gmail", APP);
const SHEETS = target("google-drive", "app:.app.json#google-drive", APP);
const CALENDAR = target("google-calendar", "app:.app.json#google-calendar", APP);
const SLACK = target("slack", "app:.app.json#slack", APP);
type Mapping = readonly [capabilityId: string, expectedIdentity: string, target: LegacyCatalogTarget | null];
const canonicalCards: Readonly<Record<LegacyCardId, unknown>> = Object.freeze({
  "customer-support": customerSupport, "eisenhower-email-organizer": eisenhowerEmailOrganizer,
  "github-data-to-spreadsheet": githubDataToSpreadsheet, "github-issue-to-slack": githubIssueToSlack,
  "github-pr-to-slack": githubPrToSlack, "interview-scheduler": interviewScheduler,
  "meeting-prep-assistant": meetingPrepAssistant, "reddit-on-slack": redditOnSlack,
  "tweet-assistant": tweetAssistant, "twitter-sentiment": twitterSentiment,
});
const mappings: Readonly<Record<LegacyCardId, readonly Mapping[]>> = Object.freeze({
  "customer-support": [["mock-delivery-status", "mock:Mock Delivery Status", null]],
  "eisenhower-email-organizer": [["gmail-modify-labels", "composio:GMAIL_ADD_LABEL_TO_EMAIL", GMAIL]],
  "github-data-to-spreadsheet": [
    ["github-page-views", "composio:GITHUB_GET_PAGE_VIEWS", GITHUB],
    ["google-sheets-append", "composio:GOOGLESHEETS_SPREADSHEETS_VALUES_APPEND", SHEETS],
    ["github-repository-clones", "composio:GITHUB_GET_REPOSITORY_CLONES", GITHUB],
    ["slack-send-message", "composio:SLACK_SENDS_A_MESSAGE_TO_A_SLACK_CHANNEL", SLACK],
  ],
  "github-issue-to-slack": [["slack-send-message", "composio:SLACK_SEND_MESSAGE", SLACK]],
  "github-pr-to-slack": [["slack-send-message", "composio:SLACK_SEND_MESSAGE", SLACK]],
  "interview-scheduler": [
    ["google-sheets-batch-get", "composio:GOOGLESHEETS_BATCH_GET", SHEETS],
    ["google-calendar-create-event", "composio:GOOGLECALENDAR_CREATE_EVENT", CALENDAR],
    ["google-sheets-batch-update", "composio:GOOGLESHEETS_BATCH_UPDATE", SHEETS],
  ],
  "meeting-prep-assistant": [["web-search", "composio:COMPOSIO_SEARCH_EXA_ANSWER", null], ["gmail-send-email", "composio:GMAIL_SEND_EMAIL", GMAIL]],
  "reddit-on-slack": [["reddit-search", "composio:REDDIT_SEARCH_ACROSS_SUBREDDITS", null], ["slack-send-message", "composio:SLACK_SEND_MESSAGE", SLACK]],
  "tweet-assistant": [
    ["x-create-post", "composio:TWITTER_CREATION_OF_A_POST", null],
    ["web-search-exa", "composio:COMPOSIO_SEARCH_EXA_ANSWER", null],
    ["web-search-duckduckgo", "composio:COMPOSIO_SEARCH_DUCK_DUCK_GO_SEARCH", null],
  ],
  "twitter-sentiment": [["mock-twitter-search", "mock:Search full archive of tweets", null]],
});

export function computeLegacyRecipeDigest(recipe: Omit<LegacyPluginRecipe, "recipeDigest"> | LegacyPluginRecipe): string {
  return digest("rowboat:legacy-migration:recipe:v1", { cardId: recipe.cardId, recipeId: recipe.recipeId, recipeVersion: recipe.recipeVersion, inventory: recipe.inventory, capabilities: recipe.capabilities });
}
function buildRecipe(cardId: LegacyCardId): LegacyPluginRecipe {
  const inventory = buildLegacyExecutableInventory(canonicalCards[cardId]);
  const selectedMappings = mappings[cardId];
  if (selectedMappings.length !== inventory.actions.length) throw new Error("legacy_recipe_invalid");
  const capabilities = Object.freeze(selectedMappings.map(([capabilityId, expectedIdentity, selectedTarget], index) => {
    const legacyAction = inventory.actions[index]!;
    if (legacyAction.identity !== expectedIdentity) throw new Error("legacy_recipe_invalid");
    return Object.freeze({ capabilityId, legacyAction, target: selectedTarget });
  }));
  const base = Object.freeze({ recipeId: `legacy-card:${cardId}:v1`, recipeVersion: 1 as const, cardId, inventory, capabilities });
  return Object.freeze({ ...base, recipeDigest: computeLegacyRecipeDigest(base) });
}
export const LEGACY_PLUGIN_RECIPES: Readonly<Record<LegacyCardId, LegacyPluginRecipe>> = Object.freeze(Object.fromEntries(LEGACY_CARD_IDS.map(cardId => [cardId, buildRecipe(cardId)])) as unknown as Record<LegacyCardId, LegacyPluginRecipe>);

function actionId(item: LegacyInventoryItem): string { return `action:${item.ordinal}:${item.descriptorDigest.slice(0, 24)}`; }
function blocker(capability: LegacyCapabilityRecipe, code: PluginMigrationBlocker["code"]): PluginMigrationBlocker {
  return Object.freeze({ capabilityId: capability.capabilityId, legacyActionId: actionId(capability.legacyAction), code, pluginName: capability.target?.pluginName ?? null, componentId: capability.target?.componentId ?? null });
}
export function unmappedActionBlocker(item: LegacyInventoryItem, capabilityId = `unmapped-action-${item.ordinal}`): PluginMigrationBlocker {
  return Object.freeze({ capabilityId, legacyActionId: actionId(item), code: "legacy_action_unmapped", pluginName: null, componentId: null });
}
export function sourceDriftBlocker(item?: LegacyInventoryItem): PluginMigrationBlocker {
  const selected = item ?? Object.freeze({ ordinal: 0, identity: "source", descriptorDigest: "0".repeat(64) });
  return Object.freeze({ capabilityId: "source-configuration", legacyActionId: actionId(selected), code: "source_configuration_drift", pluginName: null, componentId: null });
}

export function resolveLegacyRecipe(capability: LegacyCapabilityRecipe, entries: readonly PluginCatalogEntry[]): ResolvedLegacyCapability | PluginMigrationBlocker {
  if (capability.target === null) return blocker(capability, "legacy_action_unmapped");
  if (!Array.isArray(entries) || isProxy(entries) || entries.length > 512) throw new Error("catalog_entries_invalid");
  const safeEntries: readonly PluginCatalogEntry[] = entries;
  const pluginMatches = safeEntries.filter(entry => entry.name === capability.target!.pluginName && entry.pluginName === capability.target!.pluginName);
  if (pluginMatches.length === 0) return blocker(capability, "provider_unavailable");
  if (pluginMatches.length !== 1) return blocker(capability, "component_ambiguous");
  const entry = pluginMatches[0]!;
  if (entry.admission.status === "review_required") return blocker(capability, entry.admission.reason === "license_review_required" ? "license_review_required" : "admission_review_required");
  if (entry.admission.status === "rejected") return blocker(capability, entry.admission.reason === "license_rejected" ? "license_rejected" : "admission_rejected");
  const componentMatches = entry.components.filter(item => item.component.id === capability.target!.componentId);
  if (componentMatches.length === 0) return blocker(capability, "component_missing");
  if (componentMatches.length !== 1) return blocker(capability, "component_ambiguous");
  const selected = componentMatches[0]!;
  if (selected.admission.status === "review_required") return blocker(capability, "admission_review_required");
  if (selected.admission.status === "rejected") return blocker(capability, "admission_rejected");
  if (selected.component.status !== "available") return blocker(capability, selected.component.reason === "provider_unavailable" ? "provider_unavailable" : "component_unavailable");
  const metadataDescriptor = Object.getOwnPropertyDescriptor(selected.component, "metadata");
  if (metadataDescriptor === undefined || !("value" in metadataDescriptor) || !metadataDescriptor.enumerable) return blocker(capability, "provider_unavailable");
  const metadata = metadataDescriptor.value as unknown;
  if (metadata === null || typeof metadata !== "object" || Array.isArray(metadata) || isProxy(metadata)) return blocker(capability, "provider_unavailable");
  const bindingDigestDescriptor = Object.getOwnPropertyDescriptor(metadata, "bindingDigest");
  const providerBindingDescriptor = Object.getOwnPropertyDescriptor(metadata, "providerBinding");
  if (bindingDigestDescriptor === undefined || !("value" in bindingDigestDescriptor) || !bindingDigestDescriptor.enumerable
    || providerBindingDescriptor === undefined || !("value" in providerBindingDescriptor) || !providerBindingDescriptor.enumerable) return blocker(capability, "provider_unavailable");
  const rawProviderBinding = providerBindingDescriptor.value as unknown;
  if (rawProviderBinding === null || typeof rawProviderBinding !== "object" || Array.isArray(rawProviderBinding) || isProxy(rawProviderBinding)) return blocker(capability, "provider_unavailable");
  const allowed = new Set(["componentDigest", "id", "pairedComponentDigests", "providerKind", "temporaryAdapter"]);
  const bindingKeys = Reflect.ownKeys(rawProviderBinding);
  if ((Object.getPrototypeOf(rawProviderBinding) !== Object.prototype && Object.getPrototypeOf(rawProviderBinding) !== null)
    || bindingKeys.some(key => typeof key !== "string" || !allowed.has(key))) return blocker(capability, "provider_unavailable");
  const values: Record<string, unknown> = Object.create(null) as Record<string, unknown>;
  for (const key of bindingKeys as readonly string[]) {
    const descriptor = Object.getOwnPropertyDescriptor(rawProviderBinding, key);
    if (descriptor === undefined || !("value" in descriptor) || !descriptor.enumerable) return blocker(capability, "provider_unavailable");
    values[key] = descriptor.value;
  }
  if (typeof values.id !== "string" || values.id.length === 0 || values.providerKind !== capability.target.providerKind || values.componentDigest !== bindingDigestDescriptor.value) return blocker(capability, "provider_unavailable");
  let pairedComponentDigests: readonly [string, string] | undefined;
  if (values.pairedComponentDigests !== undefined) {
    const paired = values.pairedComponentDigests;
    if (!Array.isArray(paired) || isProxy(paired) || Object.getPrototypeOf(paired) !== Array.prototype || paired.length !== 2) return blocker(capability, "provider_unavailable");
    const first = Object.getOwnPropertyDescriptor(paired, "0");
    const second = Object.getOwnPropertyDescriptor(paired, "1");
    if (first === undefined || !("value" in first) || !first.enumerable || typeof first.value !== "string"
      || second === undefined || !("value" in second) || !second.enumerable || typeof second.value !== "string"
      || Reflect.ownKeys(paired).length !== 3) return blocker(capability, "provider_unavailable");
    pairedComponentDigests = Object.freeze([first.value, second.value]);
  }
  if (values.temporaryAdapter !== undefined && values.temporaryAdapter !== true) return blocker(capability, "provider_unavailable");
  const providerBinding: ProviderBinding = Object.freeze({
    id: values.id,
    providerKind: capability.target.providerKind,
    componentDigest: values.componentDigest as string,
    ...(pairedComponentDigests === undefined ? {} : { pairedComponentDigests }),
    ...(values.temporaryAdapter === true ? { temporaryAdapter: true as const } : {}),
  });
  return Object.freeze({ capability, entry, component: selected.component, providerBinding });
}
