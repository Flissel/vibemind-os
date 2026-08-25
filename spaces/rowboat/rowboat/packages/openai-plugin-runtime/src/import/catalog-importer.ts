import { createHash, randomUUID } from "node:crypto";
import { lstat, mkdir, readFile, readdir, realpath, rename, rm, writeFile } from "node:fs/promises";
import { basename, dirname, join, resolve } from "node:path";
import { z } from "zod";
import type {
  CatalogComponentAdmission,
  CatalogInventory,
  PluginCatalogEntry,
  PluginCatalogLock,
} from "../domain/catalog.js";
import {
  PINNED_OPENAI_PLUGIN_COUNT,
  PINNED_OPENAI_PLUGINS_COMMIT,
  PLUGIN_SCHEMA_VERSION,
} from "../domain/catalog.js";
import type {
  NormalizedPluginComponent,
  SourceProvenance,
} from "../domain/plugin.js";
import { DEFAULT_POLICY, type PluginPolicy } from "../policy/default-policy.js";
import { evaluateComponentAdmission } from "../policy/capability-policy.js";
import { evaluateLicense } from "../policy/license-policy.js";
import { parsePluginManifest, type PluginManifest } from "../schema/plugin-manifest.js";
import { normalizePlugin } from "./normalize-plugin.js";
import {
  isContainedPath,
  PluginSourceSecurityError,
} from "./path-guard.js";
import {
  assertDirectoryIdentity,
  snapshotDirectoryIdentity,
} from "./directory-identity.js";
import {
  assertPinnedSource,
  OPENAI_PLUGINS_SOURCE_URL,
  stageVerifiedPluginSnapshot,
} from "./source-reader.js";

export interface CatalogImportOptions {
  readonly repositoryRoot: string;
  readonly sourceCommit: string;
  readonly sourceUrl?: string;
  readonly storeRoot: string;
  readonly clock: () => Date;
  readonly policy?: PluginPolicy;
  readonly expectedPluginCount?: number;
}

export interface CatalogLockWriteOptions {
  readonly containmentRoot?: string;
}

const CatalogSyncArgsSchema = z
  .object({
    source: z.string().min(1),
    commit: z.literal(PINNED_OPENAI_PLUGINS_COMMIT),
    output: z.string().min(1),
  })
  .strict();

export type CatalogSyncArgs = z.infer<typeof CatalogSyncArgsSchema>;

export function parseCatalogSyncArgs(args: readonly string[]): CatalogSyncArgs {
  if (args.length !== 6) throw new Error("source_mismatch:catalog_arguments");
  const input: Record<string, string> = {};
  for (let index = 0; index < args.length; index += 2) {
    const flag = args[index];
    const value = args[index + 1];
    if (flag === undefined || value === undefined || !flag.startsWith("--")) {
      throw new Error("source_mismatch:catalog_arguments");
    }
    const key = flag.slice(2);
    if (key in input) throw new Error("source_mismatch:catalog_arguments");
    input[key] = value;
  }
  const result = CatalogSyncArgsSchema.safeParse(input);
  if (!result.success) throw new Error("source_mismatch:catalog_arguments");
  return Object.freeze(result.data);
}

function compareCodePoints(left: string, right: string): number {
  return left < right ? -1 : left > right ? 1 : 0;
}

function canonicalValue(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(canonicalValue);
  if (value !== null && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value)
        .sort(([left], [right]) => compareCodePoints(left, right))
        .map(([key, child]) => [key, canonicalValue(child)]),
    );
  }
  return value;
}

function canonicalJson(value: unknown, indentation?: number): string {
  return JSON.stringify(canonicalValue(value), null, indentation);
}

function catalogDigest(payload: Omit<PluginCatalogLock, "catalogDigest">): string {
  return createHash("sha256").update(canonicalJson(payload)).digest("hex");
}

function capabilityFor(
  component: NormalizedPluginComponent,
  manifest: PluginManifest,
): "mcp_http" | "mcp_process" | "hook_command" | "read" | "write" {
  if (component.kind === "hook") return "hook_command";
  if (component.kind === "mcp") {
    return component.metadata.transport === "http" ? "mcp_http" : "mcp_process";
  }
  return manifest.interface.capabilities?.includes("Write") === true ? "write" : "read";
}

function componentAdmissions(
  manifest: PluginManifest,
  components: readonly NormalizedPluginComponent[],
  policy: PluginPolicy,
): readonly CatalogComponentAdmission[] {
  return components.map((component) => ({
    component,
    admission: evaluateComponentAdmission(
      manifest.license,
      { kind: capabilityFor(component, manifest) },
      policy,
    ),
  }));
}

function emptyInventory(): CatalogInventory {
  return {
    pluginsWithSkills: 0,
    pluginsWithApps: 0,
    pluginsWithAgents: 0,
    pluginsWithCommands: 0,
    pluginsWithMcp: 0,
    pluginsWithCommandHooks: 0,
  };
}

function addInventory(
  inventory: CatalogInventory,
  components: readonly NormalizedPluginComponent[],
): CatalogInventory {
  const kinds = new Set(components.map(({ kind }) => kind));
  return {
    pluginsWithSkills: inventory.pluginsWithSkills + Number(kinds.has("skill")),
    pluginsWithApps: inventory.pluginsWithApps + Number(kinds.has("app")),
    pluginsWithAgents: inventory.pluginsWithAgents + Number(kinds.has("agent")),
    pluginsWithCommands: inventory.pluginsWithCommands + Number(kinds.has("command")),
    pluginsWithMcp: inventory.pluginsWithMcp + Number(kinds.has("mcp")),
    pluginsWithCommandHooks: inventory.pluginsWithCommandHooks + Number(kinds.has("hook")),
  };
}

function requiredCount(options: CatalogImportOptions): number | undefined {
  if (options.expectedPluginCount !== undefined) return options.expectedPluginCount;
  return options.sourceCommit === PINNED_OPENAI_PLUGINS_COMMIT
    ? PINNED_OPENAI_PLUGIN_COUNT
    : undefined;
}

function assertValidImportOptions(options: CatalogImportOptions): void {
  if (
    !Number.isFinite(options.clock().getTime()) ||
    (options.expectedPluginCount !== undefined &&
      (!Number.isSafeInteger(options.expectedPluginCount) || options.expectedPluginCount < 0))
  ) {
    throw new PluginSourceSecurityError("source_mismatch", "catalog import options are invalid");
  }
}

export async function importCatalog(
  pluginsRoot: string,
  options: CatalogImportOptions,
): Promise<PluginCatalogLock> {
  assertValidImportOptions(options);
  const sourceUrl = options.sourceUrl ?? OPENAI_PLUGINS_SOURCE_URL;
  const policy = options.policy ?? DEFAULT_POLICY;
  const importedAt = options.clock().toISOString();
  const canonicalPluginsRoot = await realpath(pluginsRoot);
  const expectedPluginsRoot = await realpath(join(options.repositoryRoot, "plugins"));
  if (canonicalPluginsRoot !== expectedPluginsRoot) {
    throw new PluginSourceSecurityError("source_mismatch", "plugins root is not canonical");
  }
  const verifiedSource = await assertPinnedSource({
    repositoryRoot: options.repositoryRoot,
    expectedCommit: options.sourceCommit,
    sourceUrl,
    storeRoot: options.storeRoot,
  });
  const directoryEntries = await readdir(canonicalPluginsRoot, { withFileTypes: true });
  const directories = directoryEntries
    .filter((entry) => entry.isDirectory())
    .sort((left, right) => compareCodePoints(left.name, right.name));
  const count = requiredCount(options);
  if (count !== undefined && directories.length !== count) {
    throw new Error("source_mismatch:plugin_count");
  }

  let inventory = emptyInventory();
  const licenseCounts = new Map<string, number>();
  const entries: PluginCatalogEntry[] = [];
  for (const directory of directories) {
    const pluginRoot = join(canonicalPluginsRoot, directory.name);
    let snapshot: Awaited<ReturnType<typeof stageVerifiedPluginSnapshot>>;
    try {
      snapshot = await stageVerifiedPluginSnapshot(verifiedSource, pluginRoot);
    } catch (error: unknown) {
      if (
        error instanceof PluginSourceSecurityError &&
        error.message.includes("committed plugin manifest missing")
      ) {
        throw new Error("manifest_invalid:missing_manifest");
      }
      throw error;
    }
    let manifest: PluginManifest;
    try {
      manifest = parsePluginManifest(
        JSON.parse(await readFile(join(snapshot.path, ".codex-plugin", "plugin.json"), "utf8")) as unknown,
      );
    } catch (error: unknown) {
      if ((error as NodeJS.ErrnoException).code === "ENOENT") {
        throw new Error("manifest_invalid:missing_manifest");
      }
      throw error;
    }
    if (basename(pluginRoot) !== manifest.name) {
      throw new Error("manifest_invalid:name_mismatch");
    }
    const provenance: SourceProvenance = {
      sourceUrl,
      sourceCommit: options.sourceCommit,
      pluginName: manifest.name,
      pluginVersion: manifest.version,
      manifestDigest: snapshot.manifestDigest,
      treeDigest: snapshot.digest,
      importedAt,
      schemaVersion: PLUGIN_SCHEMA_VERSION,
      policyVersion: policy.version,
    };
    const normalized = await normalizePlugin(pluginRoot, provenance, verifiedSource);
    const admission = evaluateLicense(manifest.license, policy);
    const entry: PluginCatalogEntry = {
      name: manifest.name,
      ...provenance,
      admission,
      components: componentAdmissions(manifest, normalized.components, policy),
      ...(admission.status === "admitted" ? { storedContentDigest: snapshot.digest } : {}),
    };
    entries.push(entry);
    inventory = addInventory(inventory, normalized.components);
    const declaredLicense = manifest.license ?? "<missing>";
    licenseCounts.set(declaredLicense, (licenseCounts.get(declaredLicense) ?? 0) + 1);
  }

  const licenseDeclarations = Object.fromEntries(licenseCounts);

  const payload: Omit<PluginCatalogLock, "catalogDigest"> = {
    sourceUrl,
    sourceCommit: options.sourceCommit,
    importedAt,
    schemaVersion: PLUGIN_SCHEMA_VERSION,
    policyVersion: policy.version,
    inventory,
    licenseDeclarations,
    entries,
  };
  return canonicalValue({ ...payload, catalogDigest: catalogDigest(payload) }) as PluginCatalogLock;
}

function isNotFoundError(error: unknown): boolean {
  return (error as NodeJS.ErrnoException).code === "ENOENT";
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

export async function assertCatalogOutputContained(
  containmentRoot: string,
  output: string,
): Promise<void> {
  try {
    const canonicalRoot = await realpath(containmentRoot);
    const destination = resolve(output);
    if (!isContainedPath(canonicalRoot, destination)) {
      throw new Error("outside root");
    }
    const canonicalAncestor = await nearestExistingCanonicalPath(destination);
    if (!isContainedPath(canonicalRoot, canonicalAncestor)) {
      throw new Error("canonical ancestor outside root");
    }
  } catch {
    throw new Error("path_escape:catalog_output");
  }
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

export async function writeCatalogLock(
  output: string,
  lock: PluginCatalogLock,
  options: CatalogLockWriteOptions = {},
): Promise<void> {
  const destination = resolve(output);
  if (options.containmentRoot !== undefined) {
    await assertCatalogOutputContained(options.containmentRoot, destination);
  }
  const parent = dirname(destination);
  await mkdir(parent, { recursive: true });
  const parentIdentity = await snapshotDirectoryIdentity(parent);
  if (options.containmentRoot !== undefined) {
    await assertCatalogOutputContained(options.containmentRoot, parentIdentity.canonicalPath);
  }
  await assertRegularOutputIfPresent(destination);
  const temporary = resolve(
    parent,
    `.${basename(destination)}.${process.pid}.${randomUUID()}.tmp`,
  );
  try {
    await writeFile(temporary, `${canonicalJson(lock, 2)}\n`, {
      encoding: "utf8",
      flag: "wx",
    });
    await assertRegularOutputIfPresent(destination);
    await assertDirectoryIdentity(parentIdentity);
    if (options.containmentRoot !== undefined) {
      await assertCatalogOutputContained(options.containmentRoot, parentIdentity.canonicalPath);
    }
    await rename(temporary, destination);
    await assertDirectoryIdentity(parentIdentity);
  } finally {
    await rm(temporary, { force: true });
  }
}
