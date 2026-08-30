import {
  assertCredentialRequest,
  createSecretValue,
  type CredentialReference,
  type CredentialResolver,
  type SecretValue,
} from "@rowboat/openai-plugin-runtime";

export interface OpenFangCredentialResolverOptions {
  readonly baseUrl: string;
  readonly apiKey: string;
  readonly fetch: typeof fetch;
  readonly timeoutMs: number;
}

/**
 * Resolves a plugin credential reference by asking OpenFang for it, fresh,
 * on every call.
 *
 * OpenFang holds the secret value; this resolver never keeps a standing
 * copy, so revoking or rotating a reference in OpenFang takes effect on the
 * very next call. OpenFang's `/api/credentials/issue` answers 404
 * identically whether a reference is unallowlisted or
 * allowlisted-but-unresolvable, deliberately, so its response cannot be used
 * to enumerate which secrets it holds -- this resolver does not attempt to
 * tell the two apart either, or to distinguish any other failure from any
 * other. Every failure path -- a non-200 status of any kind, a malformed
 * body, a transport error, a timeout, or a reference/project id
 * `assertCredentialRequest` itself rejects -- collapses to the same
 * `credential_missing`, and no thrown message ever carries the credential
 * value, the reference, the token, or the response body.
 */
export class OpenFangCredentialResolver implements CredentialResolver {
  readonly #options: OpenFangCredentialResolverOptions;

  constructor(options: OpenFangCredentialResolverOptions) {
    this.#options = options;
  }

  async resolve(
    reference: CredentialReference,
    projectId: string,
    options?: Readonly<{ readonly signal: AbortSignal }>,
  ): Promise<SecretValue> {
    try {
      // The kernel's own reference/project-id shape is broader than what
      // OpenFang's endpoint accepts, so this only rejects what the kernel
      // itself would never have sent -- OpenFang's narrower charset is
      // enforced remotely, by its own 400, which lands in the catch below
      // like every other failure.
      assertCredentialRequest(reference, projectId);
    } catch {
      throw new Error("credential_missing");
    }

    try {
      const response = await this.#fetchBounded(
        `${this.#options.baseUrl}/api/credentials/issue`,
        {
          method: "POST",
          headers: {
            "content-type": "application/json",
            Authorization: `Bearer ${this.#options.apiKey}`,
          },
          body: JSON.stringify({ reference: reference.reference }),
        },
        options?.signal,
      );
      if (!response.ok) throw new Error("credential_missing");
      const body: unknown = await response.json();
      const value = body !== null && typeof body === "object"
        ? (body as { value?: unknown }).value
        : undefined;
      if (typeof value !== "string" || value.length === 0) throw new Error("credential_missing");
      return createSecretValue(value);
    } catch {
      throw new Error("credential_missing");
    }
  }

  /**
   * Bounds one fetch by this resolver's own timeout, in addition to the
   * caller's signal -- the same pattern OpenFangWriteReleasePolicy uses: an
   * AbortController per request, the timer and listener cleared in a
   * finally, so a connected-but-silent OpenFang cannot leave a released
   * call pending forever.
   */
  async #fetchBounded(url: string, init: RequestInit, signal: AbortSignal | undefined): Promise<Response> {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), this.#options.timeoutMs);
    const onAbort = () => controller.abort();
    if (signal?.aborted === true) controller.abort();
    else signal?.addEventListener("abort", onAbort);
    try {
      return await this.#options.fetch(url, { ...init, signal: controller.signal });
    } finally {
      clearTimeout(timer);
      signal?.removeEventListener("abort", onAbort);
    }
  }
}
