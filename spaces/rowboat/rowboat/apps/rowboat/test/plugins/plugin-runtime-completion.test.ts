import { readFile } from "node:fs/promises";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import {
  junitEvidence,
  PluginRuntimeCompletionVerifier,
  REQUIREMENT_EVIDENCE,
  REQUIREMENT_IDS,
  vitestEvidence,
  type EvidenceRun,
  type RequirementId,
} from "@/src/application/services/plugin-runtime-completion";

const ARTIFACTS = join(process.cwd(), ".artifacts");
const APP_REPORT = join(ARTIFACTS, "plugin-app-tests.json");
const RUNTIME_REPORT = join(process.cwd(), "..", "..", "packages", "openai-plugin-runtime", ".artifacts", "plugin-runtime-tests.json");
const SPACE_REPORT = join(ARTIFACTS, "plugin-space-contract.xml");

const allRequirementNames = REQUIREMENT_IDS.flatMap(requirement => REQUIREMENT_EVIDENCE[requirement]);
const syntheticRun = (overrides: Readonly<Record<string, "failed" | "skipped">> = {}): EvidenceRun => Object.freeze({
  source: "synthetic",
  tests: Object.freeze([...new Set(allRequirementNames)].map(name => Object.freeze({ name, status: overrides[name] ?? "passed" as const }))),
});

async function readOptional(path: string): Promise<string | null> {
  try {
    return await readFile(path, "utf8");
  } catch {
    return null;
  }
}

describe("plugin runtime completion evidence", () => {
  it("covers exactly the fifteen design requirements", () => {
    expect([...REQUIREMENT_IDS].sort()).toEqual(Array.from({ length: 15 }, (_, index) => `R${index + 1}`).sort());
    expect(REQUIREMENT_IDS.every(requirement => REQUIREMENT_EVIDENCE[requirement].length > 0)).toBe(true);
  });

  it("verifies a requirement only from a recorded passing run", async () => {
    const verifier = new PluginRuntimeCompletionVerifier({ loadRuns: async () => [syntheticRun()] });
    const evidence = await verifier.collect();
    expect(Object.keys(evidence).sort()).toEqual(Array.from({ length: 15 }, (_, index) => `R${index + 1}`).sort());
    expect(Object.values(evidence).every(item => item.status === "verified")).toBe(true);
  });

  it("treats a missing, failed, or skipped test as unverified", async () => {
    const target = REQUIREMENT_EVIDENCE.R15[0]!;
    const missing = await new PluginRuntimeCompletionVerifier({
      loadRuns: async () => [Object.freeze({ source: "synthetic", tests: syntheticRun().tests.filter(test => test.name !== target) })],
    }).collect();
    expect(missing.R15).toMatchObject({ status: "unverified", missing: [target] });

    const failed = await new PluginRuntimeCompletionVerifier({ loadRuns: async () => [syntheticRun({ [target]: "failed" })] }).collect();
    expect(failed.R15.status).toBe("unverified");

    const skipped = await new PluginRuntimeCompletionVerifier({ loadRuns: async () => [syntheticRun({ [target]: "skipped" })] }).collect();
    expect(skipped.R15.status).toBe("unverified");
    expect(skipped.R15.missing).toEqual([target]);
  });

  it("reports every requirement as unverified without any recorded run", async () => {
    const evidence = await new PluginRuntimeCompletionVerifier({ loadRuns: async () => [] }).collect();
    expect(Object.values(evidence).every(item => item.status === "unverified")).toBe(true);
  });

  it("reads vitest and JUnit reports without treating a skip as a pass", () => {
    const vitest = vitestEvidence("vitest", {
      testResults: [{ assertionResults: [{ fullName: "a passing test", status: "passed" }, { fullName: "a skipped test", status: "pending" }] }],
    });
    expect(vitest.tests).toEqual([{ name: "a passing test", status: "passed" }, { name: "a skipped test", status: "skipped" }]);

    const junit = junitEvidence("pytest", [
      '<testsuites><testsuite name="pytest">',
      '<testcase classname="c" name="test_passed"/>',
      '<testcase classname="c" name="test_skipped"><skipped type="pytest.skip">no service</skipped></testcase>',
      '<testcase classname="c" name="test_failed"><failure message="boom">trace</failure></testcase>',
      "</testsuite></testsuites>",
    ].join(""));
    expect(junit.tests).toEqual([
      { name: "test_passed", status: "passed" },
      { name: "test_skipped", status: "skipped" },
      { name: "test_failed", status: "failed" },
    ]);
  });

  it("rejects a malformed report instead of reading it as evidence", () => {
    expect(() => vitestEvidence("vitest", {})).toThrow("completion_evidence_invalid");
    expect(() => vitestEvidence("vitest", { testResults: [{ assertionResults: [{ fullName: 1, status: "passed" }] }] })).toThrow("completion_evidence_invalid");
  });

  it("collects current direct evidence for every design requirement from the recorded runs", async ({ skip }) => {
    const [appReport, runtimeReport, spaceReport] = await Promise.all([readOptional(APP_REPORT), readOptional(RUNTIME_REPORT), readOptional(SPACE_REPORT)]);
    const absent = ([["apps/rowboat", appReport], ["packages/openai-plugin-runtime", runtimeReport], ["spaces/rowboat/tests", spaceReport]] as const)
      .filter(([, report]) => report === null).map(([source]) => source);
    if (absent.length === 3) {
      // Nothing recorded at all: the gate is unrun, and an unrun gate is
      // reported as skipped rather than counted as evidence. Produce the
      // artifacts with `npm run plugins:evidence` from apps/rowboat.
      skip("no recorded evidence run; produce it with npm run plugins:evidence");
      return;
    }
    // A partial set cannot verify anything and must not look like a pass.
    expect(absent, "recorded evidence run is incomplete").toEqual([]);
    if (appReport === null || runtimeReport === null || spaceReport === null) return;
    const verifier = new PluginRuntimeCompletionVerifier({
      loadRuns: async () => [
        vitestEvidence("apps/rowboat", JSON.parse(appReport) as unknown),
        vitestEvidence("packages/openai-plugin-runtime", JSON.parse(runtimeReport) as unknown),
        junitEvidence("spaces/rowboat/tests", spaceReport),
      ],
    });
    const evidence = await verifier.collect();
    const unverified = (Object.entries(evidence) as [RequirementId, { status: string; missing: readonly string[] }][])
      .filter(([, item]) => item.status !== "verified")
      .map(([requirement, item]) => `${requirement}: ${item.missing.join(" | ")}`);
    expect(unverified).toEqual([]);
  });
});
