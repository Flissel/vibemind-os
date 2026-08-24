import { createHash } from "node:crypto";
import { lstat, readdir, realpath } from "node:fs/promises";
import { basename, dirname, join, relative, sep } from "node:path";
import type {
  NormalizedPluginComponent,
  PluginComponentKind,
  PluginMetadata,
  PluginMetadataValue,
} from "../domain/plugin.js";
import {
  AppDeclarationSchema,
  AppFileEnvelopeSchema,
  HookFileSchema,
  McpFileEnvelopeSchema,
  McpServerSchema,
} from "../schema/component-schemas.js";
import type { PluginManifest } from "../schema/plugin-manifest.js";
import {
  assertDirectoryIdentity,
  snapshotDirectoryIdentity,
  type DirectoryIdentity,
} from "./directory-identity.js";
import { digestTree } from "./digest-service.js";
import { isContainedPath, PluginSourceSecurityError } from "./path-guard.js";
import { streamContainedRegularFile } from "./safe-file-stream.js";

const MAX_STRUCTURED_COMPONENT_BYTES = 1024 * 1024;

interface Candidate {
  readonly kind: PluginComponentKind;
  readonly pointer: string;
  readonly declared: boolean;
}

interface ComponentEntry {
  readonly kind: PluginComponentKind;
  readonly canonicalPath: string;
  readonly scopeDigest?: string;
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

function compareCodePoints(left: string, right: string): number {
  return left < right ? -1 : left > right ? 1 : 0;
}

function candidatesFor(manifest: PluginManifest): readonly Candidate[] {
  const candidates: Candidate[] = [];
  const add = (kind: PluginComponentKind, declared: string | undefined, conventional: string): void => {
    if (declared !== undefined) candidates.push({ kind, pointer: declared, declared: true });
    candidates.push({ kind, pointer: conventional, declared: false });
  };
  add("skill", manifest.skills, "skills");
  add("agent", manifest.agents, "agents");
  add("command", manifest.commands, "commands");
  add("mcp", manifest.mcpServers, ".mcp.json");
  add("app", manifest.apps, ".app.json");
  add("hook", manifest.hooks, "hooks.json");
  for (const pointer of [
    manifest.interface.composerIcon,
    manifest.interface.logo,
    manifest.interface.logoDark,
    ...(manifest.interface.screenshots ?? []),
  ]) {
    if (pointer !== undefined) candidates.push({ kind: "asset", pointer, declared: true });
  }
  return candidates;
}

async function containedCanonicalPath(
  root: DirectoryIdentity,
  pointer: string,
  declared: boolean,
): Promise<string | undefined> {
  const candidate = join(root.canonicalPath, pointer);
  let stats;
  try {
    stats = await lstat(candidate);
  } catch {
    if (!declared) return undefined;
    throw new PluginSourceSecurityError("path_escape", "declared component missing");
  }
  if (stats.isSymbolicLink()) throw new PluginSourceSecurityError("path_escape", "component link rejected");
  const canonical = await realpath(candidate);
  if (!isContainedPath(root.canonicalPath, canonical)) {
    throw new PluginSourceSecurityError("path_escape", "component leaves source root");
  }
  await assertDirectoryIdentity(root);
  return canonical;
}

async function sortedChildren(root: DirectoryIdentity, directory: string): Promise<readonly string[]> {
  const children: string[] = [];
  for (const name of (await readdir(directory)).sort()) {
    const child = join(directory, name);
    const stats = await lstat(child);
    if (stats.isSymbolicLink()) throw new PluginSourceSecurityError("path_escape", "component link rejected");
    const canonical = await realpath(child);
    if (!isContainedPath(root.canonicalPath, canonical)) {
      throw new PluginSourceSecurityError("path_escape", "component leaves source root");
    }
    children.push(canonical);
  }
  return children;
}

async function collectEntries(
  root: DirectoryIdentity,
  kind: PluginComponentKind,
  path: string,
  output: ComponentEntry[],
): Promise<void> {
  await assertDirectoryIdentity(root);
  const stats = await lstat(path);
  if (stats.isFile()) {
    if (
      (kind !== "agent" && kind !== "command") ||
      (path.endsWith(".md") && basename(path) !== "_conventions.md")
    ) {
      output.push({ kind, canonicalPath: path });
    }
    return;
  }
  if (!stats.isDirectory()) throw new PluginSourceSecurityError("path_escape", "unsupported component entry");
  if (kind === "skill") {
    const skillsRootDigest = await digestTree(path);
    await collectSkillBundles(root, path, skillsRootDigest, output);
    return;
  }
  const children = await sortedChildren(root, path);
  if (kind === "agent" || kind === "command") {
    const scopeDigest = await digestTree(path);
    await collectRunnableMarkdown(root, kind, path, scopeDigest, output);
    return;
  }
  for (const child of children) await collectEntries(root, kind, child, output);
}

async function collectSkillBundles(
  root: DirectoryIdentity,
  directory: string,
  skillsRootDigest: string,
  output: ComponentEntry[],
): Promise<void> {
  for (const child of await sortedChildren(root, directory)) {
    const stats = await lstat(child);
    if (stats.isDirectory()) {
      await collectSkillBundles(root, child, skillsRootDigest, output);
    } else if (basename(child) === "SKILL.md") {
      output.push({ kind: "skill", canonicalPath: dirname(child), scopeDigest: skillsRootDigest });
    }
  }
}

async function collectRunnableMarkdown(
  root: DirectoryIdentity,
  kind: "agent" | "command",
  directory: string,
  scopeDigest: string,
  output: ComponentEntry[],
): Promise<void> {
  for (const child of await sortedChildren(root, directory)) {
    const stats = await lstat(child);
    if (stats.isDirectory()) {
      await collectRunnableMarkdown(root, kind, child, scopeDigest, output);
    } else if (
      (child.endsWith(".md") && basename(child) !== "_conventions.md") ||
      (kind === "agent" && child.endsWith(".yaml"))
    ) {
      output.push({ kind, canonicalPath: child, scopeDigest });
    }
  }
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
      if (retainBytes) chunks.push(Buffer.from(chunk));
    },
  });
  const digest = hash.digest("hex");
  return retainBytes ? { bytes: Buffer.concat(chunks, Number(observedSize)), digest } : { digest };
}

function canonicalValue(value: unknown): PluginMetadataValue {
  if (value === null || typeof value === "string" || typeof value === "boolean" || typeof value === "number") return value;
  if (Array.isArray(value)) return value.map(canonicalValue);
  if (typeof value === "object") {
    const result: Record<string, PluginMetadataValue> = {};
    for (const key of Object.keys(value).sort()) {
      const nested = (value as Record<string, unknown>)[key];
      if (nested !== undefined) result[key] = canonicalValue(nested);
    }
    return result;
  }
  throw new PluginSourceSecurityError("digest_mismatch", "unsupported digest value");
}

function recordDigest(value: unknown): string {
  return createHash("sha256").update("rowboat-plugin-component-v1\0").update(JSON.stringify(canonicalValue(value))).digest("hex");
}

function scopedDigest(
  kind: "skill" | "agent" | "command",
  logicalPath: string,
  contentDigest: string,
  scopeDigest: string,
): string {
  return createHash("sha256")
    .update(`rowboat-${kind}-bundle-v1\0`)
    .update(logicalPath)
    .update("\0")
    .update(contentDigest)
    .update("\0")
    .update(scopeDigest)
    .digest("hex");
}

function metadata(path: string, digest: string, extra: PluginMetadata = {}): PluginMetadata {
  return Object.freeze({ path, digest, ...extra });
}

function component(
  kind: PluginComponentKind,
  id: string,
  name: string,
  status: "available" | "invalid",
  value: PluginMetadata,
): NormalizedPluginComponent {
  return Object.freeze({ id, name, kind, status, metadata: value });
}

function invalidStructured(entry: ComponentEntry, relativePath: string, digest: string): NormalizedPluginComponent {
  return component(entry.kind, `${entry.kind}:${relativePath}`, basename(entry.canonicalPath), "invalid", metadata(relativePath, digest));
}

function expandStructured(
  entry: ComponentEntry,
  relativePath: string,
  bytes: Buffer,
  fileDigest: string,
): readonly NormalizedPluginComponent[] {
  let input: unknown;
  try {
    input = JSON.parse(bytes.toString("utf8")) as unknown;
  } catch {
    return [invalidStructured(entry, relativePath, fileDigest)];
  }
  if (entry.kind === "app") {
    const envelope = AppFileEnvelopeSchema.safeParse(input);
    if (!envelope.success) return [invalidStructured(entry, relativePath, fileDigest)];
    return Object.entries(envelope.data.apps).sort(([a], [b]) => compareCodePoints(a, b)).map(([name, raw]) => {
      const declaration = AppDeclarationSchema.safeParse(raw);
      return component(
        "app",
        `app:${relativePath}#${name}`,
        name,
        declaration.success ? "available" : "invalid",
        metadata(relativePath, recordDigest(declaration.success ? declaration.data : raw)),
      );
    });
  }
  if (entry.kind === "mcp") {
    const envelope = McpFileEnvelopeSchema.safeParse(input);
    if (!envelope.success) return [invalidStructured(entry, relativePath, fileDigest)];
    return Object.entries(envelope.data.mcpServers).sort(([a], [b]) => compareCodePoints(a, b)).map(([name, raw]) => {
      const declaration = McpServerSchema.safeParse(raw);
      return component(
        "mcp",
        `mcp:${relativePath}#${name}`,
        name,
        declaration.success ? "available" : "invalid",
        metadata(
          relativePath,
          recordDigest(declaration.success ? declaration.data : raw),
          declaration.success ? { transport: declaration.data.type } : {},
        ),
      );
    });
  }
  const valid = HookFileSchema.safeParse(input).success;
  return [component("hook", `hook:${relativePath}`, basename(entry.canonicalPath), valid ? "available" : "invalid", metadata(relativePath, fileDigest))];
}

export async function discoverPluginComponentsFromIdentity(
  root: DirectoryIdentity,
  manifest: PluginManifest,
): Promise<ComponentDiscoveryResult> {
  const entries: ComponentEntry[] = [];
  const seenCandidates = new Set<string>();
  for (const candidate of candidatesFor(manifest)) {
    const canonical = await containedCanonicalPath(root, candidate.pointer, candidate.declared);
    if (canonical === undefined) continue;
    const key = `${candidate.kind}\0${canonical}`;
    if (seenCandidates.has(key)) continue;
    seenCandidates.add(key);
    await collectEntries(root, candidate.kind, canonical, entries);
  }

  const seenEntries = new Set<string>();
  const components: NormalizedPluginComponent[] = [];
  for (const entry of entries) {
    const key = `${entry.kind}\0${entry.canonicalPath}`;
    if (seenEntries.has(key)) continue;
    seenEntries.add(key);
    const relativePath = slashPath(relative(root.canonicalPath, entry.canonicalPath));
    if (entry.kind === "skill") {
      if (entry.scopeDigest === undefined) {
        throw new PluginSourceSecurityError("digest_mismatch", "skill scope digest missing");
      }
      const bundleDigest = await digestTree(entry.canonicalPath);
      // Reference parsing is intentionally deferred. Binding both the local
      // bundle tree and the entire stable skills root is a conservative
      // superset: sibling resource changes invalidate every skill in that root
      // instead of risking a stale digest for an unresolved relative reference.
      components.push(component(
        "skill",
        `skill:${relativePath}`,
        basename(entry.canonicalPath),
        "available",
        metadata(relativePath, scopedDigest("skill", relativePath, bundleDigest, entry.scopeDigest), {
          resourceBinding: "skills_root_superset",
        }),
      ));
      continue;
    }
    const structured = entry.kind === "mcp" || entry.kind === "app" || entry.kind === "hook";
    const read = await readComponentFile(root, entry.canonicalPath, structured);
    if (structured && read.bytes !== undefined) components.push(...expandStructured(entry, relativePath, read.bytes, read.digest));
    else {
      const digest =
        (entry.kind === "agent" || entry.kind === "command") && entry.scopeDigest !== undefined
          ? scopedDigest(entry.kind, relativePath, read.digest, entry.scopeDigest)
          : read.digest;
      components.push(component(
        entry.kind,
        `${entry.kind}:${relativePath}`,
        basename(entry.canonicalPath),
        "available",
        metadata(
          relativePath,
          digest,
          entry.kind === "agent" || entry.kind === "command"
            ? {
                resourceBinding: "opaque_directory_superset",
                surface:
                  entry.kind === "agent" && entry.canonicalPath.endsWith(".yaml")
                    ? "composer_metadata"
                    : entry.kind === "agent"
                      ? "agent_template"
                      : "command_template",
                role:
                  entry.kind === "agent" && entry.canonicalPath.endsWith(".yaml")
                    ? "non_runnable"
                    : "runnable",
              }
            : {},
        ),
      ));
    }
  }
  if (new Set(components.map(({ id }) => id)).size !== components.length) {
    throw new PluginSourceSecurityError("digest_mismatch", "duplicate component id");
  }
  await assertDirectoryIdentity(root);
  return Object.freeze({
    components: Object.freeze(components),
    hasInvalidComponent: components.some(({ status }) => status === "invalid"),
  });
}

export async function discoverPluginComponents(pluginRoot: string, manifest: PluginManifest): Promise<ComponentDiscoveryResult> {
  return discoverPluginComponentsFromIdentity(await snapshotDirectoryIdentity(pluginRoot), manifest);
}
