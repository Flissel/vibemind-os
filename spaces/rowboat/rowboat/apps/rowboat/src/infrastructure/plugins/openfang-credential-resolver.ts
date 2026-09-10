import {
  assertCredentialRequest,
  createSecretValue,
  deriveOAuthBearerReference,
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
// OpenFang resolves a credential BY NAME (vault -> dotenv -> env var) and its
// issuance endpoint accepts only `^[A-Za-z_][A-Za-z0-9_]{0,127}$`, so a
// reference must arrive as an env-shaped name. A bearer/environment reference
// already is one and passes through verbatim. An oauth reference is a resource
// URL, turned into that shape by the kernel's `deriveOAuthBearerReference` --
// the same derivation `requiredCredentialNames` (plugin-service.shared.ts)
// uses to tell an operator, up front, which name to provision. A reference
// that cannot be derived (not a URL, or a name that would exceed OpenFang's
// bound) yields undefined and the caller fails closed as `credential_missing`.
function deriveOpenFangReference(reference: CredentialReference): string | undefined {
  if (reference.kind !== "oauth") return reference.reference;
  return deriveOAuthBearerReference(reference.reference);
}

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
    const openFangReference = deriveOpenFangReference(reference);
    if (openFangReference === undefined) throw new Error("credential_missing");

    try {
      const body: unknown = await this.#fetchBounded(
        `${this.#options.baseUrl}/api/credentials/issue`,
        {
          method: "POST",
          headers: {
            "content-type": "application/json",
            Authorization: `Bearer ${this.#options.apiKey}`,
          },
          body: JSON.stringify({ reference: openFangReference }),
          redirect: "error",
        },
        options?.signal,
      );
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
   * Bounds the *whole* exchange -- the fetch and the body read -- by this
   * resolver's own timeout, in addition to the caller's signal, the same
   * pattern OpenFangWriteReleasePolicy uses: an AbortController per request,
   * the timer and listener cleared in a finally, so a connected-but-silent
   * OpenFang cannot leave a released call pending forever.
   *
   * The body is read *inside* this method, before the finally clears the
   * timer: `fetch()` itself resolves as soon as headers arrive, so a server
   * that answers 200 with a Content-Length and then never sends the body
   * would otherwise hang `response.json()` at the call site with no deadline
   * and no abort path at all -- the bug this shape exists to close.
   */
  async #fetchBounded(url: string, init: RequestInit, signal: AbortSignal | undefined): Promise<unknown> {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), this.#options.timeoutMs);
    const onAbort = () => controller.abort();
    if (signal?.aborted === true) controller.abort();
    else signal?.addEventListener("abort", onAbort);
    try {
      const response = await this.#options.fetch(url, { ...init, signal: controller.signal });
      if (!response.ok) throw new Error("credential_missing");
      return await response.json();
    } finally {
      clearTimeout(timer);
      signal?.removeEventListener("abort", onAbort);
    }
  }
}
