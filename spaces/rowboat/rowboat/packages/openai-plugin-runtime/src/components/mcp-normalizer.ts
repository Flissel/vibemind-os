import { isAbsolute } from "node:path";
import { McpServerSchema } from "../schema/component-schemas.js";
import { assertBinding, } from "../providers/provider-registry.js";
import type { ProviderBinding } from "../providers/provider.js";
import type { NormalizedApp } from "./app-normalizer.js";

const DIGEST = /^[a-f0-9]{64}$/;
const COMPONENT_NAME = /^[A-Za-z0-9][A-Za-z0-9._ -]*$/;
const SAFE_ENV_REFERENCE = /^[A-Z][A-Z0-9_]*$/;

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
  readonly timeoutSeconds?: number;
}

export type NormalizedMcpServer = NormalizedHttpMcp | NormalizedProcessMcp;

function secureUrl(raw: string, field: string): string {
  let url: URL;
  try {
    url = new URL(raw);
  } catch {
    throw new Error(`component_invalid:${field}`);
  }
  if (url.protocol !== "https:" || url.username !== "" || url.password !== "" || url.hash !== "") {
    throw new Error(`component_invalid:${field}`);
  }
  return url.toString().replace(/\/$/, raw.endsWith("/") ? "/" : "");
}

function validateCommon(name: string, digest: string): void {
  if (!COMPONENT_NAME.test(name)) throw new Error("component_invalid:mcp_name");
  if (!DIGEST.test(digest)) throw new Error("component_invalid:component_digest");
}

export function normalizeMcpServer(name: string, input: unknown, componentDigest: string): NormalizedMcpServer {
  validateCommon(name, componentDigest);
  const parsed = McpServerSchema.safeParse(input);
  if (!parsed.success) throw new Error("component_invalid:mcp_descriptor");
  const server = parsed.data;

  if (server.type === "http") {
    const result: NormalizedHttpMcp = {
      name,
      kind: "mcp-http",
      componentDigest,
      url: secureUrl(server.url, "mcp_url"),
      ...(server.oauth_resource === undefined
        ? {}
        : { oauthResource: secureUrl(server.oauth_resource, "oauth_resource") }),
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
  const workingDirectory = server.cwd;
  if (
    workingDirectory !== undefined
    && (workingDirectory.trim().length === 0
      || workingDirectory.includes("\0")
      || isAbsolute(workingDirectory)
      || workingDirectory.split(/[\\/]/u).includes(".."))
  ) {
    throw new Error("component_invalid:process_cwd");
  }
  const environmentReferences = server.env_vars ?? [];
  if (!environmentReferences.every((reference) => SAFE_ENV_REFERENCE.test(reference))) {
    throw new Error("component_invalid:process_environment_reference");
  }
  if (server.tool_timeout_sec !== undefined && !Number.isFinite(server.tool_timeout_sec)) {
    throw new Error("component_invalid:process_timeout");
  }
  const result: NormalizedProcessMcp = {
    name,
    kind: "mcp-process",
    componentDigest,
    command,
    args: Object.freeze([...args]),
    ...(workingDirectory === undefined ? {} : { workingDirectory }),
    environmentReferences: Object.freeze([...environmentReferences]),
    ...(server.tool_timeout_sec === undefined ? {} : { timeoutSeconds: server.tool_timeout_sec }),
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
