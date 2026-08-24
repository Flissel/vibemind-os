import { execFile } from "node:child_process";
import { createHash } from "node:crypto";
import { cp, mkdir, mkdtemp, readFile, rename, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";
import { describe, expect, it } from "vitest";
import {
  AppFileSchema,
  assertPinnedSource,
  discoverPluginComponents,
  getVerifiedPluginDigests,
  HttpMcpSchema,
  McpFileSchema,
  normalizePlugin,
  parsePluginManifest,
  ProcessMcpSchema,
  type SourceProvenance,
  type VerifiedPinnedSource,
} from "../src/index.js";

const fixtureRoot = join(
  dirname(fileURLToPath(import.meta.url)),
  "fixtures",
  "complete-plugin",
);

const execFileAsync = promisify(execFile);
const SOURCE_URL = "https://github.com/openai/plugins.git";

async function git(repositoryRoot: string, ...args: readonly string[]): Promise<string> {
  const result = await execFileAsync("git", ["-C", repositoryRoot, ...args], {
    encoding: "utf8",
    windowsHide: true,
  });
  return result.stdout.trim();
}

async function provenanceFor(
  root: string,
  sourceCommit: string,
  context?: VerifiedPinnedSource,
): Promise<SourceProvenance> {
  const digests = await getVerifiedPluginDigests(context ?? await verifiedFor(root), root);
  return {
    sourceUrl: SOURCE_URL,
    sourceCommit,
    pluginName: "complete-plugin",
    pluginVersion: "1.0.0",
    manifestDigest: digests.manifestDigest,
    treeDigest: digests.treeDigest,
    importedAt: "2026-08-24T00:00:00.000Z",
    schemaVersion: "rowboat-openai-plugin-runtime-v1",
    policyVersion: "rowboat-plugin-policy-v1",
  };
}

async function verifiedFor(pluginRoot: string): Promise<VerifiedPinnedSource> {
  const repositoryRoot = dirname(pluginRoot);
  const expectedCommit = await git(repositoryRoot, "rev-parse", "HEAD");
  return assertPinnedSource({
    repositoryRoot,
    expectedCommit,
    sourceUrl: SOURCE_URL,
    storeRoot: join(dirname(repositoryRoot), "store"),
  });
}

async function normalizeFixture(
  root: string,
  provenance?: SourceProvenance | Promise<SourceProvenance>,
) {
  const sourceCommit = await git(dirname(root), "rev-parse", "HEAD");
  const context = await verifiedFor(root);
  return normalizePlugin(
    root,
    provenance === undefined ? await provenanceFor(root, sourceCommit, context) : await provenance,
    context,
  );
}

async function copyFixture(): Promise<string> {
  const parent = await mkdtemp(join(tmpdir(), "rowboat-plugin-"));
  const repository = join(parent, "repository");
  await mkdir(repository);
  await writeFile(join(repository, ".gitignore"), "ignored.txt\n", "utf8");
  const root = join(repository, "complete-plugin");
  await cp(fixtureRoot, root, { recursive: true });
  await git(repository, "init");
  await git(repository, "config", "user.email", "tests@example.com");
  await git(repository, "config", "user.name", "Tests");
  await git(repository, "config", "core.autocrlf", "false");
  await git(repository, "remote", "add", "origin", SOURCE_URL);
  await git(repository, "add", "--all");
  await git(repository, "commit", "-m", "fixture");
  return root;
}

async function commitFixture(root: string, message: string): Promise<void> {
  const repository = dirname(root);
  await git(repository, "add", "--all");
  await git(repository, "commit", "-m", message);
}

describe("normalizePlugin", () => {
  it("discovers each supported surface once in deterministic order", async () => {
    const root = await copyFixture();
    const first = await normalizeFixture(root);
    const second = await normalizeFixture(root);

    expect(first).toEqual(second);
    expect(first.status).toBe("available");
    expect(first.components.map(({ kind }) => kind)).toEqual([
      "skill", "skill", "agent", "agent", "command", "mcp", "mcp",
      "app", "app", "hook", "asset",
    ]);
    expect(new Set(first.components.map(({ id }) => id)).size).toBe(11);
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
    const temporaryRoot = await copyFixture();
    await writeFile(join(temporaryRoot, "hooks.json"), "not json", "utf8");
    await commitFixture(temporaryRoot, "malformed hook");
    const plugin = await normalizeFixture(temporaryRoot);
    const hook = plugin.components.find(({ kind }) => kind === "hook");

    expect(hook?.status).toBe("invalid");
    expect(plugin.status).toBe("partially_available");
    expect(plugin.components.filter(({ kind }) => kind === "hook")).toHaveLength(1);
  });

  it("marks an empty hook envelope invalid and the plugin partial", async () => {
    const temporaryRoot = await copyFixture();
    await writeFile(join(temporaryRoot, "hooks.json"), JSON.stringify({ hooks: {} }), "utf8");
    await commitFixture(temporaryRoot, "empty hook envelope");
    const plugin = await normalizeFixture(temporaryRoot);
    const hook = plugin.components.find(({ kind }) => kind === "hook");

    expect(hook?.status).toBe("invalid");
    expect(plugin.status).toBe("partially_available");
  });

  it("uses .codex-plugin/plugin.json and does not require root/plugin.json", async () => {
    const plugin = await normalizeFixture(await copyFixture());
    expect(plugin.manifest.name).toBe("complete-plugin");
  });

  it("includes nested skill resources in the logical bundle digest", async () => {
    const temporaryRoot = await copyFixture();
    const before = await normalizeFixture(temporaryRoot);
    await writeFile(
      join(temporaryRoot, "skills", "review", "references", "checklist.md"),
      "changed checklist",
      "utf8",
    );
    await commitFixture(temporaryRoot, "change skill resource");
    const after = await normalizeFixture(temporaryRoot);
    const digestFor = (plugin: typeof before): unknown =>
      plugin.components.find(({ id }) => id === "skill:skills/review")?.metadata.digest;
    expect(digestFor(after)).not.toBe(digestFor(before));
  });

  it("discovers nested skills and binds conservative sibling resources", async () => {
    const temporaryRoot = await copyFixture();
    const before = await normalizeFixture(temporaryRoot);
    expect(before.components.some(({ id }) => id === "skill:skills/group/triage")).toBe(true);
    await writeFile(join(temporaryRoot, "skills", "shared-guidance.md"), "changed", "utf8");
    await commitFixture(temporaryRoot, "change sibling resource");
    const after = await normalizeFixture(temporaryRoot);
    const skillDigests = (plugin: typeof before) => plugin.components
      .filter(({ kind }) => kind === "skill")
      .map(({ metadata }) => metadata.digest);
    expect(skillDigests(after)).not.toEqual(skillDigests(before));
  });

  it("does not expose templates, conventions, or standalone YAML as runnable", async () => {
    const plugin = await normalizeFixture(await copyFixture());
    expect(plugin.components.filter(({ kind }) => kind === "agent")).toHaveLength(2);
    expect(plugin.components.filter(({ kind }) => kind === "command")).toHaveLength(1);
    expect(plugin.components.find(({ id }) => id.endsWith("openai.yaml"))?.metadata)
      .toMatchObject({ surface: "composer_metadata", role: "non_runnable" });
    expect(plugin.components.find(({ id }) => id.endsWith("reviewer.md"))?.metadata)
      .toMatchObject({ surface: "agent_template", role: "runnable" });
    expect(plugin.components.some(({ id }) => id.includes("tmpl"))).toBe(false);
    expect(plugin.components.some(({ id }) => id.includes("_conventions"))).toBe(false);
  });

  it("binds adjacent YAML and template resources into logical agent and command digests", async () => {
    const root = await copyFixture();
    const before = await normalizeFixture(root);
    await writeFile(join(root, "agents", "openai.yaml"), "interface: changed", "utf8");
    await writeFile(join(root, "commands", "review.md.tmpl"), "changed template", "utf8");
    await commitFixture(root, "change opaque resources");
    const after = await normalizeFixture(root);
    const digest = (plugin: typeof before, kind: "agent" | "command"): unknown =>
      plugin.components.find((component) => component.kind === kind)?.metadata.digest;
    expect(digest(after, "agent")).not.toBe(digest(before, "agent"));
    expect(digest(after, "command")).not.toBe(digest(before, "command"));
  });

  it("orders Unicode component names by stable code points", async () => {
    const root = await copyFixture();
    await writeFile(
      join(root, ".app.json"),
      JSON.stringify({
        apps: {
          "ä": { id: "connector_aa" },
          z: { id: "connector_bb" },
          A: { id: "connector_cc" },
        },
      }),
      "utf8",
    );
    await commitFixture(root, "unicode component names");
    const plugin = await normalizeFixture(root);
    expect(plugin.components.filter(({ kind }) => kind === "app").map(({ name }) => name))
      .toEqual(["A", "z", "ä"]);
  });

  it("orders astral and BMP names and canonical keys by Unicode code point", async () => {
    const root = await copyFixture();
    const astral = "\u{10000}";
    const bmp = "\uE000";
    await writeFile(join(root, "agents", `${astral}.md`), "astral agent", "utf8");
    await writeFile(join(root, "agents", `${bmp}.md`), "BMP agent", "utf8");
    await writeFile(
      join(root, ".app.json"),
      JSON.stringify({
        apps: {
          [astral]: { id: "connector_aa" },
          [bmp]: { id: "connector_bb" },
          invalid: { [astral]: "astral", [bmp]: "BMP" },
        },
      }),
      "utf8",
    );
    const manifest = parsePluginManifest(
      JSON.parse(await readFile(join(root, ".codex-plugin", "plugin.json"), "utf8")) as unknown,
    );
    const plugin = await discoverPluginComponents(root, manifest);
    const unicodeNames = (kind: "agent" | "app") => plugin.components
      .filter((component) => component.kind === kind)
      .map(({ name }) => kind === "agent" ? name.replace(/\.md$/, "") : name)
      .filter((name) => name === bmp || name === astral);
    expect(unicodeNames("agent")).toEqual([bmp, astral]);
    expect(unicodeNames("app")).toEqual([bmp, astral]);

    const expectedCanonical = JSON.stringify({ [bmp]: "BMP", [astral]: "astral" });
    const expectedDigest = createHash("sha256")
      .update("rowboat-plugin-component-v1\0")
      .update(expectedCanonical)
      .digest("hex");
    expect(plugin.components.find(({ id }) => id.endsWith("#invalid"))?.metadata.digest)
      .toBe(expectedDigest);
  });

  it("rejects a plugin directory whose basename differs from manifest name", async () => {
    const root = await copyFixture();
    const wrongRoot = join(dirname(root), "wrong-name");
    await rename(root, wrongRoot);
    await commitFixture(wrongRoot, "rename plugin directory");
    await expect(normalizeFixture(wrongRoot)).rejects.toMatchObject({ code: "source_mismatch" });
  });

  it("rejects forged verified source contexts", async () => {
    const root = await copyFixture();
    const commit = await git(dirname(root), "rev-parse", "HEAD");
    const forged = {} as VerifiedPinnedSource;
    await expect(
      normalizePlugin(root, await provenanceFor(root, commit), forged),
    ).rejects.toMatchObject({ code: "source_mismatch" });
  });

  it("rejects verified source root, URL, and commit mismatches", async () => {
    const root = await copyFixture();
    const otherRoot = await copyFixture();
    const commit = await git(dirname(root), "rev-parse", "HEAD");
    const provenance = await provenanceFor(root, commit);
    const otherContext = await verifiedFor(otherRoot);
    await expect(normalizePlugin(root, provenance, otherContext))
      .rejects.toMatchObject({ code: "source_mismatch" });
    await expect(normalizePlugin(
      root,
      { ...provenance, sourceUrl: "https://example.com/plugins.git" },
      await verifiedFor(root),
    )).rejects.toMatchObject({ code: "source_mismatch" });
    await expect(normalizePlugin(
      root,
      { ...provenance, sourceCommit: "0".repeat(40) },
      await verifiedFor(root),
    )).rejects.toMatchObject({ code: "source_mismatch" });
  });

  it.each(["HEAD", "dirty status", "ignored extra", "origin"] as const)(
    "revalidates changed %s before normalization",
    async (change) => {
      const root = await copyFixture();
      const repository = dirname(root);
      const commit = await git(repository, "rev-parse", "HEAD");
      const provenance = await provenanceFor(root, commit);
      const context = await verifiedFor(root);
      if (change === "HEAD") {
        await writeFile(join(root, "commands", "review.md"), "new commit", "utf8");
        await commitFixture(root, "advance head");
      } else if (change === "dirty status") {
        await writeFile(join(root, "commands", "review.md"), "dirty", "utf8");
      } else if (change === "ignored extra") {
        await writeFile(join(repository, "ignored.txt"), "ignored extra", "utf8");
      } else {
        await git(repository, "remote", "set-url", "origin", "https://example.com/other.git");
      }
      await expect(normalizePlugin(root, provenance, context))
        .rejects.toMatchObject({ code: "source_mismatch" });
    },
  );

  it("revalidates the source context after normalization", async () => {
    const root = await copyFixture();
    const commit = await git(dirname(root), "rev-parse", "HEAD");
    const context = await verifiedFor(root);
    await expect(normalizePlugin(
      root,
      await provenanceFor(root, commit),
      context,
      {
        beforeFinalTreeDigest: async () => {
          await writeFile(join(root, "commands", "review.md"), "dirty", "utf8");
        },
      },
    )).rejects.toMatchObject({ code: "source_mismatch" });
  });

  it("validates, copies, and deeply freezes provenance", async () => {
    const root = await copyFixture();
    const commit = await git(dirname(root), "rev-parse", "HEAD");
    const provenance = await provenanceFor(root, commit);
    const plugin = await normalizePlugin(root, provenance, await verifiedFor(root));
    expect(Object.isFrozen(plugin.provenance)).toBe(true);
    expect(() => {
      (provenance as { pluginName: string }).pluginName = "changed";
    }).not.toThrow();
    expect(plugin.provenance.pluginName).toBe("complete-plugin");

    await expect(
      normalizePlugin(root, { ...provenance, manifestDigest: "0".repeat(64) }, await verifiedFor(root)),
    ).rejects.toMatchObject({ code: "digest_mismatch" });
    await expect(
      normalizePlugin(root, { ...provenance, pluginVersion: "2.0.0" }, await verifiedFor(root)),
    ).rejects.toMatchObject({ code: "source_mismatch" });
  });

  it("fails closed when the tree changes before final verification", async () => {
    const temporaryRoot = await copyFixture();
    const commit = await git(dirname(temporaryRoot), "rev-parse", "HEAD");
    const provenance = await provenanceFor(temporaryRoot, commit);

    await expect(
      normalizePlugin(temporaryRoot, provenance, await verifiedFor(temporaryRoot), {
        beforeFinalTreeDigest: async () => {
          await writeFile(join(temporaryRoot, "commands", "review.md"), "changed", "utf8");
        },
      }),
    ).rejects.toMatchObject({ code: "source_mismatch" });
  });

  it("normalizes only the commit snapshot across transient source rewrites", async () => {
    const root = await copyFixture();
    const context = await verifiedFor(root);
    const commit = await git(dirname(root), "rev-parse", "HEAD");
    const provenance = await provenanceFor(root, commit, context);
    const firstPath = join(root, "commands", "review.md");
    const secondPath = join(root, "agents", "reviewer.md");
    const first = await readFile(firstPath);
    const second = await readFile(secondPath);
    const plugin = await normalizePlugin(root, provenance, context, {
      beforeFinalTreeDigest: async () => {
        await writeFile(firstPath, "transient first", "utf8");
        await writeFile(secondPath, "transient second", "utf8");
        await writeFile(firstPath, first);
        await writeFile(secondPath, second);
      },
    });
    expect(plugin.status).toBe("available");
    expect(plugin.components.find(({ kind }) => kind === "command")?.metadata.path)
      .toBe("commands/review.md");
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
    expect(AppFileSchema.safeParse({ apps: {} }).success).toBe(false);
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
    expect(McpFileSchema.safeParse({ mcpServers: {} }).success).toBe(false);
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
