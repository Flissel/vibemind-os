const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const IDENTIFIER = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/;
const DIGEST = /^[a-f0-9]{64}$/;

export interface PluginWriteReleaseRequest {
  readonly projectId: string;
  readonly pluginName: string;
  readonly toolName: string;
  readonly componentDigest: string;
  /** Digest of the captured arguments; the values themselves never travel. */
  readonly argumentsDigest: string;
}

export type PluginWriteReleaseDecision =
  | Readonly<{ status: "approved"; approvalId: string }>
  | Readonly<{ status: "denied"; approvalId: string }>
  | Readonly<{ status: "expired"; approvalId: string }>
  | Readonly<{ status: "unavailable" }>;

export interface IPluginWriteReleasePolicy {
  release(request: PluginWriteReleaseRequest, signal: AbortSignal): Promise<PluginWriteReleaseDecision>;
}

export function captureWriteReleaseRequest(input: unknown): PluginWriteReleaseRequest {
  if (input === null || typeof input !== "object") throw new Error("write_release_request_invalid");
  const record = input as Record<string, unknown>;
  const { projectId, pluginName, toolName, componentDigest, argumentsDigest } = record;
  if (typeof projectId !== "string" || !UUID.test(projectId)) throw new Error("write_release_request_invalid");
  if (typeof pluginName !== "string" || !IDENTIFIER.test(pluginName)) throw new Error("write_release_request_invalid");
  if (typeof toolName !== "string" || !IDENTIFIER.test(toolName)) throw new Error("write_release_request_invalid");
  if (typeof componentDigest !== "string" || !DIGEST.test(componentDigest)) throw new Error("write_release_request_invalid");
  if (typeof argumentsDigest !== "string" || !DIGEST.test(argumentsDigest)) throw new Error("write_release_request_invalid");
  return Object.freeze({ projectId, pluginName, toolName, componentDigest, argumentsDigest });
}

/**
 * The composition default. A write is never released by absence of a decision.
 */
export class DeniedWriteReleasePolicy implements IPluginWriteReleasePolicy {
  async release(_request: PluginWriteReleaseRequest, _signal: AbortSignal): Promise<PluginWriteReleaseDecision> {
    return Object.freeze({ status: "unavailable" as const });
  }
}
