import type { ProviderBinding } from "../providers/provider.js";

export interface InstallationProviderBinding {
  readonly componentId: string;
  readonly binding: ProviderBinding;
}

export interface PluginInstallation {
  readonly id: string;
  readonly projectId: string;
  readonly pluginName: string;
  readonly pluginVersion: string;
  readonly sourceCommit: string;
  readonly manifestDigest: string;
  readonly treeDigest: string;
  readonly policyVersion: string;
  readonly enabled: boolean;
  readonly revision: number;
  readonly providerBindings?: readonly InstallationProviderBinding[];
}
