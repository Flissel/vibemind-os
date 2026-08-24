import { PluginSourceSecurityError } from "./path-guard.js";

export type GitProbeCommand =
  | readonly ["rev-parse", "HEAD"]
  | readonly ["status", "--porcelain"];

export interface GitProbe {
  run(repositoryRoot: string, command: GitProbeCommand): Promise<string>;
}

export interface PinnedSourceRequest {
  readonly repositoryRoot: string;
  readonly expectedCommit: string;
  readonly probe: GitProbe;
}

const COMMIT_PATTERN = /^[0-9a-f]{40}$/;
const HEAD_COMMAND = ["rev-parse", "HEAD"] as const;
const STATUS_COMMAND = ["status", "--porcelain"] as const;

async function runProbe(
  probe: GitProbe,
  repositoryRoot: string,
  command: GitProbeCommand,
): Promise<string> {
  try {
    return await probe.run(repositoryRoot, command);
  } catch {
    throw new PluginSourceSecurityError("source_mismatch", "Git probe failed");
  }
}

export async function assertPinnedSource({
  repositoryRoot,
  expectedCommit,
  probe,
}: PinnedSourceRequest): Promise<void> {
  if (!COMMIT_PATTERN.test(expectedCommit)) {
    throw new PluginSourceSecurityError("source_mismatch", "invalid expected commit");
  }

  const observedHead = (
    await runProbe(probe, repositoryRoot, HEAD_COMMAND)
  ).trim();

  if (!COMMIT_PATTERN.test(observedHead) || observedHead !== expectedCommit) {
    throw new PluginSourceSecurityError("source_mismatch", "HEAD does not match pin");
  }

  const status = await runProbe(probe, repositoryRoot, STATUS_COMMAND);
  if (status.trim() !== "") {
    throw new PluginSourceSecurityError("source_mismatch", "source tree is dirty");
  }
}
