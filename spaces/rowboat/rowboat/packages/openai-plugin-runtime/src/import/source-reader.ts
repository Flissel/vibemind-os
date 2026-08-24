import { execFile } from "node:child_process";
import { createHash } from "node:crypto";
import { chmod, mkdir, realpath, writeFile } from "node:fs/promises";
import { basename, dirname, isAbsolute, join, relative, resolve, sep } from "node:path";
import { promisify } from "node:util";
import { ContentStore } from "../store/content-store.js";
import {
  assertDirectoryIdentity,
  snapshotDirectoryIdentity,
  type DirectoryIdentity,
} from "./directory-identity.js";
import { digestTree, type GitFileMode } from "./digest-service.js";
import { isContainedPath, PluginSourceSecurityError } from "./path-guard.js";
import {
  createSnapshotTempRoot,
  recoverStaleSnapshotTempRoots,
  removeSnapshotTempRoot,
} from "./snapshot-temp.js";

const execFileAsync = promisify(execFile);
const COMMIT_PATTERN = /^[0-9a-f]{40}$/;
const BLOB_PATTERN = /^[0-9a-f]{40,64}$/;
const MAX_GIT_TEXT_BYTES = 16 * 1024 * 1024;
export const OPENAI_PLUGINS_SOURCE_URL = "https://github.com/openai/plugins.git" as const;
export const VERIFIED_SNAPSHOT_LIMITS = Object.freeze({
  maxBlobBytes: 64 * 1024 * 1024,
  maxTotalBytes: 256 * 1024 * 1024,
  maxFileCount: 20_000,
  maxRelativeDepth: 64,
  gitTimeoutMs: 30_000,
});

export interface PinnedSourceRequest {
  readonly repositoryRoot: string;
  readonly expectedCommit: string;
  readonly sourceUrl: string;
  readonly storeRoot: string;
}

interface VerifiedContextDetails {
  readonly repositoryIdentity: DirectoryIdentity;
  readonly storeRoot: string;
  readonly snapshots: Map<string, VerifiedPluginSnapshot>;
}

const VERIFIED_CONTEXT_TOKEN = Symbol("VerifiedPinnedSource");
const contextDetails = new WeakMap<VerifiedPinnedSource, VerifiedContextDetails>();

export class VerifiedPinnedSource {
  readonly repositoryRoot: string;
  readonly sourceCommit: string;
  readonly sourceUrl: string;

  constructor(token: symbol, repositoryRoot: string, sourceCommit: string, sourceUrl: string) {
    if (token !== VERIFIED_CONTEXT_TOKEN) {
      throw new PluginSourceSecurityError("source_mismatch", "verified source context cannot be constructed");
    }
    this.repositoryRoot = repositoryRoot;
    this.sourceCommit = sourceCommit;
    this.sourceUrl = sourceUrl;
    Object.freeze(this);
  }
}

interface GitTreeBlob {
  readonly oid: string;
  readonly path: string;
  readonly mode: GitFileMode;
  readonly size: number;
}

interface VerifiedPluginSnapshot {
  readonly path: string;
  readonly digest: string;
  readonly manifestDigest: string;
  readonly fileModes: Readonly<Record<string, GitFileMode>>;
}

export interface VerifiedPluginDigests {
  readonly treeDigest: string;
  readonly manifestDigest: string;
}

function validSourceUrl(sourceUrl: string): boolean {
  try {
    const parsed = new URL(sourceUrl);
    return (
      sourceUrl === OPENAI_PLUGINS_SOURCE_URL &&
      parsed.protocol === "https:" &&
      parsed.username === "" &&
      parsed.password === "" &&
      parsed.search === "" &&
      parsed.hash === ""
    );
  } catch {
    return false;
  }
}

export async function runGitVerificationCommand(
  repositoryRoot: string,
  args: readonly string[],
  options: { readonly maxBuffer: number; readonly timeoutMs?: number },
): Promise<Buffer> {
  try {
    const result = await execFileAsync("git", ["-C", repositoryRoot, ...args], {
      encoding: "buffer",
      maxBuffer: options.maxBuffer,
      timeout: options.timeoutMs ?? VERIFIED_SNAPSHOT_LIMITS.gitTimeoutMs,
      killSignal: "SIGKILL",
      windowsHide: true,
    });
    return result.stdout;
  } catch {
    throw new PluginSourceSecurityError("source_mismatch", "Git verification failed");
  }
}

async function gitBuffer(repositoryRoot: string, args: readonly string[], maxBuffer: number): Promise<Buffer> {
  return runGitVerificationCommand(repositoryRoot, args, { maxBuffer });
}

async function gitText(repositoryRoot: string, args: readonly string[]): Promise<string> {
  return (await gitBuffer(repositoryRoot, args, MAX_GIT_TEXT_BYTES)).toString("utf8");
}

async function verifyGitState(
  repositoryIdentity: DirectoryIdentity,
  expectedCommit: string,
  sourceUrl: string,
): Promise<void> {
  await assertDirectoryIdentity(repositoryIdentity);
  const root = repositoryIdentity.canonicalPath;
  const head = (await gitText(root, ["rev-parse", "HEAD"])).trim();
  if (head !== expectedCommit || !COMMIT_PATTERN.test(head)) {
    throw new PluginSourceSecurityError("source_mismatch", "HEAD does not match pin");
  }
  const status = await gitText(root, [
    "status",
    "--porcelain=v1",
    "--untracked-files=all",
    "--ignored",
  ]);
  if (status.trim() !== "") {
    throw new PluginSourceSecurityError("source_mismatch", "source contains changed or extra files");
  }
  const origin = (await gitText(root, ["remote", "get-url", "origin"])).trim();
  if (origin !== sourceUrl) {
    throw new PluginSourceSecurityError("source_mismatch", "origin URL does not match pin");
  }
  await assertDirectoryIdentity(repositoryIdentity);
}

export async function assertPinnedSource({
  repositoryRoot,
  expectedCommit,
  sourceUrl,
  storeRoot,
}: PinnedSourceRequest): Promise<VerifiedPinnedSource> {
  if (
    !COMMIT_PATTERN.test(expectedCommit) ||
    !validSourceUrl(sourceUrl) ||
    !isAbsolute(repositoryRoot) ||
    !isAbsolute(storeRoot)
  ) {
    throw new PluginSourceSecurityError("source_mismatch", "invalid pinned source identity");
  }
  let repositoryIdentity: DirectoryIdentity;
  try {
    repositoryIdentity = await snapshotDirectoryIdentity(await realpath(repositoryRoot));
  } catch {
    throw new PluginSourceSecurityError("source_mismatch", "repository root is unavailable");
  }
  await verifyGitState(repositoryIdentity, expectedCommit, sourceUrl);
  const canonicalStoreRoot = resolve(storeRoot);
  if (
    isContainedPath(repositoryIdentity.canonicalPath, canonicalStoreRoot) ||
    isContainedPath(canonicalStoreRoot, repositoryIdentity.canonicalPath)
  ) {
    throw new PluginSourceSecurityError("path_escape", "snapshot store overlaps source");
  }
  const context = new VerifiedPinnedSource(
    VERIFIED_CONTEXT_TOKEN,
    repositoryIdentity.canonicalPath,
    expectedCommit,
    sourceUrl,
  );
  contextDetails.set(context, {
    repositoryIdentity,
    storeRoot: canonicalStoreRoot,
    snapshots: new Map(),
  });
  return context;
}

export async function assertVerifiedPinnedSource(
  context: VerifiedPinnedSource,
  pluginRoot: string,
  sourceUrl: string,
  sourceCommit: string,
): Promise<void> {
  const details = contextDetails.get(context);
  if (
    details === undefined ||
    context.sourceUrl !== sourceUrl ||
    context.sourceCommit !== sourceCommit
  ) {
    throw new PluginSourceSecurityError("source_mismatch", "verified source context mismatch");
  }
  await verifyGitState(details.repositoryIdentity, context.sourceCommit, context.sourceUrl);
  let canonicalPluginRoot: string;
  try {
    canonicalPluginRoot = await realpath(pluginRoot);
  } catch {
    throw new PluginSourceSecurityError("source_mismatch", "plugin root is unavailable");
  }
  if (!isContainedPath(context.repositoryRoot, canonicalPluginRoot)) {
    throw new PluginSourceSecurityError("source_mismatch", "plugin root is outside verified source");
  }
  await assertDirectoryIdentity(details.repositoryIdentity);
}

export function parseGitTreeInventory(output: Buffer, pluginRelativePath: string): readonly GitTreeBlob[] {
  const entries: GitTreeBlob[] = [];
  let totalBytes = 0;
  for (const raw of output.toString("utf8").split("\0")) {
    if (raw === "") continue;
    const match = /^(100644|100755) blob ([0-9a-f]{40,64})\s+(\d+)\t(.+)$/.exec(raw);
    if (match === null) {
      throw new PluginSourceSecurityError("source_mismatch", "unsupported Git tree entry");
    }
    const mode = match[1] as GitFileMode | undefined;
    const oid = match[2];
    const sizeText = match[3];
    const path = match[4];
    if (mode === undefined || oid === undefined || sizeText === undefined || path === undefined || !BLOB_PATTERN.test(oid)) {
      throw new PluginSourceSecurityError("source_mismatch", "malformed Git tree entry");
    }
    const size = Number(sizeText);
    if (!Number.isSafeInteger(size) || size < 0 || size > VERIFIED_SNAPSHOT_LIMITS.maxBlobBytes) {
      throw new PluginSourceSecurityError("source_mismatch", "snapshot blob exceeds budget");
    }
    const relativePath = relative(pluginRelativePath, path).split(sep).join("/");
    if (
      relativePath === ".." ||
      relativePath.startsWith("../") ||
      relativePath.includes("\\") ||
      isAbsolute(relativePath)
    ) {
      throw new PluginSourceSecurityError("path_escape", "Git plugin path escaped");
    }
    const depth = relativePath.split("/").length;
    if (depth > VERIFIED_SNAPSHOT_LIMITS.maxRelativeDepth) {
      throw new PluginSourceSecurityError("source_mismatch", "snapshot depth exceeds budget");
    }
    totalBytes += size;
    if (
      entries.length + 1 > VERIFIED_SNAPSHOT_LIMITS.maxFileCount ||
      totalBytes > VERIFIED_SNAPSHOT_LIMITS.maxTotalBytes
    ) {
      throw new PluginSourceSecurityError("source_mismatch", "snapshot inventory exceeds budget");
    }
    entries.push({ oid, path: relativePath, mode, size });
  }
  if (entries.length === 0) {
    throw new PluginSourceSecurityError("source_mismatch", "plugin has no committed files");
  }
  return entries.sort((left, right) => left.path < right.path ? -1 : left.path > right.path ? 1 : 0);
}

export async function stageVerifiedPluginSnapshot(
  context: VerifiedPinnedSource,
  pluginRoot: string,
): Promise<VerifiedPluginSnapshot> {
  const details = contextDetails.get(context);
  if (details === undefined) {
    throw new PluginSourceSecurityError("source_mismatch", "verified source context missing");
  }
  await assertVerifiedPinnedSource(context, pluginRoot, context.sourceUrl, context.sourceCommit);
  const canonicalPluginRoot = await realpath(pluginRoot);
  const cached = details.snapshots.get(canonicalPluginRoot);
  if (cached !== undefined) return cached;
  const pluginRelativePath = relative(context.repositoryRoot, canonicalPluginRoot).split(sep).join("/");
  const tree = parseGitTreeInventory(
    await gitBuffer(
      context.repositoryRoot,
      ["ls-tree", "-r", "-l", "-z", "--full-tree", context.sourceCommit, "--", pluginRelativePath],
      MAX_GIT_TEXT_BYTES,
    ),
    pluginRelativePath,
  );
  await recoverStaleSnapshotTempRoots();
  const materializedRepository = await createSnapshotTempRoot();
  const materializedPlugin = join(materializedRepository, basename(canonicalPluginRoot));
  let manifestDigest: string | undefined;
  try {
    await mkdir(materializedPlugin);
    for (const entry of tree) {
      const destination = join(materializedPlugin, ...entry.path.split("/"));
      if (!isContainedPath(materializedPlugin, destination)) {
        throw new PluginSourceSecurityError("path_escape", "snapshot entry escaped");
      }
      await mkdir(dirname(destination), { recursive: true });
      const bytes = await gitBuffer(
        context.repositoryRoot,
        ["cat-file", "blob", entry.oid],
        VERIFIED_SNAPSHOT_LIMITS.maxBlobBytes,
      );
      if (bytes.length !== entry.size) {
        throw new PluginSourceSecurityError("source_mismatch", "Git blob size differs from inventory");
      }
      if (entry.path === ".codex-plugin/plugin.json") {
        manifestDigest = createHash("sha256").update(bytes).digest("hex");
      }
      await writeFile(destination, bytes, { flag: "wx" });
      if (process.platform !== "win32") {
        await chmod(destination, entry.mode === "100755" ? 0o755 : 0o644);
      }
    }
    const frozenModes = Object.freeze(
      Object.fromEntries(tree.map((entry) => [entry.path, entry.mode])),
    ) as Readonly<Record<string, GitFileMode>>;
    const modeResolver = (path: string): GitFileMode | undefined => frozenModes[path];
    const digest = await digestTree(materializedPlugin, { fileModeResolver: modeResolver });
    if (manifestDigest === undefined) {
      throw new PluginSourceSecurityError("source_mismatch", "committed plugin manifest missing");
    }
    const stored = await new ContentStore({
      repositoryRoot: materializedRepository,
      storeRoot: details.storeRoot,
      fileModeResolver: modeResolver,
    }).put(materializedPlugin, digest);
    await assertVerifiedPinnedSource(context, pluginRoot, context.sourceUrl, context.sourceCommit);
    const snapshot = Object.freeze({
      path: stored.path,
      digest: stored.digest,
      manifestDigest,
      fileModes: frozenModes,
    });
    details.snapshots.set(canonicalPluginRoot, snapshot);
    return snapshot;
  } finally {
    await removeSnapshotTempRoot(materializedRepository);
  }
}

export async function getVerifiedPluginFileModes(
  context: VerifiedPinnedSource,
  pluginRoot: string,
): Promise<Readonly<Record<string, GitFileMode>>> {
  return (await stageVerifiedPluginSnapshot(context, pluginRoot)).fileModes;
}

export async function getVerifiedPluginDigests(
  context: VerifiedPinnedSource,
  pluginRoot: string,
): Promise<VerifiedPluginDigests> {
  const snapshot = await stageVerifiedPluginSnapshot(context, pluginRoot);
  return Object.freeze({
    treeDigest: snapshot.digest,
    manifestDigest: snapshot.manifestDigest,
  });
}
