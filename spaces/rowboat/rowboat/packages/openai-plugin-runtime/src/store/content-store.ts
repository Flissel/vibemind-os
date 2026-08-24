import { randomUUID } from "node:crypto";
import type { Stats } from "node:fs";
import {
  lstat,
  mkdir,
  readFile,
  readdir,
  realpath,
  rm,
  writeFile,
} from "node:fs/promises";
import {
  basename,
  dirname,
  isAbsolute,
  join,
  relative,
  resolve,
} from "node:path";
import { digestTree } from "../import/digest-service.js";
import {
  isContainedPath,
  PluginSourceSecurityError,
} from "../import/path-guard.js";

export interface ContentStoreConfig {
  readonly storeRoot: string;
  readonly repositoryRoot: string;
  readonly publicationObserver?: ContentStorePublicationObserver;
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
}

const DIGEST_PATTERN = /^[0-9a-f]{64}$/;
const LOCK_ATTEMPTS = 1_000;
const LOCK_RETRY_DELAY_MS = 10;

export type ContentStorePublicationState = "absent" | "complete" | "invalid";

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

async function canonicalizeProspectivePath(candidate: string): Promise<string> {
  const missingNames: string[] = [];
  let cursor = candidate;

  while (true) {
    try {
      await lstat(cursor);
      const canonicalAncestor = await realpath(cursor);
      return resolve(canonicalAncestor, ...missingNames.reverse());
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
  destination: string,
  markerPath: string,
  expectedDigest: string,
): Promise<ContentStorePublicationState> {
  const [destinationPresent, markerState] = await Promise.all([
    pathExists(destination),
    completionMarkerState(markerPath, expectedDigest),
  ]);

  if (!destinationPresent && markerState === "absent") {
    return "absent";
  }

  if (!destinationPresent || markerState !== "valid") {
    return "invalid";
  }

  try {
    return (await digestTree(destination)) === expectedDigest
      ? "complete"
      : "invalid";
  } catch (error: unknown) {
    if (
      isNotFoundError(error) ||
      error instanceof PluginSourceSecurityError
    ) {
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
  lockPath: string,
  destination: string,
  markerPath: string,
  expectedDigest: string,
): Promise<"owned" | "published"> {
  for (let attempt = 0; attempt < LOCK_ATTEMPTS; attempt += 1) {
    try {
      await mkdir(lockPath);
      return "owned";
    } catch (error: unknown) {
      if (!isAlreadyExistsError(error)) {
        throw error;
      }
    }

    if (
      (await inspectPublication(destination, markerPath, expectedDigest)) ===
      "complete"
    ) {
      return "published";
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

async function copyRegularTree(
  sourceRoot: string,
  sourceDirectory: string,
  destinationDirectory: string,
): Promise<void> {
  const names = (await readdir(sourceDirectory)).sort();

  for (const name of names) {
    const source = join(sourceDirectory, name);
    if (!isContainedPath(sourceRoot, source)) {
      throw new PluginSourceSecurityError("path_escape", "source entry escaped root");
    }

    const destination = join(destinationDirectory, name);
    const stats = await lstat(source);

    if (stats.isSymbolicLink()) {
      throw new PluginSourceSecurityError("path_escape", "symbolic link rejected");
    }

    if (stats.isDirectory()) {
      await mkdir(destination);
      await copyRegularTree(sourceRoot, source, destination);
      continue;
    }

    if (stats.isFile()) {
      await writeFile(destination, await readFile(source), { flag: "wx" });
      continue;
    }

    throw new PluginSourceSecurityError("path_escape", "unsupported entry rejected");
  }
}

export class ContentStore {
  private readonly configuredStoreRoot: string;
  private readonly configuredRepositoryRoot: string;
  private readonly publicationObserver: ContentStorePublicationObserver | undefined;

  constructor({
    storeRoot,
    repositoryRoot,
    publicationObserver,
  }: ContentStoreConfig) {
    if (!isAbsolute(storeRoot) || !isAbsolute(repositoryRoot)) {
      throw new PluginSourceSecurityError("path_escape", "roots must be absolute");
    }

    this.configuredStoreRoot = resolve(storeRoot);
    this.configuredRepositoryRoot = resolve(repositoryRoot);
    this.publicationObserver = publicationObserver;
    assertSeparatedRoots(this.configuredStoreRoot, this.configuredRepositoryRoot);
  }

  /**
   * Publication never renames over the digest destination. The writer reserves that
   * directory with exclusive mkdir, fills it from a verified staging tree, then
   * creates an external completion marker with exclusive write. A destination
   * without its valid marker is incomplete and is never returned or removed here.
   * The per-digest lock coordinates cooperating writers; destination reservation
   * remains the no-replace boundary for uncoordinated filesystem races.
   */
  async put(
    pluginRoot: string,
    expectedDigest: string,
  ): Promise<StoredPluginContent> {
    if (!DIGEST_PATTERN.test(expectedDigest)) {
      throw new PluginSourceSecurityError("digest_mismatch", "invalid expected digest");
    }

    const repositoryRoot = await realpath(this.configuredRepositoryRoot);
    const prospectiveStoreRoot = await canonicalizeProspectivePath(
      this.configuredStoreRoot,
    );
    assertSeparatedRoots(prospectiveStoreRoot, repositoryRoot);

    await mkdir(this.configuredStoreRoot, { recursive: true });
    const storeRoot = await realpath(this.configuredStoreRoot);
    assertSeparatedRoots(storeRoot, repositoryRoot);

    if (!isAbsolute(pluginRoot)) {
      throw new PluginSourceSecurityError("path_escape", "plugin root must be absolute");
    }

    const pluginStats = await lstat(pluginRoot);
    if (pluginStats.isSymbolicLink() || !pluginStats.isDirectory()) {
      throw new PluginSourceSecurityError("path_escape", "plugin root must be a directory");
    }

    const canonicalPluginRoot = await realpath(pluginRoot);
    if (!isContainedPath(repositoryRoot, canonicalPluginRoot)) {
      throw new PluginSourceSecurityError("path_escape", "plugin root leaves repository");
    }

    const sourceDigest = await digestTree(canonicalPluginRoot);
    if (sourceDigest !== expectedDigest) {
      throw new PluginSourceSecurityError("digest_mismatch", "source digest differs");
    }

    const destination = join(storeRoot, expectedDigest);
    const markerPath = join(storeRoot, `.complete-${expectedDigest}`);
    const lockPath = join(storeRoot, `.lock-${expectedDigest}`);
    const result = storedContent(expectedDigest, destination);
    assertDirectChildWithPrefix(storeRoot, markerPath, `.complete-${expectedDigest}`);
    assertDirectChildWithPrefix(storeRoot, lockPath, `.lock-${expectedDigest}`);

    const initialState = await inspectPublication(
      destination,
      markerPath,
      expectedDigest,
    );
    await this.publicationObserver?.afterInitialInspect?.(initialState);
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

    await mkdir(temporaryDirectory);
    try {
      await copyRegularTree(
        canonicalPluginRoot,
        canonicalPluginRoot,
        temporaryDirectory,
      );

      const stagedDigest = await digestTree(temporaryDirectory);
      if (stagedDigest !== expectedDigest) {
        throw new PluginSourceSecurityError("digest_mismatch", "staged digest differs");
      }

      const lockState = await acquirePublicationLock(
        lockPath,
        destination,
        markerPath,
        expectedDigest,
      );
      if (lockState === "published") {
        return result;
      }

      try {
        const lockedState = await inspectPublication(
          destination,
          markerPath,
          expectedDigest,
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

        try {
          await mkdir(destination);
        } catch (error: unknown) {
          if (!isAlreadyExistsError(error)) {
            throw error;
          }

          const racedState = await inspectPublication(
            destination,
            markerPath,
            expectedDigest,
          );
          if (racedState === "complete") {
            return result;
          }

          throw new PluginSourceSecurityError(
            "digest_mismatch",
            "destination reservation already exists",
          );
        }

        await this.publicationObserver?.afterReserve?.(destination);

        await copyRegularTree(
          temporaryDirectory,
          temporaryDirectory,
          destination,
        );

        if ((await digestTree(destination)) !== expectedDigest) {
          throw new PluginSourceSecurityError(
            "digest_mismatch",
            "reserved destination digest differs",
          );
        }

        try {
          await writeFile(markerPath, `${expectedDigest}\n`, {
            encoding: "utf8",
            flag: "wx",
          });
        } catch (error: unknown) {
          if (
            !isAlreadyExistsError(error) ||
            (await inspectPublication(destination, markerPath, expectedDigest)) !==
              "complete"
          ) {
            throw new PluginSourceSecurityError(
              "digest_mismatch",
              "completion marker could not be created exclusively",
            );
          }
        }

        if (
          (await inspectPublication(destination, markerPath, expectedDigest)) !==
          "complete"
        ) {
          throw new PluginSourceSecurityError(
            "digest_mismatch",
            "completed publication failed verification",
          );
        }

        return result;
      } finally {
        await rm(lockPath, { recursive: true, force: true });
      }
    } finally {
      await rm(temporaryDirectory, { recursive: true, force: true });
    }
  }
}
