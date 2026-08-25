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
}

export type ProviderResult =
  | { readonly status: "success"; readonly output: unknown }
  | { readonly status: "failed"; readonly reason: string };

export interface ProviderDescriptor {
  readonly id: string;
  readonly kind: ProviderKind;
  readonly temporaryAdapter: boolean;
}

export interface ProviderBinding {
  readonly id: string;
  readonly providerKind: ProviderKind;
  readonly componentDigest: string;
  readonly pairedComponentDigests?: readonly [string, string];
  readonly temporaryAdapter?: true;
}

export interface PluginProvider {
  readonly id: string;
  describe(): ProviderDescriptor;
  invoke(request: ProviderRequest, context: ProviderContext): Promise<ProviderResult>;
}

export type ProviderResolution =
  | { readonly status: "available"; readonly provider: PluginProvider }
  | { readonly status: "unavailable"; readonly reason: Extract<PluginReasonCode, "provider_unavailable"> };
