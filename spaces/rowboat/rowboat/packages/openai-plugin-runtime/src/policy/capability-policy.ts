import type { PluginPolicy } from "./default-policy.js";
import {
  createAdmittedDecision,
  createRestrictedDecision,
  evaluateLicense,
  type AdmissionDecision,
} from "./license-policy.js";

export type CapabilityKind =
  | "mcp_http"
  | "mcp_process"
  | "hook_command"
  | "read"
  | "write";

export interface CapabilityReference {
  readonly kind: CapabilityKind;
  readonly reference?: string;
}

function rejectUnsupportedCapability(
  _unsupportedKind: never,
  policy: PluginPolicy,
): AdmissionDecision {
  return createRestrictedDecision("rejected", "component_unsupported", policy);
}

export function evaluateCapability(
  capability: CapabilityReference,
  policy: PluginPolicy,
): AdmissionDecision {
  switch (capability.kind) {
    case "mcp_http":
      return policy.allowHttpMcp
        ? createAdmittedDecision(policy)
        : createRestrictedDecision("rejected", "http_mcp_not_admitted", policy);
    case "mcp_process":
      return policy.allowProcessMcp
        ? createAdmittedDecision(policy)
        : createRestrictedDecision("rejected", "process_not_admitted", policy);
    case "hook_command":
      return policy.allowCommandHooks
        ? createAdmittedDecision(policy)
        : createRestrictedDecision("rejected", "hook_not_admitted", policy);
    case "read":
      return createAdmittedDecision(policy);
    case "write":
      return policy.allowWriteCapabilities
        ? createAdmittedDecision(policy)
        : createRestrictedDecision(
            "review_required",
            "write_review_required",
            policy,
          );
  }

  return rejectUnsupportedCapability(capability.kind, policy);
}

const STATUS_SEVERITY = {
  admitted: 0,
  review_required: 1,
  rejected: 2,
} as const;

export function evaluateComponentAdmission(
  parentLicense: string | undefined,
  capability: CapabilityReference,
  policy: PluginPolicy,
): AdmissionDecision {
  const parentDecision = evaluateLicense(parentLicense, policy);
  const componentDecision = evaluateCapability(capability, policy);

  if (STATUS_SEVERITY[parentDecision.status] >= STATUS_SEVERITY[componentDecision.status]) {
    return parentDecision;
  }

  return componentDecision;
}
