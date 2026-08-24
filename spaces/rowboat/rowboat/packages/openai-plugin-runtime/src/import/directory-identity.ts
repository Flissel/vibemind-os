import { lstat, realpath, stat } from "node:fs/promises";
import { dirname, isAbsolute, resolve } from "node:path";
import { PluginSourceSecurityError } from "./path-guard.js";

interface ObservedDirectoryIdentity {
  readonly configuredPath: string;
  readonly canonicalPath: string;
  readonly device: bigint;
  readonly inode: bigint;
  readonly parentConfiguredPath: string;
  readonly parentCanonicalPath: string;
  readonly parentDevice: bigint;
  readonly parentInode: bigint;
}

export type DirectoryIdentity = ObservedDirectoryIdentity;

function identitiesMatch(
  expected: ObservedDirectoryIdentity,
  observed: ObservedDirectoryIdentity,
): boolean {
  return (
    expected.configuredPath === observed.configuredPath &&
    expected.canonicalPath === observed.canonicalPath &&
    expected.device === observed.device &&
    expected.inode === observed.inode &&
    expected.parentConfiguredPath === observed.parentConfiguredPath &&
    expected.parentCanonicalPath === observed.parentCanonicalPath &&
    expected.parentDevice === observed.parentDevice &&
    expected.parentInode === observed.parentInode
  );
}

async function observeDirectoryIdentity(
  configuredPath: string,
): Promise<ObservedDirectoryIdentity> {
  if (!isAbsolute(configuredPath)) {
    throw new PluginSourceSecurityError("path_escape", "directory root must be absolute");
  }

  const resolvedPath = resolve(configuredPath);
  const configuredStats = await lstat(resolvedPath, { bigint: true });
  if (configuredStats.isSymbolicLink() || !configuredStats.isDirectory()) {
    throw new PluginSourceSecurityError(
      "path_escape",
      "directory root must be a stable directory",
    );
  }

  const canonicalPath = await realpath(resolvedPath);
  const canonicalStats = await stat(canonicalPath, { bigint: true });
  if (
    !canonicalStats.isDirectory() ||
    configuredStats.dev !== canonicalStats.dev ||
    configuredStats.ino !== canonicalStats.ino
  ) {
    throw new PluginSourceSecurityError(
      "path_escape",
      "directory root identity changed during observation",
    );
  }

  const parentConfiguredPath = dirname(resolvedPath);
  const parentCanonicalPath = await realpath(parentConfiguredPath);
  const parentStats = await stat(parentCanonicalPath, { bigint: true });
  if (!parentStats.isDirectory()) {
    throw new PluginSourceSecurityError(
      "path_escape",
      "directory parent must remain a directory",
    );
  }

  return {
    configuredPath: resolvedPath,
    canonicalPath,
    device: canonicalStats.dev,
    inode: canonicalStats.ino,
    parentConfiguredPath,
    parentCanonicalPath,
    parentDevice: parentStats.dev,
    parentInode: parentStats.ino,
  };
}

/**
 * The configured root and its parent are sampled twice. Portable Node does not
 * expose handle-relative openat/renameat2 operations, so the canonical parent is
 * an operator trust boundary and must not be replaceable by untrusted actors.
 */
export async function snapshotDirectoryIdentity(
  configuredPath: string,
): Promise<DirectoryIdentity> {
  const first = await observeDirectoryIdentity(configuredPath);
  const second = await observeDirectoryIdentity(configuredPath);

  if (!identitiesMatch(first, second)) {
    throw new PluginSourceSecurityError(
      "path_escape",
      "directory or parent identity changed during setup",
    );
  }

  return Object.freeze(first);
}

export async function assertDirectoryIdentity(
  expected: DirectoryIdentity,
): Promise<void> {
  let observed: ObservedDirectoryIdentity;

  try {
    observed = await observeDirectoryIdentity(expected.configuredPath);
  } catch (error: unknown) {
    if (error instanceof PluginSourceSecurityError) {
      throw error;
    }

    throw new PluginSourceSecurityError(
      "path_escape",
      "directory identity could not be revalidated",
    );
  }

  if (!identitiesMatch(expected, observed)) {
    throw new PluginSourceSecurityError(
      "path_escape",
      "directory or parent identity changed",
    );
  }
}
