import { chmod, lstat, mkdir, mkdtemp, readFile, readdir, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { basename, dirname, join, resolve } from "node:path";

const TEST_TEMP_PREFIX = "rowboat-openai-plugin-runtime-test-";
const TEST_TEMP_SENTINEL = ".rowboat-openai-plugin-runtime-test-temp";
const TEST_TEMP_SENTINEL_CONTENT = "rowboat-openai-plugin-runtime-test-temp-v1\n";
const registeredRoots = new Set<string>();

function assertOwnedTestRoot(root: string): string {
  const resolved = resolve(root);
  if (
    dirname(resolved) !== resolve(tmpdir()) ||
    !basename(resolved).startsWith(TEST_TEMP_PREFIX)
  ) {
    throw new Error("test temp root is outside the owned boundary");
  }
  return resolved;
}

async function makeWritable(root: string): Promise<void> {
  let stats;
  try {
    stats = await lstat(root);
  } catch {
    return;
  }
  if (stats.isDirectory()) {
    for (const name of await readdir(root)) await makeWritable(join(root, name));
  }
  await chmod(root, 0o700);
}

export async function createOwnedTestRoot(label: string): Promise<string> {
  if (!/^[a-z0-9-]+$/.test(label)) throw new Error("invalid test temp label");
  const root = assertOwnedTestRoot(
    await mkdtemp(join(tmpdir(), `${TEST_TEMP_PREFIX}${label}-`)),
  );
  await writeFile(join(root, TEST_TEMP_SENTINEL), TEST_TEMP_SENTINEL_CONTENT, {
    flag: "wx",
  });
  registeredRoots.add(root);
  return root;
}

export async function cleanupOwnedTestRoot(root: string): Promise<void> {
  const ownedRoot = assertOwnedTestRoot(root);
  let sentinel: string;
  try {
    sentinel = await readFile(join(ownedRoot, TEST_TEMP_SENTINEL), "utf8");
  } catch {
    throw new Error("test temp sentinel is missing");
  }
  if (sentinel !== TEST_TEMP_SENTINEL_CONTENT) {
    throw new Error("test temp sentinel is invalid");
  }
  await makeWritable(ownedRoot);
  await rm(ownedRoot, { recursive: true, force: true, maxRetries: 5, retryDelay: 50 });
  registeredRoots.delete(ownedRoot);
}

export async function cleanupRegisteredTestRoots(): Promise<void> {
  const roots = [...registeredRoots];
  const results = await Promise.allSettled(roots.map(cleanupOwnedTestRoot));
  const failure = results.find((result) => result.status === "rejected");
  if (failure?.status === "rejected") throw failure.reason;
}

export async function createOwnedTestDirectory(label: string, child: string): Promise<string> {
  const root = await createOwnedTestRoot(label);
  const directory = join(root, child);
  await mkdir(directory, { recursive: true });
  return directory;
}
