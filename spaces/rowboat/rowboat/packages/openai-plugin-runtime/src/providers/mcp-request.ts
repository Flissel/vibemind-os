import type { ProviderRequest } from "./provider.js";

const MCP_OPERATION_NAME = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/;
/** Every MCP operation is conservatively treated as write until a trusted
 * catalog artifact can classify individual tool capabilities. */
export function validateMcpInvocation(
  boundComponentName: string,
  request: ProviderRequest,
): string {
  if (request.componentName !== boundComponentName) {
    throw new Error("provider_invalid:component_mismatch");
  }
  if (
    typeof request.operationName !== "string"
    || !MCP_OPERATION_NAME.test(request.operationName)
  ) {
    throw new Error("provider_invalid:operation_name");
  }
  if (request.capability !== "write") {
    throw new Error("provider_invalid:capability_mismatch");
  }
  return request.operationName;
}
