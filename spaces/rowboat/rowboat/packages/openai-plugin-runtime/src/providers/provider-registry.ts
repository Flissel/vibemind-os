import type {
  PluginProvider,
  ProviderBinding,
  ProviderDescriptor,
  ProviderResolution,
} from "./provider.js";

const BINDING_ID = /^[a-z0-9]+(?:[._-][a-z0-9]+)*$/;
const DIGEST = /^[a-f0-9]{64}$/;
const PROVIDER_KINDS = new Set([
  "mcp-http",
  "mcp-process",
  "rowboat-native",
  "legacy-composio-adapter",
  "openai-connector-bridge",
]);

export function assertBinding(binding: ProviderBinding): void {
  if (!BINDING_ID.test(binding.id)) {
    throw new Error("provider_invalid:binding_id");
  }
  if (!DIGEST.test(binding.componentDigest)) {
    throw new Error("provider_invalid:component_digest");
  }
  if (!PROVIDER_KINDS.has(binding.providerKind)) {
    throw new Error("provider_invalid:provider_kind");
  }
  if (binding.pairedComponentDigests !== undefined) {
    if (
      binding.pairedComponentDigests.length !== 2
      || !binding.pairedComponentDigests.every((digest) => DIGEST.test(digest))
      || binding.pairedComponentDigests[0] !== binding.componentDigest
    ) {
      throw new Error("provider_invalid:paired_component_digests");
    }
  }
}

function bindingSignature(binding: ProviderBinding): string {
  return JSON.stringify({
    id: binding.id,
    providerKind: binding.providerKind,
    componentDigest: binding.componentDigest,
    pairedComponentDigests: binding.pairedComponentDigests ?? null,
    temporaryAdapter: binding.temporaryAdapter ?? false,
  });
}

interface RegisteredProvider {
  readonly signature: string;
  readonly provider: PluginProvider;
}

function snapshotProvider(provider: PluginProvider, described: ProviderDescriptor): PluginProvider {
  const descriptor = Object.freeze({
    id: described.id,
    kind: described.kind,
    temporaryAdapter: described.temporaryAdapter,
  });
  const invoke = provider.invoke.bind(provider);
  return Object.freeze({
    id: provider.id,
    describe: (): typeof descriptor => descriptor,
    invoke,
  });
}

export class ProviderRegistry {
  readonly #bindings = new Map<string, RegisteredProvider>();

  register(binding: ProviderBinding, provider: PluginProvider): void {
    assertBinding(binding);
    if (this.#bindings.has(binding.id)) {
      throw new Error(`provider_duplicate:${binding.id}`);
    }
    if (binding.providerKind === "openai-connector-bridge") {
      throw new Error("provider_unavailable:openai-connector-bridge");
    }
    const descriptor = provider.describe();
    if (
      !BINDING_ID.test(provider.id)
      || !BINDING_ID.test(descriptor.id)
      || descriptor.id !== provider.id
      || descriptor.kind !== binding.providerKind
      || descriptor.temporaryAdapter !== (binding.temporaryAdapter === true)
    ) {
      throw new Error("provider_invalid:descriptor_mismatch");
    }
    const facade = snapshotProvider(provider, descriptor);
    this.#bindings.set(binding.id, Object.freeze({
      signature: bindingSignature(binding),
      provider: facade,
    }));
  }

  resolve(binding: ProviderBinding): ProviderResolution {
    try {
      assertBinding(binding);
    } catch {
      return Object.freeze({ status: "unavailable", reason: "provider_unavailable" });
    }
    const registered = this.#bindings.get(binding.id);
    if (registered === undefined || registered.signature !== bindingSignature(binding)) {
      return Object.freeze({ status: "unavailable", reason: "provider_unavailable" });
    }
    return Object.freeze({ status: "available", provider: registered.provider });
  }
}
