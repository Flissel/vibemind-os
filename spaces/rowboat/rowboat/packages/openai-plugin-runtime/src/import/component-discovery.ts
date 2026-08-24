import { createHash } from "node:crypto";
import { lstat, readdir, realpath } from "node:fs/promises";
import { basename, join, relative, sep } from "node:path";
import type {
  NormalizedPluginComponent,
  PluginComponentKind,
  PluginMetadata,
} from "../domain/plugin.js";
import type { PluginManifest } from "../schema/plugin-manifest.js";
import {
  AppFileSchema,
  HookFileSchema,
  McpFileSchema,
} from "../schema/component-schemas.js";
import {
  assertDirectoryIdentity,
  snapshotDirectoryIdentity,
  type DirectoryIdentity,
} from "./directory-identity.js";
import { isContainedPath, PluginSourceSecurityError } from "./path-guard.js";
import { streamContainedRegularFile } from "./safe-file-stream.js";

const MAX_STRUCTURED_COMPONENT_BYTES = 1024 * 1024;

interface Candidate {
  readonly kind: PluginComponentKind;
  readonly pointer: string;
  readonly declared: boolean;
}

interface FileEntry {
  readonly kind: PluginComponentKind;
  readonly canonicalPath: string;
}

interface ReadComponentFile {
  readonly bytes?: Buffer;
  readonly digest: string;
}

export interface ComponentDiscoveryResult {
  readonly components: readonly NormalizedPluginComponent[];
  readonly hasInvalidComponent: boolean;
}

function slashPath(value: string): string {
  return value.split(sep).join("/");
}

function candidatesFor(manifest: PluginManifest): readonly Candidate[] {
  const candidates: Candidate[] = [];
  const add = (
    kind: PluginComponentKind,
    declared: string | undefined,
    conventional: string,
  ): void => {
    if (declared !== undefined) {
      candidates.push({ kind, pointer: declared, declared: true });
    }
    candidates.push({ kind, pointer: conventional, declared: false });
  };

  add("skill", manifest.skills, "skills");
  add("agent", manifest.agents, "agents");
  add("command", manifest.commands, "commands");
  add("mcp", manifest.mcpServers, ".mcp.json");
  add("app", manifest.apps, ".app.json");
  add("hook", manifest.hooks, "hooks/hooks.json");

  const assetPointers = [
    manifest.interface.composerIcon,
    manifest.interface.logo,
    ...(manifest.interface.screenshots ?? []),
  ];
  for (const pointer of assetPointers) {
    if (pointer !== undefined) {
      candidates.push({ kind: "asset", pointer, declared: true });
    }
  }

  return candidates;
}

async function containedCanonicalPath(
  root: DirectoryIdentity,
  pointer: string,
  declared: boolean,
): Promise<string | undefined> {
  if (pointer.length === 0) {
    if (declared) {
      throw new PluginSourceSecurityError("path_escape", "empty component pointer");
    }
    return undefined;
  }

  const candidate = join(root.canonicalPath, pointer);
  let candidateStats;
  try {
    candidateStats = await lstat(candidate);
  } catch {
    if (!declared) {
      return undefined;
    }
    throw new PluginSourceSecurityError(
      "path_escape",
      "declared component path does not exist",
    );
  }
  if (candidateStats.isSymbolicLink()) {
    throw new PluginSourceSecurityError("path_escape", "component link rejected");
  }
  let canonical: string;
  try {
    canonical = await realpath(candidate);
  } catch {
    if (!declared) {
      return undefined;
    }
    throw new PluginSourceSecurityError(
      "path_escape",
      "declared component path does not exist",
    );
  }

  if (!isContainedPath(root.canonicalPath, canonical)) {
    throw new PluginSourceSecurityError("path_escape", "component leaves source root");
  }
  await assertDirectoryIdentity(root);
  return canonical;
}

async function collectFiles(
  root: DirectoryIdentity,
  kind: PluginComponentKind,
  path: string,
  output: FileEntry[],
): Promise<void> {
  await assertDirectoryIdentity(root);
  const stats = await lstat(path);
  if (stats.isSymbolicLink()) {
    throw new PluginSourceSecurityError("path_escape", "component link rejected");
  }
  if (stats.isFile()) {
    output.push({ kind, canonicalPath: path });
    return;
  }
  if (!stats.isDirectory()) {
    throw new PluginSourceSecurityError("path_escape", "unsupported component entry");
  }

  const names = (await readdir(path)).sort();
  for (const name of names) {
    const child = join(path, name);
    const childStats = await lstat(child);
    if (childStats.isSymbolicLink()) {
      throw new PluginSourceSecurityError("path_escape", "component link rejected");
    }
    const canonicalChild = await realpath(child);
    if (!isContainedPath(root.canonicalPath, canonicalChild)) {
      throw new PluginSourceSecurityError("path_escape", "component leaves source root");
    }
    await collectFiles(root, kind, canonicalChild, output);
  }
  await assertDirectoryIdentity(root);
}

async function readComponentFile(
  root: DirectoryIdentity,
  path: string,
  retainBytes: boolean,
): Promise<ReadComponentFile> {
  const chunks: Buffer[] = [];
  const hash = createHash("sha256");
  let observedSize = 0n;

  await streamContainedRegularFile(root, path, {}, {
    onOpen(size): void {
      observedSize = size;
      if (retainBytes && size > BigInt(MAX_STRUCTURED_COMPONENT_BYTES)) {
        throw new PluginSourceSecurityError("path_escape", "component file too large");
      }
    },
    onChunk(chunk): void {
      hash.update(chunk);
      if (retainBytes) {
        chunks.push(Buffer.from(chunk));
      }
    },
  });

  if (observedSize > BigInt(Number.MAX_SAFE_INTEGER)) {
    throw new PluginSourceSecurityError("path_escape", "component size is unsafe");
  }
  const digest = hash.digest("hex");
  if (!retainBytes) {
    return { digest };
  }
  return { bytes: Buffer.concat(chunks, Number(observedSize)), digest };
}

function parseJson(bytes: Buffer): unknown {
  return JSON.parse(bytes.toString("utf8")) as unknown;
}

function isStructuredComponentValid(
  kind: PluginComponentKind,
  bytes: Buffer,
): boolean {
  if (kind !== "mcp" && kind !== "app" && kind !== "hook") {
    return true;
  }

  try {
    const input = parseJson(bytes);
    if (kind === "mcp") {
      const unwrapped =
        typeof input === "object" &&
        input !== null &&
        !Array.isArray(input) &&
        "mcpServers" in input
          ? (input as Record<string, unknown>).mcpServers
          : input;
      return McpFileSchema.safeParse(unwrapped).success;
    }
    if (kind === "app") {
      return AppFileSchema.safeParse(input).success;
    }
    return HookFileSchema.safeParse(input).success;
  } catch {
    return false;
  }
}

function componentMetadata(relativePath: string, digest: string): PluginMetadata {
  return Object.freeze({ path: relativePath, digest });
}

export async function discoverPluginComponentsFromIdentity(
  root: DirectoryIdentity,
  manifest: PluginManifest,
): Promise<ComponentDiscoveryResult> {
  const entries: FileEntry[] = [];
  const seenCandidates = new Set<string>();

  for (const candidate of candidatesFor(manifest)) {
    const canonical = await containedCanonicalPath(
      root,
      candidate.pointer,
      candidate.declared,
    );
    if (canonical === undefined) {
      continue;
    }
    const candidateKey = `${candidate.kind}\0${canonical}`;
    if (seenCandidates.has(candidateKey)) {
      continue;
    }
    seenCandidates.add(candidateKey);
    await collectFiles(root, candidate.kind, canonical, entries);
  }

  const seenFiles = new Set<string>();
  const components: NormalizedPluginComponent[] = [];
  let hasInvalidComponent = false;
  for (const entry of entries) {
    const key = `${entry.kind}\0${entry.canonicalPath}`;
    if (seenFiles.has(key)) {
      continue;
    }
    seenFiles.add(key);

    const relativePath = slashPath(relative(root.canonicalPath, entry.canonicalPath));
    const structured =
      entry.kind === "mcp" || entry.kind === "app" || entry.kind === "hook";
    const { bytes, digest } = await readComponentFile(
      root,
      entry.canonicalPath,
      structured,
    );
    const valid = bytes === undefined || isStructuredComponentValid(entry.kind, bytes);
    hasInvalidComponent ||= !valid;
    components.push(
      Object.freeze({
        id: `${entry.kind}:${relativePath}`,
        name: basename(entry.canonicalPath),
        kind: entry.kind,
        status: valid ? "available" : "invalid",
        metadata: componentMetadata(relativePath, digest),
      }),
    );
  }

  await assertDirectoryIdentity(root);
  return Object.freeze({
    components: Object.freeze(components),
    hasInvalidComponent,
  });
}

export async function discoverPluginComponents(
  pluginRoot: string,
  manifest: PluginManifest,
): Promise<ComponentDiscoveryResult> {
  const root = await snapshotDirectoryIdentity(pluginRoot);
  return discoverPluginComponentsFromIdentity(root, manifest);
}
