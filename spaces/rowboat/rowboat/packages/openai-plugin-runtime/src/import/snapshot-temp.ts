import { chmod, lstat, mkdtemp, readFile, readdir, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { basename, dirname, join, resolve } from "node:path";
import { PluginSourceSecurityError } from "./path-guard.js";

const SNAPSHOT_PREFIX = "rowboat-git-snapshot-";
const SNAPSHOT_SENTINEL = ".rowboat-openai-plugin-runtime-snapshot";
const SNAPSHOT_SENTINEL_CONTENT = "rowboat-openai-plugin-runtime-snapshot-v1\n";

function assertSnapshotRoot(root: string): string {
  const resolved = resolve(root);
  if (
    dirname(resolved) !== resolve(tmpdir()) ||
    !basename(resolved).startsWith(SNAPSHOT_PREFIX)
  ) {
    throw new PluginSourceSecurityError("path_escape", "snapshot temp root is not owned");
  }
  return resolved;
}

async function makeWritable(path: string): Promise<void> {
  let stats;
  try {
    stats = await lstat(path);
  } catch {
    return;
  }
  if (stats.isDirectory()) {
    for (const name of await readdir(path)) await makeWritable(join(path, name));
  }
  await chmod(path, 0o700);
}

export async function createSnapshotTempRoot(): Promise<string> {
  const root = assertSnapshotRoot(await mkdtemp(join(tmpdir(), SNAPSHOT_PREFIX)));
  try {
    await writeFile(join(root, SNAPSHOT_SENTINEL), SNAPSHOT_SENTINEL_CONTENT, { flag: "wx" });
    return root;
  } catch (error: unknown) {
    await rm(root, { recursive: true, force: true, maxRetries: 5, retryDelay: 50 });
    throw error;
  }
}

export async function removeSnapshotTempRoot(root: string): Promise<void> {
  const ownedRoot = assertSnapshotRoot(root);
  let sentinel: string;
  try {
    sentinel = await readFile(join(ownedRoot, SNAPSHOT_SENTINEL), "utf8");
  } catch {
    throw new PluginSourceSecurityError("path_escape", "snapshot temp sentinel is missing");
  }
  if (sentinel !== SNAPSHOT_SENTINEL_CONTENT) {
    throw new PluginSourceSecurityError("path_escape", "snapshot temp sentinel is invalid");
  }
  await makeWritable(ownedRoot);
  await rm(ownedRoot, { recursive: true, force: true, maxRetries: 5, retryDelay: 50 });
}
