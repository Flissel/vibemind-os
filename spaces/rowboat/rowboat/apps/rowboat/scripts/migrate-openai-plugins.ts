import { mkdir, open, rename, rm } from "node:fs/promises";
import { readFileSync } from "node:fs";
import { createHash } from "node:crypto";
import { dirname, isAbsolute, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import type { PluginCatalogLock } from "@rowboat/openai-plugin-runtime";
import { LegacyPluginMigration } from "@/src/application/services/legacy-plugin-migration";
import { LEGACY_CARD_IDS } from "@/src/application/services/legacy-plugin-recipes";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const TOKEN = /^[A-Za-z0-9._~-]{1,8192}$/;
export type MigrationCliArguments =
  | Readonly<{ mode: "dry-run"; scope: "fixtures" | "all"; output?: string; overwrite: boolean }>
  | Readonly<{ mode: "apply"; projectId: string; confirmationToken: string; idempotencyKey: string; output?: string; overwrite: boolean }>;

export function parseMigrationCliArguments(argv: readonly string[]): MigrationCliArguments {
  const flags = new Map<string, string | true>();
  const booleans = new Set(["--dry-run", "--apply", "--overwrite"]);
  const valued = new Set(["--scope", "--output", "--project-id", "--confirmation-token", "--idempotency-key"]);
  for (let index = 0; index < argv.length; index += 1) {
    const flag = argv[index]!;
    if (flags.has(flag) || (!booleans.has(flag) && !valued.has(flag))) throw new Error("migration_cli_invalid");
    if (booleans.has(flag)) flags.set(flag, true);
    else {
      const value = argv[++index];
      if (value === undefined || value.startsWith("--") || value.length === 0 || /[\u0000-\u001f\u007f-\u009f]/u.test(value)) throw new Error("migration_cli_invalid");
      flags.set(flag, value);
    }
  }
  const dryRun = flags.has("--dry-run"); const apply = flags.has("--apply");
  if (dryRun === apply) throw new Error("migration_cli_invalid");
  const output = flags.get("--output"); const overwrite = flags.has("--overwrite");
  if (output !== undefined && (typeof output !== "string" || isAbsolute(output) || output.split(/[\\/]/).includes(".."))) throw new Error("migration_cli_invalid");
  if (dryRun) {
    const scope = flags.get("--scope");
    if ((scope !== "fixtures" && scope !== "all") || flags.has("--project-id") || flags.has("--confirmation-token") || flags.has("--idempotency-key")) throw new Error("migration_cli_invalid");
    return Object.freeze({ mode: "dry-run" as const, scope, ...(output === undefined ? {} : { output }), overwrite });
  }
  if (flags.has("--scope")) throw new Error("migration_cli_invalid");
  const projectId = flags.get("--project-id"); const confirmationToken = flags.get("--confirmation-token"); const idempotencyKey = flags.get("--idempotency-key");
  if (typeof projectId !== "string" || !UUID.test(projectId) || typeof confirmationToken !== "string" || !TOKEN.test(confirmationToken)
    || typeof idempotencyKey !== "string" || !TOKEN.test(idempotencyKey)) throw new Error("migration_cli_invalid");
  return Object.freeze({ mode: "apply" as const, projectId, confirmationToken, idempotencyKey, ...(output === undefined ? {} : { output }), overwrite });
}

async function atomicWrite(relativePath: string, value: unknown, overwrite: boolean): Promise<void> {
  const target = resolve(process.cwd(), relativePath); const root = resolve(process.cwd());
  if (target !== root && !target.startsWith(`${root}\\`) && !target.startsWith(`${root}/`)) throw new Error("migration_output_invalid");
  await mkdir(dirname(target), { recursive: true });
  const temporary = `${target}.${process.pid}.tmp`;
  try {
    const handle = await open(temporary, "wx", 0o600);
    try { await handle.writeFile(`${JSON.stringify(value)}\n`, "utf8"); await handle.sync(); } finally { await handle.close(); }
    if (!overwrite) { const probe = await open(target, "wx", 0o600); await probe.close(); await rm(target); }
    await rename(temporary, target);
  } catch (error) { await rm(temporary, { force: true }); throw error; }
}

function fixtureUuid(cardId: string): string {
  const bytes = Buffer.from(createHash("sha256").update("rowboat:migration-cli-fixture:v1\0").update(cardId).digest("hex").slice(0, 32), "hex");
  bytes[6] = (bytes[6]! & 0x0f) | 0x50; bytes[8] = (bytes[8]! & 0x3f) | 0x80; const hex = bytes.toString("hex");
  return `${hex.slice(0,8)}-${hex.slice(8,12)}-${hex.slice(12,16)}-${hex.slice(16,20)}-${hex.slice(20)}`;
}
function fixtureReport(): unknown {
  const catalog = JSON.parse(readFileSync(new URL("../../../config/openai-plugin-catalog.lock.json", import.meta.url), "utf8")) as PluginCatalogLock;
  const previews = LEGACY_CARD_IDS.map(cardId => {
    const configuration = JSON.parse(readFileSync(new URL(`../app/lib/prebuilt-cards/${cardId}.json`, import.meta.url), "utf8")) as Readonly<Record<string, unknown>>;
    const updatedAt = typeof configuration.lastUpdatedAt === "string" ? configuration.lastUpdatedAt : catalog.importedAt;
    return new LegacyPluginMigration().preview({ projectId: fixtureUuid(cardId), sourceProjectRevision: Date.parse(updatedAt), sourceUpdatedAt: updatedAt, legacyCardId: cardId, sourceConfiguration: configuration }, catalog);
  });
  const blockerReasons: Record<string, number> = Object.create(null) as Record<string, number>;
  for (const preview of previews) for (const blocker of preview.blockers) blockerReasons[blocker.code] = (blockerReasons[blocker.code] ?? 0) + 1;
  return Object.freeze({ version: 1, catalogDigest: catalog.catalogDigest, sourceCommit: catalog.sourceCommit, policyVersion: catalog.policyVersion,
    generatedAt: catalog.importedAt, snapshotToken: "fixtures-v1", scope: "fixtures", projectCount: previews.length,
    blockerCount: previews.reduce((sum, preview) => sum + preview.blockers.length, 0), blockerReasons,
    mutationCount: 0, receiptIds: [], mutationsApplied: false,
    projects: previews.map(preview => ({ projectId: preview.projectId, sourceProjectRevision: preview.sourceProjectRevision, sourceDigest: preview.sourceDigest,
      sourceInventoryDigest: preview.sourceInventoryDigest, recipeId: preview.recipeId, recipeDigest: preview.recipeDigest,
      rollbackSnapshotDigest: preview.rollbackSnapshotDigest, targetCatalogDigest: preview.targetCatalogDigest,
      targetInstallationIds: preview.targetInstallationIds, status: preview.status, blockers: preview.blockers })) });
}

export async function runMigrationCli(argv: readonly string[]): Promise<unknown> {
  const selected = parseMigrationCliArguments(argv);
  let output: unknown;
  if (selected.mode === "dry-run" && selected.scope === "fixtures") output = fixtureReport();
  else {
    const module = await import("../di/plugin-migration-container");
    output = selected.mode === "dry-run" ? await module.previewAllMigrations() : await module.applyProjectMigration(selected);
  }
  if (selected.output !== undefined) await atomicWrite(selected.output, output, selected.overwrite);
  return output;
}

async function main(): Promise<void> {
  try { process.stdout.write(`${JSON.stringify(await runMigrationCli(process.argv.slice(2)))}\n`); }
  catch (error) { const code = error instanceof Error && /^[a-z0-9_]+$/.test(error.message) ? error.message : "migration_failed"; process.stderr.write(`${JSON.stringify({ error: code })}\n`); process.exitCode = 1; }
}
const invoked = process.argv[1] === fileURLToPath(import.meta.url);
if (invoked) void main();
