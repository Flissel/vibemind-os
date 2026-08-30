import { captureWriteReleaseRequest, type IPluginWriteReleasePolicy, type PluginWriteReleaseDecision, type PluginWriteReleaseRequest } from "@/src/application/policies/plugin-write-release.policy";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

export interface OpenFangWriteReleaseOptions {
  readonly baseUrl: string;
  readonly fetch: typeof fetch;
  readonly timeoutMs: number;
  readonly pollIntervalMs: number;
  readonly now?: () => number;
}

/**
 * Asks OpenFang to release one write.
 *
 * OpenFang models an approval as a request that a human resolves or that times
 * out, and it exposes no per-id read, so the decision is polled from the list.
 * Only identifiers and digests travel: the arguments themselves never leave
 * this process.
 */
export class OpenFangWriteReleasePolicy implements IPluginWriteReleasePolicy {
  readonly #options: OpenFangWriteReleaseOptions;

  constructor(options: OpenFangWriteReleaseOptions) {
    this.#options = options;
  }

  async release(input: PluginWriteReleaseRequest, signal: AbortSignal): Promise<PluginWriteReleaseDecision> {
    const request = captureWriteReleaseRequest(input);
    const now = this.#options.now ?? (() => Date.now());
    const deadline = now() + this.#options.timeoutMs;
    let approvalId: string;
    try {
      const created = await this.#json(`${this.#options.baseUrl}/api/approvals`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          agent_id: request.projectId,
          tool_name: request.toolName,
          description: `Rowboat plugin write: ${request.pluginName}`,
          action_summary: `component ${request.componentDigest} arguments ${request.argumentsDigest}`,
        }),
      }, deadline - now(), signal);
      const id = (created as { id?: unknown }).id;
      if (typeof id !== "string" || !UUID.test(id)) return Object.freeze({ status: "unavailable" as const });
      approvalId = id;
    } catch {
      return Object.freeze({ status: "unavailable" as const });
    }

    while (now() < deadline) {
      try {
        const listed = await this.#json(`${this.#options.baseUrl}/api/approvals`, { method: "GET" }, deadline - now(), signal);
        const approvals = (listed as { approvals?: unknown }).approvals;
        const record = Array.isArray(approvals)
          ? approvals.find(candidate => candidate !== null && typeof candidate === "object" && (candidate as { id?: unknown }).id === approvalId)
          : undefined;
        const status = record === undefined ? undefined : (record as { status?: unknown }).status;
        if (status === "approved") return Object.freeze({ status: "approved" as const, approvalId });
        if (status === "rejected") return Object.freeze({ status: "denied" as const, approvalId });
        if (status === "expired") return Object.freeze({ status: "expired" as const, approvalId });
      } catch {
        return Object.freeze({ status: "unavailable" as const });
      }
      await new Promise(resolve => setTimeout(resolve, this.#options.pollIntervalMs));
    }
    // No decision inside our own window is the same as no release.
    return Object.freeze({ status: "expired" as const, approvalId });
  }

  async #json(url: string, init: RequestInit, remainingMs: number, signal: AbortSignal): Promise<unknown> {
    if (remainingMs <= 0) throw new Error("openfang_deadline_exceeded");
    const response = await this.#fetchBounded(url, init, remainingMs, signal);
    if (!response.ok) throw new Error("openfang_unavailable");
    return response.json();
  }

  /**
   * Bounds one fetch by the time remaining until our own deadline, in
   * addition to the caller's signal. Neither the initial POST nor a GET poll
   * may hang past that deadline: an OpenFang that accepts the connection and
   * never answers must not stall release() forever.
   */
  async #fetchBounded(url: string, init: RequestInit, remainingMs: number, signal: AbortSignal): Promise<Response> {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), remainingMs);
    const onAbort = () => controller.abort();
    if (signal.aborted) controller.abort();
    else signal.addEventListener("abort", onAbort);
    try {
      return await this.#options.fetch(url, { ...init, signal: controller.signal });
    } finally {
      clearTimeout(timer);
      signal.removeEventListener("abort", onAbort);
    }
  }
}
