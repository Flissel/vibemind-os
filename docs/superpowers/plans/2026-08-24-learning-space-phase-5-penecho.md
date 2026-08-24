# Phase 5: PenEcho Canvas Activities

> **For agentic workers:** Treat PenEcho as a versioned learning artifact provider. A saved canvas or AI response is not a graded success without Learning-owned readback.

**Goal:** Make PenEcho a resumable, source-aware, rubric-graded LearnHouse activity controlled through semantic MCP operations.

## Task 1: Freeze the Canvas Contract

**Files:** `spaces/learning/contracts/penecho.py`, `spaces/learning/tests/contract/test_penecho_contract.py`

- [ ] Write failing tests for launch context, help policy, mode, source references, canvas revision, artifact metadata, structured objects, rendered snapshot, rubric result, confidence, and misconceptions.
- [ ] Implement strict versioned models and maximum payload/locator constraints.
- [ ] Reject data URLs, provider credentials, arbitrary origins, and unversioned saves at the Learning boundary.
- [ ] Commit: `feat(learning): define PenEcho activity contract`.

## Task 2: Add the LearnHouse Activity Type

**Files:** `spaces/learning/learnhouse/apps/api/src/db/courses/activities.py`, `spaces/learning/learnhouse/apps/api/src/services/learning/penecho_activity.py`, `spaces/learning/learnhouse/apps/api/src/tests/learning/test_penecho_activity.py`

- [ ] Write failing API tests for create/read/update/clone/publish of `penecho_canvas`, schema validation, and backward compatibility with existing custom activities.
- [ ] Implement a dedicated subtype or validated `TYPE_CUSTOM/SUBTYPE_CUSTOM` details discriminator according to the smallest compatible upstream seam.
- [ ] Store only launch configuration and Learning artifact references in LearnHouse.
- [ ] Commit in LearnHouse fork: `feat: add PenEcho canvas activity`; update parent pin.

## Task 3: Add the PenEcho Learning Session API

**Files:** `spaces/learning/penecho/src/server/learning-session.js`, `src/server/main.js`, `test/learning-session.test.js`, `test/server-security.test.js`

- [ ] Write failing Node tests for create/open/save/export, optimistic revision checks, immutable snapshots, project/canvas ownership, session headers, loopback/origin policy, and denied `/api/ai/command` provider bypass.
- [ ] Implement a narrow local API over existing canvas-project/canvas storage; keep existing PenEcho security middleware.
- [ ] Return structured canvas JSON plus bounded PNG snapshot/artifact metadata without embedding credentials.
- [ ] Commit in PenEcho fork: `feat: add versioned learning canvas sessions`; update parent pin.

## Task 4: Implement the PenEcho Bridge

**Files:** `spaces/learning/bridge/penecho_client.py`, `spaces/learning/services/evaluation/canvas_artifacts.py`, `spaces/learning/tests/unit/test_penecho_client.py`, `integration/test_canvas_artifacts.py`

- [ ] Write failing tests for session authentication, timeout, origin mismatch, save conflict, malformed canvas, artifact hash/path validation, and PenEcho outage.
- [ ] Implement typed loopback client and Learning-owned artifact copy/hash/revision persistence.
- [ ] Verify artifact roots, reject symlinks/path traversal, and never return host paths to the browser.
- [ ] Commit: `feat(learning): bridge versioned PenEcho artifacts`.

## Task 5: Render PenEcho in the Learning Workspace

**Files:** `spaces/learning/learnhouse/apps/web/components/Objects/Activities/PenEcho/PenEchoActivity.tsx`, `spaces/learning/learnhouse/apps/web/components/Objects/Activities/PenEcho/PenEchoSubmission.tsx`, `spaces/learning/learnhouse/apps/web/components/Objects/Activities/ActivityPreview/ActivityPreview.tsx`, `spaces/learning/learnhouse/apps/web/components/Objects/Editor/ActivitySwitcher.tsx`, `spaces/learning/learnhouse/apps/web/components/Objects/Activities/PenEcho/PenEchoActivity.test.tsx`, `spaces/learning/learnhouse/apps/web/tests/e2e/penecho-activity.spec.ts`

- [ ] Write failing tests for launch, resume, save conflict, selected-region hint, exam help restriction, offline state, submit confirmation, and accessible full-workspace mode.
- [ ] Implement sandboxed loopback embedding that replaces the center workspace while preserving course context and status strip.
- [ ] Validate all `postMessage` origin/source/schema data and keep keys out of renderer state.
- [ ] Run desktop/compact screenshots and verify canvas is visible, usable, and non-overlapping.
- [ ] Commit in LearnHouse fork: `feat: render PenEcho learning activities`; update parent pin.

## Task 6: Evaluate Canvas Submissions

**Files:** `spaces/learning/services/evaluation/penecho.py`, `spaces/learning/tests/unit/test_penecho_evaluation.py`, `integration/test_penecho_mastery_boundary.py`

- [ ] Write failing tests for per-criterion rubric score, accepted structured objects, rendered snapshot evidence, source grounding, misconception tags, schema failure, and confidence threshold.
- [ ] Implement OpenFang-authorized multimodal/rubric evaluation with persisted provenance.
- [ ] Prove confidence below `0.65` creates review and produces zero mastery mutation; accepted evaluation applies once.
- [ ] Commit: `feat(learning): evaluate PenEcho submissions safely`.

## Task 7: Connect Canvas MCP Tools

**Files:** `spaces/learning/mcp/tools/canvas.py`, `spaces/learning/tests/contract/test_canvas_tools.py`, `spaces/learning/tests/e2e/test_canvas_activity.py`

- [ ] Write failing tests for open/save/hint/submit/review, idempotency, expected canvas revision, help policy, confirmation, dependency failure, and UI projection failure.
- [ ] Implement handlers through PenEcho client, Learning artifact/evaluation services, and terminal readback.
- [ ] Add an E2E path: open -> draw fixture -> save -> reload -> hint -> submit -> evaluate -> mastery/review -> next task.
- [ ] Commit: `feat(learning): expose semantic canvas tools`.

## Phase Gate

```powershell
npm --prefix spaces/learning/penecho test
python -m pytest spaces/learning/tests/contract/test_canvas_tools.py spaces/learning/tests/unit/test_penecho_evaluation.py spaces/learning/tests/e2e/test_canvas_activity.py -q
bun --cwd spaces/learning/learnhouse/apps/web test PenEcho
git diff --check
```

Required evidence: immutable resume, origin/schema security, artifact hashes, low-confidence exclusion, semantic MCP flow, and visual canvas verification.
