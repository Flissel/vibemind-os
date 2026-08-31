import { link, lstat, mkdir, open, realpath, rm } from "node:fs/promises";
import { readFileSync } from "node:fs";
import { createHash } from "node:crypto";
import { basename, isAbsolute, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import type { PluginCatalogLock } from "@rowboat/openai-plugin-runtime";
import { LegacyPluginMigration } from "@/src/application/services/legacy-plugin-migration";
import { LEGACY_CARD_IDS } from "@/src/application/services/legacy-plugin-recipes";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const TOKEN = /^[A-Za-z0-9._~-]{1,8192}$/;
const RESERVED = /^(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?$/i;
function safeOutputSyntax(value: string): boolean {
  const parts = value.split(/[\\/]/); return !isAbsolute(value) && parts.length === 2 && parts[0] === ".artifacts" && parts[1] !== "" && parts[1] !== "." && parts[1] !== ".."
    && !RESERVED.test(parts[1]) && !parts[1].endsWith(".") && !parts[1].endsWith(" ") && !/[:\u0000-\u001f\u007f-\u009f]/u.test(parts[1]);
}
export type MigrationCliArguments =
  | Readonly<{ mode: "dry-run"; scope: "fixtures" | "all"; output?: string }>
  | Readonly<{ mode: "apply"; projectId: string; confirmationToken: string; output?: string }>;

export function parseMigrationCliArguments(argv: readonly string[]): MigrationCliArguments {
  const flags = new Map<string, string | true>();
  const booleans = new Set(["--dry-run", "--apply"]);
  const valued = new Set(["--scope", "--output", "--project-id", "--confirmation-token"]);
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
  const output = flags.get("--output");
  if (output !== undefined && (typeof output !== "string" || !safeOutputSyntax(output))) throw new Error("migration_cli_invalid");
  if (dryRun) {
    const scope = flags.get("--scope");
    if ((scope !== "fixtures" && scope !== "all") || flags.has("--project-id") || flags.has("--confirmation-token")) throw new Error("migration_cli_invalid");
    return Object.freeze({ mode: "dry-run" as const, scope, ...(output === undefined ? {} : { output }) });
  }
  if (flags.has("--scope")) throw new Error("migration_cli_invalid");
  const projectId = flags.get("--project-id"); const confirmationToken = flags.get("--confirmation-token");
  if (typeof projectId !== "string" || !UUID.test(projectId) || typeof confirmationToken !== "string" || !TOKEN.test(confirmationToken)) throw new Error("migration_cli_invalid");
  return Object.freeze({ mode: "apply" as const, projectId, confirmationToken, ...(output === undefined ? {} : { output }) });
}

export async function publishMigrationOutput(relativePath: string, value: unknown, beforeLink?: () => Promise<void>): Promise<void> {
  const parts = relativePath.split(/[\\/]/);
  if (!safeOutputSyntax(relativePath)) throw new Error("migration_output_invalid");
  const requestedRoot = resolve(process.cwd()); const root = await realpath(requestedRoot);
  const rootStat = await lstat(root); if (!rootStat.isDirectory() || rootStat.isSymbolicLink()) throw new Error("migration_output_invalid");
  const artifactDirectory = join(root, ".artifacts"); await mkdir(artifactDirectory, { recursive: false }).catch((error: unknown) => {
    if (!(error instanceof Error) || !("code" in error) || error.code !== "EEXIST") throw error;
  });
  const artifactStat = await lstat(artifactDirectory); const actualArtifactDirectory = await realpath(artifactDirectory);
  if (!artifactStat.isDirectory() || artifactStat.isSymbolicLink() || relative(root, actualArtifactDirectory) !== ".artifacts") throw new Error("migration_output_invalid");
  const target = join(actualArtifactDirectory, basename(parts[1])); const temporary = join(actualArtifactDirectory, `.${parts[1]}.${process.pid}.${Date.now()}.tmp`);
  try {
    const handle = await open(temporary, "wx", 0o600);
    try { await handle.writeFile(`${JSON.stringify(value)}\n`, "utf8"); await handle.sync(); } finally { await handle.close(); }
    if (beforeLink !== undefined) await beforeLink();
    const currentArtifactStat = await lstat(artifactDirectory); const currentArtifactRealPath = await realpath(artifactDirectory);
    if (!currentArtifactStat.isDirectory() || currentArtifactStat.isSymbolicLink() || currentArtifactRealPath !== actualArtifactDirectory) throw new Error("migration_output_invalid");
    try { await link(temporary, target); } catch (error) {
      if (error instanceof Error && "code" in error && error.code === "EEXIST") throw new Error("migration_output_exists");
      throw error;
    }
    try { const directoryHandle = await open(actualArtifactDirectory, "r"); try { await directoryHandle.sync(); } finally { await directoryHandle.close(); } }
    catch (error) { if (process.platform !== "win32") throw error; }
  } catch (error) { if (error instanceof Error && /^[a-z0-9_]+$/.test(error.message)) throw error; throw new Error("migration_output_failed"); }
  finally { await rm(temporary, { force: true }); }
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
  if (selected.output !== undefined) await publishMigrationOutput(selected.output, output);
  return output;
}

async function main(): Promise<void> {
  try { process.stdout.write(`${JSON.stringify(await runMigrationCli(process.argv.slice(2)))}\n`); }
  catch (error) { const code = error instanceof Error && /^[a-z0-9_]+$/.test(error.message) ? error.message : "migration_failed"; process.stderr.write(`${JSON.stringify({ error: code })}\n`); process.exitCode = 1; }
}
const invoked = process.argv[1] === fileURLToPath(import.meta.url);
if (invoked) void main();
