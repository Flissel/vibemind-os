import { chmod, mkdir, stat, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { digestTree, type FileModeResolver } from "../../src/import/digest-service.js";
import {
  createOwnedTempRoot,
  recoverStaleOwnedTempRoots,
  removeOwnedTempRoot,
} from "../../src/import/snapshot-temp.js";
import { ContentStore } from "../../src/store/content-store.js";

const PREFIX = "rowboat-openai-plugin-runtime-test-mode-subprocess-";
const KIND = "test-root";

async function main(): Promise<void> {
  await recoverStaleOwnedTempRoots(PREFIX, KIND);
  const root = await createOwnedTempRoot(PREFIX, KIND);
  try {
    const repositoryRoot = join(root, "repository");
    const pluginRoot = join(repositoryRoot, "plugin");
    const storeRoot = join(root, "store", "nested");
    await mkdir(pluginRoot, { recursive: true });
    await writeFile(join(pluginRoot, "run.sh"), "#!/bin/sh\nexit 0\n", "utf8");
    await chmod(join(pluginRoot, "run.sh"), 0o755);
    const fileModeResolver: FileModeResolver = (relativePath) =>
      relativePath === "run.sh" ? "100755" : undefined;
    const digest = await digestTree(pluginRoot, { fileModeResolver });
    const previousUmask = process.umask(0o111);
    try {
      const store = new ContentStore({
        storeRoot,
        repositoryRoot,
        fileModeResolver,
      });

      const first = await store.put(pluginRoot, digest);
      const publishedFile = join(first.path, "run.sh");
      const firstMode = (await stat(publishedFile)).mode & 0o777;
      if (firstMode !== 0o755) throw new Error("first publication mode mismatch");
      for (const directory of [join(root, "store"), storeRoot, first.path]) {
        if (((await stat(directory)).mode & 0o777) !== 0o700) {
          throw new Error("owned directory mode mismatch");
        }
      }

      const second = await store.put(pluginRoot, digest);
      const secondMode = (await stat(join(second.path, "run.sh"))).mode & 0o777;
      if (secondMode !== 0o755) throw new Error("reused publication mode mismatch");

      await chmod(publishedFile, 0o644);
      let rejectedTamper = false;
      try {
        await store.put(pluginRoot, digest);
      } catch (error: unknown) {
        rejectedTamper =
          typeof error === "object" &&
          error !== null &&
          "code" in error &&
          error.code === "digest_mismatch";
      }
      if (!rejectedTamper) throw new Error("tampered publication was reused");
    } finally {
      process.umask(previousUmask);
    }
  } finally {
    await removeOwnedTempRoot(root, PREFIX, KIND);
  }
}

await main();
