import { execFile } from "node:child_process";
import { createHash } from "node:crypto";
import { readFile, readdir } from "node:fs/promises";
import { join } from "node:path";
import { promisify } from "node:util";
import { describe, expect, it } from "vitest";
import {
  digestTree,
  normalizePlugin,
  type SourceProvenance,
} from "../src/index.js";

const sourceRoot = process.env.OPENAI_PLUGINS_SOURCE_ROOT;
const PINNED_COMMIT = "11c74d6ba24d3a6d48f54a194cd00ef3beea18f9";
const execFileAsync = promisify(execFile);

describe.skipIf(sourceRoot === undefined)("pinned OpenAI plugin catalog", () => {
  it(
    "normalizes every direct catalog plugin at the exact pinned shapes",
    async () => {
      if (sourceRoot === undefined) throw new Error("source root is required");
      const head = await execFileAsync("git", ["-C", sourceRoot, "rev-parse", "HEAD"]);
      const status = await execFileAsync("git", ["-C", sourceRoot, "status", "--porcelain"]);
      expect(head.stdout.trim()).toBe(PINNED_COMMIT);
      expect(status.stdout.trim()).toBe("");
      const pluginsRoot = join(sourceRoot, "plugins");
      const directories = (await readdir(pluginsRoot, { withFileTypes: true }))
        .filter((entry) => entry.isDirectory())
        .sort((left, right) => left.name.localeCompare(right.name));

      let manifestCount = 0;
      let appFileCount = 0;
      let mcpFileCount = 0;
      let appComponentCount = 0;
      let mcpComponentCount = 0;
      for (const directory of directories) {
        const pluginRoot = join(pluginsRoot, directory.name);
        const manifestPath = join(pluginRoot, ".codex-plugin", "plugin.json");
        const manifestBytes = await readFile(manifestPath);
        const manifestInput = JSON.parse(manifestBytes.toString("utf8")) as {
          readonly name: string;
          readonly version: string;
          readonly apps?: string;
          readonly mcpServers?: string;
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
        const plugin = await normalizePlugin(pluginRoot, provenance);
        expect(plugin.status, directory.name).toBe("available");
        manifestCount += 1;
        if (manifestInput.apps !== undefined) appFileCount += 1;
        if (manifestInput.mcpServers !== undefined) mcpFileCount += 1;
        appComponentCount += plugin.components.filter(({ kind }) => kind === "app").length;
        mcpComponentCount += plugin.components.filter(({ kind }) => kind === "mcp").length;
      }

      expect({
        manifestCount,
        appFileCount,
        mcpFileCount,
        appComponentCount,
        mcpComponentCount,
      }).toEqual({
        manifestCount: 180,
        appFileCount: 154,
        mcpFileCount: 8,
        appComponentCount: 156,
        mcpComponentCount: 8,
      });
    },
    120_000,
  );
});
