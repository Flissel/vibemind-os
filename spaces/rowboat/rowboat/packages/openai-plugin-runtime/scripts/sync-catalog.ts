import { isAbsolute, dirname } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import {
  importCatalog,
  OPENAI_PLUGINS_SOURCE_URL,
  parseCatalogSyncArgs,
  PINNED_OPENAI_PLUGIN_COUNT,
  writeCatalogLock,
} from "../src/index.js";

export async function syncCatalog(
  args: readonly string[],
  storeRoot: string | undefined,
): Promise<void> {
  const parsed = parseCatalogSyncArgs(args);
  if (storeRoot === undefined || !isAbsolute(storeRoot)) {
    throw new Error("source_mismatch:ROWBOAT_PLUGIN_STORE");
  }
  const lock = await importCatalog(parsed.source, {
    repositoryRoot: dirname(parsed.source),
    sourceCommit: parsed.commit,
    sourceUrl: OPENAI_PLUGINS_SOURCE_URL,
    storeRoot,
    clock: (): Date => new Date(),
    expectedPluginCount: PINNED_OPENAI_PLUGIN_COUNT,
  });
  await writeCatalogLock(parsed.output, lock);
  process.stdout.write(`${lock.catalogDigest} ${lock.entries.length}\n`);
}

const invokedPath = process.argv[1];
if (
  invokedPath !== undefined &&
  pathToFileURL(fileURLToPath(import.meta.url)).href === pathToFileURL(invokedPath).href
) {
  syncCatalog(process.argv.slice(2), process.env.ROWBOAT_PLUGIN_STORE).catch(
    (error: unknown) => {
      const message = error instanceof Error ? error.message : "catalog sync failed";
      process.stderr.write(`${message}\n`);
      process.exitCode = 1;
    },
  );
}
