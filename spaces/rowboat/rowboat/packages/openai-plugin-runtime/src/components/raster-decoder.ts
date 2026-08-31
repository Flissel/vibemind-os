import sharp, { type Metadata } from "sharp";

const MAX_RASTER_PIXELS = 4 * 1024 * 1024;
const MAX_RASTER_RAW_BYTES = MAX_RASTER_PIXELS * 4;
const MAX_CONCURRENT_RASTER_DECODES = 2;

export type DecodedRasterMime =
  | "image/avif"
  | "image/gif"
  | "image/jpeg"
  | "image/png"
  | "image/webp";

let activeDecodes = 0;
const decodeWaiters: Array<() => void> = [];

function unsafe(reason: string): Error {
  return new Error(`asset_unsafe: ${reason}`);
}

async function acquireDecodePermit(): Promise<void> {
  if (activeDecodes < MAX_CONCURRENT_RASTER_DECODES) {
    activeDecodes += 1;
    return;
  }
  await new Promise<void>((resolve) => decodeWaiters.push(resolve));
}

function releaseDecodePermit(): void {
  const next = decodeWaiters.shift();
  if (next !== undefined) {
    next();
    return;
  }
  activeDecodes -= 1;
}

function metadataMatchesMime(metadata: Metadata, mime: DecodedRasterMime): boolean {
  switch (mime) {
    case "image/avif":
      return metadata.format === "heif" && metadata.compression === "av1";
    case "image/gif":
      return metadata.format === "gif";
    case "image/jpeg":
      return metadata.format === "jpeg";
    case "image/png":
      return metadata.format === "png";
    case "image/webp":
      return metadata.format === "webp";
  }
}

function boundedDimensions(metadata: Metadata): {
  readonly width: number;
  readonly height: number;
  readonly rawBytes: number;
} {
  const { width, height } = metadata;
  if (
    width === undefined || height === undefined ||
    !Number.isSafeInteger(width) || !Number.isSafeInteger(height) ||
    width < 1 || height < 1
  ) {
    throw unsafe("raster dimensions are invalid");
  }
  const pixels = width * height;
  const rawBytes = pixels * 4;
  if (
    !Number.isSafeInteger(pixels) || pixels > MAX_RASTER_PIXELS ||
    !Number.isSafeInteger(rawBytes) || rawBytes > MAX_RASTER_RAW_BYTES
  ) {
    throw unsafe("decoded raster exceeds resource bounds");
  }
  return { width, height, rawBytes };
}

export async function decodeRasterAsset(
  bytes: Buffer,
  expectedMime: DecodedRasterMime,
): Promise<void> {
  await acquireDecodePermit();
  try {
    const decoder = sharp(bytes, {
      animated: true,
      failOn: "error",
      limitInputPixels: MAX_RASTER_PIXELS,
      sequentialRead: true,
    });
    const metadata = await decoder.metadata();
    if (!metadataMatchesMime(metadata, expectedMime)) {
      throw unsafe("decoded format differs from detected type");
    }
    if ((metadata.pages ?? 1) !== 1) {
      throw unsafe("animated or multipage raster rejected");
    }
    const expected = boundedDimensions(metadata);
    const decoded = await decoder
      .clone()
      .ensureAlpha()
      .raw()
      .toBuffer({ resolveWithObject: true });
    if (
      decoded.info.width !== expected.width ||
      decoded.info.height !== expected.height ||
      decoded.info.channels !== 4 ||
      decoded.data.byteLength !== expected.rawBytes
    ) {
      throw unsafe("decoded raster output is inconsistent");
    }
  } catch (error: unknown) {
    if (error instanceof Error && error.message.startsWith("asset_unsafe:")) throw error;
    throw unsafe("raster decode failed");
  } finally {
    releaseDecodePermit();
  }
}
