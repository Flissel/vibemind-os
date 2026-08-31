import { describe, expect, it } from "vitest";
import { OpenFangWriteReleasePolicy } from "@/src/infrastructure/policies/openfang.plugin-write-release.policy";

const request = Object.freeze({
  projectId: "11111111-1111-4111-8111-111111111111",
  pluginName: "github",
  toolName: "plugin_github_github",
  componentDigest: "a".repeat(64),
  argumentsDigest: "b".repeat(64),
});

function policyWith(responses: readonly unknown[], calls: string[] = []) {
  let index = 0;
  const fetchImpl = async (input: string, init?: { method?: string; body?: string }) => {
    calls.push(`${init?.method ?? "GET"} ${input}`);
    const body = responses[Math.min(index++, responses.length - 1)];
    return { ok: true, status: 200, json: async () => body } as Response;
  };
  return new OpenFangWriteReleasePolicy({
    baseUrl: "http://openfang.invalid:4200",
    fetch: fetchImpl as unknown as typeof fetch,
    timeoutMs: 1_000,
    pollIntervalMs: 1,
  });
}

describe("OpenFang write release", () => {
  it("creates one request and returns the approval decision", async () => {
    const calls: string[] = [];
    const policy = policyWith([
      { id: "3f0f8a1e-0000-4000-8000-000000000001" },
      { approvals: [{ id: "3f0f8a1e-0000-4000-8000-000000000001", status: "approved" }] },
    ], calls);
    expect(await policy.release(request, new AbortController().signal))
      .toEqual({ status: "approved", approvalId: "3f0f8a1e-0000-4000-8000-000000000001" });
    expect(calls[0]).toBe("POST http://openfang.invalid:4200/api/approvals");
    expect(calls[1]).toBe("GET http://openfang.invalid:4200/api/approvals");
  });

  it("reports a rejection and an expiry as themselves", async () => {
    for (const [status, expected] of [["rejected", "denied"], ["expired", "expired"]] as const) {
      const policy = policyWith([
        { id: "3f0f8a1e-0000-4000-8000-000000000002" },
        { approvals: [{ id: "3f0f8a1e-0000-4000-8000-000000000002", status }] },
      ]);
      expect((await policy.release(request, new AbortController().signal)).status).toBe(expected);
    }
  });

  it("sends the arguments digest in the request body, matching the expected shape", async () => {
    // PluginWriteReleaseRequest itself never carries raw argument values --
    // only their digest -- so no input this adapter can receive could ever
    // make this fail on "sent a raw value"; what it actually pins is that
    // the digest crosses into the body correctly and the body matches the
    // shape OpenFang expects, plus a canary against an unrelated
    // secret-looking literal ending up in it.
    const bodies: string[] = [];
    const fetchImpl = async (_input: string, init?: { body?: string }) => {
      if (init?.body !== undefined) bodies.push(init.body);
      return { ok: true, status: 200, json: async () => ({ id: "3f0f8a1e-0000-4000-8000-000000000003", approvals: [{ id: "3f0f8a1e-0000-4000-8000-000000000003", status: "approved" }] }) } as Response;
    };
    const policy = new OpenFangWriteReleasePolicy({ baseUrl: "http://openfang.invalid:4200", fetch: fetchImpl as unknown as typeof fetch, timeoutMs: 1_000, pollIntervalMs: 1 });
    await policy.release(request, new AbortController().signal);
    expect(bodies[0]).toContain(request.argumentsDigest);
    expect(bodies[0]).not.toContain("sk-");
    expect(JSON.parse(bodies[0]!)).toMatchObject({ agent_id: request.projectId, tool_name: request.toolName });
  });

  it("returns unavailable when OpenFang cannot be reached at all, and expired when it never decides before the deadline", async () => {
    const unreachable = new OpenFangWriteReleasePolicy({
      baseUrl: "http://openfang.invalid:4200",
      fetch: (async () => { throw new Error("ECONNREFUSED"); }) as unknown as typeof fetch,
      timeoutMs: 50, pollIntervalMs: 1,
    });
    expect(await unreachable.release(request, new AbortController().signal)).toEqual({ status: "unavailable" });

    const undecided = policyWith([
      { id: "3f0f8a1e-0000-4000-8000-000000000004" },
      { approvals: [{ id: "3f0f8a1e-0000-4000-8000-000000000004", status: "pending" }] },
    ]);
    expect((await undecided.release(request, new AbortController().signal)).status).toBe("expired");
  });

  it("returns unavailable when OpenFang answers a non-OK status, on either the create POST or the poll GET", async () => {
    const nonOkOnCreate = new OpenFangWriteReleasePolicy({
      baseUrl: "http://openfang.invalid:4200",
      fetch: (async () => ({ ok: false, status: 500, json: async () => ({ error: "internal" }) })) as unknown as typeof fetch,
      timeoutMs: 1_000, pollIntervalMs: 1,
    });
    expect(await nonOkOnCreate.release(request, new AbortController().signal)).toEqual({ status: "unavailable" });

    let calls = 0;
    const nonOkOnPoll = new OpenFangWriteReleasePolicy({
      baseUrl: "http://openfang.invalid:4200",
      fetch: (async () => {
        calls += 1;
        if (calls === 1) return { ok: true, status: 200, json: async () => ({ id: "3f0f8a1e-0000-4000-8000-000000000012" }) } as Response;
        return { ok: false, status: 500, json: async () => ({ error: "internal" }) } as Response;
      }) as unknown as typeof fetch,
      timeoutMs: 1_000, pollIntervalMs: 1,
    });
    expect(await nonOkOnPoll.release(request, new AbortController().signal)).toEqual({ status: "unavailable" });
  });

  it("returns unavailable when the created approval's id is not a UUID", async () => {
    const policy = policyWith([{ id: "not-a-uuid" }]);
    expect(await policy.release(request, new AbortController().signal)).toEqual({ status: "unavailable" });
  });

  it("bounds the body read too, not just the fetch: headers arriving does not free the deadline", async () => {
    // Same defect, and the same fix, as OpenFangCredentialResolver's own
    // suite: fetch() settling (headers arrived) must not free the timer
    // before the body is read too, or a 200 that never sends its body would
    // hang release() forever with no deadline and no abort path.
    const start = Date.now();
    const fetchImpl = (async (_url: string, init?: { signal?: AbortSignal }) => ({
      ok: true,
      status: 200,
      json: () => new Promise<never>((_resolve, reject) => {
        init?.signal?.addEventListener("abort", () => reject(new Error("aborted")));
      }),
    }) as unknown as Response) as unknown as typeof fetch;
    const policy = new OpenFangWriteReleasePolicy({
      baseUrl: "http://openfang.invalid:4200", fetch: fetchImpl, timeoutMs: 50, pollIntervalMs: 1,
    });
    const decision = await policy.release(request, new AbortController().signal);
    expect(decision.status).not.toBe("approved");
    expect(Date.now() - start).toBeLessThan(500);
  });

  it("aborts a request that never settles once the deadline passes", async () => {
    const start = Date.now();
    const fetchImpl = (_input: string, init?: { signal?: AbortSignal }) =>
      new Promise<Response>((_resolve, reject) => {
        init?.signal?.addEventListener("abort", () => reject(new Error("aborted")));
      });
    const policy = new OpenFangWriteReleasePolicy({
      baseUrl: "http://openfang.invalid:4200",
      fetch: fetchImpl as unknown as typeof fetch,
      timeoutMs: 50,
      pollIntervalMs: 1,
    });
    const decision = await policy.release(request, new AbortController().signal);
    expect(decision.status).not.toBe("approved");
    expect(Date.now() - start).toBeLessThan(500);
  });
});

/**
 * Authentication on the release calls.
 *
 * Found by the live end-to-end proof, not by any unit test on this branch:
 * this adapter sent no `Authorization` header at all, while OpenFang's own
 * auth middleware makes `/api/approvals` public for GET only -- every POST
 * needs the bearer token. So on any daemon with an api_key configured the
 * create POST answered 401, `#fetchBounded` threw, `release()` returned
 * `unavailable`, and every write stayed under review however many humans
 * approved it. Worse, an api_key is not optional in that deployment: OpenFang's
 * `/api/credentials/issue` refuses outright on a fail-open daemon (empty
 * api_key AND auth disabled), which is exactly the condition under which the
 * unauthenticated POST would have worked -- the two are the same predicate,
 * inverted, so no configuration satisfied both.
 *
 * These pin the header itself: its presence, its exact value, that it rides
 * the poll GET as well as the create POST (a daemon may protect either), and
 * that an unconfigured key still sends nothing rather than an empty bearer.
 */
function headerCapturingFetch(captured: { url: string; method: string; authorization: string | null }[], responses: readonly unknown[]) {
  let index = 0;
  return (async (input: string, init?: RequestInit) => {
    captured.push({
      url: String(input),
      method: init?.method ?? "GET",
      authorization: new Headers(init?.headers).get("authorization"),
    });
    const body = responses[Math.min(index++, responses.length - 1)];
    return { ok: true, status: 200, json: async () => body } as Response;
  }) as unknown as typeof fetch;
}

describe("OpenFang write release authentication", () => {
  const approvalId = "3f0f8a1e-0000-4000-8000-0000000000a1";
  const responses = [{ id: approvalId }, { approvals: [{ id: approvalId, status: "approved" }] }];

  it("sends Authorization: Bearer <key> on the create POST and on the poll GET alike", async () => {
    const captured: { url: string; method: string; authorization: string | null }[] = [];
    const policy = new OpenFangWriteReleasePolicy({
      baseUrl: "http://openfang.invalid:4200",
      fetch: headerCapturingFetch(captured, responses),
      timeoutMs: 1_000, pollIntervalMs: 1,
      apiKey: "release-key-value",
    });
    expect(await policy.release(request, new AbortController().signal)).toEqual({ status: "approved", approvalId });
    expect(captured).toHaveLength(2);
    // The create POST.
    expect(captured[0]!.method).toBe("POST");
    expect(captured[0]!.authorization).toBe("Bearer release-key-value");
    // The poll GET -- the half a "the POST is authenticated now" fix forgets.
    expect(captured[1]!.method).toBe("GET");
    expect(captured[1]!.authorization).toBe("Bearer release-key-value");
    // Authenticating must not disturb what the create POST already sends.
    expect(captured[0]!.url).toBe("http://openfang.invalid:4200/api/approvals");
  });

  it("preserves the content-type header on the create POST while adding the bearer", async () => {
    const seen: { contentType: string | null; authorization: string | null }[] = [];
    const fetchImpl = (async (_input: string, init?: RequestInit) => {
      const headers = new Headers(init?.headers);
      seen.push({ contentType: headers.get("content-type"), authorization: headers.get("authorization") });
      return { ok: true, status: 200, json: async () => ({ id: approvalId, approvals: [{ id: approvalId, status: "approved" }] }) } as Response;
    }) as unknown as typeof fetch;
    const policy = new OpenFangWriteReleasePolicy({
      baseUrl: "http://openfang.invalid:4200", fetch: fetchImpl, timeoutMs: 1_000, pollIntervalMs: 1, apiKey: "k",
    });
    await policy.release(request, new AbortController().signal);
    expect(seen[0]).toEqual({ contentType: "application/json", authorization: "Bearer k" });
  });

  it("sends no Authorization header at all when no key is configured, and a 401 still fails closed", async () => {
    const captured: { url: string; method: string; authorization: string | null }[] = [];
    const policy = new OpenFangWriteReleasePolicy({
      baseUrl: "http://openfang.invalid:4200",
      fetch: headerCapturingFetch(captured, responses),
      timeoutMs: 1_000, pollIntervalMs: 1,
    });
    expect(await policy.release(request, new AbortController().signal)).toEqual({ status: "approved", approvalId });
    expect(captured.map(call => call.authorization)).toEqual([null, null]);

    // And an unconfigured key is never a reason to treat a refusal as a
    // release: a 401 is still `unavailable`, so the write stays under review.
    const refused = new OpenFangWriteReleasePolicy({
      baseUrl: "http://openfang.invalid:4200",
      fetch: (async () => ({ ok: false, status: 401, json: async () => ({ error: "Missing Authorization: Bearer <api_key> header" }) })) as unknown as typeof fetch,
      timeoutMs: 1_000, pollIntervalMs: 1,
    });
    expect(await refused.release(request, new AbortController().signal)).toEqual({ status: "unavailable" });
  });

  it("sends no Authorization header for a blank or whitespace-only key rather than an empty bearer", async () => {
    for (const blank of ["", "   "]) {
      const captured: { url: string; method: string; authorization: string | null }[] = [];
      const policy = new OpenFangWriteReleasePolicy({
        baseUrl: "http://openfang.invalid:4200",
        fetch: headerCapturingFetch(captured, responses),
        timeoutMs: 1_000, pollIntervalMs: 1,
        apiKey: blank,
      });
      await policy.release(request, new AbortController().signal);
      expect(captured.map(call => call.authorization)).toEqual([null, null]);
    }
  });

  it("never puts the key in the request body, the URL, or a thrown message", async () => {
    const key = "release-key-value";
    const seen: string[] = [];
    const fetchImpl = (async (input: string, init?: RequestInit) => {
      seen.push(String(input));
      if (typeof init?.body === "string") seen.push(init.body);
      return { ok: false, status: 500, json: async () => ({ error: "internal" }) } as Response;
    }) as unknown as typeof fetch;
    const policy = new OpenFangWriteReleasePolicy({
      baseUrl: "http://openfang.invalid:4200", fetch: fetchImpl, timeoutMs: 1_000, pollIntervalMs: 1, apiKey: key,
    });
    const decision = await policy.release(request, new AbortController().signal);
    expect(decision).toEqual({ status: "unavailable" });
    expect(seen.join("\n")).not.toContain(key);
  });
});
