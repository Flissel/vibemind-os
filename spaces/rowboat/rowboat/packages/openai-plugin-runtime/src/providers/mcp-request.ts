import type { ProviderRequest } from "./provider.js";

const MCP_OPERATION_NAME = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/;
const COMPONENT_DIGEST = /^[a-f0-9]{64}$/;
const POLICY_VERSION = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/;
const MAX_MCP_OPERATIONS = 128;

export interface McpOperationBinding {
  readonly operationName: string;
  readonly capability: "read" | "write";
  readonly componentDigest: string;
  readonly policyVersion: string;
}

export function captureMcpOperationBindings(
  input: readonly McpOperationBinding[],
  componentDigest: string,
  policyVersion: string,
): readonly McpOperationBinding[] {
  if (
    !Array.isArray(input)
    || input.length < 1
    || input.length > MAX_MCP_OPERATIONS
    || !COMPONENT_DIGEST.test(componentDigest)
    || !POLICY_VERSION.test(policyVersion)
  ) {
    throw new Error("provider_invalid:operation_bindings");
  }
  const names = new Set<string>();
  const captured = input.map((binding) => {
    if (typeof binding !== "object" || binding === null) {
      throw new Error("provider_invalid:operation_bindings");
    }
    const prototype = Object.getPrototypeOf(binding);
    const descriptors = Object.getOwnPropertyDescriptors(binding);
    const expectedKeys = ["operationName", "capability", "componentDigest", "policyVersion"];
    if (
      (prototype !== Object.prototype && prototype !== null)
      || Object.getOwnPropertySymbols(binding).length !== 0
      || Object.keys(descriptors).length !== expectedKeys.length
      || expectedKeys.some((key) => {
        const descriptor = descriptors[key];
        return descriptor === undefined
          || descriptor.get !== undefined
          || descriptor.set !== undefined
          || !descriptor.enumerable
          || !("value" in descriptor);
      })
    ) {
      throw new Error("provider_invalid:operation_bindings");
    }
    const operationName = descriptors.operationName?.value as unknown;
    const capability = descriptors.capability?.value as unknown;
    const observedDigest = descriptors.componentDigest?.value as unknown;
    const observedPolicyVersion = descriptors.policyVersion?.value as unknown;
    if (
      typeof operationName !== "string"
      || !MCP_OPERATION_NAME.test(operationName)
      || (capability !== "read" && capability !== "write")
      || observedDigest !== componentDigest
      || observedPolicyVersion !== policyVersion
      || names.has(operationName)
    ) {
      throw new Error("provider_invalid:operation_bindings");
    }
    names.add(operationName);
    return Object.freeze({
      operationName,
      capability,
      componentDigest: observedDigest,
      policyVersion: observedPolicyVersion,
    });
  });
  return Object.freeze(captured);
}

export function validateMcpInvocation(
  boundComponentName: string,
  request: ProviderRequest,
  operations: readonly McpOperationBinding[],
): McpOperationBinding {
  if (request.componentName !== boundComponentName) {
    throw new Error("provider_invalid:component_mismatch");
  }
  if (
    typeof request.operationName !== "string"
    || !MCP_OPERATION_NAME.test(request.operationName)
  ) {
    throw new Error("provider_invalid:operation_name");
  }
  const binding = operations.find((candidate) => candidate.operationName === request.operationName);
  if (binding === undefined) throw new Error("provider_invalid:operation_not_admitted");
  if (request.capability !== binding.capability) {
    throw new Error("provider_invalid:capability_mismatch");
  }
  return binding;
}
