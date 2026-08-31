/**
 * Loads the pinned catalog lock into MongoDB.
 *
 * The web app reads its catalog from the database, never from the lock file, so
 * a deployment without this step fails every plugin path with
 * `catalog_digest_mismatch`. Run it once per database after
 * `mongodb-ensure-indexes`, and again after the pin changes.
 *
 * The write is immutable: loading the same lock twice is a no-op, and a lock
 * whose digest differs from the stored catalog is refused rather than merged.
 *
 * MongoDB must be a replica set: the catalog is written in one transaction so a
 * partially stored catalog can never be read as complete.
 */
import { readFile } from "node:fs/promises";
import { isAbsolute, resolve } from "node:path";
import { PINNED_PLUGIN_CATALOG_DIGEST, validatePluginCatalogLock, type PluginCatalogLock } from "@rowboat/openai-plugin-runtime";

const DEFAULT_LOCK = "config/openai-plugin-catalog.lock.json";
const MAX_LOCK_BYTES = 32 * 1024 * 1024;

export interface CatalogLoadArguments {
  readonly lock: string;
  readonly verifyOnly: boolean;
}

export function parseCatalogLoadArguments(argv: readonly string[]): CatalogLoadArguments {
  const flags = new Map<string, string | true>();
  for (let index = 0; index < argv.length; index += 1) {
    const flag = argv[index]!;
    if (flags.has(flag)) throw new Error("catalog_load_invalid");
    if (flag === "--verify-only") {
      flags.set(flag, true);
      continue;
    }
    if (flag !== "--lock") throw new Error("catalog_load_invalid");
    const value = argv[++index];
    if (value === undefined || value.length === 0 || value.startsWith("-") || /[^A-Za-z0-9._/:\-]/u.test(value)) throw new Error("catalog_load_invalid");
    flags.set(flag, value);
  }
  const lock = flags.get("--lock");
  if (lock !== undefined && typeof lock !== "string") throw new Error("catalog_load_invalid");
  return Object.freeze({ lock: lock ?? DEFAULT_LOCK, verifyOnly: flags.get("--verify-only") === true });
}

export async function readCatalogLock(path: string, root: string): Promise<PluginCatalogLock> {
  const resolved = isAbsolute(path) ? resolve(path) : resolve(root, path);
  const raw = await readFile(resolved, "utf8");
  if (Buffer.byteLength(raw, "utf8") > MAX_LOCK_BYTES) throw new Error("catalog_load_too_large");
  const parsed: unknown = JSON.parse(raw);
  const lock = validatePluginCatalogLock(parsed);
  // The lock file is not authority for what the app accepts: the pin is.
  if (lock.catalogDigest !== PINNED_PLUGIN_CATALOG_DIGEST) throw new Error("catalog_digest_mismatch");
  return lock;
}

export function catalogLoadReport(lock: PluginCatalogLock, stored: PluginCatalogLock | null): Readonly<Record<string, unknown>> {
  return Object.freeze({
    version: 1,
    catalogDigest: lock.catalogDigest,
    sourceCommit: lock.sourceCommit,
    policyVersion: lock.policyVersion,
    entries: lock.entries.length,
    alreadyStored: stored !== null && stored.catalogDigest === lock.catalogDigest,
  });
}

async function main(): Promise<void> {
  const args = parseCatalogLoadArguments(process.argv.slice(2));
  const root = resolve(process.cwd(), "..", "..");
  const lock = await readCatalogLock(args.lock, root);
  const [{ db, mongoClient }, repositoryModule] = await Promise.all([
    import("@/app/lib/mongodb"),
    import("@/src/infrastructure/repositories/mongodb.plugins.repository"),
  ]);
  const repository = new repositoryModule.MongodbPluginsRepository({
    pluginsDatabase: db,
    pluginTransactionRunner: new repositoryModule.MongoPluginTransactionRunner({ pluginsMongoClient: mongoClient }),
  });
  try {
    const before = await repository.getCatalog(lock.catalogDigest);
    if (before === null && !args.verifyOnly) await repository.putCatalog(lock);
    const after = await repository.getCatalog(lock.catalogDigest);
    if (after === null) throw new Error(args.verifyOnly ? "catalog_not_loaded" : "catalog_load_failed");
    process.stdout.write(`${JSON.stringify(catalogLoadReport(lock, before))}\n`);
  } finally {
    await mongoClient.close();
  }
}

if (process.argv[1] !== undefined && process.argv[1].endsWith("load-plugin-catalog.ts")) {
  main().catch((error: unknown) => {
    process.stderr.write(`${error instanceof Error ? error.message : "catalog_load_failed"}\n`);
    process.exitCode = 1;
  });
}
