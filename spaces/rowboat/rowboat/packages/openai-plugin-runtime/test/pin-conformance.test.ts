import { execFile } from "node:child_process";
import { createHash } from "node:crypto";
import { readFile, readdir, realpath, stat } from "node:fs/promises";
import { join } from "node:path";
import { promisify } from "node:util";
import { describe, expect, it } from "vitest";
import {
  digestTree,
  assertPinnedSource,
  normalizePlugin,
  type GitProbe,
  type GitProbeCommand,
  type SourceProvenance,
} from "../src/index.js";

const sourceRoot = process.env.OPENAI_PLUGINS_SOURCE_ROOT;
const PINNED_COMMIT = "11c74d6ba24d3a6d48f54a194cd00ef3beea18f9";
const execFileAsync = promisify(execFile);

class RealGitProbe implements GitProbe {
  async run(repositoryRoot: string, command: GitProbeCommand): Promise<string> {
    const result = await execFileAsync("git", ["-C", repositoryRoot, ...command], {
      encoding: "utf8",
    });
    return result.stdout;
  }
}

async function filesUnder(directory: string): Promise<readonly string[]> {
  try {
    const entries = await readdir(directory, { withFileTypes: true });
    const files: string[] = [];
    for (const entry of entries) {
      const path = join(directory, entry.name);
      if (entry.isDirectory()) files.push(...(await filesUnder(path)));
      else if (entry.isFile()) files.push(path);
    }
    return files;
  } catch {
    return [];
  }
}

describe.skipIf(sourceRoot === undefined)("pinned OpenAI plugin catalog", () => {
  it(
    "normalizes every direct catalog plugin at the exact pinned shapes",
    async () => {
      if (sourceRoot === undefined) throw new Error("source root is required");
      const head = await execFileAsync("git", ["-C", sourceRoot, "rev-parse", "HEAD"]);
      const status = await execFileAsync("git", ["-C", sourceRoot, "status", "--porcelain"]);
      expect(head.stdout.trim()).toBe(PINNED_COMMIT);
      expect(status.stdout.trim()).toBe("");
      const verifiedSource = await assertPinnedSource({
        repositoryRoot: sourceRoot,
        expectedCommit: PINNED_COMMIT,
        sourceUrl: "https://github.com/openai/plugins.git",
        probe: new RealGitProbe(),
      });
      const pluginsRoot = join(sourceRoot, "plugins");
      const directories = (await readdir(pluginsRoot, { withFileTypes: true }))
        .filter((entry) => entry.isDirectory())
        .sort((left, right) => left.name.localeCompare(right.name));

      let manifestCount = 0;
      let appFileCount = 0;
      let mcpFileCount = 0;
      let appComponentCount = 0;
      let mcpComponentCount = 0;
      let skillInventoryCount = 0;
      let agentInventoryCount = 0;
      let commandInventoryCount = 0;
      let hookInventoryCount = 0;
      let skillComponentCount = 0;
      let agentComponentCount = 0;
      let commandComponentCount = 0;
      let hookComponentCount = 0;
      let assetComponentCount = 0;
      const assetInventory = new Set<string>();
      for (const directory of directories) {
        const pluginRoot = join(pluginsRoot, directory.name);
        const manifestPath = join(pluginRoot, ".codex-plugin", "plugin.json");
        const manifestBytes = await readFile(manifestPath);
        const manifestInput = JSON.parse(manifestBytes.toString("utf8")) as {
          readonly name: string;
          readonly version: string;
          readonly apps?: string;
          readonly mcpServers?: string;
          readonly interface: {
            readonly composerIcon?: string;
            readonly logo?: string;
            readonly logoDark?: string;
            readonly screenshots?: readonly string[];
          };
        };
        const provenance: SourceProvenance = {
          sourceUrl: "https://github.com/openai/plugins.git",
          sourceCommit: PINNED_COMMIT,
          pluginName: manifestInput.name,
          pluginVersion: manifestInput.version,
          manifestDigest: createHash("sha256").update(manifestBytes).digest("hex"),
          treeDigest: await digestTree(pluginRoot),
          importedAt: "2026-08-24T00:00:00.000Z",
          schemaVersion: "rowboat-openai-plugin-runtime-v1",
          policyVersion: "rowboat-plugin-policy-v1",
        };
        const plugin = await normalizePlugin(pluginRoot, provenance, verifiedSource);
        expect(plugin.status, directory.name).toBe("available");
        expect(plugin.components.every(({ status: componentStatus }) => componentStatus === "available"), directory.name).toBe(true);
        expect(new Set(plugin.components.map(({ id }) => id)).size, directory.name).toBe(plugin.components.length);
        manifestCount += 1;
        if (manifestInput.apps !== undefined) appFileCount += 1;
        if (manifestInput.mcpServers !== undefined) mcpFileCount += 1;
        appComponentCount += plugin.components.filter(({ kind }) => kind === "app").length;
        mcpComponentCount += plugin.components.filter(({ kind }) => kind === "mcp").length;
        skillComponentCount += plugin.components.filter(({ kind }) => kind === "skill").length;
        agentComponentCount += plugin.components.filter(({ kind }) => kind === "agent").length;
        commandComponentCount += plugin.components.filter(({ kind }) => kind === "command").length;
        hookComponentCount += plugin.components.filter(({ kind }) => kind === "hook").length;
        assetComponentCount += plugin.components.filter(({ kind }) => kind === "asset").length;

        const skillFiles = await filesUnder(join(pluginRoot, "skills"));
        skillInventoryCount += skillFiles.filter((path) => path.endsWith("SKILL.md")).length;
        const agentFiles = await filesUnder(join(pluginRoot, "agents"));
        agentInventoryCount += agentFiles.filter((path) => path.endsWith(".md") && !path.endsWith(".md.tmpl") && !path.endsWith("_conventions.md")).length;
        const commandFiles = await filesUnder(join(pluginRoot, "commands"));
        commandInventoryCount += commandFiles.filter((path) => path.endsWith(".md") && !path.endsWith(".md.tmpl") && !path.endsWith("_conventions.md")).length;
        try {
          if ((await stat(join(pluginRoot, "hooks.json"))).isFile()) hookInventoryCount += 1;
        } catch {
          // This plugin has no conventional hook surface.
        }
        for (const pointer of [
          manifestInput.interface.composerIcon,
          manifestInput.interface.logo,
          manifestInput.interface.logoDark,
          ...(manifestInput.interface.screenshots ?? []),
        ]) {
          if (pointer !== undefined) assetInventory.add(await realpath(join(pluginRoot, pointer)));
        }
      }

      // Task 4 proves discovery status only. Policy admission states such as
      // review_required/unavailable/unsupported are asserted in Tasks 5 and 6.
      expect({
        manifestCount,
        appFileCount,
        mcpFileCount,
        appComponentCount,
        mcpComponentCount,
        skillInventoryCount,
        agentInventoryCount,
        commandInventoryCount,
        hookInventoryCount,
        assetInventoryCount: assetInventory.size,
        skillComponentCount,
        agentComponentCount,
        commandComponentCount,
        hookComponentCount,
        assetComponentCount,
      }).toEqual({
        manifestCount: 180,
        appFileCount: 154,
        mcpFileCount: 8,
        appComponentCount: 156,
        mcpComponentCount: 8,
        skillInventoryCount: 603,
        agentInventoryCount: 9,
        commandInventoryCount: 40,
        hookInventoryCount: 2,
        assetInventoryCount: 262,
        skillComponentCount: 603,
        agentComponentCount: 9,
        commandComponentCount: 40,
        hookComponentCount: 2,
        assetComponentCount: 262,
      });
    },
    120_000,
  );
});
