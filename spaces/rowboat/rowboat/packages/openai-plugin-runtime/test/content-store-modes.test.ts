import { execFile } from "node:child_process";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";
import { describe, expect, it } from "vitest";
import * as contentStoreModule from "../src/store/content-store.js";
import type { GitFileMode } from "../src/import/digest-service.js";
import * as safeFileStreamModule from "../src/import/safe-file-stream.js";

const execFileAsync = promisify(execFile);

interface ModeStats {
  readonly mode: bigint;
  isFile(): boolean;
}

interface ModeHandle {
  chmod(mode: number): Promise<void>;
  stat(options: { readonly bigint: true }): Promise<ModeStats>;
}

interface DirectoryModeStats {
  readonly mode: bigint;
  isDirectory(): boolean;
  isSymbolicLink(): boolean;
}

interface DirectoryModeOperations {
  chmod(path: string, mode: number): Promise<void>;
  stat(path: string): Promise<DirectoryModeStats>;
}

type EnforceMaterializedGitMode = (
  handle: ModeHandle,
  mode: GitFileMode,
  platform: NodeJS.Platform,
) => Promise<void>;

type VerifyPersistedGitMode = (
  handle: Pick<ModeHandle, "stat">,
  mode: GitFileMode,
  platform: NodeJS.Platform,
) => Promise<void>;

type EnforceOwnedDirectoryMode = (
  path: string,
  platform: NodeJS.Platform,
  operations: DirectoryModeOperations,
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

function persistedModeVerifier(): VerifyPersistedGitMode {
  const candidate = (
    safeFileStreamModule as unknown as {
      readonly verifyPersistedGitMode?: VerifyPersistedGitMode;
    }
  ).verifyPersistedGitMode;
  expect(candidate).toBeTypeOf("function");
  if (candidate === undefined) throw new Error("persisted mode verifier unavailable");
  return candidate;
}

function directoryModeEnforcer(): EnforceOwnedDirectoryMode {
  const candidate = (
    contentStoreModule as unknown as {
      readonly enforceOwnedDirectoryMode?: EnforceOwnedDirectoryMode;
    }
  ).enforceOwnedDirectoryMode;
  expect(candidate).toBeTypeOf("function");
  if (candidate === undefined) throw new Error("directory mode enforcer unavailable");
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

  it("rejects a persisted POSIX file whose physical mode disagrees with the resolver", async () => {
    const handle: Pick<ModeHandle, "stat"> = {
      async stat(): Promise<ModeStats> {
        return { mode: 0o100644n, isFile: () => true };
      },
    };

    await expect(persistedModeVerifier()(handle, "100755", "linux"))
      .rejects.toMatchObject({ code: "digest_mismatch" });
  });

  it("enforces and verifies owned directory modes after creation", async () => {
    const operations: string[] = [];
    const filesystem: DirectoryModeOperations = {
      async chmod(path, mode): Promise<void> {
        operations.push(`chmod:${path}:${mode.toString(8)}`);
      },
      async stat(path): Promise<DirectoryModeStats> {
        operations.push(`stat:${path}`);
        return {
          mode: 0o40700n,
          isDirectory: () => true,
          isSymbolicLink: () => false,
        };
      },
    };

    await directoryModeEnforcer()("owned", "linux", filesystem);

    expect(operations).toStrictEqual(["chmod:owned:700", "stat:owned"]);
  });

  it("fails closed when an owned directory does not persist mode 0700", async () => {
    const filesystem: DirectoryModeOperations = {
      async chmod(): Promise<void> {},
      async stat(): Promise<DirectoryModeStats> {
        return {
          mode: 0o40755n,
          isDirectory: () => true,
          isSymbolicLink: () => false,
        };
      },
    };

    await expect(directoryModeEnforcer()("owned", "linux", filesystem))
      .rejects.toMatchObject({ code: "digest_mismatch" });
  });

  it("does not assert POSIX directory modes on Windows", async () => {
    const filesystem: DirectoryModeOperations = {
      async chmod(): Promise<void> {
        throw new Error("Windows must not chmod owned directories");
      },
      async stat(): Promise<DirectoryModeStats> {
        throw new Error("Windows must not assert POSIX directory bits");
      },
    };

    await expect(directoryModeEnforcer()("owned", "win32", filesystem))
      .resolves.toBeUndefined();
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
