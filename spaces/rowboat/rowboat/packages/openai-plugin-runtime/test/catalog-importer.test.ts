import { execFile } from "node:child_process";
import { mkdir, readFile, symlink, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { promisify } from "node:util";
import { afterEach, describe, expect, it } from "vitest";
import {
  OPENAI_PLUGINS_SOURCE_URL,
  importCatalog,
  parseCatalogSyncArgs,
  writeCatalogLock,
} from "../src/index.js";
import {
  cleanupRegisteredTestRoots,
  createOwnedTestRoot,
} from "./test-temp.js";
import { resolveCatalogSyncPaths } from "../scripts/sync-catalog.js";

const execFileAsync = promisify(execFile);
const FIXED_TIME = "2026-08-24T00:00:00.000Z";

interface FixturePlugin {
  readonly directoryName: string;
  readonly manifestName?: string;
  readonly version?: string;
  readonly license?: string;
  readonly surfaces?: readonly ("skills" | "apps" | "agents" | "commands" | "mcp" | "hooks")[];
  readonly conventionalSurfaces?: readonly ("agents" | "commands" | "hooks")[];
}

afterEach(cleanupRegisteredTestRoots);

async function createSource(
  label: string,
  plugins: readonly FixturePlugin[],
): Promise<{ readonly repositoryRoot: string; readonly pluginsRoot: string; readonly commit: string; readonly storeRoot: string }> {
  const root = await createOwnedTestRoot(label);
  const repositoryRoot = join(root, "source");
  const pluginsRoot = join(repositoryRoot, "plugins");
  const storeRoot = join(root, "store");
  await mkdir(pluginsRoot, { recursive: true });

  for (const plugin of plugins) {
    const pluginRoot = join(pluginsRoot, plugin.directoryName);
    const manifestRoot = join(pluginRoot, ".codex-plugin");
    await mkdir(manifestRoot, { recursive: true });
    const surfaces = new Set(plugin.surfaces ?? []);
    const conventionalSurfaces = new Set(plugin.conventionalSurfaces ?? []);
    const manifest: Record<string, unknown> = {
      name: plugin.manifestName ?? plugin.directoryName,
      version: plugin.version ?? "1.0.0",
      description: `${plugin.directoryName} fixture`,
      interface: { displayName: plugin.directoryName },
    };
    if (plugin.license !== undefined) manifest.license = plugin.license;
    if (surfaces.has("skills")) {
      manifest.skills = "./skills/";
      await mkdir(join(pluginRoot, "skills", "sample"), { recursive: true });
      await writeFile(join(pluginRoot, "skills", "sample", "SKILL.md"), "# Sample\n");
    }
    if (surfaces.has("apps")) {
      manifest.apps = "./.app.json";
      await writeFile(join(pluginRoot, ".app.json"), JSON.stringify({ apps: { sample: { id: "connector_abcdef" } } }));
    }
    if (surfaces.has("agents") || conventionalSurfaces.has("agents")) {
      if (surfaces.has("agents")) manifest.agents = "./agents/";
      await mkdir(join(pluginRoot, "agents"));
      await writeFile(join(pluginRoot, "agents", "sample.md"), "# Agent\n");
    }
    if (surfaces.has("commands") || conventionalSurfaces.has("commands")) {
      if (surfaces.has("commands")) manifest.commands = "./commands/";
      await mkdir(join(pluginRoot, "commands"));
      await writeFile(join(pluginRoot, "commands", "sample.md"), "# Command\n");
    }
    if (surfaces.has("mcp")) {
      manifest.mcpServers = "./.mcp.json";
      await writeFile(join(pluginRoot, ".mcp.json"), JSON.stringify({ mcpServers: { sample: { type: "http", url: "https://example.com/mcp" } } }));
    }
    if (surfaces.has("hooks") || conventionalSurfaces.has("hooks")) {
      if (surfaces.has("hooks")) manifest.hooks = "./hooks.json";
      await writeFile(join(pluginRoot, "hooks.json"), JSON.stringify({ hooks: { PreToolUse: [{ command: "echo blocked" }] } }));
    }
    await writeFile(join(manifestRoot, "plugin.json"), `${JSON.stringify(manifest)}\n`);
  }

  await execFileAsync("git", ["init", "--quiet", repositoryRoot]);
  await execFileAsync("git", ["-C", repositoryRoot, "config", "user.email", "rowboat@example.invalid"]);
  await execFileAsync("git", ["-C", repositoryRoot, "config", "user.name", "Rowboat Test"]);
  await execFileAsync("git", ["-C", repositoryRoot, "remote", "add", "origin", OPENAI_PLUGINS_SOURCE_URL]);
  await execFileAsync("git", ["-C", repositoryRoot, "add", "."]);
  await execFileAsync("git", ["-C", repositoryRoot, "commit", "--quiet", "-m", "fixture"]);
  const { stdout } = await execFileAsync("git", ["-C", repositoryRoot, "rev-parse", "HEAD"]);
  return { repositoryRoot, pluginsRoot, commit: stdout.trim(), storeRoot };
}

function options(source: Awaited<ReturnType<typeof createSource>>) {
  return {
    repositoryRoot: source.repositoryRoot,
    sourceCommit: source.commit,
    sourceUrl: OPENAI_PLUGINS_SOURCE_URL,
    storeRoot: source.storeRoot,
    clock: (): Date => new Date(FIXED_TIME),
  } as const;
}

async function createGitInvocationRoot(label: string): Promise<string> {
  const root = await createOwnedTestRoot(label);
  await execFileAsync("git", ["init", "--quiet", root]);
  return root;
}

describe("importCatalog", () => {
  it("resolves a relative CLI output from the validated invocation root", async () => {
    const source = await createSource("catalog-cli-paths", [
      { directoryName: "alpha", license: "MIT" },
    ]);
    const invocationRoot = await createGitInvocationRoot("catalog-invocation-root");
    const relativeOutput = join(
      "spaces",
      "rowboat",
      "rowboat",
      "config",
      "openai-plugin-catalog.lock.json",
    );

    const paths = await resolveCatalogSyncPaths([
      "--source", source.pluginsRoot,
      "--commit", "11c74d6ba24d3a6d48f54a194cd00ef3beea18f9",
      "--output", relativeOutput,
    ], source.storeRoot, invocationRoot);

    expect(paths.output).toBe(join(invocationRoot, relativeOutput));
    expect(paths.repositoryRoot).toBe(source.repositoryRoot);
    expect(paths.source).toBe(source.pluginsRoot);
    expect(paths.storeRoot).toBe(source.storeRoot);
  });

  it("rejects absolute output outside the invocation worktree", async () => {
    const source = await createSource("catalog-absolute-outside", [
      { directoryName: "alpha", license: "MIT" },
    ]);
    const invocationRoot = await createGitInvocationRoot("catalog-absolute-root");
    const outsideRoot = await createOwnedTestRoot("catalog-absolute-target");

    await expect(resolveCatalogSyncPaths([
      "--source", source.pluginsRoot,
      "--commit", "11c74d6ba24d3a6d48f54a194cd00ef3beea18f9",
      "--output", join(outsideRoot, "catalog.json"),
    ], source.storeRoot, invocationRoot)).rejects.toThrow("path_escape:catalog_output");
  });

  it("rejects a non-Git root and a Git worktree subdirectory", async () => {
    const source = await createSource("catalog-fake-root", [
      { directoryName: "alpha", license: "MIT" },
    ]);
    const nonGitRoot = await createOwnedTestRoot("catalog-non-git-root");
    const invocationRoot = await createGitInvocationRoot("catalog-git-root");
    const subdirectory = join(invocationRoot, "subdirectory");
    await mkdir(subdirectory);
    const args = [
      "--source", source.pluginsRoot,
      "--commit", "11c74d6ba24d3a6d48f54a194cd00ef3beea18f9",
      "--output", "catalog.json",
    ] as const;

    await expect(resolveCatalogSyncPaths(args, source.storeRoot, nonGitRoot))
      .rejects.toThrow("source_mismatch:invocation_root");
    await expect(resolveCatalogSyncPaths(args, source.storeRoot, subdirectory))
      .rejects.toThrow("source_mismatch:invocation_root");
  });

  it("rejects an output whose existing ancestor escapes through a reparse point", async () => {
    const source = await createSource("catalog-reparse-source", [
      { directoryName: "alpha", license: "MIT" },
    ]);
    const invocationRoot = await createGitInvocationRoot("catalog-reparse-root");
    const outsideRoot = await createOwnedTestRoot("catalog-reparse-target");
    const link = join(invocationRoot, "escaped");
    try {
      await symlink(outsideRoot, link, process.platform === "win32" ? "junction" : "dir");
    } catch {
      return;
    }

    await expect(resolveCatalogSyncPaths([
      "--source", source.pluginsRoot,
      "--commit", "11c74d6ba24d3a6d48f54a194cd00ef3beea18f9",
      "--output", join("escaped", "catalog.json"),
    ], source.storeRoot, invocationRoot)).rejects.toThrow("path_escape:catalog_output");
  });

  it("accepts only the exact pinned commit at the CLI boundary", () => {
    expect(parseCatalogSyncArgs([
      "--source", "C:\\source\\plugins",
      "--commit", "11c74d6ba24d3a6d48f54a194cd00ef3beea18f9",
      "--output", "C:\\output\\catalog.json",
    ])).toEqual({
      source: "C:\\source\\plugins",
      commit: "11c74d6ba24d3a6d48f54a194cd00ef3beea18f9",
      output: "C:\\output\\catalog.json",
    });
    expect(() => parseCatalogSyncArgs([
      "--source", "C:\\source\\plugins",
      "--commit", "0000000000000000000000000000000000000000",
      "--output", "C:\\output\\catalog.json",
    ])).toThrow("source_mismatch:catalog_arguments");
  });

  it("rejects a catalog when folder and manifest names differ", async () => {
    const source = await createSource("catalog-mismatch", [
      { directoryName: "wrong", manifestName: "right", license: "MIT" },
    ]);

    await expect(importCatalog(source.pluginsRoot, options(source))).rejects.toThrow(
      "manifest_invalid:name_mismatch",
    );
  });

  it("rejects a direct plugin directory with no manifest", async () => {
    const source = await createSource("catalog-missing", [
      { directoryName: "alpha", license: "MIT" },
    ]);
    await mkdir(join(source.pluginsRoot, "missing"));
    await writeFile(join(source.pluginsRoot, "missing", "README.md"), "missing manifest\n");
    await execFileAsync("git", ["-C", source.repositoryRoot, "add", "."]);
    await execFileAsync("git", ["-C", source.repositoryRoot, "commit", "--quiet", "-m", "missing"]);
    const { stdout } = await execFileAsync("git", ["-C", source.repositoryRoot, "rev-parse", "HEAD"]);

    await expect(importCatalog(source.pluginsRoot, {
      ...options(source),
      sourceCommit: stdout.trim(),
    })).rejects.toThrow("manifest_invalid:missing_manifest");
  });

  it("sorts entries by code point and records complete source, schema, and policy provenance", async () => {
    const source = await createSource("catalog-provenance", [
      { directoryName: "zeta", license: "Apache-2.0" },
      { directoryName: "alpha", license: "MIT" },
    ]);

    const lock = await importCatalog(source.pluginsRoot, options(source));

    expect(lock.entries.map((entry) => entry.name)).toEqual(["alpha", "zeta"]);
    expect(lock.sourceUrl).toBe(OPENAI_PLUGINS_SOURCE_URL);
    expect(lock.sourceCommit).toBe(source.commit);
    expect(lock.entries[0]).toMatchObject({
      name: "alpha",
      pluginName: "alpha",
      pluginVersion: "1.0.0",
      sourceUrl: OPENAI_PLUGINS_SOURCE_URL,
      sourceCommit: source.commit,
      manifestDigest: expect.stringMatching(/^[a-f0-9]{64}$/),
      treeDigest: expect.stringMatching(/^[a-f0-9]{64}$/),
      importedAt: FIXED_TIME,
      schemaVersion: "rowboat-plugin-schema-v1",
      policyVersion: "rowboat-plugin-policy-v1",
      admission: { status: "admitted", policyVersion: "rowboat-plugin-policy-v1" },
      storedContentDigest: expect.stringMatching(/^[a-f0-9]{64}$/),
    });
    expect(lock.policyVersion).toBe("rowboat-plugin-policy-v1");
    expect(lock.schemaVersion).toBe("rowboat-plugin-schema-v1");
    expect(lock.catalogDigest).toMatch(/^[a-f0-9]{64}$/);
  });

  it("counts plugin surfaces and license declarations", async () => {
    const source = await createSource("catalog-inventory", [
      { directoryName: "alpha", license: "MIT", surfaces: ["skills", "apps", "agents", "commands", "mcp", "hooks"] },
      { directoryName: "beta", license: "UNLICENSED" },
      { directoryName: "gamma" },
    ]);

    const lock = await importCatalog(source.pluginsRoot, options(source));

    expect(lock.inventory).toEqual({
      pluginsWithSkills: 1,
      pluginsWithApps: 1,
      pluginsWithAgents: 1,
      pluginsWithCommands: 1,
      pluginsWithMcp: 1,
      pluginsWithCommandHooks: 1,
    });
    expect(lock.licenseDeclarations).toEqual({
      MIT: 1,
      UNLICENSED: 1,
      "<missing>": 1,
    });
    expect(lock.entries.find(({ name }) => name === "beta")?.admission).toMatchObject({
      status: "rejected",
      reason: "license_rejected",
    });
    expect(lock.entries.find(({ name }) => name === "gamma")?.admission).toMatchObject({
      status: "review_required",
      reason: "license_review_required",
    });
  });

  it("counts conventional agent, command, and hook surfaces", async () => {
    const source = await createSource("catalog-conventional", [
      {
        directoryName: "alpha",
        license: "MIT",
        conventionalSurfaces: ["agents", "commands", "hooks"],
      },
    ]);

    const lock = await importCatalog(source.pluginsRoot, options(source));

    expect(lock.inventory).toMatchObject({
      pluginsWithAgents: 1,
      pluginsWithCommands: 1,
      pluginsWithCommandHooks: 1,
    });
  });

  it("fails closed when an expected catalog count differs", async () => {
    const source = await createSource("catalog-count", [
      { directoryName: "alpha", license: "MIT" },
    ]);

    await expect(importCatalog(source.pluginsRoot, {
      ...options(source),
      expectedPluginCount: 180,
    })).rejects.toThrow("source_mismatch:plugin_count");
  });

  it("binds required provenance to the catalog digest", async () => {
    const source = await createSource("catalog-digest", [
      { directoryName: "alpha", license: "MIT" },
    ]);
    const first = await importCatalog(source.pluginsRoot, options(source));
    const later = await importCatalog(source.pluginsRoot, {
      ...options(source),
      clock: (): Date => new Date("2026-08-25T00:00:00.000Z"),
    });

    expect(later.catalogDigest).not.toBe(first.catalogDigest);
  });

  it("writes canonical JSON with a final newline", async () => {
    const source = await createSource("catalog-write", [
      { directoryName: "alpha", license: "MIT" },
    ]);
    const lock = await importCatalog(source.pluginsRoot, options(source));
    const output = join(await createOwnedTestRoot("catalog-output"), "catalog.lock.json");

    await writeCatalogLock(output, lock);
    const written = await readFile(output, "utf8");

    expect(written.endsWith("\n")).toBe(true);
    expect(JSON.parse(written)).toEqual(lock);
    expect(written).toBe(`${JSON.stringify(lock, null, 2)}\n`);
  });
});
