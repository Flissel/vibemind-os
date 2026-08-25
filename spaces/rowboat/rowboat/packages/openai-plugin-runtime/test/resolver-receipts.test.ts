import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import {
  DEFAULT_POLICY,
  ProviderRegistry,
  buildReceipt,
  resolveInstallation,
  type PluginCatalogLock,
  type PluginInstallation,
  type PluginProvider,
  type ProviderBinding,
} from "../src/index.js";

const catalog = JSON.parse(readFileSync(resolve(
  process.cwd(),
  "../../config/openai-plugin-catalog.lock.json",
), "utf8")) as PluginCatalogLock;
const box = catalog.entries.find((entry) => entry.name === "box");
if (box === undefined) throw new Error("test fixture missing pinned box entry");

const installation: PluginInstallation = Object.freeze({
  id: "installation-box",
  projectId: "project-1",
  pluginName: box.pluginName,
  pluginVersion: box.pluginVersion,
  sourceCommit: box.sourceCommit,
  manifestDigest: box.manifestDigest,
  treeDigest: box.treeDigest,
  policyVersion: box.policyVersion,
  enabled: true,
  revision: 1,
});

function installationFor(name: string): PluginInstallation {
  const entry = catalog.entries.find((candidate) => candidate.name === name);
  if (entry === undefined) throw new Error(`test fixture missing ${name}`);
  return Object.freeze({
    ...installation,
    id: `installation-${name}`,
    pluginName: entry.pluginName,
    pluginVersion: entry.pluginVersion,
    sourceCommit: entry.sourceCommit,
    manifestDigest: entry.manifestDigest,
    treeDigest: entry.treeDigest,
    policyVersion: entry.policyVersion,
  });
}

describe("installed plugin resolution", () => {
  it("resolves independent skills while an app provider is unavailable", () => {
    const resolved = resolveInstallation(installation, catalog, new ProviderRegistry(), DEFAULT_POLICY);
    expect(resolved.status).toBe("partially_available");
    expect(resolved.skills.length).toBeGreaterThan(0);
    expect(resolved.apps[0]).toMatchObject({
      status: "unavailable",
      reason: "provider_unavailable",
    });
  });

  it("rejects installation provenance mismatches against the pinned catalog", () => {
    const resolved = resolveInstallation({ ...installation, treeDigest: "0".repeat(64) }, catalog, new ProviderRegistry(), DEFAULT_POLICY);
    expect(resolved).toMatchObject({ status: "unavailable", reason: "digest_mismatch" });
    expect(resolved.components).toEqual([]);
  });

  it("resolves a provider only through its exact catalog component digest", () => {
    const app = box.components.find(({ component }) => component.kind === "app");
    if (app === undefined || typeof app.component.metadata.digest !== "string") throw new Error("box app fixture missing");
    const binding: ProviderBinding = Object.freeze({
      id: "box-app", providerKind: "rowboat-native", componentDigest: app.component.metadata.digest,
    });
    const provider: PluginProvider = Object.freeze({
      id: "box-provider",
      describe: () => Object.freeze({ id: "box-provider", kind: "rowboat-native", temporaryAdapter: false }),
      invoke: async () => Object.freeze({ status: "success", output: null }),
    });
    const registry = new ProviderRegistry();
    registry.register(binding, provider);
    const exact = resolveInstallation({
      ...installation,
      providerBindings: [{ componentId: app.component.id, binding }],
    }, catalog, registry, DEFAULT_POLICY);
    expect(exact.apps[0]).toMatchObject({ status: "available" });
    const mismatched = resolveInstallation({
      ...installation,
      providerBindings: [{ componentId: app.component.id, binding: { ...binding, componentDigest: "0".repeat(64) } }],
    }, catalog, registry, DEFAULT_POLICY);
    expect(mismatched.apps[0]).toMatchObject({ status: "unavailable", reason: "provider_unavailable" });
  });

  it("rejects own registry method shadows without invoking accessors or forging availability", () => {
    const app = box.components.find(({ component }) => component.kind === "app");
    if (app === undefined || typeof app.component.metadata.digest !== "string") throw new Error("box app fixture missing");
    const binding: ProviderBinding = Object.freeze({
      id: "box-shadow", providerKind: "rowboat-native", componentDigest: app.component.metadata.digest,
    });
    const provider: PluginProvider = Object.freeze({
      id: "box-shadow-provider",
      describe: () => Object.freeze({ id: "box-shadow-provider", kind: "rowboat-native", temporaryAdapter: false }),
      invoke: async () => Object.freeze({ status: "success", output: null }),
    });
    const boundInstallation = { ...installation, providerBindings: [{ componentId: app.component.id, binding }] };
    let getterCalls = 0;
    const accessorRegistry = new ProviderRegistry();
    accessorRegistry.register(binding, provider);
    Object.defineProperty(accessorRegistry, "resolve", {
      enumerable: true,
      get() {
        getterCalls += 1;
        return () => Object.freeze({ status: "available", provider });
      },
    });
    const dataRegistry = new ProviderRegistry();
    dataRegistry.register(binding, provider);
    Object.defineProperty(dataRegistry, "resolve", {
      enumerable: true,
      value: () => Object.freeze({ status: "available", provider }),
    });
    expect(resolveInstallation(boundInstallation, catalog, accessorRegistry, DEFAULT_POLICY)).toMatchObject({
      status: "unavailable", reason: "source_mismatch", components: [],
    });
    expect(resolveInstallation(boundInstallation, catalog, dataRegistry, DEFAULT_POLICY)).toMatchObject({
      status: "unavailable", reason: "source_mismatch", components: [],
    });
    const forgedRegistry = Object.create(ProviderRegistry.prototype) as ProviderRegistry;
    expect(resolveInstallation(boundInstallation, catalog, forgedRegistry, DEFAULT_POLICY)).toMatchObject({
      status: "unavailable", reason: "source_mismatch", components: [],
    });
    expect(getterCalls).toBe(0);
  });

  it("evaluates plugin license admission before resolving components", () => {
    const resolved = resolveInstallation(installationFor("convex"), catalog, new ProviderRegistry(), DEFAULT_POLICY);
    expect(resolved).toMatchObject({ status: "unavailable", reason: "license_rejected" });
    expect(resolved.components).toEqual([]);
  });

  it("rejects a caller-rehashed catalog instead of trusting self-consistency", () => {
    const forged = JSON.parse(JSON.stringify(catalog)) as Record<string, unknown>;
    const entries = forged.entries as Array<Record<string, unknown>>;
    entries[0] = { ...entries[0], pluginVersion: "forged" };
    const { catalogDigest: ignored, ...payload } = forged;
    void ignored;
    const canonical = (value: unknown): unknown => Array.isArray(value)
      ? value.map(canonical)
      : value !== null && typeof value === "object"
        ? Object.fromEntries(Object.entries(value).sort(([a], [b]) => a < b ? -1 : a > b ? 1 : 0).map(([key, child]) => [key, canonical(child)]))
        : value;
    forged.catalogDigest = createHash("sha256").update(JSON.stringify(canonical(payload))).digest("hex");
    const resolved = resolveInstallation(installation, forged, new ProviderRegistry(), DEFAULT_POLICY);
    expect(resolved).toMatchObject({ status: "unavailable", reason: "source_mismatch" });
  });

  it("rejects accessors and proxies without invoking them", () => {
    let getterCalls = 0;
    const malicious = { ...installation } as Record<string, unknown>;
    Object.defineProperty(malicious, "pluginName", { enumerable: true, get() { getterCalls += 1; return "box"; } });
    expect(resolveInstallation(malicious, catalog, new ProviderRegistry(), DEFAULT_POLICY).reason).toBe("source_mismatch");
    expect(resolveInstallation(new Proxy(installation, {}), catalog, new ProviderRegistry(), DEFAULT_POLICY).reason).toBe("source_mismatch");
    expect(getterCalls).toBe(0);
  });

  it("returns deterministic deeply immutable kind/name-sorted structures", () => {
    const resolved = resolveInstallation(installation, catalog, new ProviderRegistry(), DEFAULT_POLICY);
    expect(resolved.components.map(({ kind, name }) => `${kind}:${name}`)).toEqual(
      [...resolved.components].sort((left, right) => left.kind < right.kind ? -1 : left.kind > right.kind ? 1 : left.name.localeCompare(right.name)).map(({ kind, name }) => `${kind}:${name}`),
    );
    expect(Object.isFrozen(resolved)).toBe(true);
    expect(Object.isFrozen(resolved.components)).toBe(true);
    expect(Object.isFrozen(resolved.components[0])).toBe(true);
  });
});

describe("secret-free receipts", () => {
  it("omits secret values from receipts", () => {
    const receipt = buildReceipt({
      type: "execution",
      receiptId: "receipt-1",
      projectId: "project-1",
      pluginName: "box",
      status: "failed",
      credential: { value: "super-secret-value" },
    }, ["credential.value"]);
    expect(JSON.stringify(receipt)).not.toContain("super-secret-value");
    expect(receipt.redactions).toContain("credential.value");
  });

  it("redacts nested secret-bearing keys before hashing exact canonical output bytes", () => {
    const event = {
      type: "execution", receiptId: "receipt-2", projectId: "project-1", pluginName: "box", status: "success",
      output: { z: "visible", nested: { token: "super-secret-value", a: "😀" } },
    };
    const receipt = buildReceipt(event, [], { maxOutputBytes: 1 });
    const canonicalRedacted = '{"nested":{"a":"😀","token":"[REDACTED]"},"z":"visible"}';
    expect(receipt.output).toEqual({
      truncated: true,
      digest: createHash("sha256").update(Buffer.from(canonicalRedacted, "utf8")).digest("hex"),
    });
    expect(JSON.stringify(receipt)).not.toContain("super-secret-value");
    expect(receipt.redactions).toContain("output.nested.token");
  });

  it("redacts common mixed-case auth and key variants in nested arrays before canonical hashing", () => {
    const secrets = {
      API_KEY: "raw-api-upper",
      accessToken: "raw-access",
      "api-key": "raw-api-kebab",
      apiKey: "raw-api-camel",
      auth: "raw-auth",
      authorization: "raw-authorization",
      benign: { authenticationMode: "oauth", monkey: "banana", tokenized: "public" },
      clientSecret: "raw-client",
      privateKey: "raw-private-camel",
      private_key: "raw-private-snake",
      refreshToken: "raw-refresh",
    };
    const base = { type: "execution", receiptId: "receipt-variants", projectId: "project-1", pluginName: "box", status: "success" };
    const receipt = buildReceipt({ ...base, output: { records: [secrets] } }, [], { maxOutputBytes: 1 });
    const redacted = "[REDACTED]";
    const expectedCanonical = JSON.stringify({ records: [{
      API_KEY: redacted,
      accessToken: redacted,
      "api-key": redacted,
      apiKey: redacted,
      auth: redacted,
      authorization: redacted,
      benign: { authenticationMode: "oauth", monkey: "banana", tokenized: "public" },
      clientSecret: redacted,
      privateKey: redacted,
      private_key: redacted,
      refreshToken: redacted,
    }] });
    expect(receipt.output).toEqual({
      truncated: true,
      digest: createHash("sha256").update(Buffer.from(expectedCanonical, "utf8")).digest("hex"),
    });
    const serialized = JSON.stringify(receipt);
    for (const value of Object.values(secrets).filter((candidate): candidate is string => typeof candidate === "string")) {
      expect(serialized).not.toContain(value);
    }
    expect(receipt.redactions).toEqual([
      "output.records.0.API_KEY",
      "output.records.0.accessToken",
      "output.records.0.api-key",
      "output.records.0.apiKey",
      "output.records.0.auth",
      "output.records.0.authorization",
      "output.records.0.clientSecret",
      "output.records.0.privateKey",
      "output.records.0.private_key",
      "output.records.0.refreshToken",
    ]);
    const retained = buildReceipt({ ...base, receiptId: "receipt-benign", output: secrets.benign });
    expect(retained.output).toEqual({ authenticationMode: "oauth", monkey: "banana", tokenized: "public" });
  });

  it("produces the same truncated digest across object insertion order", () => {
    const base = { type: "execution", receiptId: "receipt-3", projectId: "project-1", pluginName: "box", status: "success" };
    const first = buildReceipt({ ...base, output: { b: 2, a: 1 } }, [], { maxOutputBytes: 1 });
    const second = buildReceipt({ ...base, output: { a: 1, b: 2 } }, [], { maxOutputBytes: 1 });
    expect(first.output).toEqual(second.output);
  });

  it("never invokes toJSON or accessors and rejects sparse arrays", () => {
    let calls = 0;
    const output = Object.defineProperty({ toJSON() { calls += 1; return "secret"; } }, "hidden", {
      enumerable: true, get() { calls += 1; return "secret"; },
    });
    const base = { type: "execution", receiptId: "receipt-4", projectId: "project-1", pluginName: "box", status: "failed" };
    expect(() => buildReceipt({ ...base, output })).toThrow("receipt_invalid");
    const sparse = new Array(2); sparse[1] = "value";
    expect(() => buildReceipt({ ...base, output: sparse })).toThrow("receipt_invalid");
    expect(calls).toBe(0);
  });

  it("rejects prototype-risk and ambiguous sensitive paths", () => {
    const base = { type: "execution", receiptId: "receipt-5", projectId: "project-1", pluginName: "box", status: "failed" };
    expect(() => buildReceipt(base, ["output.__proto__.token"])).toThrow("receipt_invalid");
    expect(() => buildReceipt(base, ["output.*.token"])).toThrow("receipt_invalid");
    const receipt = buildReceipt({ ...base, output: { token: "secret", tokenized: "public" } }, ["output.token"]);
    expect(receipt.redactions).toContain("output.token");
    expect(JSON.stringify(receipt)).toContain("public");
  });

  it("enforces global receipt bounds and deeply freezes retained output", () => {
    const base = { type: "execution", receiptId: "receipt-6", projectId: "project-1", pluginName: "box", status: "success" };
    expect(() => buildReceipt({ ...base, output: { value: "éé" } }, [], { maxStringBytes: 3 })).toThrow("receipt_invalid");
    const receipt = buildReceipt({ ...base, output: { nested: ["ok"] } });
    expect(Object.isFrozen(receipt)).toBe(true);
    expect(Object.isFrozen(receipt.redactions)).toBe(true);
    expect(Object.isFrozen(receipt.output)).toBe(true);
    expect(Object.isFrozen((receipt.output as { nested: readonly string[] }).nested)).toBe(true);
  });

  it("captures limit and path inputs without invoking accessors", () => {
    let calls = 0;
    const base = { type: "execution", receiptId: "receipt-7", projectId: "project-1", pluginName: "box", status: "success" };
    const limits = Object.defineProperty({}, "maxOutputBytes", { enumerable: true, get() { calls += 1; return 1; } });
    expect(() => buildReceipt(base, [], limits)).toThrow("receipt_invalid");
    const paths = ["output.token"];
    Object.defineProperty(paths, "extra", { enumerable: true, value: "output.secret" });
    expect(() => buildReceipt(base, paths)).toThrow("receipt_invalid");
    expect(calls).toBe(0);
  });
});
