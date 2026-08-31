import { describe, expect, it } from "vitest";
import { mkdtempSync, readFileSync, readdirSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { randomUUID } from "node:crypto";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { parseMigrationCliArguments, publishMigrationOutput } from "../../scripts/migrate-openai-plugins";

describe("plugin migration CLI parser", () => {
  it("accepts only documented fixture and apply shapes", () => {
    expect(parseMigrationCliArguments(["--dry-run", "--scope", "fixtures", "--output", ".artifacts/report.json"])).toEqual({ mode: "dry-run", scope: "fixtures", output: ".artifacts/report.json" });
    expect(parseMigrationCliArguments(["--apply", "--project-id", "11111111-1111-4111-8111-111111111111", "--confirmation-token", "signed-token", "--output", ".artifacts/apply.json"]))
      .toEqual({ mode: "apply", projectId: "11111111-1111-4111-8111-111111111111", confirmationToken: "signed-token", output: ".artifacts/apply.json" });
  });
  it.each([
    ["duplicate", ["--dry-run", "--dry-run", "--scope", "fixtures"]], ["ambiguous", ["--dry-run", "--apply", "--scope", "fixtures"]],
    ["unknown", ["--dry-run", "--scope", "fixtures", "--wat", "x"]], ["undocumented idempotency", ["--apply", "--idempotency-key", "x"]],
    ["overwrite", ["--dry-run", "--scope", "fixtures", "--overwrite"]], ["outside artifacts", ["--dry-run", "--scope", "fixtures", "--output", "report.json"]],
  ] as const)("rejects %s arguments or output at the boundary", async (_name, args) => {
    if (args.some(value => value === "report.json")) await expect(publishMigrationOutput("report.json", {})).rejects.toThrow("migration_output_invalid");
    else expect(() => parseMigrationCliArguments([...args])).toThrow("migration_cli_invalid");
  });
});

describe("plugin migration CLI process", () => {
  const cli = new URL("../../scripts/migrate-openai-plugins.ts", import.meta.url); const tsx = new URL("../../node_modules/tsx/dist/cli.mjs", import.meta.url);
  it("emits one machine JSON fixture report and creates a no-clobber artifact", () => {
    const name = `migration-${randomUUID()}.json`; const relative = `.artifacts/${name}`; const absolute = join(process.cwd(), ".artifacts", name);
    try {
      const result = spawnSync(process.execPath, [fileURLToPath(tsx), fileURLToPath(cli), "--dry-run", "--scope", "fixtures", "--output", relative], { cwd: process.cwd(), encoding: "utf8" });
      expect(result.status).toBe(0); expect(result.stderr).toBe(""); const stdout = JSON.parse(result.stdout) as Record<string, unknown>; expect(stdout).toEqual(JSON.parse(readFileSync(absolute, "utf8"))); expect(stdout).toMatchObject({ scope: "fixtures", projectCount: 10, mutationCount: 0, mutationsApplied: false });
      expect(JSON.stringify(stdout)).not.toMatch(/PLUGIN_|mongodb(?:\+srv)?:|secret|credential/i);
    } finally { rmSync(absolute, { force: true }); }
  });
  it("never overwrites a target created concurrently and cleans its temp", async () => {
    const name = `migration-${randomUUID()}.json`; const relative = `.artifacts/${name}`; const absolute = join(process.cwd(), ".artifacts", name);
    try {
      await expect(publishMigrationOutput(relative, { safe: true }, async () => { writeFileSync(absolute, "keep", "utf8"); })).rejects.toThrow("migration_output_exists");
      expect(readFileSync(absolute, "utf8")).toBe("keep"); const prefix = `.${name}.`; expect(readdirSync(join(process.cwd(), ".artifacts")).some(entry => entry.startsWith(prefix))).toBe(false);
    } finally { rmSync(absolute, { force: true }); }
  });
  it.each([".", ".artifacts/CON", ".artifacts/trailing.", ".artifacts/nested/report.json"])("rejects unsafe output %s", async output => { await expect(publishMigrationOutput(output, {})).rejects.toThrow("migration_output_invalid"); });
  it("rejects a symlink or junction artifacts parent without writing through it", () => {
    const root = mkdtempSync(join(tmpdir(), "rowboat-migration-root-")); const outside = mkdtempSync(join(tmpdir(), "rowboat-migration-outside-"));
    try {
      symlinkSync(outside, join(root, ".artifacts"), process.platform === "win32" ? "junction" : "dir");
      const result = spawnSync(process.execPath, [fileURLToPath(tsx), fileURLToPath(cli), "--dry-run", "--scope", "fixtures", "--output", ".artifacts/report.json"], {
        cwd: root, encoding: "utf8", env: { ...process.env, TSX_TSCONFIG_PATH: join(process.cwd(), "tsconfig.json") },
      });
      expect(result.status).toBe(1); expect(result.stdout).toBe(""); expect(JSON.parse(result.stderr)).toEqual({ error: "migration_output_invalid" }); expect(readdirSync(outside)).toEqual([]);
    } finally { rmSync(root, { recursive: true, force: true }); rmSync(outside, { recursive: true, force: true }); }
  });
});
