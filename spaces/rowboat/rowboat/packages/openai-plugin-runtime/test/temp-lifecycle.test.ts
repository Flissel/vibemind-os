import { access, rm, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import {
  cleanupOwnedTestRoot,
  cleanupRegisteredTestRoots,
  createOwnedTestRoot,
} from "./test-temp.js";

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
    const sentinel = join(root, ".rowboat-openai-plugin-runtime-test-temp");
    await rm(sentinel);

    await expect(cleanupOwnedTestRoot(root)).rejects.toThrow("sentinel is missing");

    await writeFile(sentinel, "rowboat-openai-plugin-runtime-test-temp-v1\n", { flag: "wx" });
  });
});
