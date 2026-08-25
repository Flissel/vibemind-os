import { createHash } from "node:crypto";
import { extname, relative, sep } from "node:path";
import { snapshotDirectoryIdentity } from "../import/directory-identity.js";
import { streamContainedRegularFile } from "../import/safe-file-stream.js";
import {
  assertSafeDescriptorPath,
  inspectPotentialComponentPath,
  resolveExistingComponentPath,
} from "./component-path-security.js";

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
const ACTIVE_TEXT_MARKUP = /(?:<!doctype\s+html\b|<\?xml\b|<\s*\/?\s*(?:a|applet|audio|base|body|button|canvas|details|dialog|embed|form|frame|frameset|head|html|iframe|img|input|link|marquee|math|meta|object|picture|script|select|source|style|svg|template|textarea|track|video)\b[^>]*>|<\s*\/?\s*[a-z][^>]*\son[a-z]+\s*=|<\s*\/?\s*[a-z][^>]*(?:href|src)\s*=\s*["']?\s*javascript:)/iu;

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

function hasPrefix(bytes: Buffer, prefix: readonly number[]): boolean {
  return prefix.every((value, index) => bytes[index] === value);
}

function validPng(bytes: Buffer): boolean {
  if (!hasPrefix(bytes, [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a])) return false;
  let offset = 8;
  let first = true;
  while (offset + 12 <= bytes.length) {
    const length = bytes.readUInt32BE(offset);
    const end = offset + 12 + length;
    if (!Number.isSafeInteger(end) || end > bytes.length) return false;
    const type = bytes.subarray(offset + 4, offset + 8).toString("ascii");
    if (first) {
      if (type !== "IHDR" || length !== 13) return false;
      if (bytes.readUInt32BE(offset + 8) === 0 || bytes.readUInt32BE(offset + 12) === 0) return false;
      first = false;
    }
    if (type === "IEND") return length === 0 && end === bytes.length;
    offset = end;
  }
  return false;
}

function validJpeg(bytes: Buffer): boolean {
  if (bytes.length < 8 || !hasPrefix(bytes, [0xff, 0xd8]) || !hasPrefix(bytes.subarray(bytes.length - 2), [0xff, 0xd9])) {
    return false;
  }
  let offset = 2;
  let sawSegment = false;
  while (offset < bytes.length - 2) {
    if (bytes[offset] !== 0xff) return false;
    while (bytes[offset] === 0xff) offset += 1;
    const marker = bytes[offset];
    if (marker === undefined || marker === 0x00 || marker === 0xd9) return false;
    offset += 1;
    if (marker === 0x01 || (marker >= 0xd0 && marker <= 0xd7)) continue;
    if (offset + 2 > bytes.length - 2) return false;
    const segmentLength = bytes.readUInt16BE(offset);
    if (segmentLength < 2 || offset + segmentLength > bytes.length - 2) return false;
    sawSegment = true;
    offset += segmentLength;
    if (marker === 0xda) return sawSegment && offset <= bytes.length - 2;
  }
  return sawSegment && offset === bytes.length - 2;
}

function validGif(bytes: Buffer): boolean {
  const header = bytes.subarray(0, 6).toString("ascii");
  return (
    bytes.length >= 14 &&
    (header === "GIF87a" || header === "GIF89a") &&
    bytes.readUInt16LE(6) > 0 &&
    bytes.readUInt16LE(8) > 0 &&
    bytes[bytes.length - 1] === 0x3b
  );
}

function validWebp(bytes: Buffer): boolean {
  if (
    bytes.length < 20 ||
    bytes.subarray(0, 4).toString("ascii") !== "RIFF" ||
    bytes.readUInt32LE(4) !== bytes.length - 8 ||
    bytes.subarray(8, 12).toString("ascii") !== "WEBP"
  ) return false;
  let offset = 12;
  let chunks = 0;
  while (offset + 8 <= bytes.length) {
    const length = bytes.readUInt32LE(offset + 4);
    const paddedLength = length + (length % 2);
    const end = offset + 8 + paddedLength;
    if (!Number.isSafeInteger(end) || end > bytes.length) return false;
    chunks += 1;
    offset = end;
  }
  return chunks > 0 && offset === bytes.length;
}

function validateRasterSignature(mime: string, bytes: Buffer): void {
  const valid = (() => {
    switch (mime) {
      case "image/png": return validPng(bytes);
      case "image/jpeg": return validJpeg(bytes);
      case "image/gif": return validGif(bytes);
      case "image/webp": return validWebp(bytes);
      case "image/bmp": return bytes.subarray(0, 2).toString("ascii") === "BM";
      case "image/x-icon": return hasPrefix(bytes, [0x00, 0x00, 0x01, 0x00]);
      case "image/avif": return bytes.subarray(4, 8).toString("ascii") === "ftyp" && bytes.subarray(8, 12).toString("ascii").startsWith("avi");
      default: return false;
    }
  })();
  if (!valid) throw unsafe("raster signature mismatch");
}

function decodeHtmlEntities(value: string): string {
  return value
    .replace(/&#x([0-9a-f]+);?/giu, (_match, digits: string) => {
      const point = Number.parseInt(digits, 16);
      return Number.isSafeInteger(point) && point <= 0x10ffff ? String.fromCodePoint(point) : "";
    })
    .replace(/&#([0-9]+);?/gu, (_match, digits: string) => {
      const point = Number.parseInt(digits, 10);
      return Number.isSafeInteger(point) && point <= 0x10ffff ? String.fromCodePoint(point) : "";
    })
    .replace(/&(?:colon);?/giu, ":")
    .replace(/&(?:tab|newline);?/giu, "");
}

function decodedDestination(value: string): string {
  let decoded = decodeHtmlEntities(value);
  for (let attempt = 0; attempt < 2; attempt += 1) {
    try {
      const next = decodeURIComponent(decoded);
      if (next === decoded) break;
      decoded = next;
    } catch {
      break;
    }
  }
  return decoded.replace(/[\u0000-\u0020\u007f-\u009f\s]/gu, "").toLowerCase();
}

function validateMarkdownDestinations(markdown: string): void {
  const destinations: string[] = [];
  const inline = /!?\[[^\]]*\]\(\s*(?:<([^>]+)>|([^\s)]+))/gu;
  const autolink = /<([^<>\s]+:[^<>]*)>/gu;
  const definition = /^\s*\[[^\]]+\]:\s*(?:<([^>]+)>|(\S+))/gmu;
  for (const pattern of [inline, autolink, definition]) {
    for (const match of markdown.matchAll(pattern)) {
      const destination = match[1] ?? match[2];
      if (destination !== undefined) destinations.push(destination);
    }
  }
  for (const destination of destinations) {
    const normalized = decodedDestination(destination);
    const scheme = /^([a-z][a-z0-9+.-]*):/u.exec(normalized)?.[1];
    if (scheme === "javascript" || scheme === "data" || scheme === "vbscript" || scheme === "file") {
      throw unsafe("active Markdown destination rejected");
    }
  }
}

function validatePlainText(bytes: Buffer, mime: string): void {
  let text: string;
  try {
    text = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
  } catch {
    throw unsafe("plain text is not valid UTF-8");
  }
  if (text.includes("\0")) throw unsafe("plain text contains NUL");
  if (ACTIVE_TEXT_MARKUP.test(text.replace(/^\uFEFF/, ""))) {
    throw unsafe("active text content rejected");
  }
  if (mime === "text/markdown") validateMarkdownDestinations(text);
}

export async function readBoundedContainedFile(
  candidate: string,
  pluginRoot: string,
  maxBytes: number = DEFAULT_MAX_ASSET_BYTES,
  componentRoot: string = pluginRoot,
): Promise<ContainedFile> {
  const limit = validatedLimit(maxBytes);
  const resolved = await resolveExistingComponentPath(pluginRoot, componentRoot, candidate);
  const rootIdentity = await snapshotDirectoryIdentity(resolved.canonicalComponentRoot);
  const chunks: Buffer[] = [];
  const hash = createHash("sha256");
  let size = 0n;
  await streamContainedRegularFile(rootIdentity, resolved.canonicalPath, {}, {
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
    path: assertSafeDescriptorPath(
      slashPath(relative(resolved.canonicalPluginRoot, resolved.canonicalPath)),
    ),
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
  const exists = await inspectPotentialComponentPath(pluginRoot, pluginRoot, candidate);
  if (!exists) {
    if (options.optional !== true) throw unsafe("asset is missing");
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
  else validatePlainText(file.bytes, mime);
  return Object.freeze({
    path: file.path,
    mime,
    digest: file.digest,
    placeholder: false,
  });
}
