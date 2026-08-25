import { createHash } from "node:crypto";
import { extname, relative, sep } from "node:path";
import { inflateSync } from "node:zlib";
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

function crc32(bytes: Buffer): number {
  let crc = 0xffffffff;
  for (const byte of bytes) {
    crc ^= byte;
    for (let bit = 0; bit < 8; bit += 1) {
      crc = (crc >>> 1) ^ ((crc & 1) === 1 ? 0xedb88320 : 0);
    }
  }
  return (crc ^ 0xffffffff) >>> 0;
}

function validPng(bytes: Buffer): boolean {
  if (!hasPrefix(bytes, [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a])) return false;
  let offset = 8;
  let first = true;
  let sawIdat = false;
  let idatEnded = false;
  let sawPalette = false;
  let width = 0;
  let height = 0;
  let bitDepth = 0;
  let colorType = 0;
  let interlace = 0;
  const idatChunks: Buffer[] = [];
  while (offset + 12 <= bytes.length) {
    const length = bytes.readUInt32BE(offset);
    const end = offset + 12 + length;
    if (!Number.isSafeInteger(end) || end > bytes.length) return false;
    const type = bytes.subarray(offset + 4, offset + 8).toString("ascii");
    const dataEnd = offset + 8 + length;
    if (bytes.readUInt32BE(dataEnd) !== crc32(bytes.subarray(offset + 4, dataEnd))) return false;
    if (first) {
      if (type !== "IHDR" || length !== 13) return false;
      width = bytes.readUInt32BE(offset + 8);
      height = bytes.readUInt32BE(offset + 12);
      if (width === 0 || height === 0) return false;
      bitDepth = bytes[offset + 16] ?? 0;
      colorType = bytes[offset + 17] ?? -1;
      interlace = bytes[offset + 20] ?? -1;
      const validDepth =
        (colorType === 0 && [1, 2, 4, 8, 16].includes(bitDepth)) ||
        (colorType === 2 && [8, 16].includes(bitDepth)) ||
        (colorType === 3 && [1, 2, 4, 8].includes(bitDepth)) ||
        ((colorType === 4 || colorType === 6) && [8, 16].includes(bitDepth));
      if (
        !validDepth ||
        bytes[offset + 18] !== 0 || bytes[offset + 19] !== 0 ||
        (interlace !== 0 && interlace !== 1)
      ) return false;
      first = false;
    } else if (type === "IHDR") {
      return false;
    }
    if (type === "PLTE") sawPalette = length > 0 && length % 3 === 0;
    if (type === "IDAT") {
      if (length === 0 || idatEnded) return false;
      sawIdat = true;
      idatChunks.push(bytes.subarray(offset + 8, dataEnd));
    } else if (sawIdat && type !== "IEND") {
      idatEnded = true;
    }
    if (type === "IEND") {
      if (length !== 0 || !sawIdat || end !== bytes.length || (colorType === 3 && !sawPalette)) return false;
      let decoded: Buffer;
      try {
        decoded = inflateSync(Buffer.concat(idatChunks), { maxOutputLength: 64 * 1024 * 1024 });
      } catch {
        return false;
      }
      if (interlace === 1) return decoded.length > 0;
      const channels = colorType === 0 || colorType === 3 ? 1 : colorType === 2 ? 3 : colorType === 4 ? 2 : 4;
      const rowBytes = Math.ceil((width * channels * bitDepth) / 8);
      const expected = height * (rowBytes + 1);
      return Number.isSafeInteger(expected) && expected <= 64 * 1024 * 1024 && decoded.length === expected;
    }
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

function validBmp(bytes: Buffer): boolean {
  if (bytes.length < 58 || bytes.subarray(0, 2).toString("ascii") !== "BM") return false;
  const declaredSize = bytes.readUInt32LE(2);
  const pixelOffset = bytes.readUInt32LE(10);
  const dibSize = bytes.readUInt32LE(14);
  if (declaredSize !== bytes.length || dibSize < 40 || 14 + dibSize > bytes.length) return false;
  const width = bytes.readInt32LE(18);
  const height = bytes.readInt32LE(22);
  const planes = bytes.readUInt16LE(26);
  const bitsPerPixel = bytes.readUInt16LE(28);
  const compression = bytes.readUInt32LE(30);
  const rowBytes = Math.floor((bitsPerPixel * width + 31) / 32) * 4;
  const requiredPayload = rowBytes * Math.abs(height);
  return (
    width > 0 && height !== 0 && planes === 1 &&
    [1, 4, 8, 16, 24, 32].includes(bitsPerPixel) &&
    compression === 0 && pixelOffset >= 14 + dibSize && pixelOffset < bytes.length &&
    Number.isSafeInteger(requiredPayload) && requiredPayload > 0 &&
    pixelOffset + requiredPayload <= bytes.length
  );
}

function validIco(bytes: Buffer): boolean {
  if (bytes.length < 22 || bytes.readUInt16LE(0) !== 0 || bytes.readUInt16LE(2) !== 1) return false;
  const count = bytes.readUInt16LE(4);
  const directoryEnd = 6 + count * 16;
  if (count < 1 || directoryEnd > bytes.length) return false;
  for (let index = 0; index < count; index += 1) {
    const entry = 6 + index * 16;
    const size = bytes.readUInt32LE(entry + 8);
    const offset = bytes.readUInt32LE(entry + 12);
    if (size < 1 || offset < directoryEnd || offset + size > bytes.length) return false;
    const payload = bytes.subarray(offset, offset + size);
    const isPng = hasPrefix(payload, [0x89, 0x50, 0x4e, 0x47]);
    if (isPng ? !validPng(payload) : payload.length < 40 || payload.readUInt32LE(0) < 40) return false;
  }
  return true;
}

interface BmffBox {
  readonly type: string;
  readonly start: number;
  readonly dataStart: number;
  readonly end: number;
}

function parseBmffBoxes(bytes: Buffer, start: number, end: number): readonly BmffBox[] | undefined {
  const boxes: BmffBox[] = [];
  let offset = start;
  while (offset < end) {
    if (offset + 8 > end) return undefined;
    const size32 = bytes.readUInt32BE(offset);
    const type = bytes.subarray(offset + 4, offset + 8).toString("ascii");
    if (!/^[\x20-\x7e]{4}$/u.test(type)) return undefined;
    let headerSize = 8;
    let size = size32;
    if (size32 === 1) {
      if (offset + 16 > end) return undefined;
      const extended = bytes.readBigUInt64BE(offset + 8);
      if (extended > BigInt(Number.MAX_SAFE_INTEGER)) return undefined;
      size = Number(extended);
      headerSize = 16;
    } else if (size32 === 0) {
      size = end - offset;
    }
    if (size < headerSize || offset + size > end) return undefined;
    boxes.push({ type, start: offset, dataStart: offset + headerSize, end: offset + size });
    offset += size;
  }
  return offset === end ? boxes : undefined;
}

function validAvif(bytes: Buffer): boolean {
  const boxes = parseBmffBoxes(bytes, 0, bytes.length);
  if (boxes === undefined || boxes[0]?.type !== "ftyp") return false;
  const ftyp = boxes[0];
  if (ftyp === undefined || ftyp.end - ftyp.dataStart < 12 || (ftyp.end - ftyp.dataStart) % 4 !== 0) return false;
  const brands: string[] = [];
  for (let offset = ftyp.dataStart; offset + 4 <= ftyp.end; offset += 4) {
    if (offset === ftyp.dataStart + 4) continue;
    brands.push(bytes.subarray(offset, offset + 4).toString("ascii"));
  }
  if (!brands.includes("avif") && !brands.includes("avis")) return false;
  const meta = boxes.find(({ type }) => type === "meta");
  const mdat = boxes.find(({ type }) => type === "mdat");
  if (meta === undefined || mdat === undefined || mdat.end - mdat.dataStart < 1) return false;
  if (meta.end - meta.dataStart < 12) return false;
  const metaChildren = parseBmffBoxes(bytes, meta.dataStart + 4, meta.end);
  if (metaChildren === undefined) return false;
  const requiredMinimums = new Map<string, number>([
    ["hdlr", 24],
    ["pitm", 6],
    ["iloc", 8],
    ["iinf", 6],
    ["iprp", 16],
  ]);
  for (const [type, minimum] of requiredMinimums) {
    const child = metaChildren.find((candidate) => candidate.type === type);
    if (child === undefined || child.end - child.dataStart < minimum) return false;
  }
  const iprp = metaChildren.find(({ type }) => type === "iprp");
  if (iprp === undefined) return false;
  const propertyChildren = parseBmffBoxes(bytes, iprp.dataStart, iprp.end);
  return propertyChildren !== undefined &&
    propertyChildren.some(({ type }) => type === "ipco") &&
    propertyChildren.some(({ type }) => type === "ipma");
}

function validateRasterSignature(mime: string, bytes: Buffer): void {
  const valid = (() => {
    switch (mime) {
      case "image/png": return validPng(bytes);
      case "image/jpeg": return validJpeg(bytes);
      case "image/gif": return validGif(bytes);
      case "image/webp": return validWebp(bytes);
      case "image/bmp": return validBmp(bytes);
      case "image/x-icon": return validIco(bytes);
      case "image/avif": return validAvif(bytes);
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
  let decoded = value.replace(/\\(.)/gsu, (match, character: string) => {
    const code = character.charCodeAt(0);
    const punctuation =
      (code >= 0x21 && code <= 0x2f) ||
      (code >= 0x3a && code <= 0x40) ||
      (code >= 0x5b && code <= 0x60) ||
      (code >= 0x7b && code <= 0x7e);
    return punctuation ? character : match;
  });
  for (let attempt = 0; attempt < 4; attempt += 1) {
    const entitiesDecoded = decodeHtmlEntities(decoded);
    try {
      const next = decodeURIComponent(entitiesDecoded);
      if (next === decoded) break;
      decoded = next;
    } catch {
      decoded = entitiesDecoded;
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
    if (scheme !== undefined && scheme !== "http" && scheme !== "https" && scheme !== "mailto") {
      throw unsafe("Markdown destination scheme rejected");
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
