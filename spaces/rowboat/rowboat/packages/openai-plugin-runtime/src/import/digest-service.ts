import { createHash, type Hash } from "node:crypto";
import { lstat, readFile, readdir } from "node:fs/promises";
import { join, relative, sep } from "node:path";
import { PluginSourceSecurityError } from "./path-guard.js";

type DigestEntry =
  | { readonly type: "directory"; readonly relativePath: string }
  | {
      readonly type: "file";
      readonly relativePath: string;
      readonly bytes: Buffer;
    };

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

function uint64(value: number): Buffer {
  const framed = Buffer.alloc(8);
  framed.writeBigUInt64BE(BigInt(value));
  return framed;
}

function hashEntry(hash: Hash, entry: DigestEntry): void {
  const pathBytes = Buffer.from(entry.relativePath, "utf8");
  hash.update(
    entry.type === "directory" ? Buffer.from([0x44]) : Buffer.from([0x46]),
  );
  hash.update(uint32(pathBytes.length));
  hash.update(pathBytes);

  if (entry.type === "file") {
    hash.update(uint64(entry.bytes.length));
    hash.update(entry.bytes);
  }
}

async function collectEntries(
  root: string,
  directory: string,
  entries: DigestEntry[],
): Promise<void> {
  const names = await readdir(directory);

  for (const name of names) {
    const candidate = join(directory, name);
    const stats = await lstat(candidate);
    const relativePath = normalizeRelativePath(root, candidate);

    if (stats.isSymbolicLink()) {
      throw new PluginSourceSecurityError("path_escape", "symbolic link rejected");
    }

    if (stats.isDirectory()) {
      entries.push({ type: "directory", relativePath });
      await collectEntries(root, candidate, entries);
      continue;
    }

    if (stats.isFile()) {
      entries.push({
        type: "file",
        relativePath,
        bytes: await readFile(candidate),
      });
      continue;
    }

    throw new PluginSourceSecurityError("path_escape", "unsupported entry rejected");
  }
}

export async function digestTree(root: string): Promise<string> {
  const rootStats = await lstat(root);

  if (rootStats.isSymbolicLink() || !rootStats.isDirectory()) {
    throw new PluginSourceSecurityError("path_escape", "tree root must be a directory");
  }

  const entries: DigestEntry[] = [];
  await collectEntries(root, root, entries);
  entries.sort(compareEntries);

  const hash = createHash("sha256");
  hash.update("rowboat-plugin-tree-v1\0", "utf8");
  for (const entry of entries) {
    hashEntry(hash, entry);
  }

  return hash.digest("hex");
}
