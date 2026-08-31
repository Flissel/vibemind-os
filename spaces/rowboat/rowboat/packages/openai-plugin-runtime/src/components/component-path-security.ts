import { lstat, realpath } from "node:fs/promises";
import { isAbsolute, join, relative, resolve, sep } from "node:path";
import {
  isContainedPath,
  PluginSourceSecurityError,
  resolveContainedPath,
} from "../import/path-guard.js";

export interface ResolvedComponentRoot {
  readonly canonicalPluginRoot: string;
  readonly canonicalComponentRoot: string;
}

export interface ResolvedComponentPath extends ResolvedComponentRoot {
  readonly canonicalPath: string;
}

function missing(error: unknown): boolean {
  return error instanceof Error && "code" in error && error.code === "ENOENT";
}

function reject(reason: string): never {
  throw new PluginSourceSecurityError("path_escape", reason);
}

function lexicalRelative(root: string, candidate: string): string {
  const absoluteRoot = resolve(root);
  const absoluteCandidate = resolve(candidate);
  if (!isContainedPath(absoluteRoot, absoluteCandidate)) reject("component leaves its namespace");
  return relative(absoluteRoot, absoluteCandidate);
}

async function inspectSegments(root: string, candidate: string): Promise<boolean> {
  const absoluteRoot = resolve(root);
  const relativePath = lexicalRelative(absoluteRoot, candidate);
  let rootStats;
  try {
    rootStats = await lstat(absoluteRoot);
  } catch (error: unknown) {
    if (missing(error)) return false;
    throw error;
  }
  if (rootStats.isSymbolicLink() || !rootStats.isDirectory()) {
    reject("component namespace root is not a stable directory");
  }
  const canonicalRoot = await realpath(absoluteRoot);
  let current = absoluteRoot;
  const segments = relativePath === "" ? [] : relativePath.split(sep);
  for (let index = 0; index < segments.length; index += 1) {
    const segment = segments[index];
    if (segment === undefined || segment === "" || segment === "." || segment === "..") {
      reject("component path contains an unsafe segment");
    }
    current = join(current, segment);
    let stats;
    try {
      stats = await lstat(current);
    } catch (error: unknown) {
      if (missing(error)) return false;
      throw error;
    }
    if (stats.isSymbolicLink()) reject("component path contains a link or reparse point");
    if (index < segments.length - 1 && !stats.isDirectory()) {
      reject("component path ancestor is not a directory");
    }
    const canonicalCurrent = await realpath(current);
    if (!isContainedPath(canonicalRoot, canonicalCurrent)) {
      reject("component path escapes its canonical namespace");
    }
  }
  return true;
}

export function assertSafeDescriptorPath(value: string): string {
  if (value === "" || isAbsolute(value)) reject("descriptor path is empty or absolute");
  const normalized = value.replaceAll("\\", "/");
  const segments = normalized.split("/");
  if (segments.some((segment) => segment === "" || segment === "." || segment === "..")) {
    reject("descriptor path contains an unsafe segment");
  }
  return normalized;
}

export async function resolveComponentRoot(
  pluginRoot: string,
  componentRoot: string,
): Promise<ResolvedComponentRoot> {
  if (!(await inspectSegments(pluginRoot, componentRoot))) {
    reject("component namespace root is missing");
  }
  const canonicalPluginRoot = await realpath(pluginRoot);
  const canonicalComponentRoot = await resolveContainedPath(
    canonicalPluginRoot,
    relative(pluginRoot, componentRoot),
  );
  if (!isContainedPath(canonicalPluginRoot, canonicalComponentRoot)) {
    reject("component namespace leaves plugin root");
  }
  const stats = await lstat(canonicalComponentRoot);
  if (!stats.isDirectory() || stats.isSymbolicLink()) {
    reject("component namespace root is not a directory");
  }
  return Object.freeze({ canonicalPluginRoot, canonicalComponentRoot });
}

export async function inspectPotentialComponentPath(
  pluginRoot: string,
  componentRoot: string,
  candidate: string,
): Promise<boolean> {
  const roots = await resolveComponentRoot(pluginRoot, componentRoot);
  lexicalRelative(pluginRoot, candidate);
  lexicalRelative(componentRoot, candidate);
  const pluginExists = await inspectSegments(pluginRoot, candidate);
  const componentExists = await inspectSegments(componentRoot, candidate);
  if (pluginExists !== componentExists) reject("component namespace observations differ");
  if (!pluginExists) return false;
  const canonicalPath = await realpath(candidate);
  if (
    !isContainedPath(roots.canonicalPluginRoot, canonicalPath) ||
    !isContainedPath(roots.canonicalComponentRoot, canonicalPath)
  ) {
    reject("component path leaves a canonical namespace");
  }
  return true;
}

export async function resolveExistingComponentPath(
  pluginRoot: string,
  componentRoot: string,
  candidate: string,
): Promise<ResolvedComponentPath> {
  if (!(await inspectPotentialComponentPath(pluginRoot, componentRoot, candidate))) {
    reject("component path is missing");
  }
  const roots = await resolveComponentRoot(pluginRoot, componentRoot);
  const canonicalPath = await resolveContainedPath(
    roots.canonicalComponentRoot,
    relative(componentRoot, candidate),
  );
  await resolveContainedPath(
    roots.canonicalPluginRoot,
    relative(pluginRoot, candidate),
  );
  if (
    !isContainedPath(roots.canonicalComponentRoot, canonicalPath) ||
    !isContainedPath(roots.canonicalPluginRoot, canonicalPath)
  ) {
    reject("component path leaves a canonical namespace");
  }
  return Object.freeze({ ...roots, canonicalPath });
}
