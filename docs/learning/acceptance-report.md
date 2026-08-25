# Learning Space Acceptance Report

Date: 2026-08-26

Scope: deterministic replacement implementation, security, recovery, and local runtime audit

Status: **implementation accepted; action-time cutover not authorized or executed**

## Accepted Evidence

| Gate | Evidence | Result |
| --- | --- | --- |
| Canonical Learning contracts | registry, agent scope, MCP schemas, application readback tests | passed |
| Learning Python suite | `python -m pytest spaces/learning/tests -q` | 368 passed, 12 skipped |
| Static quality | Ruff over `spaces/learning` and Golden Path script | passed |
| Compose | `docker compose ... config --quiet` with audit-only sentinel values | passed |
| OpenFang registry sync | `python scripts/sync_openfang_agents.py --check` | 0 drift |
| Deterministic Golden Path | semantic MCP harness, one correlation chain | 15 steps passed |
| Golden Path persistence | RAG, Factory, adaptive, PenEcho, receipts, UI intents | passed |
| Runtime recovery | every declared service restarted through Docker labels | 10/10 passed |
| Secret boundary | public payload, browser environment, tracked runtime source scan | passed |
| Migration reconciliation | batch `legacy-real-13845e47` | ready, 0 warnings, 0 quarantines |
| LearnHouse Learning UI | focused Bun tests | 14 passed, 0 failed |
| Public desktop render | fresh local Chrome screenshot at 1440x900 | rendered, nonblank |

The deterministic Golden Path generated three Qdrant points, published one
review-confirmed Factory job, persisted two evaluations and two mastery evidence
records, completed two of three adaptive tasks, persisted nine terminal MCP receipts,
and delivered nine revisioned UI intents. Its provider evidence is explicitly labeled
`openfang://deterministic/*`; it is not live-provider evidence.

## Failure Matrix

| Failure | Required behavior | Verified behavior |
| --- | --- | --- |
| LearnHouse unavailable | reject writes, no progress claim | dispatcher returns typed `unavailable`, no evidence |
| Qdrant unavailable | retain PostgreSQL source/outbox, no ungrounded fallback | retry/dead-letter and rebuild tests retain source truth |
| OpenFang unavailable | durable queued/failed Factory state | bounded attempts and durable Factory state tests pass |
| Redis unavailable | explicit degraded state only | readiness reports degraded and no live claim |
| PenEcho unavailable | retain non-canvas path and fail canvas explicitly | typed unavailable boundary; no substitute graded result |
| Evaluator low confidence | review item, no mastery update | adaptive and PenEcho mastery-boundary tests pass |
| UI bridge unavailable | backend truth remains; projection failure visible | `ui_delivery` reports attempted/failed with error code |
| Duplicate invocation | replay immutable receipt | durable idempotency replay passes across process restart |
| Revision conflict | fail closed | contract and application boundary tests pass |

## Restart Recovery

The existing Docker project `vibemind-learning` was audited without recreating or
deleting containers or volumes. The smoke runner restarted these services in order:

1. PostgreSQL
2. Redis
3. Qdrant
4. LearnHouse API
5. LearnHouse collaboration
6. LearnHouse Web
7. Learning API
8. Learning worker
9. Learning MCP
10. PenEcho

After every restart, the same durable MCP receipt was read back unchanged. After the
run, all ten containers reported healthy. Separate deterministic recovery tests prove
receipt replay after dispatcher recreation, queued Factory-job recovery through a new
repository process, and Qdrant retry completion through a new worker process.

## Migration Reconciliation

The read-only legacy export contains 50 records and transforms to 32 canonical
envelopes. Validation passed counts, hashes, foreign keys, score ranges, representative
content, artifact existence, quarantine emptiness, and zero destructive operations.

Canonical counts include two organizations, two courses, two chapters, two concepts,
five activities/items, three sessions/responses/evaluations, and three mastery evidence
records. The legacy source remains at `C:/Users/User/Desktop/Learning_plattform` and was
not modified or removed.

## Upstream Baselines

These results are recorded separately and are not represented as green upstream suites:

- PenEcho: 530 discovered, 522 passed, 7 failed, 1 skipped. The known Windows failures
  are three POSIX file-mode assumptions, three macOS/POSIX path assumptions, and one
  temporary-directory `EPERM` cleanup.
- LearnHouse Web: 180 passed, 3 failed, 1 module error. The known environment/upstream
  issues are the missing optional Tailwind native binding, missing
  `catalogPagination.ts`, and an Arabic translation timeout.
- LearnHouse focused Learning UI: 14 passed, 0 failed.
- LearnHouse API full upstream suite was not rerun in this audit because the pinned
  project requires Python 3.14.6; this host's Learning runtime uses Python 3.11.

## Visual Evidence

The current public LearnHouse course route rendered nonblank at desktop size after the
runtime restart. It correctly displayed the signed-out course state. A direct Chrome
mobile screenshot was generated, but command-line headless Chrome did not provide a
verified 390px CSS layout viewport; it is therefore not accepted as mobile-layout
evidence. No authenticated Learning Course Studio/Player Playwright screenshot spec
exists yet, so authenticated visual acceptance remains an explicit test gap. Structural
UI tests do verify module boundaries, revisioned intent projection, reload
reconstruction, PenEcho frame security, and stale-intent handling.

## Action-Time Package

- Dry-run report: `docs/learning/migration-dry-run.md`
- Machine report: `docs/learning/migration-dry-run.json`
- Migration batch: `legacy-real-13845e47`
- Cutover controller: `spaces/learning/deployment/cutover.py`
- Route state: `%LOCALAPPDATA%/VibeMind/Learning/route-state.json`
- Planned route change: `legacy` -> `learning_space`
- Rollback: guarded `rollback` command in `docs/learning/cutover-runbook.md`
- Retained legacy path: `C:/Users/User/Desktop/Learning_plattform`

## Explicit Non-Claims

- No real legacy import was applied to the running Learning database.
- No production/local-user Qdrant rebuild from imported legacy data was executed.
- No route cutover or rollback was executed.
- No live OpenFang/provider Golden Path was executed.
- No authenticated Learning workspace visual E2E was executed.
- No legacy file, database, Docker volume, or application was deleted.
- Structural and reachable health are not presented as live Golden Path evidence.

Real import and route switching require a fresh evidence file plus explicit action-time
confirmation for the exact batch, expected route revision, confirmation reference, and
rollback command.
