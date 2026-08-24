import { access, mkdir, readdir, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { PluginSourceSecurityError } from "../src/import/path-guard.js";
import { createSnapshotTempRoot, removeSnapshotTempRoot } from "../src/import/snapshot-temp.js";
import {
  VERIFIED_SNAPSHOT_LIMITS,
  OPENAI_PLUGINS_SOURCE_URL,
  assertPinnedSource,
  getVerifiedPluginDigests,
  parseGitTreeInventory,
  runGitVerificationCommand,
} from "../src/import/source-reader.js";
import { cleanupRegisteredTestRoots, createOwnedTestRoot } from "./test-temp.js";

afterEach(cleanupRegisteredTestRoots);

function treeEntry(path: string, size: number, mode = "100644"): string {
  return `${mode} blob ${"a".repeat(40)} ${size}\tplugins/example/${path}\0`;
}

async function gitText(repository: string, ...args: readonly string[]): Promise<string> {
  return (await runGitVerificationCommand(repository, args, {
    maxBuffer: 1024 * 1024,
    timeoutMs: 5_000,
  })).toString("utf8").trim();
}

async function snapshotTempNames(): Promise<readonly string[]> {
  return (await readdir(tmpdir()))
    .filter((name) => name.startsWith("rowboat-git-snapshot-"))
    .sort();
}

async function createPinnedPlugin(label: string, depth: number): Promise<{
  readonly repository: string;
  readonly plugin: string;
  readonly commit: string;
  readonly store: string;
}> {
  const root = await createOwnedTestRoot(label);
  const repository = join(root, "repository");
  const plugin = join(repository, "plugins", "example");
  await mkdir(join(plugin, ".codex-plugin"), { recursive: true });
  await writeFile(join(plugin, ".codex-plugin", "plugin.json"), "{}", "utf8");
  const nested = join(plugin, ...Array.from({ length: depth }, () => "d"));
  await mkdir(nested, { recursive: true });
  await writeFile(join(nested, "file.txt"), "test", "utf8");
  await gitText(repository, "init");
  await gitText(repository, "config", "user.email", "tests@example.com");
  await gitText(repository, "config", "user.name", "Tests");
  await gitText(repository, "remote", "add", "origin", OPENAI_PLUGINS_SOURCE_URL);
  await gitText(repository, "add", "--all");
  await gitText(repository, "commit", "-m", "fixture");
  return {
    repository,
    plugin,
    commit: await gitText(repository, "rev-parse", "HEAD"),
    store: join(root, "store"),
  };
}

describe("verified snapshot preflight", () => {
  it.each([
    ["total bytes", treeEntry("large.bin", VERIFIED_SNAPSHOT_LIMITS.maxTotalBytes + 1)],
    [
      "file count",
      Array.from(
        { length: VERIFIED_SNAPSHOT_LIMITS.maxFileCount + 1 },
        (_, index) => treeEntry(`files/${index}`, 0),
      ).join(""),
    ],
    [
      "relative depth",
      treeEntry(`${Array.from({ length: VERIFIED_SNAPSHOT_LIMITS.maxRelativeDepth + 1 }, () => "d").join("/")}/file`, 1),
    ],
  ])("rejects excessive %s before materialization", (_label, inventory) => {
    expect(() => parseGitTreeInventory(Buffer.from(inventory), "plugins/example"))
      .toThrow(PluginSourceSecurityError);
  });

  it("times out a bounded internal Git subprocess without exposing its output", async () => {
    const root = await createOwnedTestRoot("git-timeout");
    const repository = join(root, "repository");
    await mkdir(repository);
    await runGitVerificationCommand(repository, ["init"], {
      maxBuffer: 1024 * 1024,
      timeoutMs: 5_000,
    });
    const startedAt = Date.now();
    await expect(runGitVerificationCommand(
      repository,
      ["-c", "alias.slow=!ping 127.0.0.1 -n 6 >/dev/null", "slow"],
      { maxBuffer: 1024, timeoutMs: 10 },
    )).rejects.toMatchObject({ code: "source_mismatch" });
    expect(Date.now() - startedAt).toBeLessThan(1_000);
  });

  it("rejects excessive depth before creating a snapshot temp root", async () => {
    const fixture = await createPinnedPlugin(
      "prewrite-depth",
      VERIFIED_SNAPSHOT_LIMITS.maxRelativeDepth + 1,
    );
    const context = await assertPinnedSource({
      repositoryRoot: fixture.repository,
      expectedCommit: fixture.commit,
      sourceUrl: OPENAI_PLUGINS_SOURCE_URL,
      storeRoot: fixture.store,
    });
    const before = await snapshotTempNames();

    await expect(getVerifiedPluginDigests(context, fixture.plugin))
      .rejects.toMatchObject({ code: "source_mismatch" });

    expect(await snapshotTempNames()).toStrictEqual(before);
  }, 30_000);

  it("removes a snapshot temp root when publication fails", async () => {
    const fixture = await createPinnedPlugin("publication-failure", 1);
    await writeFile(fixture.store, "not a directory", "utf8");
    const context = await assertPinnedSource({
      repositoryRoot: fixture.repository,
      expectedCommit: fixture.commit,
      sourceUrl: OPENAI_PLUGINS_SOURCE_URL,
      storeRoot: fixture.store,
    });
    const before = await snapshotTempNames();

    await expect(getVerifiedPluginDigests(context, fixture.plugin)).rejects.toBeDefined();

    expect(await snapshotTempNames()).toStrictEqual(before);
  }, 30_000);

  it("removes product snapshot temps on success", async () => {
    const root = await createSnapshotTempRoot();
    await writeFile(join(root, "readonly.txt"), "test", { mode: 0o400 });
    await removeSnapshotTempRoot(root);
    await expect(access(root)).rejects.toBeDefined();
  });

  it("refuses product snapshot cleanup without its sentinel", async () => {
    const root = await createSnapshotTempRoot();
    const sentinel = join(root, ".rowboat-openai-plugin-runtime-snapshot");
    await rm(sentinel);
    await expect(removeSnapshotTempRoot(root)).rejects.toMatchObject({ code: "path_escape" });
    await writeFile(sentinel, "rowboat-openai-plugin-runtime-snapshot-v1\n", { flag: "wx" });
    await removeSnapshotTempRoot(root);
  });
});
