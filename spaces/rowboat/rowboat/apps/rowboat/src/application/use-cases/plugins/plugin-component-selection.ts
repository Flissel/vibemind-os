import { createHash } from "node:crypto";

const DIGEST = /^[a-f0-9]{64}$/;
const MAX_SELECTION = 512;

/**
 * An installation names the components it wants. The selection is canonicalised
 * once, at the boundary it enters through - sorted and deduplicated - so that
 * the same selection always produces the same idempotency fingerprint and the
 * same signed preview digest, whichever order the caller wrote it in.
 *
 * An absent selection means "every component of the plugin"; only an explicitly
 * supplied one passes through here, and an empty one is a request error rather
 * than a silent whole-plugin install.
 */
export function requestedComponentSelection(value: unknown): readonly string[] {
  if (!Array.isArray(value) || value.length === 0 || value.length > MAX_SELECTION) throw new Error("request_invalid");
  const unique = new Set<string>();
  for (const item of value as readonly unknown[]) {
    if (typeof item !== "string" || !DIGEST.test(item) || unique.has(item)) throw new Error("request_invalid");
    unique.add(item);
  }
  return Object.freeze([...unique].sort());
}

/** The canonical form of a selection the server derived rather than received. */
export function canonicalComponentSelection(digests: readonly string[]): readonly string[] {
  return Object.freeze([...new Set(digests)].sort());
}

/**
 * The value the signed preview envelope and the idempotency fingerprint both
 * bind. It covers the selection the caller asked for, `null` standing for the
 * absent selection - so a preview issued for one selection cannot install
 * another, and one idempotency key cannot be replayed with a different one.
 */
export function componentSelectionDigest(selection: readonly string[] | undefined): string {
  return createHash("sha256").update(JSON.stringify(selection ?? null), "utf8").digest("hex");
}
