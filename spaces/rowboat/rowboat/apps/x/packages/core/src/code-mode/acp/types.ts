// Rowboat-facing types for the ACP code-mode engine. The schemas live in
// @x/shared (so the IPC/renderer layers share them); we re-export the inferred
// types here so the engine modules import from one local barrel.
export type {
    CodingAgent,
    ApprovalPolicy,
    PermissionDecision,
    PermissionAsk,
    CodeRunEvent,
    RunPromptResult,
} from '@x/shared/dist/code-mode.js';

// VibeMind (#330, Task 17 fix round 2, review F-1): what an ask can resolve
// to inside core. "allow_once_only" is a one-time allow from a restricted
// caller: it selects the agent's allow_once option or, when the agent offers
// none, rejects (never the agent's allow_always). Not part of the IPC schema.
export type AskDecision = import('@x/shared/dist/code-mode.js').PermissionDecision | 'allow_once_only';
