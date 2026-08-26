import { createHash, createHmac, timingSafeEqual } from "node:crypto";
import { types as utilTypes } from "node:util";
import type { PluginApiIdentity } from "../../policies/plugin-api-authorization.policy";

export const SHA256 = /^[a-f0-9]{64}$/;
export const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
export const SAFE_TOKEN = /^[A-Za-z0-9._~-]{1,4096}$/;

export function actorTuple(actor: PluginApiIdentity): Readonly<{ actorKind: PluginApiIdentity["kind"]; actorId: string }> {
  if (utilTypes.isProxy(actor) || Object.getPrototypeOf(actor) !== Object.prototype) throw new Error("authorization_invalid");
  const keys = Reflect.ownKeys(actor);
  const kindDescriptor = Object.getOwnPropertyDescriptor(actor, "kind");
  if (kindDescriptor === undefined || !("value" in kindDescriptor) || !kindDescriptor.enumerable || (kindDescriptor.value !== "user" && kindDescriptor.value !== "project_api_key")) throw new Error("authorization_invalid");
  const kind = kindDescriptor.value;
  const expected = kind === "user" ? ["kind", "userId"] : ["kind", "projectId"];
  if (keys.length !== expected.length || keys.some(key => typeof key !== "string" || !expected.includes(key))) throw new Error("authorization_invalid");
  for (const key of expected) {
    const descriptor = Object.getOwnPropertyDescriptor(actor, key);
    if (descriptor === undefined || !("value" in descriptor) || !descriptor.enumerable || typeof descriptor.value !== "string") throw new Error("authorization_invalid");
  }
  const idKey = kind === "user" ? "userId" : "projectId";
  const idDescriptor = Object.getOwnPropertyDescriptor(actor, idKey)! as PropertyDescriptor & { value: string };
  return Object.freeze({ actorKind: kind, actorId: idDescriptor.value });
}

export function canonical(value: unknown): string {
  if (value === null || typeof value === "boolean" || typeof value === "string" || typeof value === "number") return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  const record = value as Readonly<Record<string, unknown>>;
  return `{${Object.keys(record).sort().map(key => `${JSON.stringify(key)}:${canonical(record[key])}`).join(",")}}`;
}

export function migrationDigest(domain: string, value: unknown): string {
  return createHash("sha256").update(domain).update("\0").update(canonical(value)).digest("hex");
}

export function assertSecret(secret: string): void {
  if (typeof secret !== "string" || Buffer.byteLength(secret, "utf8") < 32 || Buffer.byteLength(secret, "utf8") > 4096 || /[\u0000-\u001f\u007f-\u009f]/u.test(secret)) throw new Error("migration_confirmation_secret_invalid");
  for (let index = 0; index < secret.length; index += 1) {
    const code = secret.charCodeAt(index);
    if (code >= 0xd800 && code <= 0xdbff) { const next = secret.charCodeAt(++index); if (next < 0xdc00 || next > 0xdfff) throw new Error("migration_confirmation_secret_invalid"); }
    else if (code >= 0xdc00 && code <= 0xdfff) throw new Error("migration_confirmation_secret_invalid");
  }
}

export interface MigrationConfirmationClaims {
  readonly version: 1; readonly operation: "apply_plugin_migration";
  readonly actorKind: PluginApiIdentity["kind"]; readonly actorId: string; readonly projectId: string;
  readonly sourceProjectRevision: number; readonly sourceDigest: string; readonly sourceInventoryDigest: string;
  readonly recipeDigest: string; readonly rollbackSnapshotDigest: string; readonly targetCatalogDigest: string;
  readonly targetInstallationIds: readonly string[]; readonly previewDigest: string; readonly reportDigest: string;
  readonly issuedAt: string; readonly expiresAt: string; readonly nonce: string; readonly idempotencyKey: string;
}

function encode(value: string): string { return Buffer.from(value, "utf8").toString("base64url"); }
export function signMigrationConfirmation(claims: MigrationConfirmationClaims, secret: string): string {
  assertSecret(secret);
  const payload = encode(canonical(claims));
  const signature = createHmac("sha256", secret).update("rowboat:plugin-migration-confirmation:v1\0").update(payload).digest("base64url");
  return `${payload}.${signature}`;
}

export function verifyMigrationConfirmation(token: string, secret: string, now: Date): MigrationConfirmationClaims {
  assertSecret(secret);
  if (typeof token !== "string" || token.length > 8192 || !/^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$/.test(token)) throw new Error("migration_confirmation_invalid");
  const [payload, signature] = token.split(".") as [string, string];
  const expected = createHmac("sha256", secret).update("rowboat:plugin-migration-confirmation:v1\0").update(payload).digest();
  let actual: Buffer;
  try { actual = Buffer.from(signature, "base64url"); } catch { throw new Error("migration_confirmation_invalid"); }
  if (actual.toString("base64url") !== signature) throw new Error("migration_confirmation_invalid");
  if (actual.length !== expected.length || !timingSafeEqual(actual, expected)) throw new Error("migration_confirmation_invalid");
  let value: unknown;
  try { value = JSON.parse(Buffer.from(payload, "base64url").toString("utf8")); } catch { throw new Error("migration_confirmation_invalid"); }
  if (value === null || typeof value !== "object" || Array.isArray(value) || utilTypes.isProxy(value)) throw new Error("migration_confirmation_invalid");
  const claims = value as Partial<MigrationConfirmationClaims>;
  const required = ["version","operation","actorKind","actorId","projectId","sourceProjectRevision","sourceDigest","sourceInventoryDigest","recipeDigest","rollbackSnapshotDigest","targetCatalogDigest","targetInstallationIds","previewDigest","reportDigest","issuedAt","expiresAt","nonce","idempotencyKey"];
  if (Object.keys(claims).sort().join("\0") !== [...required].sort().join("\0") || claims.version !== 1 || claims.operation !== "apply_plugin_migration"
    || (claims.actorKind !== "user" && claims.actorKind !== "project_api_key") || typeof claims.actorId !== "string" || typeof claims.projectId !== "string"
    || !Number.isSafeInteger(claims.sourceProjectRevision) || (claims.sourceProjectRevision as number) < 0
    || ![claims.sourceDigest, claims.sourceInventoryDigest, claims.recipeDigest, claims.rollbackSnapshotDigest, claims.targetCatalogDigest, claims.previewDigest, claims.reportDigest].every(item => typeof item === "string" && SHA256.test(item))
    || !Array.isArray(claims.targetInstallationIds) || claims.targetInstallationIds.length === 0 || claims.targetInstallationIds.some(id => typeof id !== "string" || !UUID.test(id))
    || typeof claims.issuedAt !== "string" || typeof claims.expiresAt !== "string" || typeof claims.nonce !== "string" || !SAFE_TOKEN.test(claims.nonce)
    || typeof claims.idempotencyKey !== "string" || !SAFE_TOKEN.test(claims.idempotencyKey)) throw new Error("migration_confirmation_invalid");
  const issued = Date.parse(claims.issuedAt); const expires = Date.parse(claims.expiresAt);
  if (!Number.isFinite(issued) || !Number.isFinite(expires) || expires <= issued || expires - issued > 5 * 60_000 || now.getTime() < issued || now.getTime() >= expires) throw new Error("migration_confirmation_expired");
  return Object.freeze(claims as MigrationConfirmationClaims);
}
