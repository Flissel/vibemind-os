import { randomUUID } from "node:crypto";
import { chmod, lstat, mkdtemp, open, readdir, realpath, rmdir, unlink, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { basename, dirname, join, resolve } from "node:path";
import { assertDirectoryIdentity, snapshotDirectoryIdentity, type DirectoryIdentity } from "./directory-identity.js";
import { isContainedPath, PluginSourceSecurityError } from "./path-guard.js";

const SNAPSHOT_PREFIX = "rowboat-git-snapshot-";
const SNAPSHOT_KIND = "verified-snapshot";
export const OWNED_TEMP_SENTINEL = ".rowboat-openai-plugin-runtime-owner.json" as const;
const MAX_SENTINEL_BYTES = 1024;
const NONCE_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

interface OwnedTempSentinel {
  readonly version: 2;
  readonly kind: string;
  readonly ownerPid: number;
  readonly createdAt: string;
  readonly nonce: string;
}

export interface OwnedTempRecoveryResult {
  readonly removed: number;
  readonly preserved: number;
}

function validateNamespace(prefix: string, kind: string): void {
  if (!/^rowboat-[a-z0-9-]+-$/.test(prefix) || !/^[a-z0-9-]+$/.test(kind)) {
    throw new PluginSourceSecurityError("path_escape", "owned temp namespace is invalid");
  }
}

function assertOwnedPath(root: string, prefix: string): string {
  const resolved = resolve(root);
  if (dirname(resolved) !== resolve(tmpdir()) || !basename(resolved).startsWith(prefix)) {
    throw new PluginSourceSecurityError("path_escape", "owned temp root is not a direct child");
  }
  return resolved;
}

function serializeSentinel(kind: string, ownerPid: number = process.pid): string {
  return `${JSON.stringify({
    version: 2,
    kind,
    ownerPid,
    createdAt: new Date().toISOString(),
    nonce: randomUUID(),
  } satisfies OwnedTempSentinel)}\n`;
}

function parseSentinel(input: string, expectedKind: string): OwnedTempSentinel | undefined {
  let value: unknown;
  try {
    value = JSON.parse(input) as unknown;
  } catch {
    return undefined;
  }
  if (typeof value !== "object" || value === null || Array.isArray(value)) return undefined;
  const record = value as Record<string, unknown>;
  if (
    Object.keys(record).sort().join(",") !== "createdAt,kind,nonce,ownerPid,version" ||
    record["version"] !== 2 ||
    record["kind"] !== expectedKind ||
    typeof record["ownerPid"] !== "number" ||
    !Number.isSafeInteger(record["ownerPid"]) ||
    record["ownerPid"] <= 0 ||
    typeof record["createdAt"] !== "string" ||
    Number.isNaN(Date.parse(record["createdAt"])) ||
    new Date(record["createdAt"]).toISOString() !== record["createdAt"] ||
    typeof record["nonce"] !== "string" ||
    !NONCE_PATTERN.test(record["nonce"])
  ) return undefined;
  return Object.freeze({
    version: 2,
    kind: expectedKind,
    ownerPid: record["ownerPid"],
    createdAt: record["createdAt"],
    nonce: record["nonce"],
  });
}

async function readSentinelSameHandle(root: DirectoryIdentity, kind: string): Promise<OwnedTempSentinel | undefined> {
  const path = join(root.canonicalPath, OWNED_TEMP_SENTINEL);
  let before;
  try {
    before = await lstat(path, { bigint: true });
  } catch {
    return undefined;
  }
  if (before.isSymbolicLink() || !before.isFile() || before.size <= 0n || before.size > BigInt(MAX_SENTINEL_BYTES)) return undefined;
  const handle = await open(path, "r");
  try {
    const opened = await handle.stat({ bigint: true });
    if (!opened.isFile() || opened.dev !== before.dev || opened.ino !== before.ino || opened.size !== before.size) return undefined;
    const bytes = Buffer.alloc(Number(opened.size));
    const { bytesRead } = await handle.read(bytes, 0, bytes.length, 0);
    if (bytesRead !== bytes.length) return undefined;
    const after = await handle.stat({ bigint: true });
    if (after.dev !== opened.dev || after.ino !== opened.ino || after.size !== opened.size) return undefined;
    await assertDirectoryIdentity(root);
    return parseSentinel(bytes.toString("utf8"), kind);
  } finally {
    await handle.close();
  }
}

async function stableOwnedRoot(root: string, prefix: string): Promise<DirectoryIdentity | undefined> {
  const candidate = assertOwnedPath(root, prefix);
  let stats;
  try {
    stats = await lstat(candidate);
  } catch {
    return undefined;
  }
  if (stats.isSymbolicLink() || !stats.isDirectory()) return undefined;
  try {
    if (await realpath(candidate) !== candidate) return undefined;
    const identity = await snapshotDirectoryIdentity(candidate);
    return identity.parentCanonicalPath === resolve(tmpdir()) ? identity : undefined;
  } catch {
    return undefined;
  }
}

function ownerIsDead(ownerPid: number): boolean {
  try {
    process.kill(ownerPid, 0);
    return false;
  } catch (error: unknown) {
    return typeof error === "object" && error !== null && "code" in error && error.code === "ESRCH";
  }
}

async function inventoryOwnedTree(
  root: DirectoryIdentity,
  directory: string,
  files: string[],
  links: string[],
  directories: string[],
): Promise<void> {
  await assertDirectoryIdentity(root);
  for (const name of await readdir(directory)) {
    const candidate = join(directory, name);
    if (!isContainedPath(root.canonicalPath, candidate)) throw new PluginSourceSecurityError("path_escape", "owned temp entry escaped");
    const stats = await lstat(candidate);
    if (stats.isSymbolicLink()) links.push(candidate);
    else if (stats.isDirectory()) {
      await inventoryOwnedTree(root, candidate, files, links, directories);
      directories.push(candidate);
    } else if (stats.isFile()) files.push(candidate);
    else throw new PluginSourceSecurityError("path_escape", "owned temp entry type rejected");
  }
  await assertDirectoryIdentity(root);
}

async function removeOwnedTree(root: DirectoryIdentity): Promise<void> {
  const files: string[] = [];
  const links: string[] = [];
  const directories: string[] = [];
  await inventoryOwnedTree(root, root.canonicalPath, files, links, directories);
  const sentinelPath = join(root.canonicalPath, OWNED_TEMP_SENTINEL);
  for (const link of links) {
    await assertDirectoryIdentity(root);
    if (!(await lstat(link)).isSymbolicLink()) {
      throw new PluginSourceSecurityError("path_escape", "owned temp link changed");
    }
    await unlink(link);
  }
  for (const file of files) {
    if (file === sentinelPath) continue;
    await assertDirectoryIdentity(root);
    const stats = await lstat(file);
    if (stats.isSymbolicLink() || !stats.isFile()) throw new PluginSourceSecurityError("path_escape", "owned temp file changed");
    await chmod(file, 0o600);
    await unlink(file);
  }
  for (const directory of directories) {
    await assertDirectoryIdentity(root);
    const stats = await lstat(directory);
    if (stats.isSymbolicLink() || !stats.isDirectory()) throw new PluginSourceSecurityError("path_escape", "owned temp directory changed");
    await chmod(directory, 0o700);
    await rmdir(directory);
  }
  await assertDirectoryIdentity(root);
  const sentinelStats = await lstat(sentinelPath);
  if (sentinelStats.isSymbolicLink() || !sentinelStats.isFile()) {
    throw new PluginSourceSecurityError("path_escape", "owned temp sentinel changed");
  }
  await chmod(sentinelPath, 0o600);
  await unlink(sentinelPath);
  await assertDirectoryIdentity(root);
  await chmod(root.canonicalPath, 0o700);
  await rmdir(root.canonicalPath);
}

export async function createOwnedTempRoot(prefix: string, kind: string): Promise<string> {
  validateNamespace(prefix, kind);
  const root = assertOwnedPath(await mkdtemp(join(tmpdir(), prefix)), prefix);
  try {
    await writeFile(join(root, OWNED_TEMP_SENTINEL), serializeSentinel(kind), { flag: "wx" });
    return root;
  } catch (error: unknown) {
    const identity = await stableOwnedRoot(root, prefix);
    if (identity !== undefined) await removeOwnedTree(identity);
    throw error;
  }
}

export async function removeOwnedTempRoot(root: string, prefix: string, kind: string): Promise<void> {
  validateNamespace(prefix, kind);
  const identity = await stableOwnedRoot(root, prefix);
  if (identity === undefined) throw new PluginSourceSecurityError("path_escape", "owned temp identity is invalid");
  const sentinel = await readSentinelSameHandle(identity, kind);
  if (sentinel === undefined || sentinel.ownerPid !== process.pid) throw new PluginSourceSecurityError("path_escape", "owned temp sentinel is invalid");
  await removeOwnedTree(identity);
}

export async function recoverStaleOwnedTempRoots(prefix: string, kind: string): Promise<OwnedTempRecoveryResult> {
  validateNamespace(prefix, kind);
  let removed = 0;
  let preserved = 0;
  const parent = resolve(tmpdir());
  for (const name of await readdir(parent)) {
    if (!name.startsWith(prefix)) continue;
    const root = await stableOwnedRoot(join(parent, name), prefix);
    if (root === undefined) {
      preserved += 1;
      continue;
    }
    const sentinel = await readSentinelSameHandle(root, kind);
    if (sentinel === undefined || !ownerIsDead(sentinel.ownerPid)) {
      preserved += 1;
      continue;
    }
    await removeOwnedTree(root);
    removed += 1;
  }
  return Object.freeze({ removed, preserved });
}

export function createSnapshotTempRoot(): Promise<string> {
  return createOwnedTempRoot(SNAPSHOT_PREFIX, SNAPSHOT_KIND);
}

export function removeSnapshotTempRoot(root: string): Promise<void> {
  return removeOwnedTempRoot(root, SNAPSHOT_PREFIX, SNAPSHOT_KIND);
}

export function recoverStaleSnapshotTempRoots(): Promise<OwnedTempRecoveryResult> {
  return recoverStaleOwnedTempRoots(SNAPSHOT_PREFIX, SNAPSHOT_KIND);
}
