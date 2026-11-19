import type { z } from "zod";
import type { ConversationMessage, JsonValue } from "@x/shared/dist/turns.js";
import type { VertrauterMcpAufruf } from "@x/shared/dist/vertraute-mcp-werkzeuge.js";

export interface PermissionCheckInput {
    turnId: string;
    toolCallId: string;
    toolId: string;
    toolName: string;
    input: unknown;
}

export interface PermissionCheckAllowed {
    required: false;
    // Modified for VibeMind (Flissel/rowboat vibemind): set when the call is
    // allowed only because it is on the trusted MCP list. The runtime records
    // it on the invocation and hands it to the executor, which refuses if the
    // entry it is about to use no longer matches.
    trustedMcp?: VertrauterMcpAufruf;
}

export interface PermissionCheckRequired {
    required: true;
    // Presentation payload persisted on tool_permission_required and shown to
    // the human/classifier.
    request: JsonValue;
}

// Tool-specific policy (command analysis, filesystem boundaries, allowlists)
// lives behind this seam, outside the loop. A thrown error fails closed: the
// call is recorded as permission-required and never executes automatically.
export interface IPermissionChecker {
    check(
        input: PermissionCheckInput,
    ): Promise<PermissionCheckAllowed | PermissionCheckRequired>;
}

// Modified for VibeMind (Flissel/rowboat vibemind): ticket #330. A request
// flagged humanOnly (a write to config/, keys, channels/, ...) is never sent
// to the classifier, is denied when no human is available, and no grant is
// persisted for it. Anything that is not an object with humanOnly === true
// is an ordinary request.
export function isHumanOnlyRequest(request: unknown): boolean {
    return (
        typeof request === "object" &&
        request !== null &&
        !Array.isArray(request) &&
        (request as { humanOnly?: unknown }).humanOnly === true
    );
}

// The legacy engine's auto mode: human-only candidates go straight to the
// human prompt, only the others to the classifier.
export function partitionHumanOnly<T extends { permission?: unknown }>(
    candidates: T[],
): { humanOnly: T[]; classifiable: T[] } {
    const humanOnly: T[] = [];
    const classifiable: T[] = [];
    for (const candidate of candidates) {
        (isHumanOnlyRequest(candidate.permission) ? humanOnly : classifiable).push(candidate);
    }
    return { humanOnly, classifiable };
}

export interface PermissionClassificationInput {
    toolCallId: string;
    toolName: string;
    input: unknown;
    request: JsonValue;
}

export interface PermissionClassification {
    toolCallId: string;
    decision: "allow" | "deny" | "defer";
    reason: string;
}

export interface PermissionClassificationBatch {
    turnId: string;
    // Conversation context for the classifier: the turn's resolved context
    // plus current-turn settled messages.
    messages: Array<z.infer<typeof ConversationMessage>>;
    requests: PermissionClassificationInput[];
}

// Handles all permission-required calls from one model response in one batch
// when automatic permission is enabled. Internal model calls are opaque to
// the turn loop. Failures and omitted decisions normalize to defer.
export interface IPermissionClassifier {
    classify(
        batch: PermissionClassificationBatch,
        signal: AbortSignal,
    ): Promise<PermissionClassification[]>;
}
