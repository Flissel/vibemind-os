# VibeMind Learning Space Implementation Master Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Track every checkbox and preserve RED/GREEN evidence.

**Goal:** Replace the local Learning prototype with a production-shaped, local-only VibeMind Learning Space built on controlled LearnHouse and PenEcho forks, Qdrant RAG, a durable AI Course Factory, and concept-level adaptive learning.

**Architecture:** `spaces/learning` owns VibeMind contracts, policy, MCP, orchestration, truth readback, and Learning extensions. LearnHouse owns course authoring/delivery and its PostgreSQL schema. PenEcho owns canvas editing. OpenFang remains the only model/provider authority; PostgreSQL is durable truth, Qdrant a rebuildable semantic projection, and Redis transient infrastructure.

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, SQLAlchemy/Alembic, PostgreSQL, Redis, Qdrant, AutoGen, MCP stdio/JSON-RPC, Next.js/React/Tailwind from LearnHouse, PenEcho Node runtime, Docker Compose, pytest, Vitest/Playwright, Node test runner.

---

## Authoritative Inputs

- Approved design: `docs/superpowers/specs/2026-08-24-learning-space-design.md`
- Approved design commit: `79f1143`
- Implementation base: local `origin/master@80ff4e3`; recheck before Task 1.
- LearnHouse source pin: `5a58c3de399a8846427cbc60bdd847406613dfdc`
- PenEcho source pin: `d5801103406f3ddad2f261439bce1d9ab48f9def`
- Implementation branch: create `codex/learning-space-v1` from the refreshed integration base.
- Legacy source: `C:/Users/User/Desktop/Learning_plattform`; read-only until an explicitly confirmed cutover.

## Baseline Non-Claims

- The design worktree contains documentation only; no Learning runtime is claimed.
- The upstream pins were inspected, but fork URLs, licenses, full upstream test health, and Docker compatibility must be freshly verified in Phase 1.
- Structural registry health is not live OpenFang, LearnHouse, PenEcho, Qdrant, or Golden Path evidence.
- This local V1 provides restartable durable state and explicit degraded modes, not multi-host high availability.

## Execution Partitions

Implement in order. A phase may begin only after the prior phase gate is green and committed.

1. [Phase 1: Foundation, authority, and runtime](2026-08-24-learning-space-phase-1-foundation.md)
2. [Phase 2: LearnHouse product and semantic UI](2026-08-24-learning-space-phase-2-learnhouse-product.md)
3. [Phase 3: Qdrant RAG and AI Course Factory](2026-08-24-learning-space-phase-3-rag-course-factory.md)
4. [Phase 4: Adaptive Learning Player](2026-08-24-learning-space-phase-4-adaptive-learning.md)
5. [Phase 5: PenEcho canvas activities](2026-08-24-learning-space-phase-5-penecho.md)
6. [Phase 6: Migration, Golden Path, and cutover](2026-08-24-learning-space-phase-6-migration-cutover.md)

## Global Invariants

- [ ] Re-establish topology, branch/base parity, submodule state, and dirty/foreign changes before each phase.
- [ ] Work only in the isolated feature worktree and controlled fork branches.
- [ ] Record the failing assertion before production edits and the passing result afterward.
- [ ] Never store provider keys in Git, browser payloads, renderer state, LearnHouse, PenEcho, or AutoGen.
- [ ] Model calls flow only through Brain -> OpenFang -> `brain-learning` -> admitted Learning MCP operation.
- [ ] Missing agent, MCP server, tool, confirmation, dependency, revision, or truth readback fails closed.
- [ ] Every write accepts an idempotency key and expected revision where applicable.
- [ ] UI intents are projections of committed backend state, never execution evidence.
- [ ] PostgreSQL owns durable state; Redis owns no terminal state; Qdrant is rebuildable.
- [ ] Published course revisions are immutable and generation never auto-publishes.
- [ ] Evaluator confidence below `0.65` creates review work and cannot update mastery.
- [ ] New TypeScript is strict and uses `unknown` plus narrowing, never `any`.
- [ ] Run secret-shaped-value scans and `git diff --check` before every commit.
- [ ] Commit every numbered task separately with a conventional commit.
- [ ] Do not delete or mutate the legacy prototype during this plan.

## Requirement Coverage

| Requirement | Delivery |
| --- | --- |
| Canonical Space, agent, MCP, routing authority | Phase 1 Tasks 2-5 |
| Local Docker runtime and truthful health | Phase 1 Tasks 1, 6-7 |
| Course Studio and Learning UI layout A | Phase 2 Tasks 1-6 |
| Semantic MCP and revision-safe UI intents | Phase 1 Task 5; Phase 2 Tasks 3-5 |
| Qdrant-only Learning semantic index | Phase 3 Tasks 1-4 |
| Durable AI Course Factory and AutoGen team | Phase 3 Tasks 5-8 |
| Source verification, review, explicit publish | Phase 3 Tasks 6-8 |
| Adaptive mastery, selection, review scheduling | Phase 4 Tasks 1-6 |
| Training and exam modes | Phase 4 Tasks 4-6 |
| PenEcho activity, persistence, evaluation | Phase 5 Tasks 1-7 |
| Low-confidence grading boundary | Phase 4 Task 3; Phase 5 Task 6 |
| Legacy migration, reconciliation, rollback | Phase 6 Tasks 1-5 |
| MCP-driven Golden Path and fresh evidence | Phase 6 Tasks 6-7 |

## Cross-Phase Contract Freeze

Before Phase 2, freeze `EventEnvelopeV1`, `ToolResultV1`, `TruthReadbackV1`, and `UiIntentV1` in `spaces/learning/contracts/`. Later changes require versioned models and backward-compatibility tests. The canonical event names and MCP tool names are exactly those in sections 7 and 8 of the approved design.

## Common Verification Gate

Run from the VibeMind worktree root after every phase:

```powershell
python -m pytest spaces/learning/tests -q
python -m pytest brain/the_brain/tests/test_learning_space_contract.py scripts/tests/test_sync_openfang_agents.py -q
python scripts/sync_openfang_agents.py --check
git diff --check
git status --short --branch
```

Add upstream/product gates as introduced:

```powershell
docker compose -f spaces/learning/deployment/compose.yml config
docker compose -f spaces/learning/deployment/compose.yml run --rm learning-api pytest -q
bun --cwd spaces/learning/learnhouse/apps/web test
npm --prefix spaces/learning/penecho test
```

Do not claim full upstream health when only focused suites ran. Capture baseline failures separately and do not repair unrelated upstream defects.

## Final Completion Audit

- [ ] All canonical events resolve registry -> agent -> MCP -> owning service and terminal readback.
- [ ] A renderer/provider bypass test proves zero unauthorized calls.
- [ ] LearnHouse reload reconstructs the approved layout and rejects stale UI intents.
- [ ] Qdrant can be deleted and rebuilt without course, source, or progress loss.
- [ ] Course generation exposes provenance, unsupported claims, attempts, and quality decisions.
- [ ] Publication requires current revision plus explicit confirmation.
- [ ] Adaptive tests prove rising difficulty, remediation, spaced review, exam blueprinting, and low-confidence exclusion.
- [ ] PenEcho resumes immutable canvas revisions and returns rubric evidence without leaking credentials.
- [ ] Migration dry-run reconciles counts, hashes, foreign keys, and representative samples.
- [ ] The fresh MCP Golden Path passes with one correlation chain and terminal application readbacks.
- [ ] Cutover and rollback are executed only after action-time confirmation.
- [ ] The legacy prototype remains recoverable and no deletion is claimed.
