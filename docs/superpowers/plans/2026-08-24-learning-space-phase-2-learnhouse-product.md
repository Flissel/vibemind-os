# Phase 2: LearnHouse Product and Semantic UI

> **For agentic workers:** Preserve LearnHouse ownership boundaries and upstream tests. All UI actions must use application APIs and revisioned state.

**Goal:** Turn LearnHouse into the local VibeMind Course Studio and Learning Player with approved layout A and complete semantic MCP operations.

## Task 1: Add Learning API Adapters

**Files:** `spaces/learning/bridge/learnhouse_client.py`, `bridge/ui_bridge.py`, `spaces/learning/tests/unit/test_learnhouse_client.py`, `test_ui_bridge.py`

- [ ] Write failing adapter tests for course list/create/open, chapter open, source import, review/publish, session/task/progress reads, HTTP errors, stale revisions, and malformed payloads.
- [ ] Implement typed loopback clients with timeouts, bounded retry for reads only, correlation headers, and no secret exposure.
- [ ] Map LearnHouse responses to frozen Learning contracts; do not leak upstream response shapes through MCP.
- [ ] Commit: `feat(learning): add typed LearnHouse bridge`.

## Task 2: Establish Local Single-User Product Mode

**Files:** `spaces/learning/learnhouse/apps/api/src/core/learning_mode.py`, `spaces/learning/learnhouse/apps/api/src/routers/learning/bootstrap.py`, `spaces/learning/learnhouse/apps/api/src/tests/learning/test_bootstrap.py`, `spaces/learning/learnhouse/apps/web/lib/learning/bootstrap.ts`, `spaces/learning/learnhouse/apps/web/lib/learning/bootstrap.test.ts`

- [ ] Write failing tests requiring a deterministic local owner, organization, locale, and default permissions while rejecting non-loopback bootstrap without explicit configuration.
- [ ] Implement idempotent bootstrap through supported LearnHouse services, not direct table mutation.
- [ ] Keep authorization checks active; single-user means one provisioned owner, not disabled auth.
- [ ] Commit in the LearnHouse fork: `feat: add VibeMind local learning mode`; update the parent submodule pin in a separate `build(learning): update LearnHouse pin` commit.

## Task 3: Complete Course and Navigation MCP Tools

**Files:** `spaces/learning/mcp/tools/courses.py`, `mcp/tools/navigation.py`, `mcp/server.py`, `spaces/learning/tests/contract/test_course_tools.py`, `test_navigation_tools.py`

- [ ] Write failing tests for list/create/open/import/review/publish and chapter/progress operations, including confirmation, idempotency, revision conflict, and backend failure.
- [ ] Implement tool handlers through `LearnHouseClient`, then read the aggregate back before returning success.
- [ ] Emit `navigate`, `open_course`, `open_chapter`, `show_progress`, or `show_error` only after terminal readback.
- [ ] Prove duplicate writes return the original receipt and do not call LearnHouse twice.
- [ ] Commit: `feat(learning): connect course MCP operations`.

## Task 4: Build the Approved Learning Shell

**Files:** `spaces/learning/learnhouse/apps/web/app/orgs/[orgslug]/(withmenu)/learning/page.tsx`, `spaces/learning/learnhouse/apps/web/components/Learning/LearningShell.tsx`, `spaces/learning/learnhouse/apps/web/components/Learning/SpaceRail.tsx`, `spaces/learning/learnhouse/apps/web/components/Learning/CourseOutline.tsx`, `spaces/learning/learnhouse/apps/web/components/Learning/LearningWorkspace.tsx`, `spaces/learning/learnhouse/apps/web/components/Learning/AgentPanel.tsx`, `spaces/learning/learnhouse/apps/web/components/Learning/StatusStrip.tsx`, `spaces/learning/learnhouse/apps/web/components/Learning/LearningShell.test.tsx`

- [ ] Write component tests for outer Space rail, inner course navigation, center workspace, right Agent panel, permanent status strip, loading/empty/error/degraded states, and keyboard/focus behavior.
- [ ] Implement layout A with existing LearnHouse/Tailwind tokens, Lucide icons, restrained student-facing styling, responsive collapse, and no nested decorative cards.
- [ ] Ensure long course/chapter labels wrap without overlap at desktop and compact widths.
- [ ] Commit in LearnHouse fork: `feat: add VibeMind learning workspace` and update the parent pin.

## Task 5: Apply Revision-Safe UI Intents

**Files:** `spaces/learning/learnhouse/apps/web/services/learning/intents.ts`, `spaces/learning/learnhouse/apps/web/hooks/useLearningIntents.ts`, `spaces/learning/learnhouse/apps/web/services/learning/intents.test.ts`, `spaces/learning/bridge/ui_bridge.py`, `spaces/learning/tests/integration/test_ui_events.py`

- [ ] Write failing tests for authenticated local SSE/WebSocket connection, stale-intent rejection, reconnect, duplicate event suppression, focus intents, and reload reconstruction.
- [ ] Implement strict TypeScript decoding and aggregate revision comparison; unknown intents are ignored and recorded through the project logger.
- [ ] Ensure UI bridge failure is reported separately from successful backend mutation.
- [ ] Add integration proof that reload obtains state from APIs rather than renderer memory.
- [ ] Commit: `feat(learning): project revisioned UI intents` plus the corresponding LearnHouse pin update.

## Task 6: Add Course Studio and Player States

**Files:** `spaces/learning/learnhouse/apps/web/components/Learning/CourseStudio.tsx`, `spaces/learning/learnhouse/apps/web/components/Learning/GenerationReview.tsx`, `spaces/learning/learnhouse/apps/web/components/Learning/LearningPlayer.tsx`, `spaces/learning/learnhouse/apps/web/components/Learning/TaskRenderer.tsx`, `spaces/learning/learnhouse/apps/web/components/Learning/SourceEvidence.tsx`, `spaces/learning/learnhouse/apps/web/components/Learning/CourseStudio.test.tsx`, `spaces/learning/learnhouse/apps/web/components/Learning/LearningPlayer.test.tsx`, `spaces/learning/learnhouse/apps/web/tests/e2e/learning.spec.ts`

- [ ] Write failing tests for source import, ingestion errors, generation progress, unsupported claims, bounded regeneration, explicit publish, question entry, feedback, hints, and progress.
- [ ] Implement complete manual controls alongside MCP control; use established LearnHouse activity APIs.
- [ ] Add training/exam visual states without implementing adaptive policy yet.
- [ ] Run Playwright at 1440x900, 1024x768, and 390x844; capture screenshots and verify no overlap, inaccessible controls, or clipped text.
- [ ] Commit in LearnHouse fork: `feat: add course studio and learning player` and update the parent pin.

## Phase Gate

```powershell
python -m pytest spaces/learning/tests/contract/test_course_tools.py spaces/learning/tests/integration/test_ui_events.py -q
bun --cwd spaces/learning/learnhouse/apps/web test
bun --cwd spaces/learning/learnhouse/apps/web run build
bunx --cwd spaces/learning/learnhouse/apps/web playwright test tests/e2e/learning.spec.ts
git diff --check
```

Required evidence: semantic course workflow, stale-intent protection, API reconstruction, responsive screenshots, and explicit publication confirmation.
