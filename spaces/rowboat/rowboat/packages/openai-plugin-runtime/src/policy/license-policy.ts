import type { PluginReasonCode } from "../domain/plugin.js";
import type { PluginPolicy } from "./default-policy.js";

export type AdmissionStatus = "admitted" | "review_required" | "rejected";

export type AdmissionDecision =
  | Readonly<{
      status: "admitted";
      policyVersion: string;
    }>
  | Readonly<{
      status: "review_required" | "rejected";
      reason: PluginReasonCode;
      policyVersion: string;
    }>;

function admitted(policy: PluginPolicy): AdmissionDecision {
  return Object.freeze({
    status: "admitted",
    policyVersion: policy.version,
  });
}

function restricted(
  status: "review_required" | "rejected",
  reason: PluginReasonCode,
  policy: PluginPolicy,
): AdmissionDecision {
  return Object.freeze({
    status,
    reason,
    policyVersion: policy.version,
  });
}

export function evaluateLicense(
  license: string | undefined,
  policy: PluginPolicy,
): AdmissionDecision {
  if (license !== undefined && policy.admittedLicenses.has(license)) {
    return admitted(policy);
  }

  if (license !== undefined && policy.rejectedLicenses.has(license)) {
    return restricted("rejected", "license_rejected", policy);
  }

  return restricted("review_required", "license_review_required", policy);
}

export function createAdmittedDecision(policy: PluginPolicy): AdmissionDecision {
  return admitted(policy);
}

export function createRestrictedDecision(
  status: "review_required" | "rejected",
  reason: PluginReasonCode,
  policy: PluginPolicy,
): AdmissionDecision {
  return restricted(status, reason, policy);
}
