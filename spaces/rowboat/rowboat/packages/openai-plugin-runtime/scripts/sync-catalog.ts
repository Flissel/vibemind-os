import { execFile } from "node:child_process";
import { readFile, realpath } from "node:fs/promises";
import { basename, dirname, isAbsolute, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { promisify } from "node:util";
import {
  importCatalog,
  assertCatalogOutputContained,
  OPENAI_PLUGINS_SOURCE_URL,
  parseCatalogSyncArgs,
  PINNED_OPENAI_PLUGIN_COUNT,
  writeCatalogLock,
  type PluginCatalogLock,
} from "../src/index.js";
import { isContainedPath } from "../src/import/path-guard.js";

const execFileAsync = promisify(execFile);

export interface CatalogSyncPaths {
  readonly source: string;
  readonly invocationRoot: string;
  readonly repositoryRoot: string;
  readonly storeRoot: string;
  readonly output: string;
  readonly commit: string;
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
  await assertCatalogOutputContained(invocationRoot, output);
  return Object.freeze({
    source,
    invocationRoot,
    repositoryRoot,
    storeRoot,
    output,
    commit: parsed.commit,
  });
}

/**
 * The `importedAt` a prior lock at this exact output path recorded, but only
 * when that prior lock's `catalogDigest` matches the freshly computed one.
 * `catalogDigest` is computed over everything except `importedAt` (see
 * `pluginCatalogDigest`'s explicit skip of that key), so an equal digest means
 * the fresh payload is content-identical to the prior one — every entry,
 * matched by plugin name, included, since a single differing entry would
 * already change the digest. Returns `undefined` when there is no prior lock,
 * it isn't parseable, or its content actually changed — the caller then
 * stamps a fresh timestamp, exactly as before.
 */
async function preservedImportedAt(output: string, freshCatalogDigest: string): Promise<string | undefined> {
  let raw: string;
  try {
    raw = await readFile(output, "utf8");
  } catch {
    return undefined;
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return undefined;
  }
  if (parsed === null || typeof parsed !== "object") return undefined;
  const candidate = parsed as { readonly catalogDigest?: unknown; readonly importedAt?: unknown };
  if (typeof candidate.catalogDigest !== "string" || typeof candidate.importedAt !== "string") return undefined;
  return candidate.catalogDigest === freshCatalogDigest ? candidate.importedAt : undefined;
}

/**
 * Every entry shares the catalog's single `importedAt` (the lock schema
 * requires it — see `validatePluginCatalogLock`'s per-entry equality check
 * against the root), so there is no independent per-entry stamp to carry
 * forward on its own; stamping the whole lock is the only shape the schema
 * allows, and it is exactly what "unchanged digest" already establishes for
 * every entry at once.
 */
function withImportedAt(lock: PluginCatalogLock, importedAt: string): PluginCatalogLock {
  return {
    ...lock,
    importedAt,
    entries: lock.entries.map((entry) => ({ ...entry, importedAt })),
  };
}

export async function syncCatalog(
  args: readonly string[],
  storeRoot: string | undefined,
  invocationRoot: string | undefined,
): Promise<void> {
  const paths = await resolveCatalogSyncPaths(args, storeRoot, invocationRoot);
  const freshLock = await importCatalog(paths.source, {
    repositoryRoot: paths.repositoryRoot,
    sourceCommit: paths.commit,
    sourceUrl: OPENAI_PLUGINS_SOURCE_URL,
    storeRoot: paths.storeRoot,
    clock: (): Date => new Date(),
    expectedPluginCount: PINNED_OPENAI_PLUGIN_COUNT,
  });
  const preserved = await preservedImportedAt(paths.output, freshLock.catalogDigest);
  const lock = preserved === undefined ? freshLock : withImportedAt(freshLock, preserved);
  await writeCatalogLock(paths.output, lock, {
    containmentRoot: paths.invocationRoot,
  });
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
