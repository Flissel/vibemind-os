import { createHash } from "node:crypto";
import { lstat } from "node:fs/promises";
import { extname, relative, resolve, sep } from "node:path";
import { snapshotDirectoryIdentity } from "../import/directory-identity.js";
import {
  isContainedPath,
  PluginSourceSecurityError,
  resolveContainedPath,
} from "../import/path-guard.js";
import { streamContainedRegularFile } from "../import/safe-file-stream.js";

const DEFAULT_MAX_ASSET_BYTES = 1024 * 1024;
const ROWBOAT_PLACEHOLDER_PATH = "rowboat://assets/plugin-placeholder";
const RASTER_MIMES = new Set([
  "image/avif",
  "image/bmp",
  "image/gif",
  "image/jpeg",
  "image/png",
  "image/webp",
  "image/x-icon",
]);
const TEXT_MIMES = new Set(["text/markdown", "text/plain"]);
const ACTIVE_EXTENSIONS = new Set([".htm", ".html", ".svg", ".xhtml", ".xml"]);

export interface AssetNormalizationOptions {
  readonly mime?: string;
  readonly optional?: boolean;
  readonly maxBytes?: number;
}

export interface NormalizedAsset {
  readonly path: string;
  readonly mime: string;
  readonly digest: string;
  readonly placeholder: boolean;
  readonly owner?: "rowboat";
}

export interface ContainedFile {
  readonly bytes: Buffer;
  readonly digest: string;
  readonly path: string;
}

function slashPath(value: string): string {
  return value.split(sep).join("/");
}

function unsafe(reason: string): Error {
  return new Error(`asset_unsafe: ${reason}`);
}

function validatedLimit(value: number | undefined): number {
  const limit = value ?? DEFAULT_MAX_ASSET_BYTES;
  if (!Number.isSafeInteger(limit) || limit < 1 || limit > DEFAULT_MAX_ASSET_BYTES) {
    throw unsafe("invalid size bound");
  }
  return limit;
}

function inferMime(path: string): string | undefined {
  switch (extname(path).toLowerCase()) {
    case ".avif": return "image/avif";
    case ".bmp": return "image/bmp";
    case ".gif": return "image/gif";
    case ".jpeg":
    case ".jpg": return "image/jpeg";
    case ".png": return "image/png";
    case ".webp": return "image/webp";
    case ".ico": return "image/x-icon";
    case ".md": return "text/markdown";
    case ".txt": return "text/plain";
    default: return undefined;
  }
}

function isMissing(error: unknown): boolean {
  return error instanceof Error && "code" in error && error.code === "ENOENT";
}

function hasPrefix(bytes: Buffer, prefix: readonly number[]): boolean {
  return prefix.every((value, index) => bytes[index] === value);
}

function validateRasterSignature(mime: string, bytes: Buffer): void {
  const valid = (() => {
    switch (mime) {
      case "image/png": return hasPrefix(bytes, [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);
      case "image/jpeg": return hasPrefix(bytes, [0xff, 0xd8, 0xff]);
      case "image/gif": return bytes.subarray(0, 6).toString("ascii") === "GIF87a" || bytes.subarray(0, 6).toString("ascii") === "GIF89a";
      case "image/webp": return bytes.subarray(0, 4).toString("ascii") === "RIFF" && bytes.subarray(8, 12).toString("ascii") === "WEBP";
      case "image/bmp": return bytes.subarray(0, 2).toString("ascii") === "BM";
      case "image/x-icon": return hasPrefix(bytes, [0x00, 0x00, 0x01, 0x00]);
      case "image/avif": return bytes.subarray(4, 8).toString("ascii") === "ftyp" && bytes.subarray(8, 12).toString("ascii").startsWith("avi");
      default: return false;
    }
  })();
  if (!valid) throw unsafe("raster signature mismatch");
}

function validatePlainText(bytes: Buffer): void {
  let text: string;
  try {
    text = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
  } catch {
    throw unsafe("plain text is not valid UTF-8");
  }
  if (text.includes("\0")) throw unsafe("plain text contains NUL");
  const sniffed = text.replace(/^\uFEFF/, "").trimStart().toLowerCase();
  if (
    sniffed.startsWith("<!doctype html") ||
    sniffed.startsWith("<html") ||
    sniffed.startsWith("<svg") ||
    sniffed.startsWith("<?xml") ||
    /^(?:<script|<iframe|<object|<embed|<body|<head)(?:\s|>)/.test(sniffed)
  ) {
    throw unsafe("active text content rejected");
  }
}

export async function readBoundedContainedFile(
  candidate: string,
  pluginRoot: string,
  maxBytes: number = DEFAULT_MAX_ASSET_BYTES,
): Promise<ContainedFile> {
  const limit = validatedLimit(maxBytes);
  const canonical = await resolveContainedPath(pluginRoot, relative(pluginRoot, candidate));
  const rootIdentity = await snapshotDirectoryIdentity(pluginRoot);
  const chunks: Buffer[] = [];
  const hash = createHash("sha256");
  let size = 0n;
  await streamContainedRegularFile(rootIdentity, canonical, {}, {
    onOpen(observed): void {
      size = observed;
      if (observed > BigInt(limit)) throw unsafe("asset exceeds size bound");
    },
    onChunk(chunk): void {
      chunks.push(Buffer.from(chunk));
      hash.update(chunk);
    },
  });
  return Object.freeze({
    bytes: Buffer.concat(chunks, Number(size)),
    digest: hash.digest("hex"),
    path: slashPath(relative(pluginRoot, canonical)),
  });
}

export async function normalizeAsset(
  candidate: string,
  pluginRoot: string,
  options: AssetNormalizationOptions = {},
): Promise<NormalizedAsset> {
  const extension = extname(candidate).toLowerCase();
  if (ACTIVE_EXTENSIONS.has(extension)) throw unsafe("active extension rejected");
  const detectedMime = inferMime(candidate);
  if (detectedMime === undefined) throw unsafe("asset type is unknown");
  if (options.mime !== undefined && options.mime !== detectedMime) {
    throw unsafe("declared MIME differs from detected type");
  }
  const mime = detectedMime;
  if (mime === undefined || (!RASTER_MIMES.has(mime) && !TEXT_MIMES.has(mime))) {
    throw unsafe("MIME is not passive");
  }
  const limit = validatedLimit(options.maxBytes);
  if (!isContainedPath(resolve(pluginRoot), resolve(candidate))) {
    throw new PluginSourceSecurityError("path_escape", "asset leaves plugin root");
  }
  try {
    const stats = await lstat(candidate);
    if (!stats.isFile() || stats.isSymbolicLink()) throw unsafe("asset is not a regular file");
  } catch (error: unknown) {
    if (!isMissing(error) || options.optional !== true) throw error;
    const digest = createHash("sha256")
      .update("rowboat-plugin-placeholder-v1\0")
      .update(mime)
      .digest("hex");
    return Object.freeze({
      path: ROWBOAT_PLACEHOLDER_PATH,
      mime,
      digest,
      placeholder: true,
      owner: "rowboat",
    });
  }
  const file = await readBoundedContainedFile(candidate, pluginRoot, limit);
  if (RASTER_MIMES.has(mime)) validateRasterSignature(mime, file.bytes);
  else validatePlainText(file.bytes);
  return Object.freeze({
    path: file.path,
    mime,
    digest: file.digest,
    placeholder: false,
  });
}
