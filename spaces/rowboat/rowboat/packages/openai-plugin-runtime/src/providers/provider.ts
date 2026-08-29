import type { PluginReasonCode } from "../domain/plugin.js";

export type ProviderKind =
  | "mcp-http"
  | "mcp-process"
  | "rowboat-native"
  | "legacy-composio-adapter"
  | "openai-connector-bridge";

export interface ProviderRequest {
  readonly projectId: string;
  readonly pluginName: string;
  readonly componentName: string;
  readonly operationName?: string;
  readonly capability: "read" | "write";
  readonly arguments: Readonly<Record<string, unknown>>;
}

export interface ProviderContext {
  readonly requestId: string;
  readonly signal?: AbortSignal;
}

export type ProviderResult =
  | { readonly status: "success"; readonly output: unknown }
  | { readonly status: "failed"; readonly reason: string };

export interface ProviderDescriptor {
  readonly id: string;
  readonly kind: ProviderKind;
  readonly temporaryAdapter: boolean;
}

/**
 * Declared as a type alias, not an interface: catalog component metadata is an
 * index-signature type, and only a type alias satisfies it structurally. The
 * binding is published in the catalog, so it has to fit there.
 */
export type ProviderBinding = {
  readonly id: string;
  readonly providerKind: ProviderKind;
  readonly componentDigest: string;
  readonly pairedComponentDigests?: readonly [string, string];
  readonly temporaryAdapter?: true;
};

export interface PluginProvider {
  readonly id: string;
  describe(): ProviderDescriptor;
  invoke(request: ProviderRequest, context: ProviderContext): Promise<ProviderResult>;
}

export type ProviderResolution =
  | { readonly status: "available"; readonly provider: PluginProvider }
  | { readonly status: "unavailable"; readonly reason: Extract<PluginReasonCode, "provider_unavailable"> };
