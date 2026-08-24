import { execFile } from "node:child_process";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";
import { describe, expect, it } from "vitest";
import * as contentStoreModule from "../src/store/content-store.js";
import type { GitFileMode } from "../src/import/digest-service.js";

const execFileAsync = promisify(execFile);

interface ModeStats {
  readonly mode: bigint;
  isFile(): boolean;
}

interface ModeHandle {
  chmod(mode: number): Promise<void>;
  stat(options: { readonly bigint: true }): Promise<ModeStats>;
}

type EnforceMaterializedGitMode = (
  handle: ModeHandle,
  mode: GitFileMode,
  platform: NodeJS.Platform,
) => Promise<void>;

function modeEnforcer(): EnforceMaterializedGitMode {
  const candidate = (
    contentStoreModule as unknown as {
      readonly enforceMaterializedGitMode?: EnforceMaterializedGitMode;
    }
  ).enforceMaterializedGitMode;
  expect(candidate).toBeTypeOf("function");
  if (candidate === undefined) throw new Error("mode enforcer unavailable");
  return candidate;
}

describe("materialized Git file modes", () => {
  it("sets and verifies the expected POSIX mode through the same handle", async () => {
    const operations: string[] = [];
    const handle: ModeHandle = {
      async chmod(mode): Promise<void> {
        operations.push(`chmod:${mode.toString(8)}`);
      },
      async stat(): Promise<ModeStats> {
        operations.push("stat");
        return { mode: 0o100755n, isFile: () => true };
      },
    };

    await modeEnforcer()(handle, "100755", "linux");

    expect(operations).toStrictEqual(["chmod:755", "stat"]);
  });

  it("fails closed when same-handle verification observes the wrong mode", async () => {
    const handle: ModeHandle = {
      async chmod(): Promise<void> {},
      async stat(): Promise<ModeStats> {
        return { mode: 0o100644n, isFile: () => true };
      },
    };

    await expect(modeEnforcer()(handle, "100755", "linux"))
      .rejects.toMatchObject({ code: "digest_mismatch" });
  });

  it("keeps Windows Git modes logical without asserting filesystem execute bits", async () => {
    const handle: ModeHandle = {
      async chmod(): Promise<void> {
        throw new Error("Windows must not chmod the materialized file");
      },
      async stat(): Promise<ModeStats> {
        throw new Error("Windows must not assert POSIX permission bits");
      },
    };

    await expect(modeEnforcer()(handle, "100755", "win32")).resolves.toBeUndefined();
  });

  it.skipIf(process.platform === "win32")(
    "publishes and reuses a 100755 file under a restrictive umask",
    async () => {
      const child = fileURLToPath(
        new URL("./helpers/content-store-umask-child.ts", import.meta.url),
      );
      await expect(execFileAsync(
        process.execPath,
        ["--import", "tsx", child],
        { timeout: 30_000, maxBuffer: 1024 * 1024, windowsHide: true },
      )).resolves.toBeDefined();
    },
  );
});
