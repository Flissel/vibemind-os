import { captureWriteReleaseRequest, type IPluginWriteReleasePolicy, type PluginWriteReleaseDecision, type PluginWriteReleaseRequest } from "@/src/application/policies/plugin-write-release.policy";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

export interface OpenFangWriteReleaseOptions {
  readonly baseUrl: string;
  readonly fetch: typeof fetch;
  readonly timeoutMs: number;
  readonly pollIntervalMs: number;
  /**
   * Bearer token this adapter presents to OpenFang's HTTP API, the same one
   * `OpenFangCredentialResolver` already presents to
   * `/api/credentials/issue`. Optional: absent (or blank) means send no
   * `Authorization` header at all, exactly as this adapter behaved before
   * this field existed -- never an empty `Bearer `.
   *
   * It is not optional in practice on any daemon that can also issue
   * credentials. OpenFang's auth middleware makes `/api/approvals` public
   * for GET only, so an unauthenticated create POST is answered 401; and its
   * `/api/credentials/issue` refuses outright on a fail-open daemon (empty
   * api_key AND auth disabled) -- which is the very condition under which
   * that unauthenticated POST would have succeeded. The two are the same
   * predicate, inverted, so without this field no single daemon
   * configuration could both release a write and issue its credential. That
   * was found by a live end-to-end run, with every unit test green.
   */
  readonly apiKey?: string;
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
    return this.#fetchBounded(url, init, remainingMs, signal);
  }

  /**
   * Bounds the *whole* exchange -- the fetch and the body read -- by the
   * time remaining until our own deadline, in addition to the caller's
   * signal. Neither the initial POST nor a GET poll may hang past that
   * deadline: an OpenFang that accepts the connection and never answers must
   * not stall release() forever.
   *
   * The body is read *inside* this method, before the finally clears the
   * timer: `fetch()` itself resolves as soon as headers arrive, so a server
   * that answers 200 with a Content-Length and then never sends the body
   * would otherwise hang `response.json()` at the call site with no deadline
   * and no abort path at all -- the bug this shape exists to close.
   */
  async #fetchBounded(url: string, init: RequestInit, remainingMs: number, signal: AbortSignal): Promise<unknown> {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), remainingMs);
    const onAbort = () => controller.abort();
    if (signal.aborted) controller.abort();
    else signal.addEventListener("abort", onAbort);
    try {
      const response = await this.#options.fetch(url, { ...this.#authorized(init), signal: controller.signal, redirect: "error" });
      if (!response.ok) throw new Error("openfang_unavailable");
      return await response.json();
    } finally {
      clearTimeout(timer);
      signal.removeEventListener("abort", onAbort);
    }
  }

  /**
   * Adds `Authorization: Bearer <apiKey>` to a request, preserving whatever
   * headers it already carries (the create POST's `content-type`).
   *
   * Applied in `#fetchBounded`, so it covers *every* exchange this adapter
   * makes -- the create POST and each poll GET alike. Only the POST is
   * refused by OpenFang's current middleware, but a daemon may protect the
   * GET too (a future allowlist change, or dashboard auth), and a release
   * that can be created but never read back is no release at all.
   *
   * With no key, or a blank one, the init is returned untouched: no header
   * is added, and a 401 then fails closed to `unavailable` through the
   * ordinary non-OK path, exactly as before. An absent key is never a reason
   * to skip asking for the release.
   */
  #authorized(init: RequestInit): RequestInit {
    const apiKey = this.#options.apiKey?.trim() ?? "";
    if (apiKey.length === 0) return init;
    const headers = new Headers(init.headers);
    headers.set("Authorization", `Bearer ${apiKey}`);
    return { ...init, headers };
  }
}
