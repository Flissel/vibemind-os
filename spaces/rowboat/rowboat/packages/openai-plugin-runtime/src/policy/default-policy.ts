export const DEFAULT_POLICY_VERSION = "rowboat-plugin-policy-v1" as const;

export interface ImmutableStringSet {
  readonly size: number;
  has(value: string): boolean;
  values(): IterableIterator<string>;
  [Symbol.iterator](): IterableIterator<string>;
}

export interface PluginPolicy {
  readonly version: string;
  readonly admittedLicenses: ImmutableStringSet;
  readonly rejectedLicenses: ImmutableStringSet;
  readonly allowHttpMcp: boolean;
  readonly allowProcessMcp: boolean;
  readonly allowCommandHooks: boolean;
  readonly allowWriteCapabilities: boolean;
}

function immutableStringSet(values: readonly string[]): ImmutableStringSet {
  const entries = Object.freeze([...values]);
  const immutableSet: ImmutableStringSet = {
    size: entries.length,
    has(value: string): boolean {
      return entries.includes(value);
    },
    values(): IterableIterator<string> {
      return entries.values();
    },
    [Symbol.iterator](): IterableIterator<string> {
      return entries.values();
    },
  };

  return Object.freeze(immutableSet);
}

export const DEFAULT_POLICY: PluginPolicy = Object.freeze({
  version: DEFAULT_POLICY_VERSION,
  admittedLicenses: immutableStringSet(["MIT", "Apache-2.0"]),
  rejectedLicenses: immutableStringSet(["UNLICENSED"]),
  allowHttpMcp: true,
  allowProcessMcp: false,
  allowCommandHooks: false,
  allowWriteCapabilities: false,
});
