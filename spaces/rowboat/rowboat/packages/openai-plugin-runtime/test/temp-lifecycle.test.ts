import { access, readFile, rm, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import {
  cleanupOwnedTestRoot,
  cleanupRegisteredTestRoots,
  createOwnedTestRoot,
} from "./test-temp.js";
import { OWNED_TEMP_SENTINEL } from "../src/import/snapshot-temp.js";

afterEach(cleanupRegisteredTestRoots);

describe("owned test temp lifecycle", () => {
  it("removes an owned root after making readonly children writable", async () => {
    const root = await createOwnedTestRoot("cleanup-success");
    await writeFile(join(root, "readonly.txt"), "test", { mode: 0o400 });

    await cleanupOwnedTestRoot(root);

    await expect(access(root)).rejects.toBeDefined();
  });

  it("refuses cleanup without the package-owned sentinel", async () => {
    const root = await createOwnedTestRoot("cleanup-failure");
    const sentinel = join(root, OWNED_TEMP_SENTINEL);
    const original = await readFile(sentinel);
    await rm(sentinel);
    try {
      await expect(cleanupOwnedTestRoot(root)).rejects.toThrow("sentinel is invalid");
    } finally {
      await writeFile(sentinel, original, { flag: "wx" });
    }
  });
});
