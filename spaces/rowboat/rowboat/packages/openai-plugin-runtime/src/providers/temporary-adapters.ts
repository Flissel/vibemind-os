import { assertBinding } from "./provider-registry.js";
import type { ProviderBinding, ProviderKind } from "./provider.js";

export interface TemporaryAdapterDigests {
  readonly reddit: string;
  readonly xTwitter: string;
  readonly googleDrive: string;
  readonly googleSheets: string;
  readonly admittedSearch: string;
  readonly mockTools: string;
}

export interface TemporaryAdapterBinding extends ProviderBinding {
  readonly temporaryAdapter: true;
}

function temporaryBinding(id: string, providerKind: ProviderKind, componentDigest: string): TemporaryAdapterBinding {
  const binding = {
    id,
    providerKind,
    componentDigest,
    temporaryAdapter: true,
  } as const;
  assertBinding(binding);
  return Object.freeze(binding);
}

export function createTemporaryAdapterBindings(digests: TemporaryAdapterDigests): readonly TemporaryAdapterBinding[] {
  return Object.freeze([
    temporaryBinding("temporary.reddit", "legacy-composio-adapter", digests.reddit),
    temporaryBinding("temporary.x-twitter", "legacy-composio-adapter", digests.xTwitter),
    temporaryBinding("temporary.google-drive", "legacy-composio-adapter", digests.googleDrive),
    temporaryBinding("temporary.google-sheets", "legacy-composio-adapter", digests.googleSheets),
    temporaryBinding("temporary.admitted-search", "rowboat-native", digests.admittedSearch),
    temporaryBinding("temporary.mock-tools", "rowboat-native", digests.mockTools),
  ]);
}
