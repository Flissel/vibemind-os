import { randomUUID } from "node:crypto";
import { access, readFile, symlink, unlink, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import {
  createOwnedTempRoot,
  createSnapshotTempRoot,
  OWNED_TEMP_SENTINEL,
  recoverStaleSnapshotTempRoots,
  removeSnapshotTempRoot,
} from "../src/import/snapshot-temp.js";
import {
  cleanupOwnedTestRoot,
  createOwnedTestRoot,
  recoverOwnedTestRoots,
} from "./test-temp.js";

function deadPid(): number {
  const candidate = 2_147_483_647;
  try {
    process.kill(candidate, 0);
  } catch (error: unknown) {
    if (typeof error === "object" && error !== null && "code" in error && error.code === "ESRCH") {
      return candidate;
    }
  }
  throw new Error("test environment cannot prove a dead PID");
}

async function rewriteOwner(root: string, ownerPid: number): Promise<Buffer> {
  const path = join(root, OWNED_TEMP_SENTINEL);
  const original = await readFile(path);
  const parsed = JSON.parse(original.toString("utf8")) as Record<string, unknown>;
  await writeFile(path, `${JSON.stringify({ ...parsed, ownerPid })}\n`, "utf8");
  return original;
}

describe("stale owned temp recovery", () => {
  it("removes a v2 snapshot whose owner PID is proven dead", async () => {
    const root = await createSnapshotTempRoot();
    await rewriteOwner(root, deadPid());

    const result = await recoverStaleSnapshotTempRoots();
    expect(result.removed).toBeGreaterThanOrEqual(1);
    await expect(access(root)).rejects.toBeDefined();
  });

  it("preserves a snapshot owned by a live PID", async () => {
    const root = await createSnapshotTempRoot();
    const result = await recoverStaleSnapshotTempRoots();
    expect(result.preserved).toBeGreaterThanOrEqual(1);
    await expect(access(root)).resolves.toBeUndefined();
    await removeSnapshotTempRoot(root);
  });

  it.each(["missing", "malformed"] as const)("preserves a %s sentinel", async (state) => {
    const root = await createSnapshotTempRoot();
    const path = join(root, OWNED_TEMP_SENTINEL);
    const original = await readFile(path);
    if (state === "missing") await unlink(path);
    else await writeFile(path, "not-json", "utf8");

    const result = await recoverStaleSnapshotTempRoots();
    expect(result.preserved).toBeGreaterThanOrEqual(1);
    await expect(access(root)).resolves.toBeUndefined();

    await writeFile(path, original, state === "missing" ? { flag: "wx" } : undefined);
    await removeSnapshotTempRoot(root);
  });

  it("preserves a symlink or junction without touching its target", async () => {
    const target = await createOwnedTestRoot("recovery-target");
    const link = join(tmpdir(), `rowboat-git-snapshot-link-${randomUUID()}`);
    await symlink(target, link, process.platform === "win32" ? "junction" : "dir");
    try {
      const result = await recoverStaleSnapshotTempRoots();
      expect(result.preserved).toBeGreaterThanOrEqual(1);
      await expect(access(target)).resolves.toBeUndefined();
    } finally {
      await unlink(link);
      await cleanupOwnedTestRoot(target);
    }
  });

  it("recovers an interrupted pin store through its exact suite namespace", async () => {
    const prefix = "rowboat-openai-plugin-runtime-test-pin-store-";
    const kind = "test-root";
    const root = await createOwnedTempRoot(prefix, kind);
    await rewriteOwner(root, deadPid());

    await expect(recoverOwnedTestRoots("pin-store")).resolves.toMatchObject({ removed: 1 });
    await expect(access(root)).rejects.toBeDefined();
  });
});
