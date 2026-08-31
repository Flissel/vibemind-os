/**
 * Completion evidence for the design requirements R1 to R15.
 *
 * A requirement counts as verified only when every test bound to it is present
 * in a recorded run *and* passed there. The presence of source code, of a route
 * file, or of a skipped test is never evidence: a skip is reported as unrun,
 * exactly like a missing test.
 */

export type RequirementId =
  | "R1" | "R2" | "R3" | "R4" | "R5" | "R6" | "R7" | "R8"
  | "R9" | "R10" | "R11" | "R12" | "R13" | "R14" | "R15";

export type EvidenceStatus = "passed" | "failed" | "skipped";

export interface EvidenceTest {
  readonly name: string;
  readonly status: EvidenceStatus;
}

export interface EvidenceRun {
  readonly source: string;
  readonly tests: readonly EvidenceTest[];
}

export interface RequirementEvidence {
  readonly status: "verified" | "unverified";
  readonly required: readonly string[];
  readonly missing: readonly string[];
}

/**
 * Requirement to test binding. Each entry names tests that directly exercise
 * the requirement; the names must match the recorded run exactly, so renaming a
 * test breaks the evidence instead of silently weakening it.
 */
export const REQUIREMENT_EVIDENCE: Readonly<Record<RequirementId, readonly string[]>> = Object.freeze({
  R1: Object.freeze([
    "importCatalog counts conventional agent, command, and hook surfaces",
    "exact plugin catalog lock validation rejects every execution, admission, license, inventory, and cardinality mutation even after rehash",
  ]),
  R2: Object.freeze([
    "verified snapshot preflight times out a bounded internal Git subprocess without exposing its output",
    "plugin repository contract atomically stores and reconstructs only the exact validated 180-entry catalog",
  ]),
  R3: Object.freeze([
    "plugin admission policy classifies MIT as admitted",
    "plugin admission policy classifies UNLICENSED as rejected",
  ]),
  R4: Object.freeze([
    "secret-free receipts redacts nested secret-bearing keys before hashing exact canonical output bytes",
    "legacy plugin migration recipes never serializes credentials, raw tool configuration, or secret-shaped values",
    "write-safe plugin shadow parity persists redacted receipt evidence that carries no argument or result values",
    "OpenFang write release never sends argument values, only their digest",
    "PluginToolRuntime runs a write that OpenFang released and records the approval",
  ]),
  R5: Object.freeze([
    "instruction component normalizers loads complete skill instructions and binds every directly referenced local resource",
    "instruction component normalizers rejects a skill resource that leaves the plugin root",
  ]),
  R6: Object.freeze([
    "normalizePlugin binds adjacent YAML and template resources into logical agent and command digests",
  ]),
  R7: Object.freeze([
    "importCatalog counts conventional agent, command, and hook surfaces",
    "normalizePlugin binds adjacent YAML and template resources into logical agent and command digests",
  ]),
  R8: Object.freeze([
    "process MCP provider rejects accessor-backed execution-root provenance without invoking accessors",
    "process MCP provider bounds and redacts output while retaining full-output digests",
    "plugin operation classification classifies an operation the component declares read-only as read",
    "plugin operation classification classifies everything else as write",
    "plugin operation classification ignores an unreadable declaration rather than trusting it",
    "plugin operation classification requires all entries to be strings, rejecting mixed-type arrays even if the operation is present",
  ]),
  R9: Object.freeze([
    "component provider normalizers normalizes an app only to its exact connector ID",
    "component schemas strictly validates app connector identifiers",
  ]),
  R10: Object.freeze([
    "policy-controlled hook execution does not spawn a command hook without admission",
    "hook event and matcher capture rejects accessor and prototype event tricks without invoking them",
  ]),
  R11: Object.freeze([
    "asset normalizer rejects active asset MIME image/svg+xml",
    "asset normalizer admits bounded raster and plain-text assets with contained paths and digests",
  ]),
  R12: Object.freeze([
    "plugin catalog view state renders the server-owned available state",
    "signed plugin preview authority accepts only a signed server preview envelope at install",
    "PluginToolRuntime builds a plugin tool that delegates exclusively to the plugin runtime",
  ]),
  R13: Object.freeze([
    "legacy plugin migration recipes defines exactly the ten checked-in cards from their real tool configuration",
    "plugin migration apply returns the strict machine report and exact replay provenance (replayed=false)",
  ]),
  R14: Object.freeze([
    "test_existing_status_contract_is_unchanged",
    "test_existing_chat_contract_does_not_require_plugin_fields",
    "test_plugin_api_is_versioned_and_fail_closed",
  ]),
  R15: Object.freeze([
    "reversible plugin runtime cutover permits shadow -> openai: true",
    "reversible plugin runtime cutover permits openai -> legacy: true",
    "reversible plugin runtime cutover rejects cutover when parity or rollback evidence is missing",
    "write-safe plugin shadow parity compares write-capable descriptors without executing the shadow provider",
    "legacy plugin removal gate blocks removal when rollback retention has not elapsed",
  ]),
});

export const REQUIREMENT_IDS: readonly RequirementId[] = Object.freeze(Object.keys(REQUIREMENT_EVIDENCE) as RequirementId[]);

function evidenceInvalid(): never {
  throw new Error("completion_evidence_invalid");
}

/**
 * Reads a vitest JSON report. Only an explicitly passed assertion counts;
 * anything else is carried through with its real status.
 */
export function vitestEvidence(source: string, report: unknown): EvidenceRun {
  if (report === null || typeof report !== "object") evidenceInvalid();
  const files = (report as { testResults?: unknown }).testResults;
  if (!Array.isArray(files)) evidenceInvalid();
  const tests: EvidenceTest[] = [];
  for (const file of files) {
    if (file === null || typeof file !== "object") evidenceInvalid();
    const assertions = (file as { assertionResults?: unknown }).assertionResults;
    if (!Array.isArray(assertions)) evidenceInvalid();
    for (const assertion of assertions) {
      if (assertion === null || typeof assertion !== "object") evidenceInvalid();
      const name = (assertion as { fullName?: unknown }).fullName;
      const status = (assertion as { status?: unknown }).status;
      if (typeof name !== "string" || typeof status !== "string") evidenceInvalid();
      tests.push(Object.freeze({ name, status: status === "passed" ? "passed" : status === "failed" ? "failed" : "skipped" }));
    }
  }
  return Object.freeze({ source, tests: Object.freeze(tests) });
}

const TESTCASE = /<testcase\b([^>]*?)(\/>|>([\s\S]*?)<\/testcase>)/g;
const NAME_ATTRIBUTE = /\bname="([^"]*)"/;

/**
 * Reads a JUnit XML report, which is how the Python space contract run is
 * recorded. A testcase carrying a failure, error, or skipped child is not a
 * pass.
 */
export function junitEvidence(source: string, xml: string): EvidenceRun {
  if (typeof xml !== "string") evidenceInvalid();
  const tests: EvidenceTest[] = [];
  for (const match of xml.matchAll(TESTCASE)) {
    const attributes = match[1] ?? "";
    const body = match[3] ?? "";
    const name = NAME_ATTRIBUTE.exec(attributes)?.[1];
    if (name === undefined) evidenceInvalid();
    const status: EvidenceStatus = /<(failure|error)\b/.test(body) ? "failed" : /<skipped\b/.test(body) ? "skipped" : "passed";
    tests.push(Object.freeze({ name, status }));
  }
  return Object.freeze({ source, tests: Object.freeze(tests) });
}

export interface PluginRuntimeCompletionDependencies {
  readonly loadRuns: () => Promise<readonly EvidenceRun[]>;
}

export class PluginRuntimeCompletionVerifier {
  readonly #dependencies: PluginRuntimeCompletionDependencies;

  constructor(dependencies: PluginRuntimeCompletionDependencies) {
    this.#dependencies = dependencies;
  }

  async collect(): Promise<Readonly<Record<RequirementId, RequirementEvidence>>> {
    const runs = await this.#dependencies.loadRuns();
    if (!Array.isArray(runs)) evidenceInvalid();
    const passed = new Set<string>();
    for (const run of runs) {
      if (run === null || typeof run !== "object" || !Array.isArray(run.tests)) evidenceInvalid();
      for (const test of run.tests) if (test.status === "passed") passed.add(test.name);
    }
    const evidence: Record<string, RequirementEvidence> = {};
    for (const requirement of REQUIREMENT_IDS) {
      const required = REQUIREMENT_EVIDENCE[requirement];
      const missing = required.filter(name => !passed.has(name));
      evidence[requirement] = Object.freeze({ status: missing.length === 0 ? "verified" : "unverified", required, missing: Object.freeze(missing) });
    }
    return Object.freeze(evidence) as Readonly<Record<RequirementId, RequirementEvidence>>;
  }
}
