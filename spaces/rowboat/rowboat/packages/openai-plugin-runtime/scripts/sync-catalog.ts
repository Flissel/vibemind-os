import { execFile } from "node:child_process";
import { randomUUID } from "node:crypto";
import { lstat, mkdir, realpath, rename, rm } from "node:fs/promises";
import { basename, dirname, isAbsolute, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { promisify } from "node:util";
import {
  importCatalog,
  OPENAI_PLUGINS_SOURCE_URL,
  parseCatalogSyncArgs,
  PINNED_OPENAI_PLUGIN_COUNT,
  writeCatalogLock,
} from "../src/index.js";
import { isContainedPath } from "../src/import/path-guard.js";
import {
  assertDirectoryIdentity,
  snapshotDirectoryIdentity,
} from "../src/import/directory-identity.js";

const execFileAsync = promisify(execFile);

export interface CatalogSyncPaths {
  readonly source: string;
  readonly invocationRoot: string;
  readonly repositoryRoot: string;
  readonly storeRoot: string;
  readonly output: string;
  readonly commit: string;
}

function isNotFoundError(error: unknown): boolean {
  return (error as NodeJS.ErrnoException).code === "ENOENT";
}

async function canonicalGitWorktreeRoot(input: string): Promise<string> {
  try {
    const canonicalInput = await realpath(input);
    const result = await execFileAsync(
      "git",
      ["-C", canonicalInput, "rev-parse", "--show-toplevel"],
      { timeout: 30_000, maxBuffer: 1024 * 1024, windowsHide: true },
    );
    const canonicalTopLevel = await realpath(result.stdout.trim());
    if (canonicalTopLevel !== canonicalInput) {
      throw new Error("worktree root differs");
    }
    return canonicalInput;
  } catch {
    throw new Error("source_mismatch:invocation_root");
  }
}

async function nearestExistingCanonicalPath(target: string): Promise<string> {
  let candidate = target;
  while (true) {
    try {
      return await realpath(candidate);
    } catch (error: unknown) {
      if (!isNotFoundError(error)) throw error;
      const parent = dirname(candidate);
      if (parent === candidate) throw error;
      candidate = parent;
    }
  }
}

async function assertCanonicalOutputContainment(
  invocationRoot: string,
  output: string,
): Promise<void> {
  let canonicalAncestor: string;
  try {
    canonicalAncestor = await nearestExistingCanonicalPath(output);
  } catch {
    throw new Error("path_escape:catalog_output");
  }
  if (!isContainedPath(invocationRoot, canonicalAncestor)) {
    throw new Error("path_escape:catalog_output");
  }
}

export async function resolveCatalogSyncPaths(
  args: readonly string[],
  storeRootInput: string | undefined,
  invocationRootInput: string | undefined,
): Promise<CatalogSyncPaths> {
  const parsed = parseCatalogSyncArgs(args);
  if (
    !isAbsolute(parsed.source) ||
    storeRootInput === undefined ||
    !isAbsolute(storeRootInput) ||
    invocationRootInput === undefined ||
    !isAbsolute(invocationRootInput)
  ) {
    throw new Error("source_mismatch:catalog_roots");
  }
  const source = await realpath(parsed.source);
  const invocationRoot = await canonicalGitWorktreeRoot(invocationRootInput);
  if (basename(source) !== "plugins") {
    throw new Error("source_mismatch:plugins_root");
  }
  const repositoryRoot = dirname(source);
  const storeRoot = resolve(storeRootInput);
  const output = isAbsolute(parsed.output)
    ? resolve(parsed.output)
    : resolve(invocationRoot, parsed.output);
  if (
    !isContainedPath(invocationRoot, output) ||
    isContainedPath(repositoryRoot, output) ||
    isContainedPath(storeRoot, output)
  ) {
    throw new Error("path_escape:catalog_output");
  }
  await assertCanonicalOutputContainment(invocationRoot, output);
  return Object.freeze({
    source,
    invocationRoot,
    repositoryRoot,
    storeRoot,
    output,
    commit: parsed.commit,
  });
}

async function assertRegularOutputIfPresent(output: string): Promise<void> {
  try {
    const stats = await lstat(output);
    if (!stats.isFile() || stats.isSymbolicLink()) {
      throw new Error("path_escape:catalog_output");
    }
  } catch (error: unknown) {
    if (!isNotFoundError(error)) throw error;
  }
}

async function writeContainedCatalogLock(
  paths: CatalogSyncPaths,
  lock: Awaited<ReturnType<typeof importCatalog>>,
): Promise<void> {
  await assertCanonicalOutputContainment(paths.invocationRoot, paths.output);
  const parent = dirname(paths.output);
  await mkdir(parent, { recursive: true });
  const parentIdentity = await snapshotDirectoryIdentity(parent);
  if (!isContainedPath(paths.invocationRoot, parentIdentity.canonicalPath)) {
    throw new Error("path_escape:catalog_output");
  }
  await assertRegularOutputIfPresent(paths.output);
  const temporary = resolve(
    parent,
    `.${basename(paths.output)}.${process.pid}.${randomUUID()}.tmp`,
  );
  try {
    await writeCatalogLock(temporary, lock);
    await assertRegularOutputIfPresent(paths.output);
    await assertDirectoryIdentity(parentIdentity);
    await rename(temporary, paths.output);
    await assertDirectoryIdentity(parentIdentity);
  } finally {
    await rm(temporary, { force: true });
  }
}

export async function syncCatalog(
  args: readonly string[],
  storeRoot: string | undefined,
  invocationRoot: string | undefined,
): Promise<void> {
  const paths = await resolveCatalogSyncPaths(args, storeRoot, invocationRoot);
  const lock = await importCatalog(paths.source, {
    repositoryRoot: paths.repositoryRoot,
    sourceCommit: paths.commit,
    sourceUrl: OPENAI_PLUGINS_SOURCE_URL,
    storeRoot: paths.storeRoot,
    clock: (): Date => new Date(),
    expectedPluginCount: PINNED_OPENAI_PLUGIN_COUNT,
  });
  await writeContainedCatalogLock(paths, lock);
  process.stdout.write(`${lock.catalogDigest} ${lock.entries.length}\n`);
}

const invokedPath = process.argv[1];
if (
  invokedPath !== undefined &&
  pathToFileURL(fileURLToPath(import.meta.url)).href === pathToFileURL(invokedPath).href
) {
  syncCatalog(
    process.argv.slice(2),
    process.env.ROWBOAT_PLUGIN_STORE,
    process.env.INIT_CWD ?? process.env.ROWBOAT_INVOCATION_ROOT,
  ).catch(
    (error: unknown) => {
      const message = error instanceof Error ? error.message : "catalog sync failed";
      process.stderr.write(`${message}\n`);
      process.exitCode = 1;
    },
  );
}
