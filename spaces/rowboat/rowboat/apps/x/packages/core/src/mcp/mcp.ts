// Modified for VibeMind (Flissel/rowboat vibemind): effectiveServerUrl and the trusted-call check in executeTool (vertraute-mcp-werkzeuge.ts).
import container from "../di/container.js";
import { Client } from "@modelcontextprotocol/sdk/client";
import { SSEClientTransport } from "@modelcontextprotocol/sdk/client/sse.js";
import { StreamableHTTPClientTransport } from "@modelcontextprotocol/sdk/client/streamableHttp.js";
import z from "zod";
import { IMcpConfigRepo } from "./repo.js";
import { Transport } from "@modelcontextprotocol/sdk/shared/transport.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";
import {
    connectionState,
    ListToolsResponse,
    McpServerConfig,
    McpServerDefinition,
    McpServerList,
} from "@x/shared/dist/mcp.js";
import {
    type VertrauterMcpAufruf,
    vertrauterAufrufPasst,
} from "@x/shared/dist/vertraute-mcp-werkzeuge.js";
import { spacesMcpServers } from "../spaces/orgs.js";

type mcpState = {
    state: z.infer<typeof connectionState>,
    client: Client | null,
    error: string | null,
    /** Config snapshot the client connected with — a change forces a reconnect. */
    configKey?: string,
};
const clients: Record<string, mcpState> = {};

/**
 * The server list every consumer sees: mcp.json entries merged with entries
 * DERIVED from the spaces org registry (spaces_orgs.json) at read time. The
 * registry is the single source of truth for orgs — nothing spaces-related is
 * ever written to mcp.json, so the two can't drift. Derived entries win on a
 * name collision: a stale file entry must never shadow the live registry
 * (an old token would silently act as the wrong member).
 */
async function effectiveServers(): Promise<z.infer<typeof McpServerConfig>["mcpServers"]> {
    const repo = container.resolve<IMcpConfigRepo>('mcpConfigRepo');
    const { mcpServers } = await repo.getConfig();
    return { ...mcpServers, ...(await spacesMcpServers()) };
}

/**
 * URL of the entry getClient (and so executeTool) uses for serverName,
 * resolved the same way (effectiveServers). null for stdio entries and for
 * names without an entry. Used by the permission checker so a trust decision
 * is made on the entry that is actually executed, not on a lookalike.
 */
export async function effectiveServerUrl(serverName: string): Promise<string | null> {
    const mcpServers = await effectiveServers();
    if (!Object.prototype.hasOwnProperty.call(mcpServers, serverName)) {
        return null;
    }
    const config = mcpServers[serverName];
    if (!config || "command" in config) {
        return null;
    }
    return typeof config.url === "string" ? config.url : null;
}

async function getClient(
    serverName: string,
    // Modified for VibeMind (Flissel/rowboat vibemind): runs on the entry this
    // call is about to use, BEFORE a cached client is reused or a transport is
    // created; throwing aborts the call with nothing connected.
    pruefeEintrag?: (config: z.infer<typeof McpServerDefinition>) => void,
): Promise<Client> {
    const mcpServers = await effectiveServers();
    const config = mcpServers[serverName];
    if (!config) {
        throw new Error(`MCP server ${serverName} not found`);
    }
    pruefeEintrag?.(config);
    const configKey = JSON.stringify(config);
    const cached = clients[serverName];
    if (cached && cached.state === "connected" && cached.configKey === configKey) {
        return cached.client!;
    }
    if (cached?.client) {
        // Config changed since this client connected (token rotated, org
        // re-added, mcp.json edited) — drop it and reconnect fresh.
        try {
            await cached.client.close();
        } catch {
            // stale client; ignore
        }
        delete clients[serverName];
    }
    let transport: Transport | undefined = undefined;
    try {
        // create transport
        if ("command" in config) {
            transport = new StdioClientTransport({
                command: config.command,
                args: config.args,
                env: config.env,
            });
        } else {
            // Forward any configured headers (e.g. Authorization) so
            // auth-protected remote MCP servers can be reached.
            const requestInit = config.headers
                ? { headers: config.headers }
                : undefined;
            try {
                transport = new StreamableHTTPClientTransport(new URL(config.url), {
                    requestInit,
                });
            } catch {
                // if that fails, try sse transport
                transport = new SSEClientTransport(new URL(config.url), {
                    requestInit,
                });
            }
        }

        if (!transport) {
            throw new Error(`No transport found for ${serverName}`);
        }

        // create client
        const client = new Client({
            name: 'rowboatx',
            version: '1.0.0',
        });
        await client.connect(transport);

        // store
        clients[serverName] = {
            state: "connected",
            client,
            error: null,
            configKey,
        };
        return client;
    } catch (error) {
        clients[serverName] = {
            state: "error",
            client: null,
            error: error instanceof Error ? error.message : "Unknown error",
        };
        transport?.close();
        throw error;
    }
}

export async function cleanup() {
    for (const [serverName, { client }] of Object.entries(clients)) {
        await client?.transport?.close();
        await client?.close();
        delete clients[serverName];
    }
}

/**
 * Force-close all MCP client connections.
 * Used during force abort to immediately reject any pending MCP tool calls.
 * Clients will be lazily reconnected on next use.
 */
export async function forceCloseAllMcpClients(): Promise<void> {
    for (const [serverName, { client }] of Object.entries(clients)) {
        try {
            await client?.close();
        } catch {
            // Ignore errors during force close
        }
        delete clients[serverName];
    }
}

export async function listServers(): Promise<z.infer<typeof McpServerList>> {
    const mcpServers = await effectiveServers();
    const result: z.infer<typeof McpServerList> = {
        mcpServers: {},
    };
    for (const [serverName, config] of Object.entries(mcpServers)) {
        const state = clients[serverName];
        result.mcpServers[serverName] = {
            config,
            state: state ? state.state : "disconnected",
            error: state ? state.error : null,
        };
    }
    return result;
}

export async function listTools(serverName: string, cursor?: string): Promise<z.infer<typeof ListToolsResponse>> {
    const client = await getClient(serverName);
    const { tools, nextCursor } = await client.listTools({
        cursor,
    });
    return {
        tools,
        nextCursor,
    }
}

/**
 * opts.vertraut: set ONLY for a call the permission checker let through
 * without a prompt (trusted MCP list). The entry this call connects with must
 * then still be the checked, trusted one — otherwise (mcp.json changed between
 * check and call, e.g. to another URL or a stdio command) the call is refused
 * before any client is reused or created. Calls without the marker (human or
 * classifier approved, legacy paths) run as before.
 */
export async function executeTool(
    serverName: string,
    toolName: string,
    input: Record<string, unknown>,
    opts: { vertraut?: VertrauterMcpAufruf } = {},
): Promise<unknown> {
    const { vertraut } = opts;
    const client = await getClient(
        serverName,
        vertraut
            ? (config) => {
                  const serverUrl = "command" in config ? null : config.url;
                  if (!vertrauterAufrufPasst(vertraut, { serverName, toolName, serverUrl })) {
                      throw new Error(
                          `Der Eintrag ${serverName} hat sich seit der Prüfung geändert. Aufruf abgebrochen.`,
                      );
                  }
              }
            : undefined,
    );
    const result = await client.callTool({
        name: toolName,
        arguments: input,
    });
    return result;
}
