import { createHash } from "node:crypto";
import { extname, relative, sep } from "node:path";
import { snapshotDirectoryIdentity } from "../import/directory-identity.js";
import { streamContainedRegularFile } from "../import/safe-file-stream.js";
import {
  assertSafeDescriptorPath,
  inspectPotentialComponentPath,
  resolveExistingComponentPath,
} from "./component-path-security.js";
import { decodeRasterAsset, type DecodedRasterMime } from "./raster-decoder.js";

const DEFAULT_MAX_ASSET_BYTES = 1024 * 1024;
const ROWBOAT_PLACEHOLDER_PATH = "rowboat://assets/plugin-placeholder";
const RASTER_MIMES: ReadonlySet<string> = new Set<DecodedRasterMime>([
  "image/avif",
  "image/gif",
  "image/jpeg",
  "image/png",
  "image/webp",
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
    case ".gif": return "image/gif";
    case ".jpeg":
    case ".jpg": return "image/jpeg";
    case ".png": return "image/png";
    case ".webp": return "image/webp";
    case ".md": return "text/markdown";
    case ".txt": return "text/plain";
    default: return undefined;
  }
}

function isRasterMime(mime: string): mime is DecodedRasterMime {
  return RASTER_MIMES.has(mime);
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
  if (!isRasterMime(mime) && !TEXT_MIMES.has(mime)) {
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
  if (isRasterMime(mime)) await decodeRasterAsset(file.bytes, mime);
  else validatePlainText(file.bytes, mime);
  return Object.freeze({
    path: file.path,
    mime,
    digest: file.digest,
    placeholder: false,
  });
}
