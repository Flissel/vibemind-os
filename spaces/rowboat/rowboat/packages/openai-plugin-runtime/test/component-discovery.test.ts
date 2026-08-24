import { createHash } from "node:crypto";
import { cp, mkdtemp, readFile, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import {
  AppFileSchema,
  digestTree,
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

async function provenanceFor(root: string): Promise<SourceProvenance> {
  const manifest = await readFile(join(root, ".codex-plugin", "plugin.json"));
  return {
    sourceUrl: "https://github.com/openai/plugins.git",
    sourceCommit: "11c74d6ba24d3a6d48f54a194cd00ef3beea18f9",
    pluginName: "complete-plugin",
    pluginVersion: "1.0.0",
    manifestDigest: createHash("sha256").update(manifest).digest("hex"),
    treeDigest: await digestTree(root),
    importedAt: "2026-08-24T00:00:00.000Z",
    schemaVersion: "rowboat-openai-plugin-runtime-v1",
    policyVersion: "rowboat-plugin-policy-v1",
  };
}

describe("normalizePlugin", () => {
  it("discovers each supported surface once in deterministic order", async () => {
    const provenance = await provenanceFor(fixtureRoot);
    const first = await normalizePlugin(fixtureRoot, provenance);
    const second = await normalizePlugin(fixtureRoot, provenance);

    expect(first).toEqual(second);
    expect(first.status).toBe("available");
    expect(first.components.map(({ kind }) => kind)).toEqual([
      "skill", "skill", "agent", "command", "mcp", "mcp",
      "app", "app", "hook", "asset",
    ]);
    expect(new Set(first.components.map(({ id }) => id)).size).toBe(10);
    expect(first.components.filter(({ kind }) => kind === "skill")).toHaveLength(2);
    expect(first.components.find(({ id }) => id.includes("review"))?.metadata.path)
      .toBe("skills/review");
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

    const provenance = await provenanceFor(temporaryRoot);
    const plugin = await normalizePlugin(temporaryRoot, provenance);
    const hook = plugin.components.find(({ kind }) => kind === "hook");

    expect(hook?.status).toBe("invalid");
    expect(plugin.status).toBe("partially_available");
    expect(plugin.components.filter(({ kind }) => kind === "hook")).toHaveLength(1);
  });

  it("uses .codex-plugin/plugin.json and does not require root/plugin.json", async () => {
    const plugin = await normalizePlugin(fixtureRoot, await provenanceFor(fixtureRoot));
    expect(plugin.manifest.name).toBe("complete-plugin");
  });

  it("includes nested skill resources in the logical bundle digest", async () => {
    const temporaryRoot = await mkdtemp(join(tmpdir(), "rowboat-plugin-"));
    await cp(fixtureRoot, temporaryRoot, { recursive: true });
    const before = await normalizePlugin(temporaryRoot, await provenanceFor(temporaryRoot));
    await writeFile(
      join(temporaryRoot, "skills", "review", "references", "checklist.md"),
      "changed checklist",
      "utf8",
    );
    const after = await normalizePlugin(temporaryRoot, await provenanceFor(temporaryRoot));
    const digestFor = (plugin: typeof before): unknown =>
      plugin.components.find(({ id }) => id === "skill:skills/review")?.metadata.digest;
    expect(digestFor(after)).not.toBe(digestFor(before));
  });

  it("validates, copies, and deeply freezes provenance", async () => {
    const provenance = await provenanceFor(fixtureRoot);
    const plugin = await normalizePlugin(fixtureRoot, provenance);
    expect(Object.isFrozen(plugin.provenance)).toBe(true);
    expect(() => {
      (provenance as { pluginName: string }).pluginName = "changed";
    }).not.toThrow();
    expect(plugin.provenance.pluginName).toBe("complete-plugin");

    await expect(
      normalizePlugin(fixtureRoot, { ...provenance, manifestDigest: "0".repeat(64) }),
    ).rejects.toMatchObject({ code: "digest_mismatch" });
    await expect(
      normalizePlugin(fixtureRoot, { ...provenance, pluginVersion: "2.0.0" }),
    ).rejects.toMatchObject({ code: "source_mismatch" });
  });

  it("fails closed when the tree changes before final verification", async () => {
    const temporaryRoot = await mkdtemp(join(tmpdir(), "rowboat-plugin-"));
    await cp(fixtureRoot, temporaryRoot, { recursive: true });
    const provenance = await provenanceFor(temporaryRoot);

    await expect(
      normalizePlugin(temporaryRoot, provenance, {
        beforeFinalTreeDigest: async () => {
          await writeFile(join(temporaryRoot, "commands", "review.md"), "changed", "utf8");
        },
      }),
    ).rejects.toMatchObject({ code: "digest_mismatch" });
  });
});

describe("component schemas", () => {
  it("strictly validates app connector identifiers", () => {
    expect(
      AppFileSchema.safeParse({
        apps: {
          demo: {
            id: "asdk_app_ab12",
            category: "Productivity",
            capabilities: ["read", "write"],
          },
        },
      })
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
    const process = ProcessMcpSchema.safeParse({
        command: "node",
        args: ["server.mjs"],
        cwd: "",
        env_vars: ["MCP_TOKEN"],
        tool_timeout_sec: 5,
      });
    expect(process.success).toBe(true);
    if (process.success) expect(process.data.type).toBe("process");
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
