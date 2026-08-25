import { isProxy } from "node:util/types";

export const REDACTED = "[REDACTED]" as const;

const SAFE_KEY = /^[A-Za-z0-9_-]{1,128}$/;
const FORBIDDEN_KEYS = new Set(["__proto__", "prototype", "constructor"]);
const ALWAYS_SECRET_SEGMENTS = new Set([
  "bearer", "password", "secret", "token", "tokens", "passphrase", "jwt", "pem",
]);
const OPERATIONAL_SECRET_SEGMENTS = new Set([
  "env", "header", "headers", "stack", "url", "urls", "command", "commands",
  "error", "errors", "message", "messages", "path", "paths",
]);
const DIRECT_SECRET_KEYS = new Set([
  "auth", "authorization", "cookie", "credential", "credentials", "cert", "certificate", "session",
]);
const SECRET_COMPOUNDS = new Set([
  "apikey", "privatekey", "accesstoken", "refreshtoken", "clientsecret", "sessionid", "sessiontoken",
  "sessioncookie", "setcookie", "sshkey", "signingkey", "encryptionkey",
]);
const SECRET_KEY_PREFIXES = new Set(["api", "private", "ssh", "signing", "encryption", "certificate"]);

function isSensitiveKey(key: string | undefined): boolean {
  if (key === undefined) return false;
  const normalized = key.replace(/([a-z0-9])([A-Z])/g, "$1_$2").toLowerCase();
  const collapsed = normalized.replace(/[_-]/g, "");
  if (DIRECT_SECRET_KEYS.has(normalized) || SECRET_COMPOUNDS.has(collapsed)) return true;
  const segments = normalized.split(/[_-]/);
  if (segments.some((part) => ALWAYS_SECRET_SEGMENTS.has(part) || OPERATIONAL_SECRET_SEGMENTS.has(part))) return true;
  if (
    segments.some((part) => part === "auth" || part === "authorization")
    || segments.some((part) => part === "credential" || part === "credentials")
  ) return true;
  return segments.some((part, index) => part === "key" && index > 0 && SECRET_KEY_PREFIXES.has(segments[index - 1]!));
}

export interface RedactionLimits {
  readonly maxDepth?: number;
  readonly maxKeys?: number;
  readonly maxItems?: number;
  readonly maxStringBytes?: number;
  readonly maxTotalStringBytes?: number;
}

export interface RedactionResult {
  readonly value: unknown;
  readonly paths: readonly string[];
}

export interface CapturedRedactionLimits {
  readonly maxDepth: number;
  readonly maxKeys: number;
  readonly maxItems: number;
  readonly maxStringBytes: number;
  readonly maxTotalStringBytes: number;
}

interface Budget {
  keys: number;
  items: number;
  stringBytes: number;
}

function fail(): never {
  throw new Error("receipt_invalid");
}

export function captureRedactionLimits(input: unknown): CapturedRedactionLimits {
  if (input === null || typeof input !== "object" || Array.isArray(input) || isProxy(input)) fail();
  const prototype = Object.getPrototypeOf(input);
  if (prototype !== Object.prototype && prototype !== null) fail();
  const allowed = new Set(["maxDepth", "maxKeys", "maxItems", "maxStringBytes", "maxTotalStringBytes"]);
  const keys = Reflect.ownKeys(input);
  if (keys.length > allowed.size || keys.some((key) => typeof key !== "string")) fail();
  const captured: Record<string, number> = Object.create(null) as Record<string, number>;
  for (const key of keys as string[]) {
    const descriptor = Object.getOwnPropertyDescriptor(input, key);
    if (descriptor === undefined) fail();
    if (!allowed.has(key) || !("value" in descriptor) || !descriptor.enumerable) fail();
    if (!Number.isSafeInteger(descriptor.value) || (descriptor.value as number) < 0 || (descriptor.value as number) > 16 * 1024 * 1024) fail();
    captured[key] = descriptor.value as number;
  }
  return Object.freeze({
    maxDepth: captured.maxDepth ?? 8,
    maxKeys: captured.maxKeys ?? 256,
    maxItems: captured.maxItems ?? 256,
    maxStringBytes: captured.maxStringBytes ?? 16_384,
    maxTotalStringBytes: captured.maxTotalStringBytes ?? 65_536,
  });
}

function dataEntries(
  value: object,
  remainingKeys: number,
  remainingItems: number,
): readonly [string, unknown][] {
  if (isProxy(value)) fail();
  const prototype = Object.getPrototypeOf(value);
  const array = Array.isArray(value);
  if (prototype !== (array ? Array.prototype : Object.prototype) && prototype !== null) fail();
  if (array) {
    const lengthDescriptor = Object.getOwnPropertyDescriptor(value, "length");
    if (
      lengthDescriptor === undefined
      || !("value" in lengthDescriptor)
      || !Number.isSafeInteger(lengthDescriptor.value)
      || (lengthDescriptor.value as number) < 0
      || (lengthDescriptor.value as number) > remainingItems
    ) fail();
    const length = lengthDescriptor.value as number;
    const ownKeys = Reflect.ownKeys(value);
    if (ownKeys.length !== length + 1 || ownKeys.some((key) => typeof key !== "string")) fail();
    const result: [string, unknown][] = [];
    for (let index = 0; index < length; index += 1) {
      const key = String(index);
      const descriptor = Object.getOwnPropertyDescriptor(value, key);
      if (descriptor === undefined || !("value" in descriptor) || !descriptor.enumerable) fail();
      result.push([key, descriptor.value]);
    }
    return result;
  }
  const ownKeys = Reflect.ownKeys(value);
  if (ownKeys.length > remainingKeys || ownKeys.some((key) => typeof key !== "string")) fail();
  const result: [string, unknown][] = [];
  for (const key of ownKeys as string[]) {
    const descriptor = Object.getOwnPropertyDescriptor(value, key);
    if (descriptor === undefined || !("value" in descriptor) || !descriptor.enumerable) fail();
    result.push([key, descriptor.value]);
  }
  return result;
}

function pathSegments(path: string): readonly string[] {
  if (typeof path !== "string" || path.length === 0 || path.length > 512) fail();
  const segments = path.split(".");
  if (segments.some((part) => !SAFE_KEY.test(part) || FORBIDDEN_KEYS.has(part))) fail();
  return Object.freeze(segments);
}

export function canonicalSensitivePaths(input: readonly string[]): readonly string[] {
  if (!Array.isArray(input) || isProxy(input)) fail();
  const lengthDescriptor = Object.getOwnPropertyDescriptor(input, "length");
  if (
    lengthDescriptor === undefined
    || !("value" in lengthDescriptor)
    || !Number.isSafeInteger(lengthDescriptor.value)
    || (lengthDescriptor.value as number) < 0
    || (lengthDescriptor.value as number) > 256
  ) fail();
  const length = lengthDescriptor.value as number;
  const ownKeys = Reflect.ownKeys(input);
  if (ownKeys.length !== length + 1 || ownKeys.some((key) => typeof key !== "string")) fail();
  const result = new Set<string>();
  for (let index = 0; index < length; index += 1) {
    const descriptor = Object.getOwnPropertyDescriptor(input, String(index));
    if (descriptor === undefined || !("value" in descriptor)) fail();
    result.add(pathSegments(descriptor.value as string).join("."));
  }
  return Object.freeze([...result].sort());
}

export function redactBounded(
  input: unknown,
  sensitivePaths: readonly string[],
  limits: RedactionLimits = {},
): RedactionResult {
  const requested = new Set(canonicalSensitivePaths(sensitivePaths));
  const capturedLimits = captureRedactionLimits(limits);
  const maximumDepth = capturedLimits.maxDepth;
  const maximumKeys = capturedLimits.maxKeys;
  const maximumItems = capturedLimits.maxItems;
  const maximumStringBytes = capturedLimits.maxStringBytes;
  const maximumTotalStringBytes = capturedLimits.maxTotalStringBytes;
  const budget: Budget = { keys: 0, items: 0, stringBytes: 0 };
  const seen = new Set<object>();
  const redactions = new Set<string>();

  const visit = (value: unknown, segments: readonly string[], depth: number): unknown => {
    const path = segments.join(".");
    const key = segments.at(-1);
    if (requested.has(path) || isSensitiveKey(key)) {
      redactions.add(path);
      for (const requestedPath of requested) {
        if (requestedPath.startsWith(`${path}.`)) redactions.add(requestedPath);
      }
      return REDACTED;
    }
    if (typeof value === "string") {
      const bytes = Buffer.byteLength(value, "utf8");
      budget.stringBytes += bytes;
      if (bytes > maximumStringBytes || budget.stringBytes > maximumTotalStringBytes) fail();
      return value;
    }
    if (value === null || typeof value === "boolean" || typeof value === "number") {
      if (typeof value === "number" && !Number.isFinite(value)) fail();
      return value;
    }
    if (typeof value !== "object" || depth >= maximumDepth || seen.has(value)) fail();
    seen.add(value);
    const entries = dataEntries(
      value,
      maximumKeys - budget.keys,
      maximumItems - budget.items,
    );
    if (Array.isArray(value)) {
      budget.items += entries.length;
      if (budget.items > maximumItems) fail();
      const result = entries.map(([index, child]) => visit(child, [...segments, index], depth + 1));
      seen.delete(value);
      return Object.freeze(result);
    }
    budget.keys += entries.length;
    if (budget.keys > maximumKeys) fail();
    const result: Record<string, unknown> = Object.create(null) as Record<string, unknown>;
    for (const [entryKey, child] of [...entries].sort(([left], [right]) => left < right ? -1 : left > right ? 1 : 0)) {
      if (!SAFE_KEY.test(entryKey) || FORBIDDEN_KEYS.has(entryKey)) fail();
      result[entryKey] = visit(child, [...segments, entryKey], depth + 1);
    }
    seen.delete(value);
    return Object.freeze(result);
  };

  const value = visit(input, [], 0);
  return Object.freeze({ value, paths: Object.freeze([...redactions].sort()) });
}
