import { randomUUID } from "node:crypto";
import {
  lstat,
  mkdir,
  readFile,
  readdir,
  realpath,
  rename,
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
}

const DIGEST_PATTERN = /^[0-9a-f]{64}$/;

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

function isRenameRaceError(error: unknown): boolean {
  if (typeof error !== "object" || error === null || !("code" in error)) {
    return false;
  }

  return (
    error.code === "EEXIST" ||
    error.code === "ENOTEMPTY" ||
    error.code === "EPERM" ||
    error.code === "EACCES"
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

async function verifyExistingDigest(
  destination: string,
  expectedDigest: string,
): Promise<boolean> {
  try {
    return (await digestTree(destination)) === expectedDigest;
  } catch (error: unknown) {
    if (isNotFoundError(error)) {
      return false;
    }

    throw error;
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

  constructor({ storeRoot, repositoryRoot }: ContentStoreConfig) {
    if (!isAbsolute(storeRoot) || !isAbsolute(repositoryRoot)) {
      throw new PluginSourceSecurityError("path_escape", "roots must be absolute");
    }

    this.configuredStoreRoot = resolve(storeRoot);
    this.configuredRepositoryRoot = resolve(repositoryRoot);
    assertSeparatedRoots(this.configuredStoreRoot, this.configuredRepositoryRoot);
  }

  async put(pluginRoot: string, expectedDigest: string): Promise<string> {
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
    if (await verifyExistingDigest(destination, expectedDigest)) {
      return destination;
    }

    try {
      await lstat(destination);
      throw new PluginSourceSecurityError(
        "digest_mismatch",
        "existing digest directory is invalid",
      );
    } catch (error: unknown) {
      if (!isNotFoundError(error)) {
        throw error;
      }
    }

    const temporaryDirectory = join(
      storeRoot,
      `.tmp-${expectedDigest}-${randomUUID()}`,
    );
    const temporaryRelative = relative(storeRoot, temporaryDirectory);
    if (
      dirname(temporaryRelative) !== "." ||
      !basename(temporaryRelative).startsWith(`.tmp-${expectedDigest}-`)
    ) {
      throw new PluginSourceSecurityError("path_escape", "invalid staging path");
    }

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

      try {
        await rename(temporaryDirectory, destination);
      } catch (error: unknown) {
        if (
          !isRenameRaceError(error) ||
          !(await verifyExistingDigest(destination, expectedDigest))
        ) {
          throw error;
        }
      }

      if (!(await verifyExistingDigest(destination, expectedDigest))) {
        throw new PluginSourceSecurityError(
          "digest_mismatch",
          "published digest differs",
        );
      }

      return destination;
    } finally {
      await rm(temporaryDirectory, { recursive: true, force: true });
    }
  }
}
