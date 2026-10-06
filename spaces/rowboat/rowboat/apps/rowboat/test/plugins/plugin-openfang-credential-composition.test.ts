import { describe, expect, it } from "vitest";
import { resolveOpenFangCredentialSource, resolveOpenFangCredentialTimeoutMs } from "@/di/plugins-container";

// Mirrors the constants documented alongside the implementation and in
// docs/openai-plugin-runtime-operations.md.
const MIN_CREDENTIAL_TIMEOUT_MS = 100;
const MAX_CREDENTIAL_TIMEOUT_MS = 30_000;
const DEFAULT_CREDENTIAL_TIMEOUT_MS = 5_000;

describe("resolveOpenFangCredentialTimeoutMs", () => {
  it("falls back to the default for input that is not a valid whole-millisecond duration", () => {
    for (const raw of [
      undefined, "", "abc", "-500", "1500.75", "1e5", " 5000 ", "5000ms",
      // Beyond Number.MAX_SAFE_INTEGER: syntactically all digits, but not a
      // safe integer once parsed, so this must not silently clamp either.
      "99999999999999999999",
    ]) {
      expect(resolveOpenFangCredentialTimeoutMs(raw)).toBe(DEFAULT_CREDENTIAL_TIMEOUT_MS);
    }
  });

  it("falls back to the default -- not up to the floor -- for a duration below the floor", () => {
    // "0" and values just under the floor must not silently clamp UP to the
    // floor: that would still let OPENFANG_CREDENTIAL_TIMEOUT_MS=0 (or any
    // near-zero misconfiguration) produce a working, just-barely-bounded
    // resolver instead of the documented default.
    for (const raw of ["0", "1", "50", "99"]) {
      expect(resolveOpenFangCredentialTimeoutMs(raw)).toBe(DEFAULT_CREDENTIAL_TIMEOUT_MS);
    }
  });

  it("passes an in-range duration through unchanged", () => {
    expect(resolveOpenFangCredentialTimeoutMs("1000")).toBe(1_000);
    expect(resolveOpenFangCredentialTimeoutMs("15000")).toBe(15_000);
  });

  it("passes a duration of exactly the floor or exactly the ceiling through unchanged", () => {
    expect(resolveOpenFangCredentialTimeoutMs(String(MIN_CREDENTIAL_TIMEOUT_MS))).toBe(MIN_CREDENTIAL_TIMEOUT_MS);
    expect(resolveOpenFangCredentialTimeoutMs(String(MAX_CREDENTIAL_TIMEOUT_MS))).toBe(MAX_CREDENTIAL_TIMEOUT_MS);
  });

  it("clamps a duration above the ceiling down to the ceiling, rather than dropping to the default", () => {
    expect(resolveOpenFangCredentialTimeoutMs("600000")).toBe(MAX_CREDENTIAL_TIMEOUT_MS);
    expect(resolveOpenFangCredentialTimeoutMs(String(MAX_CREDENTIAL_TIMEOUT_MS + 1))).toBe(MAX_CREDENTIAL_TIMEOUT_MS);
  });
});

describe("resolveOpenFangCredentialSource", () => {
  const secureUrl = "https://openfang.example.com";
  const apiKey = "test-openfang-key";
  const issueKey = "test-issue-key";

  it("authorizes OpenFang only when url, api key AND issue key are set", () => {
    expect(resolveOpenFangCredentialSource("https://pc.ts.net", "k", "")).toBeUndefined();
    expect(resolveOpenFangCredentialSource("https://pc.ts.net", "k", undefined)).toBeUndefined();
    expect(resolveOpenFangCredentialSource("https://pc.ts.net", "k", "   ")).toBeUndefined();
    expect(resolveOpenFangCredentialSource("https://pc.ts.net", "k", "ik")).toEqual({ baseUrl: "https://pc.ts.net", apiKey: "k", issueKey: "ik" });
  });

  it("is undefined when either variable is missing, empty, or whitespace-only", () => {
    expect(resolveOpenFangCredentialSource(undefined, undefined, issueKey)).toBeUndefined();
    expect(resolveOpenFangCredentialSource("", "", issueKey)).toBeUndefined();
    expect(resolveOpenFangCredentialSource("   ", "   ", issueKey)).toBeUndefined();
    expect(resolveOpenFangCredentialSource(secureUrl, undefined, issueKey)).toBeUndefined();
    expect(resolveOpenFangCredentialSource(undefined, apiKey, issueKey)).toBeUndefined();
    expect(resolveOpenFangCredentialSource(secureUrl, "", issueKey)).toBeUndefined();
    expect(resolveOpenFangCredentialSource("", apiKey, issueKey)).toBeUndefined();
    expect(resolveOpenFangCredentialSource(secureUrl, "   ", issueKey)).toBeUndefined();
    expect(resolveOpenFangCredentialSource("   ", apiKey, issueKey)).toBeUndefined();
  });

  it("is defined, with both values trimmed, when both are present, non-blank, and the URL is secure", () => {
    expect(resolveOpenFangCredentialSource(secureUrl, apiKey, issueKey)).toEqual({ baseUrl: secureUrl, apiKey, issueKey });
    expect(resolveOpenFangCredentialSource(`  ${secureUrl}  `, `  ${apiKey}  `, issueKey)).toEqual({ baseUrl: secureUrl, apiKey, issueKey });
  });

  it("does not construct a resolver from a trailing-space-only value that would send a blank bearer token", () => {
    // A `.env` typo like OPENFANG_API_KEY=" " must fail closed here, not
    // reach the wire as `Authorization: Bearer  `.
    expect(resolveOpenFangCredentialSource(secureUrl, " ", issueKey)).toBeUndefined();
    expect(resolveOpenFangCredentialSource(" ", apiKey, issueKey)).toBeUndefined();
  });

  it("is undefined for a URL that is neither https nor loopback, even with a well-formed key", () => {
    for (const insecure of [
      "http://openfang.example.com",
      "http://evil.example.com:4200",
      "ftp://127.0.0.1",
      "not-a-url",
      "",
    ]) {
      expect(resolveOpenFangCredentialSource(insecure, apiKey, issueKey)).toBeUndefined();
    }
  });

  it("is defined for https, and for http restricted to a loopback host", () => {
    for (const secure of [
      "https://openfang.example.com",
      "https://127.0.0.1:4200",
      "http://127.0.0.1:4200",
      "http://localhost:4200",
      "http://[::1]:4200",
    ]) {
      expect(resolveOpenFangCredentialSource(secure, apiKey, issueKey)).toEqual({ baseUrl: secure, apiKey, issueKey });
    }
  });

  it("is undefined for http to a hostname that merely contains a loopback-looking substring", () => {
    // Guards against a naive .includes()/.startsWith() implementation: only
    // an exact loopback hostname match is accepted.
    for (const spoofed of [
      "http://127.0.0.1.evil.example.com",
      "http://notlocalhost.example.com",
      "http://evil.example.com/127.0.0.1",
    ]) {
      expect(resolveOpenFangCredentialSource(spoofed, apiKey, issueKey)).toBeUndefined();
    }
  });
});
