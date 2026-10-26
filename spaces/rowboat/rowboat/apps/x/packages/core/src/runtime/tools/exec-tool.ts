// Modified for VibeMind (Flissel/rowboat vibemind): ToolContext.channel / autorunContentForbidden / headlessSandbox, channel quarantine (Teil E).
import { ToolAttachment } from "@x/shared/dist/agent.js";
import { RunEvent } from "@x/shared/dist/runs.js";
import { z } from "zod";
import { BuiltinTools } from "./catalog.js";
import { executeTool } from "../../mcp/mcp.js";
import { IAbortRegistry } from "../turns/abort-registry.js";
import { sandboxChecker, type HeadlessSandbox } from "./headless-sandbox.js";
import { quarantineChecker, quarantineMode } from "./channel-quarantine.js";

/**
 * Context passed to every tool execution, providing abort signal and run metadata.
 */
export interface ToolContext {
    runId: string;
    // The chat session the triggering turn belongs to (turns runtime); null
    // for standalone/headless turns and legacy runs. code_agent_run keys its
    // persistent ACP session on this (context survives across turns) and uses
    // it to resolve Code-section session pins (cwd/agent/policy/model).
    sessionId?: string | null;
    toolCallId: string;
    signal: AbortSignal;
    abortRegistry: IAbortRegistry;
    publish: (event: z.infer<typeof RunEvent>) => Promise<void>;
    // The composer code-mode chip for the message that triggered this turn. When set,
    // it is the authoritative coding agent — code_agent_run uses it rather than the
    // agent the model guessed, so switching the chip deterministically switches agents.
    codeMode?: 'claude' | 'codex' | null;
    // Set for Code-section sessions: the session's working directory and approval
    // policy. code_agent_run honors these over the model's cwd argument and the
    // global approval policy.
    codeCwd?: string | null;
    codePolicy?: 'ask' | 'auto-approve-reads' | 'yolo' | null;
    // Modified for VibeMind (Flissel/rowboat vibemind): set for turns started
    // from a mobile channel (turn_created.config.channel). Builtins apply
    // their execution-time channel restrictions when present (file-grep /
    // file-glob read filter, fetch-url host + redirect checks).
    channel?: { readOnlyMcpTools: string[] };
    // Modified for VibeMind (Flissel/rowboat vibemind): true for channel
    // runs and every turn that is not a desktop user chat (background agents
    // such as note tagging / curation). The file write builtins then refuse
    // to introduce autorun content (live / live_note frontmatter, task
    // fences) — see knowledge/autorun-write-check.ts.
    autorunContentForbidden?: boolean;
    // Set for runs no user is watching (headless, legacy engine, channel):
    // file builtins are confined to the sandbox (headless-sandbox.ts).
    headlessSandbox?: HeadlessSandbox;
}

async function execMcpTool(agentTool: z.infer<typeof ToolAttachment> & { type: "mcp" }, input: Record<string, unknown>): Promise<unknown> {
    const result = await executeTool(agentTool.mcpServerName, agentTool.name, input);
    return result;
}

export async function execTool(agentTool: z.infer<typeof ToolAttachment>, input: Record<string, unknown>, ctx?: ToolContext): Promise<unknown> {
    // Check abort before starting any tool
    ctx?.signal.throwIfAborted();

    switch (agentTool.type) {
        case "mcp":
            // MCP tools: let complete on graceful stop (most are fast)
            return execMcpTool(agentTool, input);
        case "builtin": {
            const builtinTool = BuiltinTools[agentTool.name];
            if (!builtinTool || !builtinTool.execute) {
                throw new Error(`Unsupported builtin tool: ${agentTool.name}`);
            }
            if (ctx?.headlessSandbox) {
                const refused = await sandboxChecker().toolError(agentTool.name, input, ctx.headlessSandbox);
                if (refused) return { error: refused };
            }
            // Channel quarantine knowledge/Kanal/ (fix round 4): channel runs
            // write only there, other non-desktop runs never touch it.
            const mode = quarantineMode(ctx);
            if (mode) {
                const refused = await quarantineChecker().toolError(agentTool.name, input, mode);
                if (refused) return { error: refused };
            }
            return builtinTool.execute(input, ctx);
        }
    }
}
