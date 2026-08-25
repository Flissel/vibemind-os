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

function captureBinding(binding: ProviderBinding): ProviderBinding {
  try {
    const id = binding.id;
    const providerKind = binding.providerKind;
    const componentDigest = binding.componentDigest;
    const pairedSource = binding.pairedComponentDigests;
    const temporarySource = binding.temporaryAdapter;
    const pairedLength = pairedSource?.length;
    if (pairedLength !== undefined && pairedLength !== 2) {
      throw new Error("invalid paired digest length");
    }
    const pairedComponentDigests = pairedSource === undefined
      ? undefined
      : Object.freeze([pairedSource[0], pairedSource[1]] as const);
    return Object.freeze({
      id,
      providerKind,
      componentDigest,
      ...(pairedComponentDigests === undefined ? {} : { pairedComponentDigests }),
      ...(temporarySource === undefined ? {} : { temporaryAdapter: temporarySource }),
    });
  } catch {
    throw new Error("provider_invalid:binding");
  }
}

function validateBinding(binding: ProviderBinding): void {
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

export function assertBinding(binding: ProviderBinding): void {
  validateBinding(captureBinding(binding));
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

interface ProviderSnapshot {
  readonly id: string;
  readonly descriptor: Readonly<ProviderDescriptor>;
  readonly invoke: PluginProvider["invoke"];
}

function captureProvider(provider: PluginProvider): ProviderSnapshot {
  try {
    const id = provider.id;
    const describe = provider.describe;
    const invokeMethod = provider.invoke;
    if (typeof describe !== "function" || typeof invokeMethod !== "function") {
      throw new Error("invalid provider methods");
    }
    const described = describe.call(provider);
    const descriptor = Object.freeze({
      id: described.id,
      kind: described.kind,
      temporaryAdapter: described.temporaryAdapter,
    });
    return Object.freeze({
      id,
      descriptor,
      invoke: invokeMethod.bind(provider),
    });
  } catch {
    throw new Error("provider_invalid:descriptor_mismatch");
  }
}

function snapshotProvider(snapshot: ProviderSnapshot): PluginProvider {
  const descriptor = snapshot.descriptor;
  return Object.freeze({
    id: snapshot.id,
    describe: (): typeof descriptor => descriptor,
    invoke: snapshot.invoke,
  });
}

export class ProviderRegistry {
  readonly #bindings = new Map<string, RegisteredProvider>();

  register(binding: ProviderBinding, provider: PluginProvider): void {
    const bindingSnapshot = captureBinding(binding);
    validateBinding(bindingSnapshot);
    if (this.#bindings.has(bindingSnapshot.id)) {
      throw new Error(`provider_duplicate:${bindingSnapshot.id}`);
    }
    if (bindingSnapshot.providerKind === "openai-connector-bridge") {
      throw new Error("provider_unavailable:openai-connector-bridge");
    }
    const providerSnapshot = captureProvider(provider);
    const descriptor = providerSnapshot.descriptor;
    if (
      !BINDING_ID.test(providerSnapshot.id)
      || !BINDING_ID.test(descriptor.id)
      || descriptor.id !== providerSnapshot.id
      || descriptor.kind !== bindingSnapshot.providerKind
      || descriptor.temporaryAdapter !== (bindingSnapshot.temporaryAdapter === true)
    ) {
      throw new Error("provider_invalid:descriptor_mismatch");
    }
    const facade = snapshotProvider(providerSnapshot);
    this.#bindings.set(bindingSnapshot.id, Object.freeze({
      signature: bindingSignature(bindingSnapshot),
      provider: facade,
    }));
  }

  resolve(binding: ProviderBinding): ProviderResolution {
    let bindingSnapshot: ProviderBinding;
    try {
      bindingSnapshot = captureBinding(binding);
      validateBinding(bindingSnapshot);
    } catch {
      return Object.freeze({ status: "unavailable", reason: "provider_unavailable" });
    }
    const registered = this.#bindings.get(bindingSnapshot.id);
    if (registered === undefined || registered.signature !== bindingSignature(bindingSnapshot)) {
      return Object.freeze({ status: "unavailable", reason: "provider_unavailable" });
    }
    return Object.freeze({ status: "available", provider: registered.provider });
  }
}
