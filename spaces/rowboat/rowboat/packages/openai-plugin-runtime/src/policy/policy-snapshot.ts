import type { ImmutableStringSet, PluginPolicy } from "./default-policy.js";

function immutableStringSet(values: Iterable<string>): ImmutableStringSet {
  const captured = Object.freeze([...values]);
  return Object.freeze({
    size: captured.length,
    has: (value: string): boolean => captured.includes(value),
    values: (): IterableIterator<string> => captured.values(),
    [Symbol.iterator]: (): IterableIterator<string> => captured.values(),
  });
}

export function capturePluginPolicy(policy: PluginPolicy): PluginPolicy {
  return Object.freeze({
    version: policy.version,
    admittedLicenses: immutableStringSet(policy.admittedLicenses),
    rejectedLicenses: immutableStringSet(policy.rejectedLicenses),
    allowHttpMcp: policy.allowHttpMcp,
    allowProcessMcp: policy.allowProcessMcp,
    allowCommandHooks: policy.allowCommandHooks,
    allowWriteCapabilities: policy.allowWriteCapabilities,
  });
}
