import { join } from "node:path";
import type { NormalizedPlugin, SourceProvenance } from "../domain/plugin.js";
import {
  parsePluginManifest,
  PluginManifestValidationError,
} from "../schema/plugin-manifest.js";
import {
  assertDirectoryIdentity,
  snapshotDirectoryIdentity,
  type DirectoryIdentity,
} from "./directory-identity.js";
import { discoverPluginComponentsFromIdentity } from "./component-discovery.js";
import { streamContainedRegularFile } from "./safe-file-stream.js";

const MAX_MANIFEST_BYTES = 1024 * 1024;

async function readManifest(root: DirectoryIdentity): Promise<unknown> {
  const chunks: Buffer[] = [];
  let manifestSize = 0n;
  await streamContainedRegularFile(root, join(root.canonicalPath, "plugin.json"), {}, {
    onOpen(size): void {
      manifestSize = size;
      if (size > BigInt(MAX_MANIFEST_BYTES)) {
        throw new PluginManifestValidationError(["<root>"]);
      }
    },
    onChunk(chunk): void {
      chunks.push(Buffer.from(chunk));
    },
  });
  await assertDirectoryIdentity(root);
  try {
    return JSON.parse(Buffer.concat(chunks, Number(manifestSize)).toString("utf8")) as unknown;
  } catch {
    throw new PluginManifestValidationError(["<root>"]);
  }
}

export async function normalizePlugin(
  pluginRoot: string,
  provenance: SourceProvenance,
): Promise<NormalizedPlugin> {
  const root = await snapshotDirectoryIdentity(pluginRoot);
  const manifest = parsePluginManifest(await readManifest(root));
  const discovery = await discoverPluginComponentsFromIdentity(root, manifest);
  await assertDirectoryIdentity(root);

  return Object.freeze({
    manifest,
    provenance,
    status: discovery.hasInvalidComponent ? "partially_available" : "available",
    components: discovery.components,
  });
}
