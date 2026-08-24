import { realpath } from "node:fs/promises";
import { isContainedPath, PluginSourceSecurityError } from "./path-guard.js";

export type GitProbeCommand =
  | readonly ["rev-parse", "HEAD"]
  | readonly ["status", "--porcelain"]
  | readonly ["remote", "get-url", "origin"];

export interface GitProbe {
  run(repositoryRoot: string, command: GitProbeCommand): Promise<string>;
}

export interface PinnedSourceRequest {
  readonly repositoryRoot: string;
  readonly expectedCommit: string;
  readonly sourceUrl: string;
  readonly probe: GitProbe;
}

const verifiedContexts = new WeakSet<object>();
const VERIFIED_CONTEXT_TOKEN = Symbol("VerifiedPinnedSource");

export class VerifiedPinnedSource {
  readonly repositoryRoot: string;
  readonly sourceCommit: string;
  readonly sourceUrl: string;

  constructor(
    token: symbol,
    repositoryRoot: string,
    sourceCommit: string,
    sourceUrl: string,
  ) {
    if (token !== VERIFIED_CONTEXT_TOKEN) {
      throw new PluginSourceSecurityError("source_mismatch", "verified source context cannot be constructed");
    }
    this.repositoryRoot = repositoryRoot;
    this.sourceCommit = sourceCommit;
    this.sourceUrl = sourceUrl;
    verifiedContexts.add(this);
    Object.freeze(this);
  }

}

const COMMIT_PATTERN = /^[0-9a-f]{40}$/;
const HEAD_COMMAND = ["rev-parse", "HEAD"] as const;
const STATUS_COMMAND = ["status", "--porcelain"] as const;
const ORIGIN_COMMAND = ["remote", "get-url", "origin"] as const;

async function runProbe(probe: GitProbe, repositoryRoot: string, command: GitProbeCommand): Promise<string> {
  try {
    return await probe.run(repositoryRoot, command);
  } catch {
    throw new PluginSourceSecurityError("source_mismatch", "Git probe failed");
  }
}

function validSourceUrl(sourceUrl: string): boolean {
  try {
    const parsed = new URL(sourceUrl);
    return parsed.protocol === "https:";
  } catch {
    return false;
  }
}

export async function assertPinnedSource({
  repositoryRoot,
  expectedCommit,
  sourceUrl,
  probe,
}: PinnedSourceRequest): Promise<VerifiedPinnedSource> {
  if (!COMMIT_PATTERN.test(expectedCommit) || !validSourceUrl(sourceUrl)) {
    throw new PluginSourceSecurityError("source_mismatch", "invalid pinned source identity");
  }
  const observedHead = (await runProbe(probe, repositoryRoot, HEAD_COMMAND)).trim();
  if (!COMMIT_PATTERN.test(observedHead) || observedHead !== expectedCommit) {
    throw new PluginSourceSecurityError("source_mismatch", "HEAD does not match pin");
  }
  const status = await runProbe(probe, repositoryRoot, STATUS_COMMAND);
  if (status.trim() !== "") {
    throw new PluginSourceSecurityError("source_mismatch", "source tree is dirty");
  }
  const origin = (await runProbe(probe, repositoryRoot, ORIGIN_COMMAND)).trim();
  if (origin !== sourceUrl) {
    throw new PluginSourceSecurityError("source_mismatch", "origin URL does not match pin");
  }
  let canonicalRepositoryRoot: string;
  try {
    canonicalRepositoryRoot = await realpath(repositoryRoot);
  } catch {
    throw new PluginSourceSecurityError("source_mismatch", "repository root is unavailable");
  }
  return new VerifiedPinnedSource(
    VERIFIED_CONTEXT_TOKEN,
    canonicalRepositoryRoot,
    expectedCommit,
    sourceUrl,
  );
}

export async function assertVerifiedPinnedSource(
  context: VerifiedPinnedSource,
  pluginRoot: string,
  sourceUrl: string,
  sourceCommit: string,
): Promise<void> {
  if (
    !verifiedContexts.has(context) ||
    context.sourceUrl !== sourceUrl ||
    context.sourceCommit !== sourceCommit
  ) {
    throw new PluginSourceSecurityError("source_mismatch", "verified source context mismatch");
  }
  let canonicalPluginRoot: string;
  try {
    canonicalPluginRoot = await realpath(pluginRoot);
  } catch {
    throw new PluginSourceSecurityError("source_mismatch", "plugin root is unavailable");
  }
  if (!isContainedPath(context.repositoryRoot, canonicalPluginRoot)) {
    throw new PluginSourceSecurityError("source_mismatch", "plugin root is outside verified source");
  }
}
