# Phase 4: Adaptive Learning Player

> **For agentic workers:** Keep scoring deterministic where possible and make every mastery mutation traceable to an accepted evaluation.

**Goal:** Deliver a real Quiz Player that adapts concept difficulty, remediates misconceptions, schedules review, and supports training and exam modes.

## Task 1: Add Learning Domain Models

**Files:** `spaces/learning/services/adaptive_engine/models.py`, `repository.py`, `spaces/learning/services/db/migrations/versions/0004_adaptive_learning.py`, `spaces/learning/tests/integration/test_adaptive_repository.py`

- [ ] Write failing tests for concepts/dependencies, item difficulty/type, item-concept mapping, sessions, attempts, responses, evaluations, mastery evidence, review schedules, misconceptions, and immutable audit links.
- [ ] Implement constraints for normalized values, course revision ownership, attempt ordering, and one mastery application per accepted evaluation.
- [ ] Commit: `feat(learning): persist adaptive learning evidence`.

## Task 2: Implement Deterministic Scoring

**Files:** `spaces/learning/services/evaluation/deterministic.py`, `schemas.py`, `spaces/learning/tests/unit/test_deterministic_scoring.py`

- [ ] Write parameterized failing tests for single/multiple choice, matching, ordering, numeric tolerance, fill-in, normalized strings, partial credit, malformed answers, and score bounds.
- [ ] Implement format-specific scorers returning score, confidence `1`, rationale codes, and no hidden expected answer in student-visible payloads.
- [ ] Commit: `feat(learning): score deterministic quiz formats`.

## Task 3: Implement Rubric Evaluation Boundary

**Files:** `spaces/learning/services/evaluation/rubric.py`, `review_queue.py`, `spaces/learning/tests/unit/test_rubric_evaluation.py`

- [ ] Write failing tests for rubric criteria, source-grounded feedback, evaluator schema failure, confidence boundaries `0.649/0.65`, retries, and zero mastery calls below threshold.
- [ ] Implement evaluator through OpenFang model gateway and persist model/prompt/source versions.
- [ ] Route low confidence or unsupported feedback to review without changing mastery.
- [ ] Commit: `feat(learning): gate rubric-based evaluation`.

## Task 4: Implement Mastery and Review Scheduling

**Files:** `spaces/learning/services/adaptive_engine/mastery.py`, `review_schedule.py`, `spaces/learning/tests/unit/test_mastery.py`, `test_review_schedule.py`

- [ ] Write exact failing tests for Beta prior `(2,2)`, `w=c/n`, accepted updates, duplicate protection, posterior mean, trace links, and no update below confidence.
- [ ] Write clock-controlled tests for intervals `1,3,7,14` days, maintenance transition, failure reset, and alternate-representation requirement.
- [ ] Implement pure functions first, then transactional repository application.
- [ ] Commit: `feat(learning): update traceable concept mastery`.

## Task 5: Implement Adaptive Item Selection

**Files:** `spaces/learning/services/adaptive_engine/selector.py`, `eligibility.py`, `spaces/learning/tests/unit/test_selector.py`

- [ ] Write failing tests for `sigmoid(logit(m)-4*(d-0.5))`, mastery clamp, weakest-concept selection, target `0.72`, band `0.65-0.80`, overdue review, misconception priority, quality exclusion, repetition penalty, and deterministic tie-break.
- [ ] Implement pure ranking with an explanation record for every selected/skipped item.
- [ ] Add trajectory tests proving stronger mastery selects harder eligible items and repeated failure selects remediation.
- [ ] Commit: `feat(learning): select adaptive learning tasks`.

## Task 6: Connect Sessions, Quiz Player, and MCP

**Files:** `spaces/learning/mcp/tools/sessions.py`, `tutor.py`, `spaces/learning/learnhouse/apps/web/components/Learning/LearningPlayer.tsx`, `TaskRenderer.tsx`, `SessionSummary.tsx`, tests alongside each file, `spaces/learning/tests/e2e/test_adaptive_session.py`

- [ ] Write failing tests for session start, next task, answer, hint policy, tutor query, progress, resume, training feedback, exam blueprint sampling, exam feedback withholding, and terminal session summary.
- [ ] Implement semantic tools and LearnHouse UI renderers for all supported activity formats.
- [ ] Return next-task UI intent only after response/evaluation/mastery/readback transaction completes.
- [ ] Add a 30-attempt synthetic learner test showing difficulty progression and scheduled remediation without relying on live AI.
- [ ] Commit: `feat(learning): deliver adaptive quiz sessions` and update LearnHouse pin.

## Phase Gate

```powershell
python -m pytest spaces/learning/tests/unit/test_deterministic_scoring.py spaces/learning/tests/unit/test_rubric_evaluation.py spaces/learning/tests/unit/test_mastery.py spaces/learning/tests/unit/test_selector.py spaces/learning/tests/e2e/test_adaptive_session.py -q
bun --cwd spaces/learning/learnhouse/apps/web test LearningPlayer
git diff --check
```

Required evidence: exact formula tests, confidence boundary, rising difficulty trajectory, remediation/review scheduling, exam mode, resume, and persisted audit trail.
