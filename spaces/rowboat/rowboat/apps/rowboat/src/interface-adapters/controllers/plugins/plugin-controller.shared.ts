import { types as utilTypes } from "node:util";

export function captureRecord(input: unknown, allowed: readonly string[]): Readonly<Record<string, unknown>> {
  if (input === null || typeof input !== "object" || utilTypes.isProxy(input)) throw new Error("request_invalid");
  const prototype = Object.getPrototypeOf(input);
  if (prototype !== Object.prototype && prototype !== null) throw new Error("request_invalid");
  const keys = Reflect.ownKeys(input);
  if (keys.some((key) => typeof key !== "string" || !allowed.includes(key))) throw new Error("request_invalid");
  const output: Record<string, unknown> = Object.create(null) as Record<string, unknown>;
  for (const key of keys as string[]) {
    const descriptor = Object.getOwnPropertyDescriptor(input, key);
    if (descriptor === undefined || !("value" in descriptor) || !descriptor.enumerable) throw new Error("request_invalid");
    output[key] = descriptor.value;
  }
  return Object.freeze(output);
}

export function captureCallerSignal(input: unknown): AbortSignal {
  if (input === null || typeof input !== "object" || utilTypes.isProxy(input) || Object.getPrototypeOf(input) !== AbortSignal.prototype) {
    throw new Error("request_invalid");
  }
  return input as AbortSignal;
}
