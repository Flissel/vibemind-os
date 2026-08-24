import { createHash, type Hash } from "node:crypto";
import { lstat, readdir, realpath } from "node:fs/promises";
import { join, relative, sep } from "node:path";
import {
  assertDirectoryIdentity,
  snapshotDirectoryIdentity,
  type DirectoryIdentity,
} from "./directory-identity.js";
import {
  streamContainedRegularFile,
  type SourceFileReadOptions,
} from "./safe-file-stream.js";
import {
  isContainedPath,
  PluginSourceSecurityError,
} from "./path-guard.js";

type DigestEntry =
  | {
      readonly type: "directory";
      readonly relativePath: string;
      readonly candidatePath: string;
    }
  | {
      readonly type: "file";
      readonly relativePath: string;
      readonly candidatePath: string;
    };

export type DigestTreeOptions = SourceFileReadOptions;

function normalizeRelativePath(root: string, candidate: string): string {
  return relative(root, candidate).split(sep).join("/");
}

function compareEntries(left: DigestEntry, right: DigestEntry): number {
  if (left.relativePath < right.relativePath) {
    return -1;
  }

  if (left.relativePath > right.relativePath) {
    return 1;
  }

  return 0;
}

function uint32(value: number): Buffer {
  const framed = Buffer.alloc(4);
  framed.writeUInt32BE(value);
  return framed;
}

function uint64(value: bigint): Buffer {
  const framed = Buffer.alloc(8);
  framed.writeBigUInt64BE(value);
  return framed;
}

function hashEntryMetadata(hash: Hash, entry: DigestEntry): void {
  const pathBytes = Buffer.from(entry.relativePath, "utf8");
  hash.update(
    entry.type === "directory" ? Buffer.from([0x44]) : Buffer.from([0x46]),
  );
  hash.update(uint32(pathBytes.length));
  hash.update(pathBytes);
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
      "source directory changed during traversal",
    );
  }

  if (!isContainedPath(root.canonicalPath, canonicalDirectory)) {
    throw new PluginSourceSecurityError(
      "path_escape",
      "source directory leaves tree root",
    );
  }

  const stats = await lstat(directory);
  if (stats.isSymbolicLink() || !stats.isDirectory()) {
    throw new PluginSourceSecurityError(
      "path_escape",
      "source directory is not stable",
    );
  }
}

async function inventoryEntries(
  root: DirectoryIdentity,
  directory: string,
  entries: DigestEntry[],
): Promise<void> {
  await assertContainedDirectory(root, directory);
  const names = await readdir(directory);

  for (const name of names) {
    const candidatePath = join(directory, name);
    const stats = await lstat(candidatePath);
    const relativePath = normalizeRelativePath(root.canonicalPath, candidatePath);

    if (stats.isSymbolicLink()) {
      throw new PluginSourceSecurityError("path_escape", "symbolic link rejected");
    }

    if (stats.isDirectory()) {
      entries.push({ type: "directory", relativePath, candidatePath });
      await inventoryEntries(root, candidatePath, entries);
      continue;
    }

    if (stats.isFile()) {
      entries.push({ type: "file", relativePath, candidatePath });
      continue;
    }

    throw new PluginSourceSecurityError("path_escape", "unsupported entry rejected");
  }

  await assertContainedDirectory(root, directory);
}

/**
 * Hashes metadata plus bounded same-handle file chunks. The root's canonical
 * parent is a trusted operator boundary because portable Node has no openat-style
 * handle-relative traversal that can close the final ancestor-swap syscall gap.
 */
export async function digestTree(
  root: string,
  options: DigestTreeOptions = {},
): Promise<string> {
  const rootIdentity = await snapshotDirectoryIdentity(root);
  const entries: DigestEntry[] = [];
  await inventoryEntries(rootIdentity, rootIdentity.canonicalPath, entries);
  await assertDirectoryIdentity(rootIdentity);
  entries.sort(compareEntries);

  const hash = createHash("sha256");
  hash.update("rowboat-plugin-tree-v1\0", "utf8");
  for (const entry of entries) {
    hashEntryMetadata(hash, entry);
    if (entry.type === "file") {
      await streamContainedRegularFile(
        rootIdentity,
        entry.candidatePath,
        options,
        {
          onOpen(size): void {
            hash.update(uint64(size));
          },
          onChunk(chunk): void {
            hash.update(chunk);
          },
        },
      );
    }
  }

  await assertDirectoryIdentity(rootIdentity);
  return hash.digest("hex");
}
