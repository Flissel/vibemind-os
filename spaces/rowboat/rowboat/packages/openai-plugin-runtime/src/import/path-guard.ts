import { realpath } from "node:fs/promises";
import { isAbsolute, relative, resolve, sep } from "node:path";

export type PluginSourceSecurityCode =
  | "path_escape"
  | "source_mismatch"
  | "digest_mismatch";

export class PluginSourceSecurityError extends Error {
  readonly code: PluginSourceSecurityCode;

  constructor(code: PluginSourceSecurityCode, reason: string) {
    super(`${code}: ${reason}`);
    this.name = "PluginSourceSecurityError";
    this.code = code;
  }
}

export function isContainedPath(root: string, candidate: string): boolean {
  const relativePath = relative(root, candidate);

  return !(
    relativePath === ".." ||
    relativePath.startsWith(`..${sep}`) ||
    isAbsolute(relativePath)
  );
}

export async function resolveContainedPath(
  root: string,
  pointer: string,
): Promise<string> {
  if (isAbsolute(pointer)) {
    throw new PluginSourceSecurityError("path_escape", "absolute pointer rejected");
  }

  const canonicalRoot = await realpath(root);
  const resolvedCandidate = resolve(canonicalRoot, pointer);

  if (!isContainedPath(canonicalRoot, resolvedCandidate)) {
    throw new PluginSourceSecurityError("path_escape", "pointer leaves source root");
  }

  const canonicalCandidate = await realpath(resolvedCandidate);

  if (!isContainedPath(canonicalRoot, canonicalCandidate)) {
    throw new PluginSourceSecurityError("path_escape", "pointer leaves source root");
  }

  return canonicalCandidate;
}
