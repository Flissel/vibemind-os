import { randomUUID } from "node:crypto";
import type { Stats } from "node:fs";
import {
  chmod,
  lstat,
  mkdir,
  open,
  readFile,
  readdir,
  realpath,
  rmdir,
  unlink,
  writeFile,
  type FileHandle,
} from "node:fs/promises";
import {
  basename,
  dirname,
  isAbsolute,
  join,
  relative,
  resolve,
} from "node:path";
import {
  assertDirectoryIdentity,
  snapshotDirectoryIdentity,
  type DirectoryIdentity,
} from "../import/directory-identity.js";
import {
  digestTree,
  type FileModeResolver,
  type GitFileMode,
} from "../import/digest-service.js";
import {
  streamContainedRegularFile,
  type SourceFileReadOptions,
} from "../import/safe-file-stream.js";
import {
  isContainedPath,
  PluginSourceSecurityError,
} from "../import/path-guard.js";

export interface ContentStoreConfig {
  readonly storeRoot: string;
  readonly repositoryRoot: string;
  readonly publicationObserver?: ContentStorePublicationObserver;
  readonly sourceFileReadOptions?: SourceFileReadOptions;
  readonly fileModeResolver?: FileModeResolver;
}

export interface ContentStoreEntry {
  readonly digest: string;
  readonly path: string;
}

export type StoredPluginContent = ContentStoreEntry;

export interface ContentStorePublicationObserver {
  afterInitialInspect?(state: ContentStorePublicationState): Promise<void>;
  beforeReserve?(destination: string): Promise<void>;
  afterReserve?(destination: string): Promise<void>;
  beforeTemporaryCleanup?(
    storeRoot: string,
    temporaryDirectory: string,
  ): Promise<void>;
}

const DIGEST_PATTERN = /^[0-9a-f]{64}$/;
const LOCK_ATTEMPTS = 1_000;
const LOCK_RETRY_DELAY_MS = 10;

interface DirectoryModeStats {
  readonly mode: bigint;
  isDirectory(): boolean;
  isSymbolicLink(): boolean;
}

interface DirectoryModeOperations {
  chmod(path: string, mode: number): Promise<void>;
  stat(path: string): Promise<DirectoryModeStats>;
}

const DIRECTORY_MODE_OPERATIONS: DirectoryModeOperations = {
  chmod,
  stat: (path) => lstat(path, { bigint: true }),
};

export type ContentStorePublicationState = "absent" | "complete" | "invalid";

type PublicationLockResult =
  | { readonly state: "owned"; readonly identity: DirectoryIdentity }
  | { readonly state: "published" };

interface CleanupInventory {
  readonly files: string[];
  readonly directories: string[];
}

function pathsOverlap(first: string, second: string): boolean {
  return isContainedPath(first, second) || isContainedPath(second, first);
}

function assertSeparatedRoots(storeRoot: string, repositoryRoot: string): void {
  if (pathsOverlap(storeRoot, repositoryRoot)) {
    throw new PluginSourceSecurityError(
      "path_escape",
      "store and repository roots overlap",
    );
  }
}

function isNotFoundError(error: unknown): boolean {
  return (
    typeof error === "object" &&
    error !== null &&
    "code" in error &&
    error.code === "ENOENT"
  );
}

function isAlreadyExistsError(error: unknown): boolean {
  return (
    typeof error === "object" &&
    error !== null &&
    "code" in error &&
    error.code === "EEXIST"
  );
}

export async function enforceOwnedDirectoryMode(
  path: string,
  platform: NodeJS.Platform = process.platform,
  operations: DirectoryModeOperations = DIRECTORY_MODE_OPERATIONS,
): Promise<void> {
  if (platform === "win32") return;
  try {
    await operations.chmod(path, 0o700);
    const stats = await operations.stat(path);
    if (
      stats.isSymbolicLink() ||
      !stats.isDirectory() ||
      Number(stats.mode & 0o777n) !== 0o700
    ) {
      throw new PluginSourceSecurityError(
        "digest_mismatch",
        "owned directory mode differs",
      );
    }
  } catch (error: unknown) {
    if (error instanceof PluginSourceSecurityError) throw error;
    throw new PluginSourceSecurityError(
      "digest_mismatch",
      "owned directory mode could not be enforced",
    );
  }
}

async function assertIdentities(
  ...identities: readonly DirectoryIdentity[]
): Promise<void> {
  for (const identity of identities) {
    await assertDirectoryIdentity(identity);
  }
}

async function initializeStoreRoot(
  candidate: string,
  repositoryIdentity: DirectoryIdentity,
): Promise<DirectoryIdentity> {
  const missingNames: string[] = [];
  let cursor = candidate;
  let ancestorIdentity: DirectoryIdentity;

  while (true) {
    try {
      const stats = await lstat(cursor);
      if (stats.isSymbolicLink() || !stats.isDirectory()) {
        throw new PluginSourceSecurityError(
          "path_escape",
          "store initialization ancestor must be a stable directory",
        );
      }

      ancestorIdentity = await snapshotDirectoryIdentity(cursor);
      break;
    } catch (error: unknown) {
      if (!isNotFoundError(error)) {
        throw error;
      }

      const parent = dirname(cursor);
      if (parent === cursor) {
        throw error;
      }

      missingNames.push(basename(cursor));
      cursor = parent;
    }
  }

  const missingSegments = missingNames.reverse();
  const prospectiveStoreRoot = resolve(
    ancestorIdentity.canonicalPath,
    ...missingSegments,
  );
  assertSeparatedRoots(
    prospectiveStoreRoot,
    repositoryIdentity.canonicalPath,
  );

  let currentIdentity = ancestorIdentity;
  for (const segment of missingSegments) {
    const childPath = join(currentIdentity.configuredPath, segment);
    await assertIdentities(repositoryIdentity, currentIdentity);

    let created = false;
    try {
      await mkdir(childPath);
      created = true;
    } catch (error: unknown) {
      if (!isAlreadyExistsError(error)) {
        throw error;
      }
    }
    if (created) await enforceOwnedDirectoryMode(childPath);

    await assertIdentities(repositoryIdentity, currentIdentity);
    const childIdentity = await snapshotDirectoryIdentity(childPath);
    if (
      childIdentity.canonicalPath !== resolve(currentIdentity.canonicalPath, segment) ||
      childIdentity.parentCanonicalPath !== currentIdentity.canonicalPath ||
      childIdentity.parentDevice !== currentIdentity.device ||
      childIdentity.parentInode !== currentIdentity.inode
    ) {
      throw new PluginSourceSecurityError(
        "path_escape",
        "store initialization child escaped its validated parent",
      );
    }

    await assertIdentities(repositoryIdentity, currentIdentity, childIdentity);
    currentIdentity = childIdentity;
  }

  await assertIdentities(repositoryIdentity, currentIdentity);
  return currentIdentity;
}

async function pathExists(candidate: string): Promise<boolean> {
  try {
    await lstat(candidate);
    return true;
  } catch (error: unknown) {
    if (isNotFoundError(error)) {
      return false;
    }

    throw error;
  }
}

async function completionMarkerState(
  markerPath: string,
  expectedDigest: string,
): Promise<"absent" | "valid" | "invalid"> {
  let stats: Stats;

  try {
    stats = await lstat(markerPath);
  } catch (error: unknown) {
    if (isNotFoundError(error)) {
      return "absent";
    }

    throw error;
  }

  if (stats.isSymbolicLink() || !stats.isFile()) {
    return "invalid";
  }

  try {
    return (await readFile(markerPath, "utf8")) === `${expectedDigest}\n`
      ? "valid"
      : "invalid";
  } catch (error: unknown) {
    if (isNotFoundError(error)) {
      return "invalid";
    }

    throw error;
  }
}

async function inspectPublication(
  storeIdentity: DirectoryIdentity,
  destination: string,
  markerPath: string,
  expectedDigest: string,
  fileModeResolver: FileModeResolver | undefined,
): Promise<ContentStorePublicationState> {
  await assertDirectoryIdentity(storeIdentity);
  const [destinationPresent, markerState] = await Promise.all([
    pathExists(destination),
    completionMarkerState(markerPath, expectedDigest),
  ]);

  if (!destinationPresent && markerState === "absent") {
    await assertDirectoryIdentity(storeIdentity);
    return "absent";
  }

  if (!destinationPresent || markerState !== "valid") {
    await assertDirectoryIdentity(storeIdentity);
    return "invalid";
  }

  try {
    const state =
      (await digestTree(destination, { fileModeResolver })) === expectedDigest ? "complete" : "invalid";
    await assertDirectoryIdentity(storeIdentity);
    return state;
  } catch (error: unknown) {
    if (isNotFoundError(error) || error instanceof PluginSourceSecurityError) {
      await assertDirectoryIdentity(storeIdentity);
      return "invalid";
    }

    throw error;
  }
}

function waitForLockRetry(): Promise<void> {
  return new Promise((resolveRetry) => {
    setTimeout(resolveRetry, LOCK_RETRY_DELAY_MS);
  });
}

async function acquirePublicationLock(
  storeIdentity: DirectoryIdentity,
  lockPath: string,
  destination: string,
  markerPath: string,
  expectedDigest: string,
  fileModeResolver: FileModeResolver | undefined,
): Promise<PublicationLockResult> {
  for (let attempt = 0; attempt < LOCK_ATTEMPTS; attempt += 1) {
    await assertDirectoryIdentity(storeIdentity);
    try {
      await mkdir(lockPath);
      await enforceOwnedDirectoryMode(lockPath);
      await assertDirectoryIdentity(storeIdentity);
      return {
        state: "owned",
        identity: await snapshotDirectoryIdentity(lockPath),
      };
    } catch (error: unknown) {
      if (!isAlreadyExistsError(error)) {
        throw error;
      }
    }

    if (
      (await inspectPublication(
        storeIdentity,
        destination,
        markerPath,
        expectedDigest,
        fileModeResolver,
      )) === "complete"
    ) {
      return { state: "published" };
    }

    await waitForLockRetry();
  }

  throw new PluginSourceSecurityError(
    "digest_mismatch",
    "publication lock remained unavailable",
  );
}

function storedContent(digest: string, path: string): StoredPluginContent {
  return Object.freeze({ digest, path });
}

function assertDirectChildWithPrefix(
  root: string,
  candidate: string,
  prefix: string,
): void {
  const candidateRelative = relative(root, candidate);
  if (
    dirname(candidateRelative) !== "." ||
    !basename(candidateRelative).startsWith(prefix)
  ) {
    throw new PluginSourceSecurityError("path_escape", "invalid store metadata path");
  }
}

async function assertContainedDirectory(
  root: DirectoryIdentity,
  directory: string,
): Promise<void> {
  await assertDirectoryIdentity(root);
  let canonicalDirectory: string;

  try {
    canonicalDirectory = await realpath(directory);
  } catch {
    throw new PluginSourceSecurityError(
      "path_escape",
      "directory changed during traversal",
    );
  }

  if (!isContainedPath(root.canonicalPath, canonicalDirectory)) {
    throw new PluginSourceSecurityError("path_escape", "directory leaves owned root");
  }

  const stats = await lstat(directory);
  if (stats.isSymbolicLink() || !stats.isDirectory()) {
    throw new PluginSourceSecurityError("path_escape", "directory is not stable");
  }
}

async function writeChunk(handle: FileHandle, chunk: Buffer): Promise<void> {
  let offset = 0;
  while (offset < chunk.length) {
    const { bytesWritten } = await handle.write(
      chunk,
      offset,
      chunk.length - offset,
      null,
    );
    if (bytesWritten === 0) {
      throw new PluginSourceSecurityError(
        "digest_mismatch",
        "destination write made no progress",
      );
    }

    offset += bytesWritten;
  }
}

export async function enforceMaterializedGitMode(
  handle: Pick<FileHandle, "chmod" | "stat">,
  mode: GitFileMode,
  platform: NodeJS.Platform = process.platform,
): Promise<void> {
  if (platform === "win32") return;

  const expectedMode = mode === "100755" ? 0o755 : 0o644;
  try {
    await handle.chmod(expectedMode);
    const stats = await handle.stat({ bigint: true });
    if (!stats.isFile() || Number(stats.mode & 0o777n) !== expectedMode) {
      throw new PluginSourceSecurityError(
        "digest_mismatch",
        "materialized Git file mode differs",
      );
    }
  } catch (error: unknown) {
    if (error instanceof PluginSourceSecurityError) throw error;
    throw new PluginSourceSecurityError(
      "digest_mismatch",
      "materialized Git file mode could not be enforced",
    );
  }
}

async function copyRegularFile(
  sourceRoot: DirectoryIdentity,
  source: string,
  destinationRoot: DirectoryIdentity,
  storeIdentity: DirectoryIdentity,
  destination: string,
  options: SourceFileReadOptions,
  mode: "100644" | "100755",
): Promise<void> {
  let destinationHandle: FileHandle | undefined;
  let expectedSize = 0n;
  let writtenSize = 0n;

  try {
    await streamContainedRegularFile(sourceRoot, source, options, {
      async onOpen(size): Promise<void> {
        await assertIdentities(destinationRoot, storeIdentity);
        destinationHandle = await open(
          destination,
          "wx",
          mode === "100755" ? 0o755 : 0o644,
        );
        expectedSize = size;
      },
      async onChunk(chunk): Promise<void> {
        if (destinationHandle === undefined) {
          throw new PluginSourceSecurityError(
            "digest_mismatch",
            "destination was not opened before copy",
          );
        }

        await writeChunk(destinationHandle, chunk);
        writtenSize += BigInt(chunk.length);
      },
    });

    if (destinationHandle === undefined || writtenSize !== expectedSize) {
      throw new PluginSourceSecurityError(
        "digest_mismatch",
        "copied file length differs",
      );
    }

    await enforceMaterializedGitMode(destinationHandle, mode);

    const destinationStats = await destinationHandle.stat({ bigint: true });
    if (!destinationStats.isFile() || destinationStats.size !== expectedSize) {
      throw new PluginSourceSecurityError(
        "digest_mismatch",
        "destination file verification failed",
      );
    }

    await assertIdentities(destinationRoot, storeIdentity);
  } finally {
    await destinationHandle?.close();
  }
}

async function copyRegularTree(
  sourceRoot: DirectoryIdentity,
  sourceDirectory: string,
  destinationRoot: DirectoryIdentity,
  destinationDirectory: string,
  storeIdentity: DirectoryIdentity,
  options: SourceFileReadOptions,
  fileModeResolver: FileModeResolver | undefined,
): Promise<void> {
  await assertContainedDirectory(sourceRoot, sourceDirectory);
  await assertIdentities(destinationRoot, storeIdentity);
  const names = (await readdir(sourceDirectory)).sort();

  for (const name of names) {
    const source = join(sourceDirectory, name);
    const destination = join(destinationDirectory, name);
    if (
      !isContainedPath(sourceRoot.canonicalPath, source) ||
      !isContainedPath(destinationRoot.canonicalPath, destination)
    ) {
      throw new PluginSourceSecurityError("path_escape", "copy entry escaped root");
    }

    const stats = await lstat(source);
    if (stats.isSymbolicLink()) {
      throw new PluginSourceSecurityError("path_escape", "symbolic link rejected");
    }

    if (stats.isDirectory()) {
      await assertIdentities(sourceRoot, destinationRoot, storeIdentity);
      await mkdir(destination);
      await enforceOwnedDirectoryMode(destination);
      await copyRegularTree(
        sourceRoot,
        source,
        destinationRoot,
        destination,
        storeIdentity,
        options,
        fileModeResolver,
      );
      continue;
    }

    if (stats.isFile()) {
      await copyRegularFile(
        sourceRoot,
        source,
        destinationRoot,
        storeIdentity,
        destination,
        options,
        fileModeResolver?.(relative(sourceRoot.canonicalPath, source).replaceAll("\\", "/")) ??
          (process.platform !== "win32" && (stats.mode & 0o111) !== 0 ? "100755" : "100644"),
      );
      continue;
    }

    throw new PluginSourceSecurityError("path_escape", "unsupported entry rejected");
  }

  await assertContainedDirectory(sourceRoot, sourceDirectory);
  await assertIdentities(destinationRoot, storeIdentity);
}

async function inventoryCleanupTree(
  ownedRoot: DirectoryIdentity,
  storeIdentity: DirectoryIdentity,
  directory: string,
  inventory: CleanupInventory,
): Promise<void> {
  await assertIdentities(storeIdentity, ownedRoot);
  await assertContainedDirectory(ownedRoot, directory);
  const names = await readdir(directory);

  for (const name of names) {
    const candidate = join(directory, name);
    const stats = await lstat(candidate);
    if (stats.isSymbolicLink()) {
      throw new PluginSourceSecurityError(
        "path_escape",
        "cleanup refuses symbolic links",
      );
    }

    if (stats.isDirectory()) {
      await inventoryCleanupTree(
        ownedRoot,
        storeIdentity,
        candidate,
        inventory,
      );
      inventory.directories.push(candidate);
      continue;
    }

    if (stats.isFile()) {
      inventory.files.push(candidate);
      continue;
    }

    throw new PluginSourceSecurityError(
      "path_escape",
      "cleanup refuses unsupported entries",
    );
  }

  await assertIdentities(storeIdentity, ownedRoot);
}

async function removeOwnedTree(
  ownedRoot: DirectoryIdentity,
  storeIdentity: DirectoryIdentity,
): Promise<void> {
  await assertIdentities(storeIdentity, ownedRoot);
  const inventory: CleanupInventory = { files: [], directories: [] };
  await inventoryCleanupTree(
    ownedRoot,
    storeIdentity,
    ownedRoot.canonicalPath,
    inventory,
  );
  await assertIdentities(storeIdentity, ownedRoot);

  for (const file of inventory.files) {
    await assertIdentities(storeIdentity, ownedRoot);
    const stats = await lstat(file);
    if (stats.isSymbolicLink() || !stats.isFile()) {
      throw new PluginSourceSecurityError(
        "path_escape",
        "cleanup file identity changed",
      );
    }

    await unlink(file);
  }

  for (const directory of inventory.directories) {
    await assertIdentities(storeIdentity, ownedRoot);
    const stats = await lstat(directory);
    if (stats.isSymbolicLink() || !stats.isDirectory()) {
      throw new PluginSourceSecurityError(
        "path_escape",
        "cleanup directory identity changed",
      );
    }

    await rmdir(directory);
  }

  await assertIdentities(storeIdentity, ownedRoot);
  await rmdir(ownedRoot.canonicalPath);
  await assertDirectoryIdentity(storeIdentity);
}

async function removeOwnedEmptyDirectory(
  ownedDirectory: DirectoryIdentity,
  storeIdentity: DirectoryIdentity,
): Promise<void> {
  await assertIdentities(storeIdentity, ownedDirectory);
  if ((await readdir(ownedDirectory.canonicalPath)).length !== 0) {
    throw new PluginSourceSecurityError(
      "path_escape",
      "owned lock directory is not empty",
    );
  }

  await assertIdentities(storeIdentity, ownedDirectory);
  await rmdir(ownedDirectory.canonicalPath);
  await assertDirectoryIdentity(storeIdentity);
}

export class ContentStore {
  private readonly configuredStoreRoot: string;
  private readonly configuredRepositoryRoot: string;
  private readonly publicationObserver: ContentStorePublicationObserver | undefined;
  private readonly sourceFileReadOptions: SourceFileReadOptions;
  private readonly fileModeResolver: FileModeResolver | undefined;

  constructor({
    storeRoot,
    repositoryRoot,
    publicationObserver,
    sourceFileReadOptions,
    fileModeResolver,
  }: ContentStoreConfig) {
    if (!isAbsolute(storeRoot) || !isAbsolute(repositoryRoot)) {
      throw new PluginSourceSecurityError("path_escape", "roots must be absolute");
    }

    this.configuredStoreRoot = resolve(storeRoot);
    this.configuredRepositoryRoot = resolve(repositoryRoot);
    this.publicationObserver = publicationObserver;
    this.sourceFileReadOptions = sourceFileReadOptions ?? {};
    this.fileModeResolver = fileModeResolver;
    assertSeparatedRoots(this.configuredStoreRoot, this.configuredRepositoryRoot);
  }

  /**
   * Publication never replaces the digest destination. The store root, repository,
   * plugin root, and owned temporary directories are tracked by canonical path and
   * device/inode identity. Portable Node lacks handle-relative openat/renameat2, so
   * repository and store parent directories are trusted operator boundaries that
   * must not be writable/replaced by an untrusted same-user actor.
   */
  async put(
    pluginRoot: string,
    expectedDigest: string,
  ): Promise<StoredPluginContent> {
    if (!DIGEST_PATTERN.test(expectedDigest)) {
      throw new PluginSourceSecurityError("digest_mismatch", "invalid expected digest");
    }

    const repositoryIdentity = await snapshotDirectoryIdentity(
      this.configuredRepositoryRoot,
    );
    const storeIdentity = await initializeStoreRoot(
      this.configuredStoreRoot,
      repositoryIdentity,
    );
    assertSeparatedRoots(storeIdentity.canonicalPath, repositoryIdentity.canonicalPath);

    if (!isAbsolute(pluginRoot)) {
      throw new PluginSourceSecurityError("path_escape", "plugin root must be absolute");
    }

    const pluginIdentity = await snapshotDirectoryIdentity(pluginRoot);
    if (
      !isContainedPath(
        repositoryIdentity.canonicalPath,
        pluginIdentity.canonicalPath,
      )
    ) {
      throw new PluginSourceSecurityError("path_escape", "plugin root leaves repository");
    }

    const sourceDigest = await digestTree(
      pluginIdentity.canonicalPath,
      { ...this.sourceFileReadOptions, fileModeResolver: this.fileModeResolver },
    );
    await assertIdentities(repositoryIdentity, storeIdentity, pluginIdentity);
    if (sourceDigest !== expectedDigest) {
      throw new PluginSourceSecurityError("digest_mismatch", "source digest differs");
    }

    const storeRoot = storeIdentity.canonicalPath;
    const destination = join(storeRoot, expectedDigest);
    const markerPath = join(storeRoot, `.complete-${expectedDigest}`);
    const lockPath = join(storeRoot, `.lock-${expectedDigest}`);
    const result = storedContent(expectedDigest, destination);
    assertDirectChildWithPrefix(storeRoot, markerPath, `.complete-${expectedDigest}`);
    assertDirectChildWithPrefix(storeRoot, lockPath, `.lock-${expectedDigest}`);

    const initialState = await inspectPublication(
      storeIdentity,
      destination,
      markerPath,
      expectedDigest,
      this.fileModeResolver,
    );
    await this.publicationObserver?.afterInitialInspect?.(initialState);
    await assertIdentities(repositoryIdentity, storeIdentity, pluginIdentity);
    if (initialState === "complete") {
      return result;
    }

    const temporaryDirectory = join(
      storeRoot,
      `.tmp-${expectedDigest}-${randomUUID()}`,
    );
    assertDirectChildWithPrefix(
      storeRoot,
      temporaryDirectory,
      `.tmp-${expectedDigest}-`,
    );

    await assertIdentities(repositoryIdentity, storeIdentity, pluginIdentity);
    await mkdir(temporaryDirectory);
    await enforceOwnedDirectoryMode(temporaryDirectory);
    const temporaryIdentity = await snapshotDirectoryIdentity(temporaryDirectory);
    try {
      await copyRegularTree(
        pluginIdentity,
        pluginIdentity.canonicalPath,
        temporaryIdentity,
        temporaryIdentity.canonicalPath,
        storeIdentity,
        this.sourceFileReadOptions,
        this.fileModeResolver,
      );
      await assertIdentities(
        repositoryIdentity,
        storeIdentity,
        pluginIdentity,
        temporaryIdentity,
      );

      const stagedDigest = await digestTree(temporaryIdentity.canonicalPath, {
        fileModeResolver: this.fileModeResolver,
      });
      await assertIdentities(
        repositoryIdentity,
        storeIdentity,
        pluginIdentity,
        temporaryIdentity,
      );
      if (stagedDigest !== expectedDigest) {
        throw new PluginSourceSecurityError("digest_mismatch", "staged digest differs");
      }

      const lockResult = await acquirePublicationLock(
        storeIdentity,
        lockPath,
        destination,
        markerPath,
        expectedDigest,
        this.fileModeResolver,
      );
      if (lockResult.state === "published") {
        return result;
      }

      try {
        await assertIdentities(
          repositoryIdentity,
          storeIdentity,
          pluginIdentity,
          temporaryIdentity,
        );
        const lockedState = await inspectPublication(
          storeIdentity,
          destination,
          markerPath,
          expectedDigest,
          this.fileModeResolver,
        );
        if (lockedState === "complete") {
          return result;
        }

        if (lockedState === "invalid") {
          throw new PluginSourceSecurityError(
            "digest_mismatch",
            "existing publication is incomplete or invalid",
          );
        }

        await this.publicationObserver?.beforeReserve?.(destination);
        await assertIdentities(
          repositoryIdentity,
          storeIdentity,
          pluginIdentity,
          temporaryIdentity,
        );

        try {
          await mkdir(destination);
          await enforceOwnedDirectoryMode(destination);
        } catch (error: unknown) {
          if (!isAlreadyExistsError(error)) {
            throw error;
          }

          const racedState = await inspectPublication(
            storeIdentity,
            destination,
            markerPath,
            expectedDigest,
            this.fileModeResolver,
          );
          if (racedState === "complete") {
            return result;
          }

          throw new PluginSourceSecurityError(
            "digest_mismatch",
            "destination reservation already exists",
          );
        }

        const destinationIdentity = await snapshotDirectoryIdentity(destination);
        await this.publicationObserver?.afterReserve?.(destination);
        await assertIdentities(
          repositoryIdentity,
          storeIdentity,
          pluginIdentity,
          temporaryIdentity,
          destinationIdentity,
        );

        await copyRegularTree(
          temporaryIdentity,
          temporaryIdentity.canonicalPath,
          destinationIdentity,
          destinationIdentity.canonicalPath,
          storeIdentity,
          {},
          this.fileModeResolver,
        );
        await assertIdentities(storeIdentity, destinationIdentity);

        if ((await digestTree(destinationIdentity.canonicalPath, {
          fileModeResolver: this.fileModeResolver,
        })) !== expectedDigest) {
          throw new PluginSourceSecurityError(
            "digest_mismatch",
            "reserved destination digest differs",
          );
        }

        await assertIdentities(
          repositoryIdentity,
          storeIdentity,
          pluginIdentity,
          temporaryIdentity,
          destinationIdentity,
        );
        try {
          await writeFile(markerPath, `${expectedDigest}\n`, {
            encoding: "utf8",
            flag: "wx",
          });
        } catch (error: unknown) {
          if (
            !isAlreadyExistsError(error) ||
            (await inspectPublication(
              storeIdentity,
              destination,
              markerPath,
              expectedDigest,
              this.fileModeResolver,
            )) !== "complete"
          ) {
            throw new PluginSourceSecurityError(
              "digest_mismatch",
              "completion marker could not be created exclusively",
            );
          }
        }

        if (
          (await inspectPublication(
            storeIdentity,
            destination,
            markerPath,
            expectedDigest,
            this.fileModeResolver,
          )) !== "complete"
        ) {
          throw new PluginSourceSecurityError(
            "digest_mismatch",
            "completed publication failed verification",
          );
        }

        await assertIdentities(
          repositoryIdentity,
          storeIdentity,
          pluginIdentity,
          temporaryIdentity,
          destinationIdentity,
        );
        return result;
      } finally {
        await removeOwnedEmptyDirectory(lockResult.identity, storeIdentity);
      }
    } finally {
      await this.publicationObserver?.beforeTemporaryCleanup?.(
        storeRoot,
        temporaryDirectory,
      );
      await removeOwnedTree(temporaryIdentity, storeIdentity);
    }
  }
}
