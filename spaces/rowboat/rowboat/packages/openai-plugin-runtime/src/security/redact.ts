import { isProxy } from "node:util/types";

export const REDACTED = "[REDACTED]" as const;

const SAFE_KEY = /^[A-Za-z0-9_-]{1,128}$/;
const FORBIDDEN_KEYS = new Set(["__proto__", "prototype", "constructor"]);
const SECRET_SEGMENTS = new Set([
  "authorization", "bearer", "cookie", "credential", "credentials", "env", "header", "headers",
  "password", "secret", "stack", "token", "tokens", "url", "urls", "command", "commands",
  "error", "errors", "message", "messages", "path", "paths",
]);

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
  if (Object.getOwnPropertySymbols(input).length !== 0) fail();
  const descriptors = Object.getOwnPropertyDescriptors(input);
  const allowed = new Set(["maxDepth", "maxKeys", "maxItems", "maxStringBytes", "maxTotalStringBytes"]);
  for (const [key, descriptor] of Object.entries(descriptors)) {
    if (!allowed.has(key) || !("value" in descriptor) || !descriptor.enumerable) fail();
    if (!Number.isSafeInteger(descriptor.value) || (descriptor.value as number) < 0 || (descriptor.value as number) > 16 * 1024 * 1024) fail();
  }
  return Object.freeze({
    maxDepth: (descriptors.maxDepth?.value as number | undefined) ?? 8,
    maxKeys: (descriptors.maxKeys?.value as number | undefined) ?? 256,
    maxItems: (descriptors.maxItems?.value as number | undefined) ?? 256,
    maxStringBytes: (descriptors.maxStringBytes?.value as number | undefined) ?? 16_384,
    maxTotalStringBytes: (descriptors.maxTotalStringBytes?.value as number | undefined) ?? 65_536,
  });
}

function dataEntries(value: object): readonly [string, unknown][] {
  if (isProxy(value)) fail();
  const prototype = Object.getPrototypeOf(value);
  const array = Array.isArray(value);
  if (prototype !== (array ? Array.prototype : Object.prototype) && prototype !== null) fail();
  if (Object.getOwnPropertySymbols(value).length !== 0) fail();
  const descriptors = Object.getOwnPropertyDescriptors(value);
  const keys = Object.keys(descriptors);
  for (const key of keys) {
    if (array && key === "length") continue;
    const descriptor = descriptors[key];
    if (descriptor === undefined || !("value" in descriptor) || !descriptor.enumerable) fail();
  }
  if (array) {
    const lengthDescriptor = descriptors.length;
    if (lengthDescriptor === undefined || !("value" in lengthDescriptor)) fail();
    for (let index = 0; index < value.length; index += 1) {
      if (!Object.hasOwn(descriptors, String(index))) fail();
    }
    const unexpected = keys.filter((key) => key !== "length" && !/^(0|[1-9][0-9]*)$/.test(key));
    if (unexpected.length !== 0) fail();
    return Array.from({ length: value.length }, (_, index) => [String(index), descriptors[String(index)]!.value]);
  }
  return keys.map((key) => [key, descriptors[key]!.value]);
}

function pathSegments(path: string): readonly string[] {
  if (typeof path !== "string" || path.length === 0 || path.length > 512) fail();
  const segments = path.split(".");
  if (segments.some((part) => !SAFE_KEY.test(part) || FORBIDDEN_KEYS.has(part))) fail();
  return Object.freeze(segments);
}

export function canonicalSensitivePaths(input: readonly string[]): readonly string[] {
  if (!Array.isArray(input) || isProxy(input)) fail();
  if (input.length > 256 || Object.getOwnPropertySymbols(input).length !== 0) fail();
  const descriptors = Object.getOwnPropertyDescriptors(input);
  if (Object.keys(descriptors).some((key) => key !== "length" && !/^(0|[1-9][0-9]*)$/.test(key))) fail();
  const result = new Set<string>();
  for (let index = 0; index < input.length; index += 1) {
    const descriptor = descriptors[String(index)];
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
    const normalizedKey = key?.replace(/([a-z0-9])([A-Z])/g, "$1_$2").toLowerCase();
    const secretKey = normalizedKey?.split(/[_-]/).some((part) => SECRET_SEGMENTS.has(part)) === true;
    if (requested.has(path) || secretKey) {
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
    const entries = dataEntries(value);
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
