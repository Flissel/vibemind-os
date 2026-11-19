// Modified for VibeMind (Flissel/rowboat vibemind): who answered (ticket #330, Task 17 fix round 2, review F-1).
import type { AskDecision, PermissionDecision } from './types.js';

interface Pending {
    runId: string;
    resolve: (decision: AskDecision) => void;
}

// VibeMind (#330, Task 17 fix round 2, review F-1): a restricted caller (a
// scoped server key, the embed bridge) answers as "delegated". It may reject
// or allow this one request; "allow_always" would leave a standing approval
// for the rest of the ACP session and needs the human.
export const DELEGATED_ALLOW_ALWAYS_REFUSAL =
    "allow_always needs the human's own answer; this caller may only allow once or reject.";

// Holds in-flight mid-run permission asks. The agent (via the broker) calls
// request() which BLOCKS the coding turn until the user answers; the renderer's
// answer arrives over IPC and calls resolve(). This is separate from the LLM
// tool-loop's pre-call permission gate, which can't model a mid-execution wait.
export class CodePermissionRegistry {
    private readonly pending = new Map<string, Pending>();
    private counter = 0;

    // Register a pending ask, hand the generated requestId to `emit` (so the caller
    // can publish the UI event), and resolve once the user answers.
    request(runId: string, emit: (requestId: string) => void): Promise<AskDecision> {
        const requestId = `cpr-${runId}-${++this.counter}`;
        return new Promise<AskDecision>((resolve) => {
            this.pending.set(requestId, { runId, resolve });
            emit(requestId);
        });
    }

    // Called from the IPC handler when the user answers a card. A delegated
    // allow_always throws and leaves the ask pending; a delegated allow_once
    // resolves as "allow_once_only" (the broker never maps it onto the
    // agent's own allow_always option). `answeredBy` has no default: every
    // caller states who answered (both IPC hosts pass the caller's kind).
    resolve(requestId: string, decision: PermissionDecision, answeredBy: 'human' | 'delegated'): void {
        const entry = this.pending.get(requestId);
        if (!entry) return;
        if (answeredBy === 'delegated' && decision === 'allow_always') {
            throw new Error(DELEGATED_ALLOW_ALWAYS_REFUSAL);
        }
        this.pending.delete(requestId);
        entry.resolve(answeredBy === 'delegated' && decision === 'allow_once' ? 'allow_once_only' : decision);
    }

    // On run stop/cancel: reject anything still waiting so the turn can unwind.
    cancelRun(runId: string): void {
        for (const [id, entry] of [...this.pending]) {
            if (entry.runId === runId) {
                this.pending.delete(id);
                entry.resolve('reject');
            }
        }
    }
}
