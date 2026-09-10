import { posix, win32 } from "node:path";
import { McpServerSchema } from "../schema/component-schemas.js";
import { assertBinding } from "../providers/provider-registry.js";
import { resolveHttpMcpOauthResource } from "../providers/credential-naming.js";
import type { ProviderBinding } from "../providers/provider.js";
import type { NormalizedApp } from "./app-normalizer.js";

const DIGEST = /^[a-f0-9]{64}$/;
const COMPONENT_NAME = /^[A-Za-z0-9][A-Za-z0-9._ -]*$/;
const SAFE_ENV_REFERENCE = /^[A-Z][A-Z0-9_]*$/;
const WINDOWS_DRIVE_PREFIX = /^[A-Za-z]:/;

export const MAX_PROCESS_TIMEOUT_MS = 300_000 as const;

export interface NormalizedHttpMcp {
  readonly name: string;
  readonly kind: "mcp-http";
  readonly componentDigest: string;
  readonly url: string;
  readonly oauthResource?: string;
  readonly bearerTokenReference?: string;
}

export interface NormalizedProcessMcp {
  readonly name: string;
  readonly kind: "mcp-process";
  readonly componentDigest: string;
  readonly command: string;
  readonly args: readonly string[];
  readonly workingDirectory?: string;
  readonly environmentReferences: readonly string[];
  readonly timeoutMilliseconds?: number;
}

export type NormalizedMcpServer = NormalizedHttpMcp | NormalizedProcessMcp;

function secureUrl(raw: string, field: string): string {
  let url: URL;
  try {
    url = new URL(raw);
  } catch {
    throw new Error(`component_invalid:${field}`);
  }
  if (
    url.protocol !== "https:"
    || url.username !== ""
    || url.password !== ""
    || url.hash !== ""
    || url.search !== ""
  ) {
    throw new Error(`component_invalid:${field}`);
  }
  return url.toString().replace(/\/$/, raw.endsWith("/") ? "/" : "");
}

function validateCommon(name: string, digest: string): void {
  if (!COMPONENT_NAME.test(name)) throw new Error("component_invalid:mcp_name");
  if (!DIGEST.test(digest)) throw new Error("component_invalid:component_digest");
}

function normalizeWorkingDirectory(raw: string): string {
  if (
    raw.trim().length === 0
    || raw.includes("\0")
    || posix.isAbsolute(raw)
    || win32.isAbsolute(raw)
    || WINDOWS_DRIVE_PREFIX.test(raw)
  ) {
    throw new Error("component_invalid:process_cwd");
  }
  const segments = raw.replace(/\\/gu, "/").split("/");
  if (segments.includes("..")) throw new Error("component_invalid:process_cwd");
  const normalized = segments.filter((segment) => segment !== "" && segment !== ".").join("/");
  return normalized === "" ? "." : normalized;
}

export function normalizeMcpServer(name: string, input: unknown, componentDigest: string): NormalizedMcpServer {
  validateCommon(name, componentDigest);
  const parsed = McpServerSchema.safeParse(input);
  if (!parsed.success) throw new Error("component_invalid:mcp_descriptor");
  const server = parsed.data;

  if (server.type === "http") {
    const url = secureUrl(server.url, "mcp_url");
    // The selection rule itself - which of url/oauth_resource/
    // bearer_token_env_var wins - lives in `resolveHttpMcpOauthResource`
    // (credential-naming.ts), shared with Rowboat's `requiredCredentialNames`
    // (plugin-service.shared.ts), so "declares nothing" reads the same way,
    // as "still an OAuth-protected resource at its own URL", on both sides.
    const oauthResource = resolveHttpMcpOauthResource({
      url,
      ...(server.oauth_resource === undefined ? {} : { oauthResource: secureUrl(server.oauth_resource, "oauth_resource") }),
      ...(server.bearer_token_env_var === undefined ? {} : { bearerTokenEnvVar: server.bearer_token_env_var }),
    });
    const result: NormalizedHttpMcp = {
      name,
      kind: "mcp-http",
      componentDigest,
      url,
      ...(oauthResource === undefined ? {} : { oauthResource }),
      ...(server.bearer_token_env_var === undefined
        ? {}
        : { bearerTokenReference: server.bearer_token_env_var }),
    };
    return Object.freeze(result);
  }

  if (server.env !== undefined) throw new Error("component_invalid:process_environment_values");
  const command = server.command.trim();
  if (command.length === 0 || command.includes("\0")) throw new Error("component_invalid:process_command");
  const args = server.args ?? [];
  if (args.some((argument) => argument.includes("\0"))) throw new Error("component_invalid:process_args");
  const workingDirectory = server.cwd === undefined
    ? undefined
    : normalizeWorkingDirectory(server.cwd);
  const environmentReferences = server.env_vars ?? [];
  if (!environmentReferences.every((reference) => SAFE_ENV_REFERENCE.test(reference))) {
    throw new Error("component_invalid:process_environment_reference");
  }
  const timeoutMilliseconds = server.tool_timeout_sec === undefined
    ? undefined
    : server.tool_timeout_sec * 1_000;
  if (
    server.tool_timeout_sec !== undefined
    && (!Number.isSafeInteger(server.tool_timeout_sec)
      || server.tool_timeout_sec < 1
      || timeoutMilliseconds === undefined
      || !Number.isSafeInteger(timeoutMilliseconds)
      || timeoutMilliseconds > MAX_PROCESS_TIMEOUT_MS)
  ) throw new Error("component_invalid:process_timeout");
  const result: NormalizedProcessMcp = {
    name,
    kind: "mcp-process",
    componentDigest,
    command,
    args: Object.freeze([...args]),
    ...(workingDirectory === undefined ? {} : { workingDirectory }),
    environmentReferences: Object.freeze([...environmentReferences]),
    ...(timeoutMilliseconds === undefined ? {} : { timeoutMilliseconds }),
  };
  return Object.freeze(result);
}

export function pairAppAndMcp(
  app: NormalizedApp,
  mcp: NormalizedMcpServer,
  policyBinding: ProviderBinding | undefined,
): ProviderBinding {
  if (policyBinding === undefined) throw new Error("provider_binding_required");
  assertBinding(policyBinding);
  if (
    policyBinding.pairedComponentDigests === undefined
    || policyBinding.pairedComponentDigests[0] !== app.componentDigest
    || policyBinding.pairedComponentDigests[1] !== mcp.componentDigest
    || policyBinding.providerKind !== mcp.kind
  ) {
    throw new Error("provider_invalid:paired_component_digests");
  }
  return Object.freeze({
    ...policyBinding,
    pairedComponentDigests: Object.freeze([...policyBinding.pairedComponentDigests]) as readonly [string, string],
  });
}
