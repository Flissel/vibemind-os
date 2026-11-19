// Modified for VibeMind (Flissel/rowboat vibemind): executeMcpTool forwards ToolContext.trustedMcp to executeTool; addMcpServer writes a stdio entry only with ToolContext.humanApproved (#330 Task 17).
// Builtin tools: mcp domain. Entries moved VERBATIM from the historical
// monolith — the merge order in ../builtin-tools.ts preserves the original
// catalog key order (provider-payload bytes; see the key-order test there).

import { z } from "zod";
import type { ToolContext } from "../exec-tool.js";
import { executeTool, listServers, listTools } from "../../../mcp/mcp.js";
import container from "../../../di/container.js";
import { IMcpConfigRepo } from "../../../mcp/repo.js";
import { approveStdioEntry } from "../../../mcp/stdio-approvals.js";
import { McpServerDefinition } from "@x/shared/dist/mcp.js";
import { BuiltinToolsSchema } from "../types.js";


export const mcpTools: z.infer<typeof BuiltinToolsSchema> = {
    addMcpServer: {
        permission: "prompt",
        description: 'Add or update an MCP server in the configuration with validation. This ensures the server definition is valid before saving.',
        inputSchema: z.object({
            serverName: z.string().describe('Name/alias for the MCP server'),
            config: McpServerDefinition,
        }),
        execute: async ({ serverName, config }: {
            serverName: string;
            config: z.infer<typeof McpServerDefinition>;
        }, ctx?: ToolContext) => {
            try {
                const validationResult = McpServerDefinition.safeParse(config);
                if (!validationResult.success) {
                    return {
                        success: false,
                        message: 'Server definition failed validation. Check the errors below.',
                        validationErrors: validationResult.error.issues.map((e) => `${e.path.join('.')}: ${e.message}`),
                        providedDefinition: config,
                    };
                }
                const definition = validationResult.data;

                // VibeMind (ticket #330, Task 17): a stdio entry starts a
                // process, so it is written (and approved to start) only when
                // a human approved this very call. The legacy engine and
                // classifier-allowed calls never set humanApproved. URL
                // entries keep today's behaviour.
                const isStdio = "command" in definition;
                if (isStdio && ctx?.humanApproved !== true) {
                    return {
                        success: false,
                        error: 'Adding an MCP server that starts a local command needs the human\'s approval in the permission prompt; it was not given for this call. The human can add it in Settings.',
                    };
                }

                const repo = container.resolve<IMcpConfigRepo>('mcpConfigRepo');
                await repo.upsert(serverName, definition);
                if (isStdio) {
                    approveStdioEntry(serverName, definition);
                }

                return {
                    success: true,
                    serverName,
                };
            } catch (error) {
                return {
                    error: `Failed to update MCP server: ${error instanceof Error ? error.message : 'Unknown error'}`,
                };
            }
        },
    },

    listMcpServers: {
        permission: "none",
        description: 'List all available MCP servers from the configuration',
        inputSchema: z.object({}),
        execute: async () => {
            try {
                const result = await listServers();
                
                return {
                    result,
                    count: Object.keys(result.mcpServers).length,
                };
            } catch (error) {
                return {
                    error: `Failed to list MCP servers: ${error instanceof Error ? error.message : 'Unknown error'}`,
                };
            }
        },
    },

    listMcpTools: {
        permission: "none",
        description: 'List all available tools from a specific MCP server',
        inputSchema: z.object({
            serverName: z.string().describe('Name of the MCP server to query'),
            cursor: z.string().optional(),
        }),
        execute: async ({ serverName, cursor }: { serverName: string, cursor?: string }) => {
            try {
                const result = await listTools(serverName, cursor);
                return {
                    serverName,
                    result,
                    count: result.tools.length,
                };
            } catch (error) {
                return {
                    error: `Failed to list MCP tools: ${error instanceof Error ? error.message : 'Unknown error'}`,
                };
            }
        },
    },

    executeMcpTool: {
        permission: "mcp-execute",
        description: 'Execute a specific tool from an MCP server. Use this to run MCP tools on behalf of the user. IMPORTANT: Always use listMcpTools first to get the tool\'s inputSchema, then match the required parameters exactly in the arguments field.',
        inputSchema: z.object({
            serverName: z.string().describe('Name of the MCP server that provides the tool'),
            toolName: z.string().describe('Name of the tool to execute'),
            arguments: z.record(z.string(), z.any()).optional().describe('Arguments to pass to the tool (as key-value pairs matching the tool\'s input schema). MUST include all required parameters from the tool\'s inputSchema.'),
        }),
        execute: async ({ serverName, toolName, arguments: args = {} }: { serverName: string, toolName: string, arguments?: Record<string, unknown> }, ctx?: ToolContext) => {
            try {
                // VibeMind: a prompt-free (trusted) call connects only if the
                // entry used now still has the checked URL (mcp.ts executeTool).
                const result = await executeTool(
                    serverName,
                    toolName,
                    args,
                    ctx?.trustedMcp ? { vertraut: ctx.trustedMcp } : {},
                );
                return {
                    success: true,
                    serverName,
                    toolName,
                    result,
                    message: `Successfully executed tool '${toolName}' from server '${serverName}'`,
                };
            } catch (error) {
                return {
                    success: false,
                    error: `Failed to execute MCP tool: ${error instanceof Error ? error.message : 'Unknown error'}`,
                    hint: 'Use listMcpTools to verify the tool exists and check its schema. Ensure all required parameters are provided in the arguments field.',
                };
            }
        },
    },
};
