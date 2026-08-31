import { describe, expect, it } from "vitest";
import { deriveRuntimeDeadlineMs, resolveOpenFangApprovalWindowMs } from "@/di/plugins-container";

// Mirrors the constants documented alongside the implementation (and in
// docs/openai-plugin-runtime-operations.md): PluginToolRuntime's constructor
// caps timeoutMilliseconds at 300_000, and the release call is given a
// 60_000 margin for its own overhead after a decision returns, so 240_000
// is the largest approval window the runtime can actually honour.
const RUNTIME_DEADLINE_CEILING_MS = 300_000;
const MAX_APPROVAL_WINDOW_MS = 240_000;
const DEFAULT_APPROVAL_WINDOW_MS = 120_000;

describe("resolveOpenFangApprovalWindowMs", () => {
  it("falls back to the default for input that is not a valid whole-millisecond duration", () => {
    for (const raw of [
      undefined, "", "abc", "0", "-500", "1500.75", "1e5", " 120000 ", "120000ms",
      // Beyond Number.MAX_SAFE_INTEGER: syntactically all digits, but not a
      // safe integer once parsed, so this must not silently clamp either.
      "99999999999999999999",
    ]) {
      expect(resolveOpenFangApprovalWindowMs(raw)).toBe(DEFAULT_APPROVAL_WINDOW_MS);
    }
  });

  it("passes an in-range window through unchanged", () => {
    expect(resolveOpenFangApprovalWindowMs("1000")).toBe(1_000);
    expect(resolveOpenFangApprovalWindowMs("90000")).toBe(90_000);
  });

  it("passes a window of exactly the max through unchanged", () => {
    expect(resolveOpenFangApprovalWindowMs(String(MAX_APPROVAL_WINDOW_MS))).toBe(MAX_APPROVAL_WINDOW_MS);
  });

  it("clamps a window above the max down to the max, rather than dropping to the default", () => {
    // A ten-minute approval workflow is a plausible configuration; it must
    // not be silently replaced by an unrelated 120s default -- an operator
    // who asked for a longer window should get as much of it as the runtime
    // allows.
    expect(resolveOpenFangApprovalWindowMs("600000")).toBe(MAX_APPROVAL_WINDOW_MS);
    expect(resolveOpenFangApprovalWindowMs(String(RUNTIME_DEADLINE_CEILING_MS))).toBe(MAX_APPROVAL_WINDOW_MS);
  });
});

describe("deriveRuntimeDeadlineMs", () => {
  it("derives exactly the ceiling for a window of exactly the max", () => {
    expect(deriveRuntimeDeadlineMs(MAX_APPROVAL_WINDOW_MS)).toBe(RUNTIME_DEADLINE_CEILING_MS);
  });

  it("never exceeds the ceiling even for a window far above the max", () => {
    expect(deriveRuntimeDeadlineMs(10_000_000)).toBe(RUNTIME_DEADLINE_CEILING_MS);
  });
});

describe("resolve then derive, as di/plugins-container.ts wires them together", () => {
  it("clamps a configured window above the max, and the derived deadline still strictly exceeds the clamped window", () => {
    // The exact regression from fix round 1: OPENFANG_APPROVAL_TIMEOUT_MS=600000
    // (a plausible ten-minute workflow, well inside the old, wider guard)
    // used to derive a runtime deadline below the window handed to the
    // adapter, timing the call out with a timed_out receipt while OpenFang
    // was still polling for a decision.
    const window = resolveOpenFangApprovalWindowMs("600000");
    const deadline = deriveRuntimeDeadlineMs(window);
    expect(deadline).toBeLessThanOrEqual(RUNTIME_DEADLINE_CEILING_MS);
    expect(deadline).toBeGreaterThan(window);
  });

  it("derives a deadline strictly greater than the resolved window for every input, valid or not", () => {
    for (const raw of [
      undefined, "", "abc", "0", "-500", "1500.75",
      "1", "1000", "60000", "120000", "239999", "240000", "240001",
      "300000", "600000", "3600000", "99999999999999999999",
    ]) {
      const window = resolveOpenFangApprovalWindowMs(raw);
      const deadline = deriveRuntimeDeadlineMs(window);
      expect(deadline).toBeGreaterThan(window);
      expect(deadline).toBeLessThanOrEqual(RUNTIME_DEADLINE_CEILING_MS);
    }
  });
});
