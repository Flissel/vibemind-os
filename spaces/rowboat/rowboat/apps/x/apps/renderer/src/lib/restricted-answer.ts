// Modified for VibeMind (Flissel/rowboat vibemind): ticket #330, Task 17 fix
// round 2 (review m-3). The host refuses some answers from a restricted caller
// (the embedded view): a human-only Allow, a session/always scope, a standing
// code-run approval, or a protected workspace entry. These are the refusal
// texts of core's runtimes, the code-run registry and the restricted gate; the
// prompt card uses this to tell the user where to answer instead.
const REFUSAL = /human's own answer|may not answer with scope|this caller may not read or change protected workspace entries/i

export function isRestrictedAnswerRefusal(error: unknown): boolean {
  return error instanceof Error && REFUSAL.test(error.message)
}

export const RESTRICTED_ANSWER_HINT = 'This answer is not accepted here. Answer this in the Rowboat window.'
