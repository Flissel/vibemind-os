import { cp, mkdtemp, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import {
  AppFileSchema,
  HttpMcpSchema,
  normalizePlugin,
  ProcessMcpSchema,
  type SourceProvenance,
} from "../src/index.js";

const fixtureRoot = join(
  dirname(fileURLToPath(import.meta.url)),
  "fixtures",
  "complete-plugin",
);

const provenance: SourceProvenance = {
  sourceUrl: "https://github.com/openai/plugins.git",
  sourceCommit: "11c74d6ba24d3a6d48f54a194cd00ef3beea18f9",
  pluginName: "complete-plugin",
  pluginVersion: "1.0.0",
  manifestDigest: "0".repeat(64),
  treeDigest: "1".repeat(64),
  importedAt: "2026-08-24T00:00:00.000Z",
  schemaVersion: "rowboat-openai-plugin-runtime-v1",
  policyVersion: "rowboat-plugin-policy-v1",
};

describe("normalizePlugin", () => {
  it("discovers each supported surface once in deterministic order", async () => {
    const first = await normalizePlugin(fixtureRoot, provenance);
    const second = await normalizePlugin(fixtureRoot, provenance);

    expect(first).toEqual(second);
    expect(first.status).toBe("available");
    expect(first.components.map(({ kind }) => kind)).toEqual([
      "skill",
      "agent",
      "command",
      "mcp",
      "app",
      "hook",
      "asset",
    ]);
    expect(new Set(first.components.map(({ id }) => id)).size).toBe(7);
    expect(
      first.components.every(
        ({ metadata }) =>
          typeof metadata.digest === "string" &&
          /^[a-f0-9]{64}$/.test(metadata.digest),
      ),
    ).toBe(true);
  });

  it("marks a malformed optional component invalid and the plugin partial", async () => {
    const temporaryRoot = await mkdtemp(join(tmpdir(), "rowboat-plugin-"));
    await cp(fixtureRoot, temporaryRoot, { recursive: true });
    await writeFile(join(temporaryRoot, "hooks", "hooks.json"), "not json", "utf8");

    const plugin = await normalizePlugin(temporaryRoot, provenance);
    const hook = plugin.components.find(({ kind }) => kind === "hook");

    expect(hook?.status).toBe("invalid");
    expect(plugin.status).toBe("partially_available");
    expect(plugin.components.filter(({ kind }) => kind === "hook")).toHaveLength(1);
  });
});

describe("component schemas", () => {
  it("strictly validates app connector identifiers", () => {
    expect(
      AppFileSchema.safeParse({ apps: { demo: { id: "connector_ab12" } } })
        .success,
    ).toBe(true);
    expect(
      AppFileSchema.safeParse({
        apps: { demo: { id: "connector_AB12", token: "secret" } },
      }).success,
    ).toBe(false);
  });

  it("strictly validates HTTP and process MCP declarations", () => {
    expect(
      HttpMcpSchema.safeParse({
        type: "http",
        url: "https://example.com/mcp",
        bearer_token_env_var: "MCP_TOKEN",
      }).success,
    ).toBe(true);
    expect(
      HttpMcpSchema.safeParse({
        type: "http",
        url: "not-a-url",
        bearer_token_env_var: "mcp_token",
      }).success,
    ).toBe(false);
    expect(
      ProcessMcpSchema.safeParse({
        type: "process",
        command: "node",
        args: ["server.mjs"],
        cwd: "",
        env_vars: ["MCP_TOKEN"],
        tool_timeout_sec: 5,
      }).success,
    ).toBe(true);
    expect(
      ProcessMcpSchema.safeParse({
        type: "process",
        command: "",
        tool_timeout_sec: 0,
        extra: true,
      }).success,
    ).toBe(false);
  });
});
