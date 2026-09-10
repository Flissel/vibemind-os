import type { NormalizedApp } from "../components/app-normalizer.js";
import { evaluateComponentAdmission } from "../policy/capability-policy.js";
import type { PluginPolicy } from "../policy/default-policy.js";
import {
  revealSecretValue,
  type CredentialResolver,
  type SecretValue,
} from "./credential-resolver.js";
import { OPENAI_API_KEY_CREDENTIAL_REFERENCE, deriveConnectorReference } from "./credential-naming.js";
import { captureProviderInvocation } from "./provider-invocation.js";
import type {
  PluginProvider,
  ProviderBinding,
  ProviderContext,
  ProviderDescriptor,
  ProviderRequest,
  ProviderResult,
} from "./provider.js";

const PROVIDER_ID = /^[a-z0-9]+(?:[._-][a-z0-9]+)*$/;
const LOCAL_BASE_URL_HOSTNAMES = new Set(["127.0.0.1", "localhost", "::1", "[::1]"]);

const DEFAULT_MODEL = "gpt-5.6";
const DEFAULT_BASE_URL = "https://api.openai.com";
const DEFAULT_TIMEOUT_MS = 30_000;
const MAX_TIMEOUT_MS = 300_000;

// Mirrors the module-local `admissionReason` in mcp-http-provider.ts:260-267
// exactly (deliberately duplicated rather than exported/refactored: a
// connector call is a remotely-executed MCP call reusing the same gate, but
// the two providers otherwise stay independent).
function admissionReason(
  parentLicense: string | undefined,
  capability: "mcp_http" | "read" | "write",
  policy: PluginPolicy,
): string | undefined {
  const decision = evaluateComponentAdmission(parentLicense, { kind: capability }, policy);
  return decision.status === "admitted" ? undefined : decision.reason;
}

function validateBaseUrl(raw: string): void {
  let url: URL;
  try {
    url = new URL(raw);
  } catch {
    throw new Error("component_invalid:base_url");
  }
  const isLocalHttp = url.protocol === "http:" && LOCAL_BASE_URL_HOSTNAMES.has(url.hostname);
  if (
    (url.protocol !== "https:" && !isLocalHttp)
    || url.username !== ""
    || url.password !== ""
    || url.search !== ""
    || url.hash !== ""
    || url.pathname !== "/"
  ) {
    throw new Error("component_invalid:base_url");
  }
}

type McpCallMatch =
  | { readonly found: false }
  | { readonly found: true; readonly failed: boolean; readonly output: unknown };

/**
 * Scans the Responses API `output` array (kept as `unknown` and narrowed
 * field-by-field -- this is untrusted upstream JSON) for the first
 * `mcp_call` item bound to `operationName`. A present, non-null `error` on
 * that item is treated the same as the item being entirely missing: both
 * collapse to the generic `provider_failed` reason upstream so no tool-level
 * error detail ever reaches the caller.
 */
function findMcpCallMatch(payload: unknown, operationName: string): McpCallMatch {
  if (typeof payload !== "object" || payload === null) return { found: false };
  const output = Reflect.get(payload, "output");
  if (!Array.isArray(output)) return { found: false };
  for (const entry of output) {
    if (typeof entry !== "object" || entry === null) continue;
    if (Reflect.get(entry, "type") !== "mcp_call") continue;
    if (Reflect.get(entry, "name") !== operationName) continue;
    const error: unknown = Reflect.get(entry, "error");
    return {
      found: true,
      failed: error !== undefined && error !== null,
      output: Reflect.get(entry, "output") ?? null,
    };
  }
  return { found: false };
}

export interface ConnectorBridgeProviderOptions {
  readonly id: string;
  readonly binding: ProviderBinding;
  readonly app: NormalizedApp;
  readonly parentLicense?: string;
  readonly policy: PluginPolicy;
  readonly credentialResolver: CredentialResolver;
  readonly fetchImpl?: typeof fetch;
  readonly model?: string;
  readonly baseUrl?: string;
  readonly timeoutMilliseconds?: number;
}

export class ConnectorBridgeProvider implements PluginProvider {
  readonly id: string;
  readonly #descriptor: ProviderDescriptor;
  readonly #app: NormalizedApp;
  readonly #parentLicense: string | undefined;
  readonly #policy: PluginPolicy;
  readonly #credentialResolver: CredentialResolver;
  readonly #fetchImpl: typeof fetch;
  readonly #model: string;
  readonly #baseUrl: string;
  readonly #timeoutMilliseconds: number;

  constructor(options: ConnectorBridgeProviderOptions) {
    if (
      !PROVIDER_ID.test(options.id)
      || options.binding.providerKind !== "openai-connector-bridge"
      || options.binding.componentDigest !== options.app.componentDigest
    ) {
      throw new Error("provider_invalid:descriptor_mismatch");
    }
    // D4: apps whose declared id is not a `connector_...` id (asdk_app_,
    // templated_apps_) have no public invocation path outside ChatGPT --
    // refused here, fail-closed, in addition to resolution never
    // constructing this provider for them in the first place.
    if (!options.app.connectorId.startsWith("connector_")) {
      throw new Error("provider_unavailable:not_a_connector");
    }
    const baseUrl = options.baseUrl ?? DEFAULT_BASE_URL;
    validateBaseUrl(baseUrl);
    const timeoutMilliseconds = options.timeoutMilliseconds ?? DEFAULT_TIMEOUT_MS;
    if (
      !Number.isSafeInteger(timeoutMilliseconds)
      || timeoutMilliseconds < 1
      || timeoutMilliseconds > MAX_TIMEOUT_MS
    ) {
      throw new Error("provider_invalid:timeout");
    }

    this.id = options.id;
    this.#descriptor = Object.freeze({
      id: options.id,
      kind: "openai-connector-bridge",
      temporaryAdapter: false,
    });
    this.#app = Object.freeze({ ...options.app });
    this.#parentLicense = options.parentLicense;
    this.#policy = Object.freeze({ ...options.policy });
    this.#credentialResolver = options.credentialResolver;
    this.#fetchImpl = options.fetchImpl ?? globalThis.fetch;
    this.#model = options.model ?? DEFAULT_MODEL;
    // `validateBaseUrl` accepts a root URL with or without its trailing
    // slash, so normalize here: the request path is appended verbatim and
    // `https://host//v1/responses` is a different resource to some proxies.
    this.#baseUrl = baseUrl.replace(/\/+$/u, "");
    this.#timeoutMilliseconds = timeoutMilliseconds;
    Object.freeze(this);
  }

  describe(): ProviderDescriptor {
    return this.#descriptor;
  }

  async invoke(request: ProviderRequest, context: ProviderContext): Promise<ProviderResult> {
    let controller: AbortController | undefined;
    let onCallerAbort: (() => void) | undefined;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let boundContext: ProviderContext = context;

    try {
      // Every sibling provider starts here (mcp-http-provider.ts:347): the
      // shared guard bounds/clones arguments, validates identifiers, and
      // checks the signal shape. Unlike the HTTP provider, this provider's
      // contract is "invoke() always resolves, never throws" -- so a throw
      // out of the guard itself (malformed request, hostile arguments,
      // non-AbortSignal signal, ...) is caught here and folded into the same
      // generic failure the rest of invoke() uses, before any timer or
      // listener exists to clean up.
      const captured = captureProviderInvocation(request, context);
      request = captured.request;
      context = captured.context;
      boundContext = context;

      const providerDenial = admissionReason(this.#parentLicense, "mcp_http", this.#policy);
      if (providerDenial !== undefined) {
        return Object.freeze({ status: "failed", reason: providerDenial });
      }
      const writeDenial = admissionReason(this.#parentLicense, "write", this.#policy);
      if (writeDenial !== undefined) {
        return Object.freeze({ status: "failed", reason: writeDenial });
      }

      // Mirrors the HTTP provider's component-binding check
      // (mcp-request.ts's `validateMcpInvocation`): a request can only
      // invoke the exact app component this provider instance was
      // constructed for, never another component smuggled in through the
      // request.
      if (request.componentName !== this.#app.name) {
        return Object.freeze({ status: "failed", reason: "provider_failed" });
      }

      const operationName = request.operationName;
      if (typeof operationName !== "string" || operationName.length === 0) {
        return Object.freeze({ status: "failed", reason: "provider_failed" });
      }

      controller = new AbortController();
      const activeController = controller;
      onCallerAbort = (): void => activeController.abort();
      if (context.signal?.aborted === true) {
        controller.abort();
      } else {
        context.signal?.addEventListener("abort", onCallerAbort, { once: true });
      }
      timer = setTimeout(() => activeController.abort(), this.#timeoutMilliseconds);

      let apiKey: SecretValue;
      let connectorToken: SecretValue;
      try {
        apiKey = await this.#credentialResolver.resolve(
          Object.freeze({ kind: "environment", reference: OPENAI_API_KEY_CREDENTIAL_REFERENCE }),
          request.projectId,
          Object.freeze({ signal: controller.signal }),
        );
        connectorToken = await this.#credentialResolver.resolve(
          Object.freeze({ kind: "environment", reference: deriveConnectorReference(this.#app.name) }),
          request.projectId,
          Object.freeze({ signal: controller.signal }),
        );
      } catch {
        // An abort/timeout during resolution must not be reported as
        // credential_missing -- the credential may never have been asked
        // for, or the resolver's own rejection may just be it honoring the
        // signal, not a genuine resolution failure. Only a signal that is
        // NOT aborted here means the resolver itself failed to resolve.
        return Object.freeze({
          status: "failed",
          reason: controller.signal.aborted ? "provider_failed" : "credential_missing",
        });
      }

      // Mirrors the HTTP provider's own post-resolution abort check
      // (mcp-http-provider.ts:365): both credentials may have resolved in
      // the instant before the timer fired, and neither credential value
      // must ever reach the fetch call once the deadline has passed.
      if (controller.signal.aborted) {
        return Object.freeze({ status: "failed", reason: "provider_failed" });
      }

      const requestBody = {
        model: this.#model,
        store: false,
        tool_choice: "required",
        max_output_tokens: 1024,
        tools: [Object.freeze({
          type: "mcp",
          server_label: this.#app.name,
          connector_id: this.#app.connectorId,
          authorization: revealSecretValue(connectorToken),
          require_approval: "never",
          allowed_tools: [operationName],
        })],
        input: `Call the tool ${operationName} exactly once with exactly these arguments, then stop: ${JSON.stringify(request.arguments)}`,
      };

      let response: Response;
      try {
        response = await this.#fetchImpl(`${this.#baseUrl}/v1/responses`, {
          method: "POST",
          headers: {
            "content-type": "application/json",
            authorization: `Bearer ${revealSecretValue(apiKey)}`,
          },
          body: JSON.stringify(requestBody),
          signal: controller.signal,
        });
      } catch {
        // Covers both a network failure and an aborted/timed-out fetch --
        // neither ever surfaces its error text.
        return Object.freeze({ status: "failed", reason: "provider_failed" });
      }

      if (response.status !== 200) {
        // The body is never read here: an upstream error payload (e.g. a
        // 401's message) must never reach the reason or result.
        return Object.freeze({ status: "failed", reason: "provider_failed" });
      }

      let payload: unknown;
      try {
        payload = await response.json();
      } catch {
        return Object.freeze({ status: "failed", reason: "provider_failed" });
      }

      const match = findMcpCallMatch(payload, operationName);
      if (!match.found || match.failed) {
        return Object.freeze({ status: "failed", reason: "provider_failed" });
      }
      return Object.freeze({ status: "success", output: match.output });
    } catch {
      // Anything that escapes the guarded stages above (most notably
      // `captureProviderInvocation` itself rejecting a malformed request,
      // hostile arguments, or a non-AbortSignal `context.signal`) still
      // resolves rather than rejects, per this provider's contract.
      return Object.freeze({ status: "failed", reason: "provider_failed" });
    } finally {
      if (timer !== undefined) clearTimeout(timer);
      if (onCallerAbort !== undefined) boundContext.signal?.removeEventListener("abort", onCallerAbort);
    }
  }
}
