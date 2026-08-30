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

  it("never sends argument values, only their digest", async () => {
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

  it("is unavailable when OpenFang cannot be reached or never decides", async () => {
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
});
