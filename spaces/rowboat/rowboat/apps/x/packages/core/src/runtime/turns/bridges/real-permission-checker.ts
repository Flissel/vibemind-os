import { z } from "zod";
// Modified for VibeMind (Flissel/rowboat vibemind): trusted MCP tools (vertraute-mcp-werkzeuge.ts) need no prompt.
import type { JsonValue } from "@x/shared/dist/turns.js";
import {
    istVertrauterMcpName,
    vertrautesMcpWerkzeug,
} from "@x/shared/dist/vertraute-mcp-werkzeuge.js";
import { effectiveServerUrl } from "../../../mcp/mcp.js";
import { getToolPermissionMetadata } from "../../assembly/permission-metadata.js";
import { BuiltinTools } from "../../tools/catalog.js";
import type { BuiltinToolPermission } from "../../tools/types.js";
import type {
    IPermissionChecker,
    PermissionCheckAllowed,
    PermissionCheckInput,
    PermissionCheckRequired,
} from "../permission.js";

type BuiltinPolicy = z.infer<typeof BuiltinToolPermission>;

export interface RealPermissionCheckerDeps {
    getMetadata?: typeof getToolPermissionMetadata;
    getPolicy?: (name: string) => BuiltinPolicy | undefined;
    // URL of the MCP server entry executeTool would connect to for a server
    // name (null: stdio entry or no entry). Default: mcp.ts effectiveServerUrl,
    // the same resolution getClient uses.
    getMcpServerUrl?: (serverName: string) => Promise<string | null>;
}

const ComposioExecuteInput = z.object({
    toolkitSlug: z.string(),
    toolSlug: z.string(),
});

const McpExecuteInput = z.object({
    serverName: z.string(),
    toolName: z.string(),
});

// Bridges the deterministic permission rules. Policy is declared per tool in
// the builtin catalog (tools/types.ts) and the checker FAILS CLOSED: any
// tool without a "none" declaration — undeclared builtins, mcp:* attachments
// on user agents, future toolId families — requires permission. Sole
// exception: the trusted MCP tools of vertraute-mcp-werkzeuge.ts (on either
// MCP path, only when the executed server entry has the trusted URL). Session
// grants are deferred, so the session grant inputs are always empty. A
// thrown metadata error propagates: the turn loop fails closed on checker
// errors.
export class RealPermissionChecker implements IPermissionChecker {
    private readonly getMetadata: typeof getToolPermissionMetadata;
    private readonly getPolicy: (name: string) => BuiltinPolicy | undefined;
    private readonly getMcpServerUrl: (serverName: string) => Promise<string | null>;

    constructor(deps: RealPermissionCheckerDeps = {}) {
        this.getMetadata = deps.getMetadata ?? getToolPermissionMetadata;
        this.getPolicy =
            deps.getPolicy ?? ((name) => BuiltinTools[name]?.permission);
        this.getMcpServerUrl = deps.getMcpServerUrl ?? effectiveServerUrl;
    }

    // The one exception to "every MCP call prompts": the code-constant list
    // in vertraute-mcp-werkzeuge.ts, matched on server name, tool name AND
    // the URL of the entry the executor resolves for that name. Other names
    // never trigger a config read; a failed lookup fails closed. The allowed
    // answer carries the checked URL (trustedMcp): the runtime logs it and
    // the executor refuses if the entry it connects with no longer matches.
    private async vertraut(
        serverName: string,
        toolName: string,
    ): Promise<PermissionCheckAllowed | null> {
        if (!istVertrauterMcpName(serverName, toolName)) {
            return null;
        }
        let serverUrl: string | null;
        try {
            serverUrl = await this.getMcpServerUrl(serverName);
        } catch {
            return null;
        }
        if (serverUrl === null || !vertrautesMcpWerkzeug({ serverName, toolName, serverUrl })) {
            return null;
        }
        return { required: false, trustedMcp: { serverName, toolName, url: serverUrl } };
    }

    async check(
        input: PermissionCheckInput,
    ): Promise<PermissionCheckAllowed | PermissionCheckRequired> {
        if (!input.toolId.startsWith("builtin:")) {
            // mcp:<server>:<tool> attachments on user agents, and any future
            // toolId family: fail closed with a family-specific request.
            if (input.toolId.startsWith("mcp:")) {
                const executed = parseMcpToolId(input.toolId);
                const trusted = executed
                    ? await this.vertraut(executed.server, executed.tool)
                    : null;
                if (trusted) {
                    return trusted;
                }
                const serverName = input.toolId.split(":")[1];
                return {
                    required: true,
                    request: {
                        kind: "mcp",
                        ...(serverName ? { serverName } : {}),
                        toolName: input.toolName,
                    },
                };
            }
            return { required: true, request: genericRequest(input) };
        }

        const name = input.toolId.slice("builtin:".length);
        const policy = this.getPolicy(name);
        switch (policy) {
            case "none":
                return { required: false };
            case "prompt":
                return { required: true, request: genericRequest(input) };
            case "composio-execute": {
                const parsed = ComposioExecuteInput.safeParse(input.input);
                return {
                    required: true,
                    request: parsed.success
                        ? {
                              kind: "composio",
                              toolkitSlug: parsed.data.toolkitSlug,
                              toolSlug: parsed.data.toolSlug,
                          }
                        : genericRequest(input),
                };
            }
            case "mcp-execute": {
                const parsed = McpExecuteInput.safeParse(input.input);
                const trusted = parsed.success
                    ? await this.vertraut(parsed.data.serverName, parsed.data.toolName)
                    : null;
                if (trusted) {
                    return trusted;
                }
                return {
                    required: true,
                    request: parsed.success
                        ? {
                              kind: "mcp",
                              serverName: parsed.data.serverName,
                              toolName: parsed.data.toolName,
                          }
                        : genericRequest(input),
                };
            }
            case "command-allowlist":
            case "file-boundary": {
                const metadata = await this.getMetadata(
                    {
                        type: "tool-call",
                        toolCallId: input.toolCallId,
                        toolName: input.toolName,
                        arguments: input.input,
                    },
                    { type: "builtin", name },
                    new Set<string>(), // session-scoped command grants: deferred
                    [], // session-scoped file grants: deferred
                );
                if (!metadata) {
                    return { required: false };
                }
                return { required: true, request: metadata as JsonValue };
            }
            case undefined:
                // Not in the catalog (or missing a declaration at runtime):
                // fail closed rather than silently executing.
                return { required: true, request: genericRequest(input) };
        }
    }
}

// Same split as RealToolRegistry.resolve: server = up to the FIRST ":" after
// "mcp:", tool = the rest (the tool that is executed, not input.toolName).
function parseMcpToolId(toolId: string): { server: string; tool: string } | null {
    const rest = toolId.slice("mcp:".length);
    const separator = rest.indexOf(":");
    if (separator <= 0 || separator === rest.length - 1) {
        return null;
    }
    return { server: rest.slice(0, separator), tool: rest.slice(separator + 1) };
}

function genericRequest(input: PermissionCheckInput): JsonValue {
    return { kind: "tool", toolId: input.toolId, toolName: input.toolName };
}
