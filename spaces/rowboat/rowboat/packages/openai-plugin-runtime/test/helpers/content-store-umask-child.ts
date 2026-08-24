import { mkdir, stat, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { digestTree, type FileModeResolver } from "../../src/import/digest-service.js";
import {
  createOwnedTempRoot,
  removeOwnedTempRoot,
} from "../../src/import/snapshot-temp.js";
import { ContentStore } from "../../src/store/content-store.js";

const PREFIX = "rowboat-openai-plugin-runtime-test-mode-subprocess-";
const KIND = "test-root";

async function main(): Promise<void> {
  process.umask(0o111);
  const root = await createOwnedTempRoot(PREFIX, KIND);
  try {
    const repositoryRoot = join(root, "repository");
    const pluginRoot = join(repositoryRoot, "plugin");
    const storeRoot = join(root, "store");
    await mkdir(pluginRoot, { recursive: true });
    await writeFile(join(pluginRoot, "run.sh"), "#!/bin/sh\nexit 0\n", "utf8");
    const fileModeResolver: FileModeResolver = (relativePath) =>
      relativePath === "run.sh" ? "100755" : undefined;
    const digest = await digestTree(pluginRoot, { fileModeResolver });
    const store = new ContentStore({
      storeRoot,
      repositoryRoot,
      fileModeResolver,
    });

    const first = await store.put(pluginRoot, digest);
    const firstMode = (await stat(join(first.path, "run.sh"))).mode & 0o777;
    if (firstMode !== 0o755) throw new Error("first publication mode mismatch");

    const second = await store.put(pluginRoot, digest);
    const secondMode = (await stat(join(second.path, "run.sh"))).mode & 0o777;
    if (secondMode !== 0o755) throw new Error("reused publication mode mismatch");
  } finally {
    await removeOwnedTempRoot(root, PREFIX, KIND);
  }
}

await main();
