import { constants, type BigIntStats } from "node:fs";
import { lstat, open, realpath, type FileHandle } from "node:fs/promises";
import { resolve } from "node:path";
import {
  assertDirectoryIdentity,
  type DirectoryIdentity,
} from "./directory-identity.js";
import {
  isContainedPath,
  PluginSourceSecurityError,
} from "./path-guard.js";

const DEFAULT_CHUNK_SIZE = 64 * 1024;
const MAXIMUM_CHUNK_SIZE = 1024 * 1024;

export interface SourceFileReadObserver {
  beforeOpen?(candidate: string): Promise<void> | void;
  onFileOpen?(candidate: string): void;
  onChunk?(candidate: string, bytesRead: number): void;
  onFileClose?(candidate: string): void;
}

export interface SourceFileReadOptions {
  readonly chunkSize?: number;
  readonly fileObserver?: SourceFileReadObserver;
}

export interface SourceFileStreamSink {
  onOpen(size: bigint): Promise<void> | void;
  onChunk(chunk: Buffer): Promise<void> | void;
}

function validatedChunkSize(options: SourceFileReadOptions): number {
  const chunkSize = options.chunkSize ?? DEFAULT_CHUNK_SIZE;
  if (
    !Number.isSafeInteger(chunkSize) ||
    chunkSize < 1 ||
    chunkSize > MAXIMUM_CHUNK_SIZE
  ) {
    throw new PluginSourceSecurityError(
      "path_escape",
      "source read chunk size is invalid",
    );
  }

  return chunkSize;
}

async function canonicalContainedFile(
  root: DirectoryIdentity,
  candidate: string,
): Promise<string> {
  const resolvedCandidate = resolve(candidate);
  if (!isContainedPath(root.canonicalPath, resolvedCandidate)) {
    throw new PluginSourceSecurityError("path_escape", "file leaves source root");
  }

  let canonicalCandidate: string;
  try {
    canonicalCandidate = await realpath(resolvedCandidate);
  } catch {
    throw new PluginSourceSecurityError(
      "path_escape",
      "file path changed before open",
    );
  }

  if (!isContainedPath(root.canonicalPath, canonicalCandidate)) {
    throw new PluginSourceSecurityError("path_escape", "file leaves source root");
  }

  return canonicalCandidate;
}

export async function streamContainedRegularFile(
  root: DirectoryIdentity,
  candidate: string,
  options: SourceFileReadOptions,
  sink: SourceFileStreamSink,
): Promise<bigint> {
  const chunkSize = validatedChunkSize(options);
  const resolvedCandidate = resolve(candidate);
  await assertDirectoryIdentity(root);
  await options.fileObserver?.beforeOpen?.(resolvedCandidate);
  await assertDirectoryIdentity(root);

  const canonicalBeforeOpen = await canonicalContainedFile(root, resolvedCandidate);
  let pathStats: BigIntStats;
  try {
    pathStats = await lstat(resolvedCandidate, { bigint: true });
  } catch {
    throw new PluginSourceSecurityError(
      "path_escape",
      "file path changed before open",
    );
  }

  if (pathStats.isSymbolicLink() || !pathStats.isFile()) {
    throw new PluginSourceSecurityError(
      "path_escape",
      "source entry is not a regular file",
    );
  }

  const noFollowFlag =
    typeof constants.O_NOFOLLOW === "number" ? constants.O_NOFOLLOW : 0;
  let handle: FileHandle;
  try {
    handle = await open(resolvedCandidate, constants.O_RDONLY | noFollowFlag);
  } catch {
    throw new PluginSourceSecurityError(
      "path_escape",
      "source file could not be opened without following links",
    );
  }

  let observerOpened = false;
  try {
    const openedStats = await handle.stat({ bigint: true });
    if (
      !openedStats.isFile() ||
      openedStats.dev !== pathStats.dev ||
      openedStats.ino !== pathStats.ino
    ) {
      throw new PluginSourceSecurityError(
        "path_escape",
        "opened file identity differs from source entry",
      );
    }

    const canonicalAfterOpen = await canonicalContainedFile(root, resolvedCandidate);
    let pathAfterOpen: BigIntStats;
    try {
      pathAfterOpen = await lstat(resolvedCandidate, { bigint: true });
    } catch {
      throw new PluginSourceSecurityError(
        "path_escape",
        "file path changed during open",
      );
    }
    if (
      canonicalAfterOpen !== canonicalBeforeOpen ||
      pathAfterOpen.isSymbolicLink() ||
      !pathAfterOpen.isFile() ||
      pathAfterOpen.dev !== openedStats.dev ||
      pathAfterOpen.ino !== openedStats.ino
    ) {
      throw new PluginSourceSecurityError(
        "path_escape",
        "file identity changed during open",
      );
    }

    await assertDirectoryIdentity(root);
    options.fileObserver?.onFileOpen?.(resolvedCandidate);
    observerOpened = true;
    await sink.onOpen(openedStats.size);

    const buffer = Buffer.allocUnsafe(chunkSize);
    let totalBytes = 0n;
    while (true) {
      const { bytesRead } = await handle.read(buffer, 0, buffer.length, null);
      if (bytesRead === 0) {
        break;
      }

      totalBytes += BigInt(bytesRead);
      if (totalBytes > openedStats.size) {
        throw new PluginSourceSecurityError(
          "path_escape",
          "source file grew while being read",
        );
      }

      options.fileObserver?.onChunk?.(resolvedCandidate, bytesRead);
      await sink.onChunk(buffer.subarray(0, bytesRead));
    }

    const finalStats = await handle.stat({ bigint: true });
    if (
      totalBytes !== openedStats.size ||
      finalStats.size !== openedStats.size ||
      finalStats.dev !== openedStats.dev ||
      finalStats.ino !== openedStats.ino ||
      finalStats.mtimeNs !== openedStats.mtimeNs ||
      finalStats.ctimeNs !== openedStats.ctimeNs
    ) {
      throw new PluginSourceSecurityError(
        "path_escape",
        "source file changed while being read",
      );
    }

    await assertDirectoryIdentity(root);
    return totalBytes;
  } finally {
    await handle.close();
    if (observerOpened) {
      options.fileObserver?.onFileClose?.(resolvedCandidate);
    }
  }
}
