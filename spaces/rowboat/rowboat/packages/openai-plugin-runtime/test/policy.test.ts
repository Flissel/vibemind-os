import { describe, expect, it } from "vitest";

import {
  DEFAULT_POLICY,
  evaluateCapability,
  evaluateComponentAdmission,
  evaluateLicense,
} from "../src/index.js";

describe("plugin admission policy", () => {
  it.each([
    ["MIT", "admitted"],
    ["Apache-2.0", "admitted"],
    ["Apache-2.0 AND CC-BY-4.0", "review_required"],
    ["Proprietary", "review_required"],
    ["UNLICENSED", "rejected"],
    ["", "review_required"],
    [undefined, "review_required"],
    ["LicenseRef-Figma-Developer-Terms", "review_required"],
    ["MIT OR Proprietary", "review_required"],
    ["Unknown-License", "review_required"],
  ] as const)("classifies %s as %s", (license, status) => {
    expect(evaluateLicense(license, DEFAULT_POLICY).status).toBe(status);
  });

  it("returns stable, reason-bearing license decisions", () => {
    expect(evaluateLicense("MIT", DEFAULT_POLICY)).toEqual({
      status: "admitted",
      policyVersion: "rowboat-plugin-policy-v1",
    });
    expect(evaluateLicense("Proprietary", DEFAULT_POLICY)).toEqual({
      status: "review_required",
      reason: "license_review_required",
      policyVersion: "rowboat-plugin-policy-v1",
    });
    expect(evaluateLicense("UNLICENSED", DEFAULT_POLICY)).toEqual({
      status: "rejected",
      reason: "license_rejected",
      policyVersion: "rowboat-plugin-policy-v1",
    });
  });

  it("admits HTTP MCP and read capabilities without inventing a rejection reason", () => {
    expect(evaluateCapability({ kind: "mcp_http" }, DEFAULT_POLICY)).toEqual({
      status: "admitted",
      policyVersion: "rowboat-plugin-policy-v1",
    });
    expect(evaluateCapability({ kind: "read" }, DEFAULT_POLICY)).toEqual({
      status: "admitted",
      policyVersion: "rowboat-plugin-policy-v1",
    });
  });

  it("rejects process MCP and command hooks by default", () => {
    expect(evaluateCapability({ kind: "mcp_process" }, DEFAULT_POLICY)).toEqual({
      status: "rejected",
      reason: "process_not_admitted",
      policyVersion: "rowboat-plugin-policy-v1",
    });
    expect(evaluateCapability({ kind: "hook_command" }, DEFAULT_POLICY)).toEqual({
      status: "rejected",
      reason: "hook_not_admitted",
      policyVersion: "rowboat-plugin-policy-v1",
    });
  });

  it("requires review for write capabilities by default", () => {
    expect(evaluateCapability({ kind: "write" }, DEFAULT_POLICY)).toEqual({
      status: "review_required",
      reason: "write_review_required",
      policyVersion: "rowboat-plugin-policy-v1",
    });
  });

  it.each([
    ["MIT", "mcp_http", "admitted"],
    ["MIT", "write", "review_required"],
    ["MIT", "mcp_process", "rejected"],
    ["Proprietary", "mcp_http", "review_required"],
    ["Proprietary", "write", "review_required"],
    ["Proprietary", "mcp_process", "rejected"],
    ["UNLICENSED", "mcp_http", "rejected"],
    ["UNLICENSED", "write", "rejected"],
    ["UNLICENSED", "mcp_process", "rejected"],
  ] as const)(
    "composes parent %s and component %s monotonically as %s",
    (license, kind, expectedStatus) => {
      const parent = evaluateLicense(license, DEFAULT_POLICY);
      const decision = evaluateComponentAdmission(license, { kind }, DEFAULT_POLICY);

      expect(decision.status).toBe(expectedStatus);
      expect(decision.policyVersion).toBe(DEFAULT_POLICY.version);
      if (
        parent.status === "rejected" ||
        (parent.status === "review_required" && kind !== "mcp_process")
      ) {
        expect(decision).toEqual(parent);
      }
    },
  );

  it("does not accept component policy overrides", () => {
    const capability = {
      kind: "mcp_http" as const,
      policyVersion: "attacker-policy",
      allowProcessMcp: true,
    };

    expect(
      evaluateComponentAdmission(
        "UNLICENSED",
        capability,
        DEFAULT_POLICY,
      ),
    ).toEqual({
      status: "rejected",
      reason: "license_rejected",
      policyVersion: "rowboat-plugin-policy-v1",
    });
  });

  it("exposes a runtime-immutable default policy", () => {
    expect(Object.isFrozen(DEFAULT_POLICY)).toBe(true);
    expect(Object.isFrozen(DEFAULT_POLICY.admittedLicenses)).toBe(true);
    expect(Object.isFrozen(DEFAULT_POLICY.rejectedLicenses)).toBe(true);
    expect(DEFAULT_POLICY.admittedLicenses.has("MIT")).toBe(true);
    expect("add" in DEFAULT_POLICY.admittedLicenses).toBe(false);
    expect("delete" in DEFAULT_POLICY.admittedLicenses).toBe(false);

    expect(() =>
      Object.defineProperty(DEFAULT_POLICY, "allowProcessMcp", { value: true }),
    ).toThrow();
    expect(() =>
      Object.defineProperty(DEFAULT_POLICY.admittedLicenses, "has", {
        value: () => false,
      }),
    ).toThrow();
  });

  it("never reads environment values during policy evaluation", () => {
    const descriptor = Object.getOwnPropertyDescriptor(process, "env");
    if (descriptor === undefined) {
      throw new Error("Expected process.env descriptor.");
    }

    Object.defineProperty(process, "env", {
      configurable: true,
      get: () => {
        throw new Error("Policy attempted to read process.env");
      },
    });

    try {
      expect(evaluateLicense("MIT", DEFAULT_POLICY).status).toBe("admitted");
      expect(
        evaluateCapability(
          { kind: "mcp_http", reference: "TOKEN_ENV_NAME" },
          DEFAULT_POLICY,
        ).status,
      ).toBe("admitted");
    } finally {
      Object.defineProperty(process, "env", descriptor);
    }
  });
});
