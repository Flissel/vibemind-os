import { describe, expect, it } from "vitest";
import { revealSecretValue, type CredentialReference } from "@rowboat/openai-plugin-runtime";
import { OpenFangCredentialResolver } from "@/src/infrastructure/plugins/openfang-credential-resolver";

const reference: CredentialReference = Object.freeze({ kind: "bearer", reference: "GITHUB_PAT_TOKEN" });
const projectId = "11111111-1111-4111-8111-111111111111";

function jsonResponse(status: number, body: unknown): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as Response;
}

function resolverWith(fetchImpl: typeof fetch, timeoutMs = 1_000): OpenFangCredentialResolver {
  return new OpenFangCredentialResolver({
    baseUrl: "http://openfang.invalid:4200",
    apiKey: "test-openfang-key",
    fetch: fetchImpl,
    timeoutMs,
  });
}

/**
 * `.rejects.toThrow(string)` is a *substring* match: it would still pass if
 * the thrown message became `credential_missing: <response body>`, or if the
 * error carried a `cause` holding the response, the reference, or the token.
 * Capture the actual error so tests can pin the exact message and assert no
 * `cause` exists at all -- vitest's `.rejects.toThrow` alone cannot catch
 * that regression.
 */
async function captureRejection(promise: Promise<unknown>): Promise<Error> {
  try {
    await promise;
  } catch (err) {
    return err as Error;
  }
  throw new Error("expected the promise to reject, but it resolved");
}

describe("OpenFangCredentialResolver", () => {
  it("resolves a 200 response to a SecretValue whose revealed value matches, without leaking it through JSON.stringify", async () => {
    const resolver = resolverWith((async () => jsonResponse(200, { reference: "GITHUB_PAT_TOKEN", value: "ghp_super_secret" })) as unknown as typeof fetch);
    const secret = await resolver.resolve(reference, projectId, { signal: new AbortController().signal });
    expect(revealSecretValue(secret)).toBe("ghp_super_secret");
    expect(JSON.stringify(secret)).not.toContain("ghp_super_secret");
    expect(JSON.stringify({ secret })).not.toContain("ghp_super_secret");
  });

  it("POSTs to <baseUrl>/api/credentials/issue with a bearer token and a body carrying only the reference", async () => {
    const calls: Array<{ url: string; init: RequestInit }> = [];
    const fetchImpl = (async (url: string, init?: RequestInit) => {
      calls.push({ url, init: init ?? {} });
      return jsonResponse(200, { reference: "GITHUB_PAT_TOKEN", value: "ghp_x" });
    }) as unknown as typeof fetch;
    const resolver = resolverWith(fetchImpl);
    await resolver.resolve(reference, projectId, { signal: new AbortController().signal });

    expect(calls).toHaveLength(1);
    expect(calls[0]!.url).toBe("http://openfang.invalid:4200/api/credentials/issue");
    expect(calls[0]!.init.method).toBe("POST");
    const headers = calls[0]!.init.headers as Record<string, string>;
    expect(headers["Authorization"] ?? headers["authorization"]).toBe("Bearer test-openfang-key");
    const body = calls[0]!.init.body as string;
    expect(JSON.parse(body)).toEqual({ reference: "GITHUB_PAT_TOKEN" });
    expect(body).not.toContain(projectId);
  });

  it("throws exactly credential_missing, with no cause, on a 404", async () => {
    const resolver = resolverWith((async () => jsonResponse(404, { error: "credential_unavailable" })) as unknown as typeof fetch);
    const err = await captureRejection(resolver.resolve(reference, projectId, { signal: new AbortController().signal }));
    expect(err.message).toBe("credential_missing");
    expect(err.cause).toBeUndefined();
  });

  it("throws exactly credential_missing, with no cause, on 401 or 403 -- an unauthorised Rowboat must not look different from an unavailable credential", async () => {
    for (const status of [401, 403]) {
      const resolver = resolverWith((async () => jsonResponse(status, { error: "unauthorized" })) as unknown as typeof fetch);
      const err = await captureRejection(resolver.resolve(reference, projectId, { signal: new AbortController().signal }));
      expect(err.message).toBe("credential_missing");
      expect(err.cause).toBeUndefined();
    }
  });

  it("throws credential_missing on a 500, a network rejection, a non-JSON body, a body without value, and a value that is empty or not a string", async () => {
    const cases: Array<() => typeof fetch> = [
      () => (async () => jsonResponse(500, { error: "internal" })) as unknown as typeof fetch,
      () => (async () => { throw new Error("ECONNREFUSED"); }) as unknown as typeof fetch,
      () => (async () => ({ ok: true, status: 200, json: async () => { throw new SyntaxError("not json"); } }) as unknown as Response) as unknown as typeof fetch,
      () => (async () => jsonResponse(200, { reference: "GITHUB_PAT_TOKEN" })) as unknown as typeof fetch,
      () => (async () => jsonResponse(200, { reference: "GITHUB_PAT_TOKEN", value: "" })) as unknown as typeof fetch,
      () => (async () => jsonResponse(200, { reference: "GITHUB_PAT_TOKEN", value: 12345 })) as unknown as typeof fetch,
      () => (async () => jsonResponse(200, { reference: "GITHUB_PAT_TOKEN", value: null })) as unknown as typeof fetch,
    ];
    for (const makeFetch of cases) {
      const resolver = resolverWith(makeFetch());
      await expect(resolver.resolve(reference, projectId, { signal: new AbortController().signal })).rejects.toThrow("credential_missing");
    }
  });

  it("throws exactly credential_missing, with no cause, on a 500 -- not a message or cause carrying the response body", async () => {
    const resolver = resolverWith((async () => jsonResponse(500, { error: "internal", detail: "should-never-surface" })) as unknown as typeof fetch);
    const err = await captureRejection(resolver.resolve(reference, projectId, { signal: new AbortController().signal }));
    expect(err.message).toBe("credential_missing");
    expect(err.cause).toBeUndefined();
  });

  it("throws exactly credential_missing, with no cause, on a non-JSON body -- not a message or cause carrying the parse error", async () => {
    const fetchImpl = (async () => ({
      ok: true,
      status: 200,
      json: async () => { throw new SyntaxError("Unexpected token in JSON, possibly containing a secret-looking fragment"); },
    }) as unknown as Response) as unknown as typeof fetch;
    const resolver = resolverWith(fetchImpl);
    const err = await captureRejection(resolver.resolve(reference, projectId, { signal: new AbortController().signal }));
    expect(err.message).toBe("credential_missing");
    expect(err.cause).toBeUndefined();
  });

  it("throws exactly credential_missing, with no cause, on a network rejection -- not a message or cause carrying the transport error", async () => {
    const fetchImpl = (async () => { throw new Error("ECONNREFUSED 127.0.0.1:4200"); }) as unknown as typeof fetch;
    const resolver = resolverWith(fetchImpl);
    const err = await captureRejection(resolver.resolve(reference, projectId, { signal: new AbortController().signal }));
    expect(err.message).toBe("credential_missing");
    expect(err.cause).toBeUndefined();
  });

  it("throws exactly credential_missing, with no cause, on an empty value -- not a message or cause carrying the (empty) value", async () => {
    const resolver = resolverWith((async () => jsonResponse(200, { reference: "GITHUB_PAT_TOKEN", value: "" })) as unknown as typeof fetch);
    const err = await captureRejection(resolver.resolve(reference, projectId, { signal: new AbortController().signal }));
    expect(err.message).toBe("credential_missing");
    expect(err.cause).toBeUndefined();
  });

  it("throws rather than hanging when the caller's signal is already aborted", async () => {
    const start = Date.now();
    // Mirrors real fetch: a request handed an already-aborted signal rejects
    // immediately rather than ever settling.
    const fetchImpl = ((_url: string, init?: { signal?: AbortSignal }) =>
      new Promise<Response>((_resolve, reject) => {
        if (init?.signal?.aborted === true) { reject(new Error("aborted")); return; }
        init?.signal?.addEventListener("abort", () => reject(new Error("aborted")));
      })) as unknown as typeof fetch;
    const resolver = resolverWith(fetchImpl, 5_000);
    const controller = new AbortController();
    controller.abort();
    await expect(resolver.resolve(reference, projectId, { signal: controller.signal })).rejects.toThrow("credential_missing");
    expect(Date.now() - start).toBeLessThan(500);
  });

  it("bounds the request by its own deadline so a connected-but-silent OpenFang cannot leave it pending forever", async () => {
    const start = Date.now();
    const fetchImpl = ((_url: string, init?: { signal?: AbortSignal }) =>
      new Promise<Response>((_resolve, reject) => {
        init?.signal?.addEventListener("abort", () => reject(new Error("aborted")));
      })) as unknown as typeof fetch;
    const resolver = resolverWith(fetchImpl, 50);
    await expect(resolver.resolve(reference, projectId, { signal: new AbortController().signal })).rejects.toThrow("credential_missing");
    expect(Date.now() - start).toBeLessThan(500);
  });

  it("bounds the body read too, not just the fetch: headers arriving does not free the deadline", async () => {
    // fetch() itself settles immediately (as real fetch does once headers
    // arrive), but response.json() only settles when the bounded
    // AbortController's own signal fires -- exactly like a real Response's
    // body-read stream, which observes the same signal that was passed to
    // fetch() for as long as the body is still being read. A server that
    // answers 200 with a Content-Length and then sends zero body bytes must
    // still be interrupted by this resolver's own deadline: if the timer
    // were cleared as soon as fetch() returned (rather than after the body
    // is read), the signal would fire but nothing would still be listening,
    // and this would hang forever.
    const start = Date.now();
    const fetchImpl = (async (_url: string, init?: { signal?: AbortSignal }) => ({
      ok: true,
      status: 200,
      json: () => new Promise<never>((_resolve, reject) => {
        init?.signal?.addEventListener("abort", () => reject(new Error("aborted")));
      }),
    }) as unknown as Response) as unknown as typeof fetch;
    const resolver = resolverWith(fetchImpl, 50);
    await expect(resolver.resolve(reference, projectId, { signal: new AbortController().signal })).rejects.toThrow("credential_missing");
    expect(Date.now() - start).toBeLessThan(500);
  });

  it("throws credential_missing when the caller's signal aborts mid-flight, not only when it starts out already aborted", async () => {
    const start = Date.now();
    const fetchImpl = ((_url: string, init?: { signal?: AbortSignal }) =>
      new Promise<Response>((_resolve, reject) => {
        init?.signal?.addEventListener("abort", () => reject(new Error("aborted")));
      })) as unknown as typeof fetch;
    const resolver = resolverWith(fetchImpl, 5_000);
    const controller = new AbortController();
    const pending = resolver.resolve(reference, projectId, { signal: controller.signal });
    // The signal starts out NOT aborted -- resolve() is already in flight --
    // and only aborts partway through, unlike the already-aborted case above.
    setTimeout(() => controller.abort(), 10);
    await expect(pending).rejects.toThrow("credential_missing");
    expect(Date.now() - start).toBeLessThan(500);
  });

  it("never reaches fetch for a reference or project id assertCredentialRequest rejects, and throws exactly credential_missing with no cause", async () => {
    let fetchCalls = 0;
    const fetchImpl = (async () => { fetchCalls++; return jsonResponse(200, { reference: "x", value: "y" }); }) as unknown as typeof fetch;
    const resolver = resolverWith(fetchImpl);

    const badReference: CredentialReference = Object.freeze({ kind: "bearer", reference: "" });
    const referenceErr = await captureRejection(resolver.resolve(badReference, projectId, { signal: new AbortController().signal }));
    expect(referenceErr.message).toBe("credential_missing");
    expect(referenceErr.cause).toBeUndefined();

    const badProjectId = "project with spaces";
    const projectIdErr = await captureRejection(resolver.resolve(reference, badProjectId, { signal: new AbortController().signal }));
    expect(projectIdErr.message).toBe("credential_missing");
    expect(projectIdErr.cause).toBeUndefined();

    expect(fetchCalls).toBe(0);
  });

  it("resolves without an options argument too, still bounded by its own deadline", async () => {
    const resolver = resolverWith((async () => jsonResponse(200, { reference: "GITHUB_PAT_TOKEN", value: "ghp_y" })) as unknown as typeof fetch);
    const secret = await resolver.resolve(reference, projectId);
    expect(revealSecretValue(secret)).toBe("ghp_y");
  });
});
