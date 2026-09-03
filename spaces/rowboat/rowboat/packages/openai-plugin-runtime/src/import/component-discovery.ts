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
import {
  digestTree,
  type FileModeResolver,
  type GitFileMode,
} from "./digest-service.js";
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

export interface ComponentDiscoveryOptions {
  readonly fileModeResolver?: FileModeResolver;
}

function slashPath(value: string): string {
  return value.split(sep).join("/");
}

function compareCodePoints(left: string, right: string): number {
  const leftIterator = left[Symbol.iterator]();
  const rightIterator = right[Symbol.iterator]();
  while (true) {
    const leftCharacter = leftIterator.next();
    const rightCharacter = rightIterator.next();
    if (leftCharacter.done || rightCharacter.done) {
      if (leftCharacter.done && rightCharacter.done) return 0;
      return leftCharacter.done ? -1 : 1;
    }
    const leftPoint = leftCharacter.value.codePointAt(0);
    const rightPoint = rightCharacter.value.codePointAt(0);
    if (leftPoint === undefined || rightPoint === undefined) {
      throw new PluginSourceSecurityError("digest_mismatch", "invalid component name");
    }
    if (leftPoint !== rightPoint) return leftPoint < rightPoint ? -1 : 1;
  }
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
  for (const name of (await readdir(directory)).sort(compareCodePoints)) {
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
  fileModeResolver: FileModeResolver | undefined,
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
    const skillsRootDigest = await digestTree(path, {
      fileModeResolver: scopedModeResolver(root.canonicalPath, path, fileModeResolver),
    });
    await collectSkillBundles(root, path, skillsRootDigest, output);
    return;
  }
  const children = await sortedChildren(root, path);
  if (kind === "agent" || kind === "command") {
    const scopeDigest = await digestTree(path, {
      fileModeResolver: scopedModeResolver(root.canonicalPath, path, fileModeResolver),
    });
    await collectRunnableMarkdown(root, kind, path, scopeDigest, output);
    return;
  }
  for (const child of children) await collectEntries(root, kind, child, output, fileModeResolver);
}

function scopedModeResolver(
  pluginRoot: string,
  scopeRoot: string,
  fileModeResolver: FileModeResolver | undefined,
): FileModeResolver | undefined {
  if (fileModeResolver === undefined) return undefined;
  const prefix = slashPath(relative(pluginRoot, scopeRoot));
  return (path) => fileModeResolver(prefix === "" ? path : `${prefix}/${path}`);
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
  surfaceRoot: string = directory,
): Promise<void> {
  for (const child of await sortedChildren(root, directory)) {
    const stats = await lstat(child);
    if (stats.isDirectory()) {
      await collectRunnableMarkdown(root, kind, child, scopeDigest, output, surfaceRoot);
    } else if (
      (child.endsWith(".md") && basename(child) !== "_conventions.md") ||
      (kind === "agent" && dirname(child) === surfaceRoot && basename(child) === "openai.yaml")
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
    for (const key of Object.keys(value).sort(compareCodePoints)) {
      const nested = (value as Record<string, unknown>)[key];
      if (nested !== undefined) result[key] = canonicalValue(nested);
    }
    return result;
  }
  throw new PluginSourceSecurityError("digest_mismatch", "unsupported digest value");
}

function recordDigest(value: unknown, containerDigest: string): string {
  return createHash("sha256")
    .update("rowboat-plugin-component-v2\0")
    .update(containerDigest)
    .update("\0")
    .update(JSON.stringify(canonicalValue(value)))
    .digest("hex");
}

function modeBoundFileDigest(contentDigest: string, mode: GitFileMode): string {
  return createHash("sha256")
    .update("rowboat-plugin-file-v2\0")
    .update(mode)
    .update("\0")
    .update(contentDigest)
    .digest("hex");
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
        metadata(
          relativePath,
          recordDigest(declaration.success ? declaration.data : raw, fileDigest),
          // The validated app declaration travels with the catalog so the
          // connector-bridge provider can be constructed from the pinned
          // record alone, without reading the content store at call time. It
          // carries an opaque connector id, never a credential value.
          declaration.success ? { appDeclaration: definedFields(declaration.data) } : {},
        ),
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
          recordDigest(declaration.success ? declaration.data : raw, fileDigest),
          // The validated server declaration travels with the catalog so a
          // provider can be constructed from the pinned record alone, without
          // reading the content store at call time. It carries credential
          // *references*, never credential values.
          declaration.success
            ? {
              transport: declaration.data.type,
              mcpServer: definedFields(declaration.data),
              credentialSlots: credentialSlotNames(declaration.data),
            }
            : {},
        ),
      );
    });
  }
  const valid = HookFileSchema.safeParse(input).success;
  return [component("hook", `hook:${relativePath}`, basename(entry.canonicalPath), valid ? "available" : "invalid", metadata(relativePath, fileDigest))];
}

/**
 * The credential references an MCP server declares. These are *names* the
 * operator binds a value to; the declaration never carries a value.
 */
function credentialSlotNames(declaration: Readonly<Record<string, unknown>>): readonly string[] {
  const names: string[] = [];
  const bearer = declaration.bearer_token_env_var;
  if (typeof bearer === "string" && bearer.length > 0) names.push(bearer);
  // `env_vars` names the credential reference for a process server; `env`
  // carries literal values for that same declaration and is never a source
  // of slot names. A declaration that sets `env` is rejected before it ever
  // reaches a provider (see mcp-normalizer.ts's
  // component_invalid:process_environment_values check), so this only ever
  // reads names, never plugin-supplied values.
  const environmentVariables = declaration.env_vars;
  if (Array.isArray(environmentVariables)) {
    for (const name of environmentVariables) {
      if (typeof name === "string" && name.length > 0) names.push(name);
    }
  }
  return Object.freeze([...new Set(names)].sort());
}

/**
 * Metadata carries only defined values: an optional field that is absent stays
 * absent rather than becoming an undefined entry the metadata contract rejects.
 * A scalar passes through as-is; an array of scalars (such as a capability
 * list) passes through as a frozen copy. Nested objects are intentionally
 * excluded — a declaration field shaped as a record (e.g. a process server's
 * literal `env` map) may carry compound values this metadata is not meant to
 * surface, so it stays out rather than being guessed at.
 */
function definedFields(value: Readonly<Record<string, unknown>>): Readonly<Record<string, PluginMetadataValue>> {
  const output: Record<string, PluginMetadataValue> = {};
  for (const key of Object.keys(value).sort()) {
    const entry = value[key];
    if (typeof entry === "string" || typeof entry === "number" || typeof entry === "boolean") {
      output[key] = entry;
    } else if (
      Array.isArray(entry) &&
      entry.every((item) => typeof item === "string" || typeof item === "number" || typeof item === "boolean")
    ) {
      output[key] = Object.freeze([...entry]) as readonly PluginMetadataValue[];
    }
  }
  return Object.freeze(output);
}

export async function discoverPluginComponentsFromIdentity(
  root: DirectoryIdentity,
  manifest: PluginManifest,
  options: ComponentDiscoveryOptions = {},
): Promise<ComponentDiscoveryResult> {
  const entries: ComponentEntry[] = [];
  const seenCandidates = new Set<string>();
  for (const candidate of candidatesFor(manifest)) {
    const canonical = await containedCanonicalPath(root, candidate.pointer, candidate.declared);
    if (canonical === undefined) continue;
    const key = `${candidate.kind}\0${canonical}`;
    if (seenCandidates.has(key)) continue;
    seenCandidates.add(key);
    await collectEntries(root, candidate.kind, canonical, entries, options.fileModeResolver);
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
      const bundleDigest = await digestTree(entry.canonicalPath, {
        fileModeResolver: scopedModeResolver(
          root.canonicalPath,
          entry.canonicalPath,
          options.fileModeResolver,
        ),
      });
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
    const rawRead = await readComponentFile(root, entry.canonicalPath, structured);
    const stats = await lstat(entry.canonicalPath);
    const mode = options.fileModeResolver?.(relativePath) ??
      (process.platform !== "win32" && (stats.mode & 0o111) !== 0 ? "100755" : "100644");
    const read = { ...rawRead, digest: modeBoundFileDigest(rawRead.digest, mode) };
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

export async function discoverPluginComponents(
  pluginRoot: string,
  manifest: PluginManifest,
  options: ComponentDiscoveryOptions = {},
): Promise<ComponentDiscoveryResult> {
  return discoverPluginComponentsFromIdentity(
    await snapshotDirectoryIdentity(pluginRoot),
    manifest,
    options,
  );
}
