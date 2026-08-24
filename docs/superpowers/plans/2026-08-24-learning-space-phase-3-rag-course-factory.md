# Phase 3: Qdrant RAG and AI Course Factory

> **For agentic workers:** No ungrounded generation may reach review-ready or publication. AutoGen coordinates roles but owns no durable lifecycle or provider credentials.

**Goal:** Make imported materials reproducible semantic sources and generate durable, source-linked course drafts through OpenFang-authorized agents.

## Task 1: Define Source, Chunk, and Outbox Models

**Files:** `spaces/learning/services/ingestion/models.py`, `repository.py`, `spaces/learning/services/db/migrations/versions/0002_sources_outbox.py`, `spaces/learning/tests/integration/test_source_repository.py`

- [ ] Write failing tests for immutable source versions, content hashes, locators, deterministic chunk IDs, artifact metadata, and idempotent Qdrant outbox rows.
- [ ] Implement PostgreSQL models/repository and unique constraints for source revision plus chunk identity.
- [ ] Ensure original files remain in the Learning data directory and database rows contain only approved metadata/path references.
- [ ] Commit: `feat(learning): persist versioned learning sources`.

## Task 2: Implement Deterministic Ingestion

**Files:** `spaces/learning/services/ingestion/parsers.py`, `normalizer.py`, `chunker.py`, `pipeline.py`, `spaces/learning/tests/unit/test_ingestion.py`, `fixtures/`

- [ ] Add fixtures and failing tests for PDF, DOCX, Markdown, plain text, duplicate files, parser failure, stable hashes, locators, and chunk boundaries.
- [ ] Implement structured parsers, normalization, deduplication, and deterministic chunking; never parse formats with ad hoc regex when a supported parser exists.
- [ ] Persist partial failure evidence without advancing a failed source revision to indexed.
- [ ] Commit: `feat(learning): add deterministic material ingestion`.

## Task 3: Replace Learning Retrieval with Qdrant

**Files:** `spaces/learning/services/ingestion/qdrant_index.py`, `outbox_worker.py`, `retrieval.py`, `spaces/learning/tests/integration/test_qdrant_index.py`, `test_retrieval.py`

- [ ] Write failing container tests for `learning_knowledge_v1`, alias `learning_knowledge_current`, deterministic point IDs, full payload, at-least-once upsert, deletion tombstones, filters, and citation locators.
- [ ] Implement Qdrant adapter and transactional outbox worker with bounded retry/dead-letter evidence.
- [ ] Implement retrieval that returns source revision and locator with every chunk.
- [ ] Prove Qdrant outage leaves PostgreSQL/outbox intact and disables grounded operations.
- [ ] Commit: `feat(learning): add rebuildable Qdrant projection`.

## Task 4: Adapt LearnHouse RAG

**Files:** `spaces/learning/learnhouse/apps/api/src/services/ai/rag/retrieval_protocol.py`, `spaces/learning/learnhouse/apps/api/src/services/ai/rag/qdrant_retriever.py`, `spaces/learning/learnhouse/apps/api/src/services/ai/rag/embedding_service.py`, `spaces/learning/learnhouse/apps/api/src/tests/ai/test_qdrant_retriever.py`, `spaces/learning/learnhouse/apps/api/src/tests/ai/test_no_course_pgvector.py`

- [ ] Write failing tests that Course Planning/Tutor retrieval uses the adapter and never writes `CourseEmbedding`/pgvector for the Learning Space.
- [ ] Add the bounded protocol and Qdrant implementation; preserve non-Learning upstream behavior only if needed for compatibility.
- [ ] Remove or disable Learning-space pgvector indexing and prove Qdrant is the sole semantic path.
- [ ] Commit in LearnHouse fork: `feat: route Learning RAG through Qdrant`; update parent pin.

## Task 5: Persist Course Factory Jobs

**Files:** `spaces/learning/services/course_factory/models.py`, `repository.py`, `state_machine.py`, `spaces/learning/services/db/migrations/versions/0003_course_factory.py`, `spaces/learning/tests/unit/test_factory_state_machine.py`, `integration/test_factory_repository.py`

- [ ] Write failing tests for every approved state/transition, immutable attempts, retry lineage, cancellation/rejection, revision conflicts, provenance, and terminal timestamps.
- [ ] Implement PostgreSQL lifecycle state; Redis may notify but cannot own a state or stage artifact.
- [ ] Reject skipped stages and in-place overwrite of prior attempts.
- [ ] Commit: `feat(learning): persist course factory lifecycle`.

## Task 6: Add OpenFang Model Gateway and AutoGen Team

**Files:** `spaces/learning/services/course_factory/model_gateway.py`, `team.py`, `roles/*.py`, `spaces/learning/tests/unit/test_model_gateway.py`, `test_course_team.py`

- [ ] Write failing tests for agent/tool authorization, correlation propagation, no direct OpenAI client, zero calls after rejection, retries, timeout, and schema-invalid output.
- [ ] Implement OpenFang-backed model gateway and AutoGen roles: Architect, Concept Mapper, Lesson Author, Assessment Designer, Source Verifier, Quality Reviewer.
- [ ] Keep prompts/output schemas versioned and persist each stage input/output hash and evidence reference.
- [ ] Prove AutoGen cannot publish or mutate lifecycle state except through the owning service.
- [ ] Commit: `feat(learning): coordinate authorized course agents`.

## Task 7: Implement Verification and Quality Gate

**Files:** `spaces/learning/services/course_factory/verification.py`, `quality_gate.py`, `schemas.py`, `spaces/learning/tests/unit/test_source_verification.py`, `test_quality_gate.py`

- [ ] Write failing tests for unsupported claims, missing citations, source revision mismatch, duplication, concept coverage, task distribution, rubric completeness, solvability, and low-confidence review.
- [ ] Implement deterministic checks first, then bounded AI review through the model gateway.
- [ ] Require all factual claims/expected answers to resolve to current source locators before `review_ready`.
- [ ] Commit: `feat(learning): verify generated course quality`.

## Task 8: Connect Generation, Review, and Publish

**Files:** `spaces/learning/mcp/tools/generation.py`, `spaces/learning/services/course_factory/publisher.py`, `spaces/learning/tests/integration/test_generation_flow.py`, `test_publish_confirmation.py`

- [ ] Write failing end-to-end service tests for import -> queued -> review_ready, status polling, unsupported-claim display, bounded regeneration, rejection, confirmation, and immutable publish revision.
- [ ] Implement worker orchestration and LearnHouse draft mapping through supported APIs.
- [ ] Publish only a current review-ready attempt with explicit confirmation and terminal LearnHouse readback.
- [ ] Commit: `feat(learning): deliver reviewable AI course generation`.

## Phase Gate

```powershell
python -m pytest spaces/learning/tests/unit/test_ingestion.py spaces/learning/tests/integration/test_qdrant_index.py spaces/learning/tests/unit/test_factory_state_machine.py spaces/learning/tests/integration/test_generation_flow.py -q
python -m pytest spaces/learning/learnhouse/apps/api/src/tests/ai/test_qdrant_retriever.py -q
docker compose -f spaces/learning/deployment/compose.yml run --rm learning-worker python -m spaces.learning.services.ingestion.rebuild --verify
git diff --check
```

Required evidence: stable source/chunk IDs, Qdrant rebuild, no Learning pgvector writes, durable generation stages, source-linked draft, and confirmed immutable publication.
