// Modified for VibeMind (Flissel/rowboat vibemind): ToolExecutionContext.channel (Teil E).
import type { z } from "zod";
import type {
    JsonValue,
    ToolDescriptor,
    ToolResultData,
} from "@x/shared/dist/turns.js";

export interface ToolExecutionContext {
    turnId: string;
    // The chat session this turn belongs to; null for standalone (headless)
    // turns. Tools that keep per-conversation state (e.g. code_agent_run's
    // persistent ACP session) key on this so context survives across turns.
    sessionId: string | null;
    toolCallId: string;
    signal: AbortSignal;
    // The loop appends a durable tool_progress event before resolving.
    reportProgress(progress: JsonValue): Promise<void>;
    // Modified for VibeMind (Flissel/rowboat vibemind): the turn's
    // config.channel for channel-origin turns (Teil E); absent otherwise.
    channel?: { readOnlyMcpTools: string[] };
    // True unless the turn is a desktop user chat: write tools then refuse
    // to introduce autorun content (knowledge/autorun-write-check.ts).
    autorunContentForbidden?: boolean;
    // Set for headless and channel runs: file builtins are confined to this
    // sandbox (runtime/tools/headless-sandbox.ts).
    headlessSandbox?: { ownFiles: string[]; ownDirs: string[]; readDirs: string[] };
}

export interface SyncRuntimeTool {
    descriptor: z.infer<typeof ToolDescriptor> & { execution: "sync" };
    execute(
        input: unknown,
        context: ToolExecutionContext,
    ): Promise<z.infer<typeof ToolResultData>>;
}

// An async tool has no in-process executor. Its invocation is exposed
// externally and its progress/result arrives later through advanceTurn.
export interface AsyncRuntimeTool {
    descriptor: z.infer<typeof ToolDescriptor> & { execution: "async" };
}

export type RuntimeTool = SyncRuntimeTool | AsyncRuntimeTool;

// Resolves persisted tool descriptors to live implementations during
// advanceTurn. A rejection or a descriptor mismatch is an infrastructure
// error: the execution is rejected and the turn is left unchanged.
export interface IToolRegistry {
    resolve(descriptor: z.infer<typeof ToolDescriptor>): Promise<RuntimeTool>;
}
