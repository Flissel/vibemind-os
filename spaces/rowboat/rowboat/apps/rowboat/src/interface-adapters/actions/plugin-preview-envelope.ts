import { createHmac, timingSafeEqual } from "node:crypto";
import { z } from "zod";

const ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/;
const DIGEST = /^[a-f0-9]{64}$/;
const COMMIT = /^[a-f0-9]{40}$/;
const IDEMPOTENCY = /^[A-Za-z0-9][A-Za-z0-9._~-]{0,127}$/;
const TOKEN = /^[A-Za-z0-9_-]{1,3072}\.[A-Za-z0-9_-]{43}$/;
const MAX_TOKEN_BYTES = 4096;
const MAX_LIFETIME_MS = 10 * 60 * 1000;

const Payload = z.object({
  version: z.literal("rowboat_plugin_preview_v1"),
  actorType: z.enum(["user", "project_api_key"]),
  actorId: z.string().regex(ID),
  projectId: z.string().regex(ID),
  pluginName: z.string().regex(ID),
  catalogDigest: z.string().regex(DIGEST),
  sourceCommit: z.string().regex(COMMIT),
  installationPresent: z.boolean(),
  expectedRevision: z.number().int().nonnegative(),
  componentDecisionsDigest: z.string().regex(DIGEST),
  credentialSlotsDigest: z.string().regex(DIGEST),
  // A digest of the component selection rather than the selection itself: the
  // largest plugin in the pinned catalog has 88 components, and 88 digests do
  // not fit the token size this envelope is capped at.
  componentSelectionDigest: z.string().regex(DIGEST),
  idempotencyKey: z.string().regex(IDEMPOTENCY),
  operation: z.literal("install"),
  issuedAt: z.number().int().nonnegative(),
  expiresAt: z.number().int().positive(),
}).strict();

export type PluginPreviewEnvelope = Readonly<z.infer<typeof Payload>>;

export function validatePluginPreviewSecret(secret: string | undefined): Buffer {
  if (typeof secret !== "string") throw new Error("preview_configuration_invalid");
  if (/[\u0000-\u001f\u007f]/u.test(secret)) throw new Error("preview_configuration_invalid");
  for (let index = 0; index < secret.length; index += 1) {
    const codeUnit = secret.charCodeAt(index);
    if (codeUnit >= 0xd800 && codeUnit <= 0xdbff) {
      if (index + 1 >= secret.length) throw new Error("preview_configuration_invalid");
      const next = secret.charCodeAt(index + 1);
      if (next < 0xdc00 || next > 0xdfff) throw new Error("preview_configuration_invalid");
      index += 1;
    } else if (codeUnit >= 0xdc00 && codeUnit <= 0xdfff) {
      throw new Error("preview_configuration_invalid");
    }
  }
  const bytes = Buffer.from(secret, "utf8");
  if (bytes.byteLength < 32 || bytes.byteLength > 4096) {
    throw new Error("preview_configuration_invalid");
  }
  return bytes;
}

function signature(secret: Buffer, payload: string): Buffer {
  return createHmac("sha256", secret).update("rowboat_plugin_preview_v1\0", "utf8").update(payload, "ascii").digest();
}

export function signPluginPreviewEnvelope(input: unknown, secret: string | undefined): string {
  const parsed = Payload.safeParse(input);
  if (!parsed.success || parsed.data.expiresAt <= parsed.data.issuedAt || parsed.data.expiresAt - parsed.data.issuedAt > MAX_LIFETIME_MS) {
    throw new Error("preview_invalid");
  }
  const payload = Buffer.from(JSON.stringify(parsed.data), "utf8").toString("base64url");
  const token = `${payload}.${signature(validatePluginPreviewSecret(secret), payload).toString("base64url")}`;
  if (Buffer.byteLength(token, "ascii") > MAX_TOKEN_BYTES) throw new Error("preview_invalid");
  return token;
}

export function verifyPluginPreviewEnvelope(token: unknown, secret: string | undefined, now: number): PluginPreviewEnvelope {
  const secretBytes = validatePluginPreviewSecret(secret);
  if (typeof token !== "string" || Buffer.byteLength(token, "ascii") > MAX_TOKEN_BYTES || !TOKEN.test(token)) throw new Error("preview_invalid");
  if (!Number.isSafeInteger(now) || now < 0) throw new Error("preview_configuration_invalid");
  const [payload, encodedSignature] = token.split(".") as [string, string];
  let actual: Buffer;
  let decoded: Buffer;
  try {
    actual = Buffer.from(encodedSignature, "base64url");
    decoded = Buffer.from(payload, "base64url");
  } catch { throw new Error("preview_invalid"); }
  if (actual.toString("base64url") !== encodedSignature || decoded.toString("base64url") !== payload) throw new Error("preview_invalid");
  const expected = signature(secretBytes, payload);
  if (actual.byteLength !== expected.byteLength || !timingSafeEqual(actual, expected)) throw new Error("preview_invalid");
  let raw: unknown;
  try { raw = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(decoded)) as unknown; } catch { throw new Error("preview_invalid"); }
  const parsed = Payload.safeParse(raw);
  if (!parsed.success || parsed.data.expiresAt <= parsed.data.issuedAt || parsed.data.expiresAt - parsed.data.issuedAt > MAX_LIFETIME_MS) throw new Error("preview_invalid");
  if (now >= parsed.data.expiresAt) throw new Error("preview_expired");
  if (parsed.data.issuedAt > now + 5_000) throw new Error("preview_invalid");
  return Object.freeze(parsed.data);
}
