import { createHash } from "node:crypto";
import { basename, join } from "node:path";
import { z } from "zod";
import type { NormalizedPlugin, SourceProvenance } from "../domain/plugin.js";
import {
  parsePluginManifest,
  PluginManifestValidationError,
} from "../schema/plugin-manifest.js";
import { discoverPluginComponentsFromIdentity } from "./component-discovery.js";
import {
  assertDirectoryIdentity,
  snapshotDirectoryIdentity,
  type DirectoryIdentity,
} from "./directory-identity.js";
import { digestTree } from "./digest-service.js";
import { PluginSourceSecurityError } from "./path-guard.js";
import { streamContainedRegularFile } from "./safe-file-stream.js";
import {
  assertVerifiedPinnedSource,
  type VerifiedPinnedSource,
} from "./source-reader.js";

const MAX_MANIFEST_BYTES = 1024 * 1024;
const SHA40 = /^[a-f0-9]{40}$/;
const SHA256 = /^[a-f0-9]{64}$/;

const SourceProvenanceSchema = z
  .object({
    sourceUrl: z.string().url(),
    sourceCommit: z.string().regex(SHA40),
    pluginName: z.string().regex(/^[a-z0-9]+(?:-[a-z0-9]+)*$/),
    pluginVersion: z.string().min(1),
    manifestDigest: z.string().regex(SHA256),
    treeDigest: z.string().regex(SHA256),
    importedAt: z.string().datetime({ offset: true }),
    schemaVersion: z.string().min(1),
    policyVersion: z.string().min(1),
  })
  .strict();

interface ReadManifestResult {
  readonly input: unknown;
  readonly digest: string;
}

export interface NormalizePluginOptions {
  readonly beforeFinalTreeDigest?: () => Promise<void> | void;
}

function validatedProvenance(input: SourceProvenance): SourceProvenance {
  const result = SourceProvenanceSchema.safeParse(input);
  if (!result.success) {
    throw new PluginSourceSecurityError("source_mismatch", "provenance is invalid");
  }
  return Object.freeze({ ...result.data });
}

async function readManifest(root: DirectoryIdentity): Promise<ReadManifestResult> {
  const chunks: Buffer[] = [];
  const hash = createHash("sha256");
  let manifestSize = 0n;
  await streamContainedRegularFile(
    root,
    join(root.canonicalPath, ".codex-plugin", "plugin.json"),
    {},
    {
      onOpen(size): void {
        manifestSize = size;
        if (size > BigInt(MAX_MANIFEST_BYTES)) {
          throw new PluginManifestValidationError(["<root>"]);
        }
      },
      onChunk(chunk): void {
        hash.update(chunk);
        chunks.push(Buffer.from(chunk));
      },
    },
  );
  await assertDirectoryIdentity(root);
  try {
    return {
      input: JSON.parse(Buffer.concat(chunks, Number(manifestSize)).toString("utf8")) as unknown,
      digest: hash.digest("hex"),
    };
  } catch {
    throw new PluginManifestValidationError(["<root>"]);
  }
}

function assertDigest(expected: string, actual: string, subject: string): void {
  if (expected !== actual) {
    throw new PluginSourceSecurityError("digest_mismatch", `${subject} digest differs`);
  }
}

export async function normalizePlugin(
  pluginRoot: string,
  provenanceInput: SourceProvenance,
  verifiedSource: VerifiedPinnedSource,
  options: NormalizePluginOptions = {},
): Promise<NormalizedPlugin> {
  const provenance = validatedProvenance(provenanceInput);
  await assertVerifiedPinnedSource(
    verifiedSource,
    pluginRoot,
    provenance.sourceUrl,
    provenance.sourceCommit,
  );
  assertDigest(provenance.treeDigest, await digestTree(pluginRoot), "source tree");

  const root = await snapshotDirectoryIdentity(pluginRoot);
  const manifestRead = await readManifest(root);
  assertDigest(provenance.manifestDigest, manifestRead.digest, "manifest");
  const manifest = parsePluginManifest(manifestRead.input);
  if (basename(root.canonicalPath) !== manifest.name) {
    throw new PluginSourceSecurityError(
      "source_mismatch",
      "plugin directory does not match manifest name",
    );
  }
  if (
    provenance.pluginName !== manifest.name ||
    provenance.pluginVersion !== manifest.version
  ) {
    throw new PluginSourceSecurityError(
      "source_mismatch",
      "provenance does not identify manifest",
    );
  }

  const discovery = await discoverPluginComponentsFromIdentity(root, manifest);
  await assertDirectoryIdentity(root);
  await options.beforeFinalTreeDigest?.();
  assertDigest(provenance.treeDigest, await digestTree(pluginRoot), "source tree");
  await assertDirectoryIdentity(root);
  await assertVerifiedPinnedSource(
    verifiedSource,
    pluginRoot,
    provenance.sourceUrl,
    provenance.sourceCommit,
  );

  return Object.freeze({
    manifest,
    provenance,
    status: discovery.hasInvalidComponent ? "partially_available" : "available",
    components: discovery.components,
  });
}
