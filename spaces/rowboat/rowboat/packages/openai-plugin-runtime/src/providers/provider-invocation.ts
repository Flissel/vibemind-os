import type { ProviderContext, ProviderRequest } from "./provider.js";

const PROVIDER_IDENTIFIER = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/;
const MAX_ARGUMENT_BYTES = 64 * 1024;
const MAX_ARGUMENT_DEPTH = 16;
const MAX_ARGUMENT_NODES = 1024;
const PROTOTYPE_RISK_KEYS = new Set(["__proto__", "constructor", "prototype"]);

function invalidRequest(): never {
  throw new Error("provider_invalid:request");
}

function dataProperties(
  value: unknown,
  allowedKeys: ReadonlySet<string>,
): Readonly<Record<string, PropertyDescriptor>> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) invalidRequest();
  const prototype = Object.getPrototypeOf(value);
  if (prototype !== Object.prototype && prototype !== null) invalidRequest();
  const descriptors = Object.getOwnPropertyDescriptors(value);
  if (
    Object.getOwnPropertySymbols(value).length !== 0
    || Object.keys(descriptors).some((key) => !allowedKeys.has(key))
    || Object.values(descriptors).some(
      (descriptor) => descriptor.get !== undefined || descriptor.set !== undefined || !descriptor.enumerable,
    )
  ) {
    invalidRequest();
  }
  return descriptors;
}

function dataValue(
  descriptors: Readonly<Record<string, PropertyDescriptor>>,
  key: string,
  required = true,
): unknown {
  const descriptor = descriptors[key];
  if (descriptor === undefined) {
    if (required) invalidRequest();
    return undefined;
  }
  if (!("value" in descriptor)) invalidRequest();
  return descriptor.value;
}

function cloneJsonValue(
  input: unknown,
  depth: number,
  state: { nodes: number; bytes: number; readonly seen: Set<object> },
): unknown {
  state.nodes += 1;
  if (state.nodes > MAX_ARGUMENT_NODES || depth > MAX_ARGUMENT_DEPTH) invalidRequest();
  if (typeof input === "string") {
    state.bytes += Buffer.byteLength(input, "utf8") + 2;
    if (state.bytes > MAX_ARGUMENT_BYTES) invalidRequest();
    return input;
  }
  if (input === null || typeof input === "boolean") return input;
  if (typeof input === "number") {
    if (!Number.isFinite(input)) invalidRequest();
    return input;
  }
  if (typeof input !== "object") invalidRequest();
  if (state.seen.has(input)) invalidRequest();
  state.seen.add(input);
  try {
    if (Array.isArray(input)) {
      if (!Number.isSafeInteger(input.length) || input.length > MAX_ARGUMENT_NODES - state.nodes) {
        invalidRequest();
      }
      const descriptors = Object.getOwnPropertyDescriptors(input);
      if (Object.getOwnPropertySymbols(input).length !== 0) invalidRequest();
      const allowed = new Set(["length", ...Array.from({ length: input.length }, (_, index) => String(index))]);
      if (Object.keys(descriptors).some((key) => !allowed.has(key))) invalidRequest();
      const captured = Array.from({ length: input.length }, (_, index) => {
        const descriptor = descriptors[String(index)];
        if (
          descriptor === undefined
          || descriptor.get !== undefined
          || descriptor.set !== undefined
          || !("value" in descriptor)
        ) {
          invalidRequest();
        }
        return cloneJsonValue(descriptor.value, depth + 1, state);
      });
      return Object.freeze(captured);
    }

    const prototype = Object.getPrototypeOf(input);
    if (prototype !== Object.prototype && prototype !== null) invalidRequest();
    const descriptors = Object.getOwnPropertyDescriptors(input);
    if (Object.getOwnPropertySymbols(input).length !== 0) invalidRequest();
    const captured: Record<string, unknown> = Object.create(null) as Record<string, unknown>;
    for (const [key, descriptor] of Object.entries(descriptors)) {
      if (
        PROTOTYPE_RISK_KEYS.has(key)
        || descriptor.get !== undefined
        || descriptor.set !== undefined
        || !descriptor.enumerable
        || !("value" in descriptor)
      ) {
        invalidRequest();
      }
      state.bytes += Buffer.byteLength(key, "utf8") + 3;
      if (state.bytes > MAX_ARGUMENT_BYTES) invalidRequest();
      captured[key] = cloneJsonValue(descriptor.value, depth + 1, state);
    }
    return Object.freeze(captured);
  } finally {
    state.seen.delete(input);
  }
}

export interface CapturedProviderInvocation {
  readonly request: ProviderRequest;
  readonly context: ProviderContext;
}

export function captureProviderInvocation(
  request: ProviderRequest,
  context: ProviderContext,
): CapturedProviderInvocation {
  try {
    const requestProperties = dataProperties(
      request,
      new Set(["projectId", "pluginName", "componentName", "operationName", "capability", "arguments"]),
    );
    const contextProperties = dataProperties(context, new Set(["requestId", "signal"]));
    const projectId = dataValue(requestProperties, "projectId");
    const pluginName = dataValue(requestProperties, "pluginName");
    const componentName = dataValue(requestProperties, "componentName");
    const operationName = dataValue(requestProperties, "operationName", false);
    const capability = dataValue(requestProperties, "capability");
    const requestId = dataValue(contextProperties, "requestId");
    const signal = dataValue(contextProperties, "signal", false);
    if (
      typeof projectId !== "string" || !PROVIDER_IDENTIFIER.test(projectId)
      || typeof pluginName !== "string" || !PROVIDER_IDENTIFIER.test(pluginName)
      || typeof componentName !== "string" || !PROVIDER_IDENTIFIER.test(componentName)
      || (operationName !== undefined && (typeof operationName !== "string" || !PROVIDER_IDENTIFIER.test(operationName)))
      || (capability !== "read" && capability !== "write")
      || typeof requestId !== "string" || !PROVIDER_IDENTIFIER.test(requestId)
      || (signal !== undefined && !(signal instanceof AbortSignal))
    ) {
      invalidRequest();
    }
    const argumentsValue = dataValue(requestProperties, "arguments");
    if (
      typeof argumentsValue !== "object"
      || argumentsValue === null
      || Array.isArray(argumentsValue)
    ) {
      invalidRequest();
    }
    const capturedArguments = cloneJsonValue(argumentsValue, 0, {
      nodes: 0,
      bytes: 0,
      seen: new Set(),
    });
    const serialized = JSON.stringify(capturedArguments);
    if (serialized === undefined || Buffer.byteLength(serialized, "utf8") > MAX_ARGUMENT_BYTES) {
      invalidRequest();
    }
    const capturedRequest = Object.freeze({
      projectId,
      pluginName,
      componentName,
      ...(operationName === undefined ? {} : { operationName }),
      capability,
      arguments: capturedArguments as Readonly<Record<string, unknown>>,
    });
    return Object.freeze({
      request: capturedRequest,
      context: Object.freeze({ requestId, ...(signal === undefined ? {} : { signal }) }),
    });
  } catch (error: unknown) {
    if (error instanceof Error && error.message === "provider_invalid:request") throw error;
    invalidRequest();
  }
}
