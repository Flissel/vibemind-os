import { isProxy } from "node:util/types";
import type {
  PluginCatalogEntry,
  PluginMigrationBlocker,
  ProviderKind,
} from "@rowboat/openai-plugin-runtime";

export const LEGACY_CARD_IDS = Object.freeze([
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
] as const);

export type LegacyCardId = (typeof LEGACY_CARD_IDS)[number];

export interface LegacyCapabilityRecipe {
  readonly capabilityId: string;
  readonly legacyToolSlug: string;
  readonly pluginName: string;
  readonly componentId: string;
  readonly providerKind: ProviderKind;
}

export interface LegacyPluginRecipe {
  readonly recipeId: string;
  readonly cardId: LegacyCardId;
  readonly capabilities: readonly LegacyCapabilityRecipe[];
}

export interface ResolvedLegacyCapability {
  readonly capability: LegacyCapabilityRecipe;
  readonly entry: PluginCatalogEntry;
  readonly component: PluginCatalogEntry["components"][number]["component"];
  readonly providerBindingId: string;
  readonly providerKind: ProviderKind;
}

function capability(
  capabilityId: string,
  legacyToolSlug: string,
  pluginName: string,
  componentId: string,
  providerKind: ProviderKind,
): LegacyCapabilityRecipe {
  return Object.freeze({ capabilityId, legacyToolSlug, pluginName, componentId, providerKind });
}

function recipe(cardId: LegacyCardId, capabilities: readonly LegacyCapabilityRecipe[]): LegacyPluginRecipe {
  return Object.freeze({ recipeId: `legacy-card:${cardId}:v1`, cardId, capabilities: Object.freeze([...capabilities]) });
}

const APP = "openai-connector-bridge" as const;

export const LEGACY_PLUGIN_RECIPES: Readonly<Record<LegacyCardId, LegacyPluginRecipe>> = Object.freeze({
  "customer-support": recipe("customer-support", [
    capability("mock-delivery-status", "MOCK:Mock Delivery Status", "mock-tools", "native:mock-delivery-status", "rowboat-native"),
  ]),
  "eisenhower-email-organizer": recipe("eisenhower-email-organizer", [
    capability("gmail-modify-labels", "GMAIL_ADD_LABEL_TO_EMAIL", "gmail", "app:.app.json#gmail", APP),
  ]),
  "github-data-to-spreadsheet": recipe("github-data-to-spreadsheet", [
    capability("github-page-views", "GITHUB_GET_PAGE_VIEWS", "github", "mcp:.mcp.json#github", "mcp-http"),
    capability("google-sheets-append", "GOOGLESHEETS_SPREADSHEETS_VALUES_APPEND", "google-drive", "app:.app.json#google-drive", APP),
    capability("github-repository-clones", "GITHUB_GET_REPOSITORY_CLONES", "github", "mcp:.mcp.json#github", "mcp-http"),
    capability("slack-send-message", "SLACK_SENDS_A_MESSAGE_TO_A_SLACK_CHANNEL", "slack", "app:.app.json#slack", APP),
  ]),
  "github-issue-to-slack": recipe("github-issue-to-slack", [
    capability("slack-send-message", "SLACK_SEND_MESSAGE", "slack", "app:.app.json#slack", APP),
  ]),
  "github-pr-to-slack": recipe("github-pr-to-slack", [
    capability("slack-send-message", "SLACK_SEND_MESSAGE", "slack", "app:.app.json#slack", APP),
  ]),
  "interview-scheduler": recipe("interview-scheduler", [
    capability("google-sheets-batch-get", "GOOGLESHEETS_BATCH_GET", "google-drive", "app:.app.json#google-drive", APP),
    capability("google-calendar-create-event", "GOOGLECALENDAR_CREATE_EVENT", "google-calendar", "app:.app.json#google-calendar", APP),
    capability("google-sheets-batch-update", "GOOGLESHEETS_BATCH_UPDATE", "google-drive", "app:.app.json#google-drive", APP),
  ]),
  "meeting-prep-assistant": recipe("meeting-prep-assistant", [
    capability("web-search", "COMPOSIO_SEARCH_EXA_ANSWER", "search", "native:admitted-search", "rowboat-native"),
    capability("gmail-send-email", "GMAIL_SEND_EMAIL", "gmail", "app:.app.json#gmail", APP),
  ]),
  "reddit-on-slack": recipe("reddit-on-slack", [
    capability("reddit-search", "REDDIT_SEARCH_ACROSS_SUBREDDITS", "reddit", "app:.app.json#reddit", APP),
    capability("slack-send-message", "SLACK_SEND_MESSAGE", "slack", "app:.app.json#slack", APP),
  ]),
  "tweet-assistant": recipe("tweet-assistant", [
    capability("x-create-post", "TWITTER_CREATION_OF_A_POST", "x", "app:.app.json#x", APP),
    capability("web-search-exa", "COMPOSIO_SEARCH_EXA_ANSWER", "search", "native:admitted-search", "rowboat-native"),
    capability("web-search-duckduckgo", "COMPOSIO_SEARCH_DUCK_DUCK_GO_SEARCH", "search", "native:admitted-search", "rowboat-native"),
  ]),
  "twitter-sentiment": recipe("twitter-sentiment", [
    capability("mock-twitter-search", "MOCK:Search full archive of tweets", "mock-tools", "native:mock-twitter-search", "rowboat-native"),
  ]),
});

function blocker(capabilityRecipe: LegacyCapabilityRecipe, code: PluginMigrationBlocker["code"]): PluginMigrationBlocker {
  return Object.freeze({
    capabilityId: capabilityRecipe.capabilityId,
    code,
    pluginName: capabilityRecipe.pluginName,
    componentId: capabilityRecipe.componentId,
  });
}

function ensureSafeEntries(entries: readonly PluginCatalogEntry[]): void {
  if (!Array.isArray(entries) || isProxy(entries) || entries.length > 512) throw new Error("catalog_entries_invalid");
}

export function resolveLegacyRecipe(
  capabilityRecipe: LegacyCapabilityRecipe,
  entries: readonly PluginCatalogEntry[],
): ResolvedLegacyCapability | PluginMigrationBlocker {
  ensureSafeEntries(entries);
  const pluginMatches = entries.filter(entry => entry.name === capabilityRecipe.pluginName && entry.pluginName === capabilityRecipe.pluginName);
  if (pluginMatches.length === 0) return blocker(capabilityRecipe, "provider_unavailable");
  if (pluginMatches.length !== 1) return blocker(capabilityRecipe, "component_ambiguous");
  const entry = pluginMatches[0]!;
  if (entry.admission.status === "review_required") {
    return blocker(capabilityRecipe, entry.admission.reason === "license_review_required" ? "license_review_required" : "admission_review_required");
  }
  if (entry.admission.status === "rejected") {
    return blocker(capabilityRecipe, entry.admission.reason === "license_rejected" ? "license_rejected" : "admission_rejected");
  }
  const componentMatches = entry.components.filter(item => item.component.id === capabilityRecipe.componentId);
  if (componentMatches.length === 0) return blocker(capabilityRecipe, "component_missing");
  if (componentMatches.length !== 1) return blocker(capabilityRecipe, "component_ambiguous");
  const selected = componentMatches[0]!;
  if (selected.admission.status === "review_required") return blocker(capabilityRecipe, "admission_review_required");
  if (selected.admission.status === "rejected") return blocker(capabilityRecipe, "admission_rejected");
  if (selected.component.status !== "available") {
    return blocker(capabilityRecipe, selected.component.reason === "provider_unavailable" ? "provider_unavailable" : "component_unavailable");
  }
  const providerBinding = selected.component.metadata.providerBinding;
  if (providerBinding === null || typeof providerBinding !== "object" || Array.isArray(providerBinding) || isProxy(providerBinding)) {
    return blocker(capabilityRecipe, "provider_unavailable");
  }
  const descriptors = Object.getOwnPropertyDescriptors(providerBinding);
  const allowed = new Set(["componentDigest", "id", "pairedComponentDigests", "providerKind", "temporaryAdapter"]);
  if (Object.getPrototypeOf(providerBinding) !== Object.prototype || Object.getOwnPropertySymbols(providerBinding).length !== 0 || Object.keys(descriptors).some(key => !allowed.has(key))) {
    return blocker(capabilityRecipe, "provider_unavailable");
  }
  for (const descriptor of Object.values(descriptors)) {
    if (!("value" in descriptor) || !descriptor.enumerable) return blocker(capabilityRecipe, "provider_unavailable");
  }
  const values = providerBinding as Readonly<Record<string, unknown>>;
  if (
    typeof values.id !== "string"
    || values.id.length === 0
    || values.providerKind !== capabilityRecipe.providerKind
    || values.componentDigest !== selected.component.metadata.bindingDigest
  ) return blocker(capabilityRecipe, "provider_unavailable");
  return Object.freeze({
    capability: capabilityRecipe,
    entry,
    component: selected.component,
    providerBindingId: values.id,
    providerKind: capabilityRecipe.providerKind,
  });
}
