# Phase 1: Foundation, Authority, and Runtime

> **For agentic workers:** Execute tasks sequentially with RED/GREEN evidence. Do not begin Phase 2 until this phase gate passes.

**Goal:** Establish pinned sources, canonical `learning` identity, versioned contracts, semantic MCP transport, truth readback, and a truthful local runtime.

## Task 1: Pin Controlled Upstreams

**Files:** `.gitmodules`, `spaces/learning/README.md`, `spaces/learning/deployment/upstream-lock.yml`, `spaces/learning/tests/contract/test_upstream_pins.py`

- [ ] Verify clean feature worktree and fetch current upstream license/default-branch metadata.
- [ ] Write a failing test requiring exact LearnHouse and PenEcho paths, commits, fork remotes, and license records.
- [ ] Add both repositories as pinned submodules under `spaces/learning/`; do not track local credentials or generated data.
- [ ] Document fork update procedure, corresponding-source obligations, and upstream baseline commands.
- [ ] Run the pin test and both upstream focused baseline suites; record unrelated failures in `spaces/learning/README.md`.
- [ ] Commit: `build(learning): pin LearnHouse and PenEcho sources`.

## Task 2: Define Canonical Contracts

**Files:** `spaces/learning/contracts/events.py`, `mcp_models.py`, `outcomes.py`, `ui_intents.py`, `spaces/learning/tests/contract/test_contracts.py`

- [ ] Write failing tests for all approved event/tool names, strict extra-field rejection, UUID correlation, actor/context, idempotency, expected revision, confirmation, terminal revision, evidence reference, and UI intent variants.
- [ ] Implement frozen Pydantic v2 models: `EventEnvelopeV1`, `ToolRequestV1`, `ToolResultV1`, `TruthReadbackV1`, and discriminated `UiIntentV1` variants.
- [ ] Ensure secret/provider fields and arbitrary filesystem paths are rejected at the public boundary.
- [ ] Add JSON-schema snapshots and compatibility tests.
- [ ] Commit: `feat(learning): define versioned space contracts`.

## Task 3: Register the Learning Space

**Files:** `config/space_agent_registry.yml`, `brain/the_brain/core/space_routing_head.py`, `spaces/_navigator/registry.py`, `brain/the_brain/tests/test_learning_space_contract.py`, `spaces/learning/tests/contract/test_registry.py`

- [ ] Write failing tests proving `learning` is canonical, routes every `learning.*` event, claims `brain-learning`, and exposes only the Learning MCP server/tool allowlist.
- [ ] Add the registry section with exact MCP targets `mcp:brain-learning:spaces-learning:<tool>`.
- [ ] Add Learning to Brain routing and navigator metadata without making it an alias for another Space.
- [ ] Prove unknown events and mismatched tool claims fail closed.
- [ ] Commit: `feat(learning): register canonical learning space`.

## Task 4: Generate and Validate the OpenFang Agent

**Files:** `scripts/sync_openfang_agents.py`, `scripts/tests/test_sync_openfang_agents.py`, `scripts/verify_openfang_mcp_registration.py`, `spaces/learning/tests/contract/test_openfang_scope.py`

- [ ] Add failing tests for generated `brain-learning`, exact MCP scope, deterministic output, check-mode drift, and rejection of broader tool access.
- [ ] Extend registry-driven generation; do not hand-maintain a conflicting agent manifest.
- [ ] Add structural verification that distinguishes configured, reachable, and verified-live states.
- [ ] Run `python scripts/sync_openfang_agents.py --check` and the focused OpenFang tests.
- [ ] Commit: `feat(learning): derive OpenFang learning agent`.

## Task 5: Implement MCP, Dispatch, and Truth Readback

**Files:** `spaces/learning/mcp/server.py`, `mcp/tools/status.py`, `bridge/dispatcher.py`, `bridge/truth_readback.py`, `spaces/learning/tests/unit/test_mcp_server.py`, `test_dispatcher.py`, `test_truth_readback.py`, `brain/the_brain/tests/test_learning_mcp_server.py`

- [ ] Write failing JSON-RPC tests for initialize/list/call, strict argument validation, unknown tools, duplicate invocation, revision conflict, confirmation-required, unavailable dependency, and readback failure.
- [ ] Implement the semantic tool catalog and a small dispatcher interface; only `learning_status` may be operational before service adapters exist, while other tools return typed `unavailable` errors.
- [ ] Implement idempotency receipt lookup and terminal readback verification interfaces without direct provider fallback.
- [ ] Prove a transport-level success without valid aggregate/revision evidence is returned as an error.
- [ ] Commit: `feat(learning): add semantic MCP authority boundary`.

## Task 6: Add Learning-Owned Persistence and Health

**Files:** `spaces/learning/services/db/models.py`, `db/session.py`, `db/migrations/versions/0001_learning_core.py`, `deployment/health.py`, `spaces/learning/tests/integration/test_persistence.py`, `test_health.py`

- [ ] Write failing PostgreSQL tests for invocation receipts, aggregate revisions, outbox rows, artifact metadata, and immutable terminal evidence.
- [ ] Implement a `learning` PostgreSQL schema with Alembic ownership separate from LearnHouse migrations.
- [ ] Implement liveness/readiness/structural/Golden-Path health as distinct states; readiness checks migrations and owned dependencies.
- [ ] Prove Redis or Qdrant degradation never fabricates terminal state.
- [ ] Commit: `feat(learning): persist receipts and truthful health`.

## Task 7: Compose the Local Runtime

**Files:** `spaces/learning/deployment/compose.yml`, `profile.yml`, `.env.example`, `deployment/test_compose.py`, `spaces/learning/tests/integration/test_runtime_smoke.py`

- [ ] Write failing compose contract tests for loopback exposure, named volumes, health dependencies, secret references, and no embedded credentials.
- [ ] Compose PostgreSQL, Redis, Qdrant, LearnHouse API/Web/collab, Learning API/worker/MCP, and PenEcho from pinned sources.
- [ ] Add restart policies and bounded health checks without claiming multi-host HA.
- [ ] Start the profile, run migrations, invoke `learning_status`, restart stateful services, and verify persisted receipt/readback data.
- [ ] Commit: `build(learning): add local runtime profile`.

## Phase Gate

```powershell
python -m pytest spaces/learning/tests/contract spaces/learning/tests/unit spaces/learning/deployment/test_compose.py -q
python -m pytest brain/the_brain/tests/test_learning_space_contract.py brain/the_brain/tests/test_learning_mcp_server.py scripts/tests/test_sync_openfang_agents.py -q
python scripts/sync_openfang_agents.py --check
docker compose -f spaces/learning/deployment/compose.yml config
git diff --check
```

Required evidence: exact pins, clean registry generation, MCP failure-boundary tests, migration/readiness proof, and restart smoke receipt. No live provider claim is required.
