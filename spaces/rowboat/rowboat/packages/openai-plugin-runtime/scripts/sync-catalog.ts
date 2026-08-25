import { realpath } from "node:fs/promises";
import { basename, dirname, isAbsolute, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import {
  importCatalog,
  OPENAI_PLUGINS_SOURCE_URL,
  parseCatalogSyncArgs,
  PINNED_OPENAI_PLUGIN_COUNT,
  writeCatalogLock,
} from "../src/index.js";
import { isContainedPath } from "../src/import/path-guard.js";

export interface CatalogSyncPaths {
  readonly source: string;
  readonly repositoryRoot: string;
  readonly storeRoot: string;
  readonly output: string;
  readonly commit: string;
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
  const invocationRoot = await realpath(invocationRootInput);
  if (basename(source) !== "plugins") {
    throw new Error("source_mismatch:plugins_root");
  }
  const repositoryRoot = dirname(source);
  const storeRoot = resolve(storeRootInput);
  const output = isAbsolute(parsed.output)
    ? resolve(parsed.output)
    : resolve(invocationRoot, parsed.output);
  if (
    (!isAbsolute(parsed.output) && !isContainedPath(invocationRoot, output)) ||
    isContainedPath(repositoryRoot, output) ||
    isContainedPath(storeRoot, output)
  ) {
    throw new Error("path_escape:catalog_output");
  }
  return Object.freeze({
    source,
    repositoryRoot,
    storeRoot,
    output,
    commit: parsed.commit,
  });
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
  await writeCatalogLock(paths.output, lock);
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
