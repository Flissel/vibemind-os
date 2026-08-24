# Phase 6: Migration, Golden Path, and Cutover

> **For agentic workers:** Export and dry-run are routine. Real import, route switch, rollback rehearsal against user data, and any deletion require action-time confirmation.

**Goal:** Reconcile the legacy prototype into the new Learning Space, prove the complete MCP workflow, and switch routes reversibly without losing data.

## Task 1: Inventory and Export the Legacy Prototype

**Files:** `spaces/learning/services/migration/legacy_schema.py`, `exporter.py`, `spaces/learning/tests/migration/test_exporter.py`, `docs/learning/migration-map.md`

- [ ] Mount `C:/Users/User/Desktop/Learning_plattform` read-only and record database/file topology without exposing secret values.
- [ ] Write failing fixture tests for universities/programs/modules/topics, sources, questions/answers/tasks, attempts/evaluations, chunks, artifacts, and mistake journal.
- [ ] Implement deterministic JSONL/manifest export with source IDs, counts, hashes, and batch ID; never copy legacy vectors as trusted data.
- [ ] Commit: `feat(learning): export legacy learning data`.

## Task 2: Transform to Canonical Imports

**Files:** `spaces/learning/services/migration/transformer.py`, `schemas.py`, `spaces/learning/tests/migration/test_transformer.py`

- [ ] Write failing mapping tests for every design mapping, missing parents, duplicate IDs, invalid scores, orphan artifacts, unsupported task formats, and stable reruns.
- [ ] Implement pure transformations into versioned import envelopes with warnings/quarantine records.
- [ ] Preserve source-system ID and migration batch ID on every output record.
- [ ] Commit: `feat(learning): transform legacy learning records`.

## Task 3: Validate and Reconcile a Dry Run

**Files:** `spaces/learning/services/migration/validator.py`, `report.py`, `spaces/learning/tests/migration/test_validator.py`, `docs/learning/migration-dry-run.md`

- [ ] Write failing tests for counts, hashes, foreign keys, representative content samples, score ranges, artifact existence, and zero destructive operations.
- [ ] Implement dry-run validation and a machine-readable plus Markdown report.
- [ ] Run against the real legacy source read-only; document accepted counts, quarantines, and blockers without changing the target database.
- [ ] Commit: `test(learning): validate legacy migration dry run`.

## Task 4: Implement Idempotent Import and Qdrant Rebuild

**Files:** `spaces/learning/services/migration/importer.py`, `spaces/learning/services/ingestion/rebuild.py`, `spaces/learning/tests/migration/test_importer.py`, `test_rebuild.py`

- [ ] Write failing fresh-database tests for transaction boundaries, rerun idempotency, batch rollback, immutable published revisions, artifact hashes, and complete Qdrant rebuild.
- [ ] Implement import through LearnHouse/Learning service boundaries, not shared in-place legacy mutation.
- [ ] Require explicit CLI `--apply --batch <id>` and confirmation token for a real import; default is dry-run.
- [ ] Commit: `feat(learning): import and rebuild migrated courses`.

## Task 5: Define Reversible Route Cutover

**Files:** `spaces/learning/deployment/cutover.py`, `route-state.schema.json`, `spaces/learning/tests/migration/test_cutover.py`, `docs/learning/cutover-runbook.md`

- [ ] Write failing tests for preflight gates, atomic route/profile switch, previous-state receipt, rollback, target health failure, repeated commands, and no deletion.
- [ ] Implement `plan`, `apply`, `verify`, and `rollback` commands; only `plan` is allowed without action-time confirmation.
- [ ] Keep the old prototype read-only and addressable through the rollback receipt.
- [ ] Commit: `feat(learning): add reversible cutover controls`.

## Task 6: Build the MCP-Driven Golden Path

**Files:** `spaces/learning/tests/e2e/test_golden_path.py`, `fixtures/golden-course/`, `scripts/run_learning_golden_path.py`

- [ ] First run the test and capture failure at the earliest missing hop.
- [ ] Drive only semantic MCP calls: status -> import -> index -> generate -> review -> confirm publish -> open chapter -> start session -> answer -> PenEcho open/save/submit -> evaluate -> mastery -> next task -> progress.
- [ ] Assert one correlation chain, application aggregate revisions, source locators, persisted terminal evidence, and UI intents.
- [ ] Use deterministic fake OpenFang responses for CI and a separately labeled live-provider profile.
- [ ] Commit: `test(learning): prove complete MCP golden path`.

## Task 7: Security, Recovery, and Acceptance Audit

**Files:** `spaces/learning/tests/e2e/test_failure_matrix.py`, `test_secret_boundary.py`, `test_restart_recovery.py`, `docs/learning/acceptance-report.md`

- [ ] Test LearnHouse, Qdrant, OpenFang, Redis, PenEcho, evaluator, and UI bridge failures against section 16 of the design.
- [ ] Scan Git, compose output, API payloads, browser bundles, logs, receipts, and artifacts for secret-shaped values.
- [ ] Restart every service during accepted/running jobs and verify recovery from PostgreSQL/outbox state.
- [ ] Run all phase gates, upstream focused suites, visual checks, migration reconciliation, and Golden Path; list any non-claims.
- [ ] Commit: `test(learning): complete replacement acceptance audit`.

## Action-Time Cutover Procedure

- [ ] Present dry-run report, acceptance report, exact import batch, route change, rollback command, and retained legacy paths to the user.
- [ ] Obtain explicit confirmation for the real import and route switch.
- [ ] Import into a fresh database, rebuild Qdrant, rerun reconciliation and Golden Path, then atomically switch the route.
- [ ] Verify current route and terminal Learning status after switch; retain rollback receipt.
- [ ] Do not remove the legacy prototype. Any later deletion is a separate destructive request.

## Phase Gate

```powershell
python -m pytest spaces/learning/tests/migration spaces/learning/tests/e2e -q
python scripts/run_learning_golden_path.py --profile deterministic
npm --prefix spaces/learning/penecho test
bun --cwd spaces/learning/learnhouse/apps/web test
python scripts/sync_openfang_agents.py --check
git diff --check
git status --short --branch
```

Completion requires fresh deterministic Golden Path evidence. Live-provider, real-data import, route cutover, rollback, and deletion remain separate claims tied to their own executed receipts and confirmations.
