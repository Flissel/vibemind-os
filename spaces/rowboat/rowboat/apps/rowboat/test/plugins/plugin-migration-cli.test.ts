import { describe, expect, it } from "vitest";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { parseMigrationCliArguments } from "../../scripts/migrate-openai-plugins";

describe("plugin migration CLI parser", () => {
  it("accepts only the exact fixture dry-run shape", () => {
    expect(parseMigrationCliArguments(["--dry-run", "--scope", "fixtures", "--output", ".artifacts/report.json"]))
      .toEqual({ mode: "dry-run", scope: "fixtures", output: ".artifacts/report.json", overwrite: false });
  });

  it("accepts an exact guarded apply shape", () => {
    expect(parseMigrationCliArguments(["--apply", "--project-id", "11111111-1111-4111-8111-111111111111", "--confirmation-token", "signed-token", "--idempotency-key", "apply-1", "--output", "apply.json"]))
      .toMatchObject({ mode: "apply", projectId: "11111111-1111-4111-8111-111111111111", confirmationToken: "signed-token", idempotencyKey: "apply-1" });
  });

  it.each([
    ["duplicate", ["--dry-run", "--dry-run", "--scope", "fixtures"]],
    ["ambiguous", ["--dry-run", "--apply", "--scope", "fixtures"]],
    ["unknown", ["--dry-run", "--scope", "fixtures", "--wat", "x"]],
    ["all apply", ["--apply", "--scope", "all"]],
    ["token control", ["--apply", "--project-id", "11111111-1111-4111-8111-111111111111", "--confirmation-token", "x\n", "--idempotency-key", "apply-1"]],
  ] as const)("rejects %s arguments", (_name, args) => expect(() => parseMigrationCliArguments([...args])).toThrow("migration_cli_invalid"));
});

describe("plugin migration CLI process", () => {
  const cli = new URL("../../scripts/migrate-openai-plugins.ts", import.meta.url);
  const tsx = new URL("../../node_modules/tsx/dist/cli.mjs", import.meta.url);
  it("emits one machine JSON fixture report and atomically writes the requested artifact", () => {
    const directory = mkdtempSync(join(process.cwd(), ".rowboat-migration-cli-"));
    try {
      const relative = `${directory.slice(process.cwd().length + 1)}\\report.json`;
      const result = spawnSync(process.execPath, [fileURLToPath(tsx), fileURLToPath(cli), "--dry-run", "--scope", "fixtures", "--output", relative], { cwd: process.cwd(), encoding: "utf8" });
      expect(result.status).toBe(0); expect(result.stderr).toBe("");
      const stdout = JSON.parse(result.stdout) as Record<string, unknown>;
      const file = JSON.parse(readFileSync(join(directory, "report.json"), "utf8")) as Record<string, unknown>;
      expect(stdout).toEqual(file); expect(stdout).toMatchObject({ scope: "fixtures", projectCount: 10, mutationCount: 0, mutationsApplied: false });
      expect(JSON.stringify(stdout)).not.toMatch(/PLUGIN_|mongodb(?:\+srv)?:|secret|credential/i);
    } finally { rmSync(directory, { recursive: true, force: true }); }
  });
  it("refuses overwrite with empty stdout and safe machine stderr", () => {
    const directory = mkdtempSync(join(process.cwd(), ".rowboat-migration-cli-"));
    try {
      writeFileSync(join(directory, "report.json"), "keep", "utf8");
      const relative = `${directory.slice(process.cwd().length + 1)}\\report.json`;
      const result = spawnSync(process.execPath, [fileURLToPath(tsx), fileURLToPath(cli), "--dry-run", "--scope", "fixtures", "--output", relative], { cwd: process.cwd(), encoding: "utf8" });
      expect(result.status).toBe(1); expect(result.stdout).toBe(""); expect(JSON.parse(result.stderr)).toEqual({ error: "migration_failed" });
      expect(readFileSync(join(directory, "report.json"), "utf8")).toBe("keep");
    } finally { rmSync(directory, { recursive: true, force: true }); }
  });
});
