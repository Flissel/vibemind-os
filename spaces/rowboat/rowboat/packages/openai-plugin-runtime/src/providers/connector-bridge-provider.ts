import type { NormalizedApp } from "../components/app-normalizer.js";
import { evaluateComponentAdmission } from "../policy/capability-policy.js";
import type { PluginPolicy } from "../policy/default-policy.js";
import {
  revealSecretValue,
  type CredentialResolver,
  type SecretValue,
} from "./credential-resolver.js";
import type {
  PluginProvider,
  ProviderBinding,
  ProviderContext,
  ProviderDescriptor,
  ProviderRequest,
  ProviderResult,
} from "./provider.js";

const PROVIDER_ID = /^[a-z0-9]+(?:[._-][a-z0-9]+)*$/;
const CONNECTOR_NAME_INVALID = /[^A-Z0-9]+/g;
const LOCAL_BASE_URL_HOSTNAMES = new Set(["127.0.0.1", "localhost", "::1", "[::1]"]);

const DEFAULT_MODEL = "gpt-5.6";
const DEFAULT_BASE_URL = "https://api.openai.com";
const DEFAULT_TIMEOUT_MS = 30_000;
const MAX_TIMEOUT_MS = 300_000;

/**
 * `CONNECTOR_` + the app name uppercased, with runs of non-alphanumeric
 * characters collapsed to a single underscore (e.g. "canva" ->
 * "CONNECTOR_CANVA", "monday-com" -> "CONNECTOR_MONDAY_COM"). This is the
 * environment-credential reference that carries the connector's per-app
 * OAuth token, resolved through the same `CredentialResolver` as the OpenAI
 * API key -- never a standing secret inside Rowboat itself.
 */
export function deriveConnectorReference(appName: string): string {
  return `CONNECTOR_${appName.toUpperCase().replace(CONNECTOR_NAME_INVALID, "_")}`;
}

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
    this.#baseUrl = baseUrl;
    this.#timeoutMilliseconds = timeoutMilliseconds;
    Object.freeze(this);
  }

  describe(): ProviderDescriptor {
    return this.#descriptor;
  }

  async invoke(request: ProviderRequest, context: ProviderContext): Promise<ProviderResult> {
    const providerDenial = admissionReason(this.#parentLicense, "mcp_http", this.#policy);
    if (providerDenial !== undefined) {
      return Object.freeze({ status: "failed", reason: providerDenial });
    }
    const writeDenial = admissionReason(this.#parentLicense, "write", this.#policy);
    if (writeDenial !== undefined) {
      return Object.freeze({ status: "failed", reason: writeDenial });
    }

    const operationName = request.operationName;
    if (typeof operationName !== "string" || operationName.length === 0) {
      return Object.freeze({ status: "failed", reason: "provider_failed" });
    }

    const controller = new AbortController();
    const onCallerAbort = (): void => controller.abort();
    if (context.signal?.aborted === true) {
      controller.abort();
    } else {
      context.signal?.addEventListener("abort", onCallerAbort, { once: true });
    }
    const timer = setTimeout(() => controller.abort(), this.#timeoutMilliseconds);

    try {
      let apiKey: SecretValue;
      let connectorToken: SecretValue;
      try {
        apiKey = await this.#credentialResolver.resolve(
          Object.freeze({ kind: "environment", reference: "OPENAI_API_KEY" }),
          request.projectId,
          Object.freeze({ signal: controller.signal }),
        );
        connectorToken = await this.#credentialResolver.resolve(
          Object.freeze({ kind: "environment", reference: deriveConnectorReference(this.#app.name) }),
          request.projectId,
          Object.freeze({ signal: controller.signal }),
        );
      } catch {
        return Object.freeze({ status: "failed", reason: "credential_missing" });
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
    } finally {
      clearTimeout(timer);
      context.signal?.removeEventListener("abort", onCallerAbort);
    }
  }
}
