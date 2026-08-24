# VibeMind Learning Space Design

**Date:** 2026-08-24

**Status:** User-approved design, written review pending

**Canonical Space ID:** `learning`

**Initial deployment:** Local, single-user VibeMind installation

## 1. Purpose

The `learning` Space turns arbitrary training material into source-grounded courses and lets the local VibeMind user learn those courses through adaptive tasks, a contextual tutor, code exercises, and PenEcho canvases.

The Space is agent-first. A user can complete the normal workflow through VibeMind voice or text intents without manually navigating the Space. The visible UI remains fully usable as a fallback and as an inspectable projection of confirmed application state.

## 2. Product Goals

- Import PDFs, DOCX files, presentations, text, web sources, videos, transcripts, and existing question catalogs.
- Generate versioned course drafts for arbitrary industries and difficulty levels.
- Preserve provenance from source material to claims, lessons, questions, rubrics, and feedback.
- Review and explicitly publish generated course content.
- Teach through explanations, adaptive tasks, contextual tutoring, code execution, and visual PenEcho work.
- Increase difficulty as mastery rises and schedule targeted remediation when misconceptions appear.
- Operate as a canonical VibeMind Space behind Brain and OpenFang authority.
- Replace the existing `Learning_plattform` prototype only after migration and a verified Golden Path.

## 3. V1 Non-Goals

- Public SaaS hosting.
- Multiple organizations or tenants.
- Payments, public storefronts, social feeds, or community forums.
- External learner invitations or trainer/student account administration.
- Production high availability. V1 is local; backup and rebuildability are required, but HA claims are out of scope.
- Pixel-coordinate or DOM-scraping automation as the primary control path.
- Automatic publication of AI-generated content.

LearnHouse features outside V1 remain in the fork where practical, but they are hidden from the VibeMind Learning UI rather than aggressively deleted. This reduces upstream merge cost.

## 4. Source Strategy and Licensing

### 4.1 LearnHouse

Create a VibeMind-controlled fork of `learnhouse/learnhouse`. Keep the upstream repository configured as `upstream` and pin the fork as a Git submodule at:

```text
vibemind-os/spaces/learning/learnhouse
```

The fork is the product base for course storage, authoring, lesson delivery, assignments, submissions, analytics, and collaboration primitives. VibeMind-specific changes must be isolated behind adapters, feature flags, theme tokens, or bounded extension modules wherever possible.

### 4.2 PenEcho

Create a VibeMind-controlled fork of `penecho/penecho`, retain its upstream remote, and pin it as a Git submodule at:

```text
vibemind-os/spaces/learning/penecho
```

PenEcho is a first-class learning runtime, not an optional later plugin.

### 4.3 License obligations

LearnHouse and PenEcho are AGPL-licensed. Their license texts, notices, copyright attribution, and corresponding-source obligations remain intact. VibeMind-specific distribution documentation must identify both forks and describe how corresponding source is obtained. No design decision assumes a proprietary relicensing right.

## 5. Repository Shape

```text
vibemind-os/spaces/learning/
  README.md
  contracts/
    events.py
    mcp_models.py
    outcomes.py
    ui_intents.py
  mcp/
    server.py
    tools/
  bridge/
    learnhouse_client.py
    penecho_client.py
    ui_bridge.py
    truth_readback.py
  services/
    course_factory/
    adaptive_engine/
    ingestion/
    evaluation/
  deployment/
    profile.yml
    health.py
  tests/
    unit/
    contract/
    integration/
    e2e/
  learnhouse/              # Git submodule
  penecho/                 # Git submodule
```

The `learning` wrapper owns VibeMind contracts and integration code. The two upstream-derived applications remain independently testable and updateable.

## 6. VibeMind Authority and Routing

The only authorized product execution chain is:

```text
User intent
  -> VibeMind ingress
  -> Brain plan, admission, and canonical Space selection
  -> OpenFang agent and capability authorization
  -> brain-learning
  -> learning MCP
  -> LearnHouse, PenEcho, or a Learning-owned service
  -> terminal application readback
  -> correlated result and UI projection
```

There is no direct Brain-to-LearnHouse, renderer-to-provider, or local fallback path that can claim successful execution. A transport response is not success until the owning application returns the expected terminal state through a truth-readback operation.

`config/space_agent_registry.yml` remains the canonical Space-to-agent-to-MCP scope source. The implementation adds `learning` as a canonical ID and adds matching events to the derived/validated routing surfaces. `learning` is not an ingress alias for another Space.

## 7. Space Events

V1 defines these event families:

```text
learning.status
learning.course.list
learning.course.create
learning.course.open
learning.material.import
learning.course.generate
learning.generation.status
learning.course.review
learning.course.publish
learning.chapter.open
learning.session.start
learning.task.next
learning.task.answer
learning.hint.request
learning.tutor.ask
learning.progress.show
learning.canvas.open
learning.canvas.save
learning.canvas.hint
learning.canvas.submit
learning.canvas.review
```

Each write event carries an idempotency key, invocation ID, actor, course/session context, expected prior revision where applicable, and correlation to the Brain/OpenFang request. Results identify the owning aggregate and its terminal revision.

Publishing, deletion, destructive replacement, and migrations require explicit confirmation. Provider-cost and capability approval continue to follow OpenFang policy.

## 8. Learning MCP

The MCP exposes semantic application operations rather than screen gestures:

```text
learning_status
learning_course_list
learning_course_create
learning_material_import
learning_course_generate
learning_generation_status
learning_course_review
learning_course_publish
learning_course_open
learning_chapter_open
learning_session_start
learning_task_next
learning_task_answer
learning_hint_request
learning_tutor_ask
learning_progress_show
learning_canvas_open
learning_canvas_save
learning_canvas_hint
learning_canvas_submit
learning_canvas_review
```

Every tool returns a typed envelope with:

- invocation and correlation IDs;
- accepted/rejected/approval-required state;
- application aggregate ID and revision;
- structured result or error;
- truth-readback evidence reference;
- optional UI intent;
- no secret or raw provider credential data.

Tool scope is allowlisted for `brain-learning`. A missing agent, server, tool, approval, backend, or valid result fails closed. Browser or desktop automation may be used only as an explicitly labeled diagnostic fallback and cannot produce product success evidence.

## 9. UI Control Model

The Learning UI subscribes to authenticated Learning state events over a bounded local WebSocket or SSE channel. MCP tools modify application state first. After successful readback, the Learning MCP emits a typed `UiIntentV1` such as:

```text
navigate
open_course
open_chapter
open_task
focus_answer
open_canvas
show_result
show_progress
show_error
```

The UI applies an intent only when its aggregate revision is not older than the currently rendered revision. Reloading the UI reconstructs state from LearnHouse and Learning APIs; UI state is never the source of truth.

### 9.1 Approved layout

The approved desktop layout is visual option A:

- VibeMind Space rail at the outer left.
- Active course and chapter navigation on the inner left.
- Current lesson/task workspace in the center.
- Learning Agent, sources, hints, and action status on the right.
- Permanent voice/MCP status strip.
- PenEcho, code, and complex case work can replace the center workspace with a full-area tool view while preserving course context.

Manual controls remain available, but no normal Golden Path step requires clicking through the UI.

## 10. User Workflows

### 10.1 Course Studio

1. Create a course and state audience, target outcome, assumed prerequisites, and target difficulty.
2. Import one or more source materials or an existing question catalog.
3. Inspect parsing, deduplication, source versions, and ingestion failures.
4. Start AI course generation.
5. Review the generated curriculum, unsupported claims, task quality, and source coverage.
6. Edit or regenerate bounded sections.
7. Explicitly publish a course revision.

### 10.2 Learning Player

1. Open a course or ask VibeMind to continue the recommended session.
2. Receive the current explanation or task.
3. Answer by voice, text, code, structured interaction, or PenEcho canvas.
4. Receive source-grounded feedback according to training or exam mode.
5. Persist result, misconception evidence, and concept-level mastery update.
6. Continue with the next adaptive action.

### 10.3 Course improvement loop

Low-quality tasks, unsupported explanations, repeated misconceptions, low-confidence evaluations, and missing source coverage enter a review queue. They never silently rewrite a published course revision.

## 11. AI Course Factory

Course generation is a persistent Learning-owned job with these states:

```text
queued -> ingesting -> structuring -> authoring -> assessing
       -> verifying -> quality_gate -> review_ready
       -> published
```

Terminal non-success states are `failed`, `cancelled`, and `rejected`. A retry creates a new attempt under the same job and never rewrites prior evidence.

### 11.1 Pipeline roles

1. **Deterministic ingestion:** Parse, normalize, hash, deduplicate, version, and chunk sources.
2. **Curriculum Architect:** Define audience, prerequisites, outcomes, and chapter sequence.
3. **Concept Mapper:** Extract concepts, dependencies, coverage, and difficulty bands.
4. **Lesson Author:** Draft explanations, examples, summaries, and glossary entries.
5. **Assessment Designer:** Create quiz, open, case, code, and PenEcho activities with rubrics.
6. **Source Verifier:** Map factual claims and expected answers to retrieved source evidence.
7. **Quality Gate:** Apply deterministic schema, coverage, duplication, solvability, and distribution checks plus bounded AI review.
8. **Human review:** Require explicit user approval before publication.

AutoGen may coordinate the reasoning team, but it owns no lifecycle, publication, or durable course state. The Learning service persists jobs, attempts, artifacts, and evidence. Model execution is requested through OpenFang; LearnHouse, AutoGen, PenEcho, and the renderer do not hold independent direct-provider authority.

## 12. Data Ownership

### 12.1 PostgreSQL

PostgreSQL is the source of truth for:

- courses and immutable published revisions;
- chapters, lessons, activities, items, and rubrics;
- source metadata and source versions;
- concept maps and item-to-concept mappings;
- generation jobs, attempts, review decisions, and provenance;
- learning sessions, responses, evaluations, and submissions;
- concept mastery, review schedules, and mistake journal entries;
- artifact metadata and Qdrant outbox records.

LearnHouse migrations remain owned by the LearnHouse fork. Learning extension tables use a clearly prefixed namespace or separate schema and their own migration chain.

### 12.2 Qdrant

Qdrant is the only semantic retrieval store for the `learning` Space. LearnHouse's native pgvector retrieval path is replaced behind a bounded adapter.

Use a versioned collection and stable alias:

```text
learning_knowledge_v1
learning_knowledge_current -> learning_knowledge_v1
```

Each point payload includes course ID, source ID, source revision, chunk ID, concept IDs, locator, content hash, visibility, and embedding model/version. Point IDs are deterministic from source revision and chunk identity.

PostgreSQL outbox processing is at-least-once and idempotent. Qdrant is a rebuildable projection; loss or corruption of the collection must not lose courses, source files, or progress.

### 12.3 Redis

Redis stores bounded caches, job notifications, locks, and UI event fan-out. Keys use a `learning:` namespace. Redis does not own terminal job or learning state.

### 12.4 Local artifacts

Original source files, generated artifacts, code submissions, and PenEcho canvas revisions live under a Learning-owned local data directory outside packaged application code. PostgreSQL stores their content hashes, media types, ownership, and revision references.

## 13. Adaptive Engine

The adaptive engine operates at concept level and selects the next activity from published, quality-approved items.

### 13.1 V1 mastery model

- Each concept begins with a neutral Beta prior `alpha=2`, `beta=2`.
- Every evaluated response produces a normalized score `s` in `[0,1]` and an evidence confidence `c` in `[0,1]`.
- Deterministically scored responses use `c=1`. Rubric-scored open, AI, or PenEcho responses use the persisted evaluator confidence.
- Evaluator confidence below `0.65` does not update mastery and creates a review item.
- For an accepted update covering `n` concepts, each concept receives weight `w=c/n`: `alpha += w*s` and `beta += w*(1-s)`.
- Mastery is the posterior mean `alpha / (alpha + beta)` and is always traceable to contributing attempts.

### 13.2 Item selection

Each item stores normalized difficulty `d` in `[0,1]`. For its tagged concepts, take the minimum current mastery `m`, clamp it to `[0.01,0.99]`, and estimate success as:

```text
p_success = sigmoid(logit(m) - 4 * (d - 0.5))
```

Rank eligible candidates by:

1. distance from the target success probability `0.72`;
2. overdue spaced-review priority;
3. unresolved misconception coverage;
4. source and task-quality status;
5. recent-item repetition penalty.

The normal target band is 0.65 to 0.80 predicted success. Exam mode samples across the configured blueprint instead of adapting after each answer and withholds feedback until completion.

Successful reviews use intervals of 1, 3, 7, and 14 days before moving to maintenance scheduling. A failed review returns the concept to the one-day interval and requests a different representation or task type.

## 14. PenEcho Integration

PenEcho is represented in LearnHouse as a `penecho_canvas` activity type.

The launch context contains:

- course, chapter, activity, and session IDs;
- task prompt and learning objective;
- source references and allowed context;
- rubric and help policy;
- training/exam mode;
- current canvas revision when resuming.

PenEcho opens inside the central Learning workspace and runs locally on loopback. It stores versioned canvas artifacts outside the packaged application. LearnHouse stores the activity, submission, evaluation, and artifact references.

The user may draw, write, enter formulas, create diagrams, and request bounded hints. The tutor can respond to a selected region without revealing a full solution when the help policy forbids it.

Submission produces:

- immutable canvas revision reference;
- cropped/rendered visual artifact;
- structured accepted PenEcho objects where available;
- rubric evaluation with per-criterion score;
- evaluator confidence;
- misconception tags and source-grounded feedback.

Low-confidence evaluation enters review and does not update mastery. PenEcho credentials and provider keys remain server-side and never enter browser or renderer code.

## 15. Runtime and Deployment

The local `learning` VibeMind profile starts only its declared services:

- LearnHouse Web;
- LearnHouse API;
- LearnHouse collaboration service;
- Learning worker;
- Learning MCP;
- PenEcho;
- required PostgreSQL, Redis, and Qdrant dependencies from VibeMind infrastructure.

Services bind to loopback or the internal container network unless an explicit VibeMind configuration enables another boundary. Secrets live in ignored environment files, Docker secrets, or the approved host secret store.

Health is split into:

- liveness: process responds;
- readiness: owned dependencies and migrations are usable;
- structural registry health: Space/agent/tool contracts agree;
- Golden Path health: a current correlated end-to-end operation reaches terminal readback.

No status endpoint may label structural configuration as live execution proof.

## 16. Failure and Degraded Behavior

- **LearnHouse unavailable:** show read-only cached navigation if available, reject writes, and do not claim session progress.
- **Qdrant unavailable:** preserve ingestion records in the outbox, disable source-grounded generation/tutoring, and never silently switch to ungrounded publication.
- **OpenFang/provider unavailable:** persist the job as queued or failed with retry metadata; deterministic course access remains available.
- **Redis unavailable:** fall back to PostgreSQL polling only where explicitly implemented; never lose terminal state.
- **PenEcho unavailable:** keep non-canvas learning available and retain the pending activity; do not replace it with a different graded task without recording the change.
- **Evaluator low confidence:** persist submission, withhold mastery update, and create review work.
- **UI bridge unavailable:** the backend operation may succeed with readback, but the result must say that UI projection failed; reloading reconstructs the state.
- **Duplicate MCP invocation:** return the existing result for the idempotency key.
- **Revision conflict:** reject with current revision and require an explicit retry or save-as-new action.

## 17. Migration and Replacement

The existing `C:/Users/User/Desktop/Learning_plattform` prototype remains untouched until the new Space passes migration and acceptance gates.

### 17.1 Migration mapping

- universities/programs/modules/topics -> LearnHouse organization defaults, courses, chapters, and concepts;
- source documents and versions -> Learning sources and local artifacts;
- questions/answers/tasks -> activities, items, expected answers, and rubrics;
- quiz attempts/evaluations -> sessions, responses, evaluations, and initial mastery evidence;
- knowledge chunks -> re-created Qdrant points from source versions, never copied as unverified vectors;
- local mistake journal -> review schedule and misconception records.

The migration is export-transform-validate-import, not shared in-place table mutation. Every record receives a source-system identifier and migration batch ID. Counts, hashes, foreign-key integrity, and representative content samples are checked before cutover.

### 17.2 Cutover sequence

1. Pin and test unmodified LearnHouse and PenEcho upstream baselines.
2. Create controlled forks and submodule pins.
3. Add read-only `learning` Space identity, registry, health, and MCP status contracts.
4. Add local single-user configuration, VibeMind theme, and UI bridge.
5. Add Learning MCP write tools with truth readback.
6. Replace LearnHouse pgvector retrieval with the Qdrant adapter and outbox.
7. Add Course Factory and Adaptive Engine.
8. Add PenEcho activity integration.
9. Export and dry-run validation of prototype data.
10. Import into a fresh Learning database and rebuild Qdrant.
11. Run the full MCP-driven Golden Path.
12. Switch the VibeMind Learning route to the new Space.
13. Keep the prototype as a read-only archive until the user separately approves removal.

Rollback switches the route/profile back to the prior application and preserves both databases. No rollback step deletes migrated or source data.

## 18. Testing Strategy

Implementation is TDD-first and preserves upstream test suites.

### 18.1 Test layers

- Upstream baseline tests before and after fork changes.
- Unit tests for contracts, adapters, adaptive scoring, item selection, and outbox idempotency.
- Registry tests for canonical `learning` identity, event mapping, agent claim, and allowed MCP scope.
- MCP contract tests for validation, confirmation, idempotency, revision conflicts, and fail-closed behavior.
- Integration tests with PostgreSQL, Redis, Qdrant, LearnHouse, and PenEcho.
- UI tests for stale-intent rejection, reload reconstruction, and the approved layout states.
- Security tests proving secrets do not reach renderer/browser payloads.
- Migration dry-run and reconciliation tests.
- Electron/VibeMind end-to-end tests driven through intents and MCP, not manual clicks.

### 18.2 Required Golden Path

```text
material import
  -> source version and artifact persisted
  -> Qdrant projection verified
  -> course generation completed
  -> unsupported claims visible
  -> user review and publish confirmation
  -> chapter and adaptive session opened
  -> answer submitted and evaluated
  -> PenEcho canvas opened, saved, and submitted
  -> evaluation and confidence persisted
  -> mastery and review schedule updated
  -> next adaptive task selected
  -> progress shown in the VibeMind UI
```

Every hop carries correlated identifiers and the test reads terminal state from the owning application.

## 19. Acceptance Criteria

The replacement is complete only when all of the following are true:

- `learning` is a canonical, registry-consistent VibeMind Space.
- The user can operate the complete normal workflow through VibeMind voice/text intents and Learning MCP.
- LearnHouse course authoring and delivery work locally under the VibeMind fork.
- Course generation produces reviewable, source-linked drafts and cannot auto-publish.
- Qdrant is the sole Learning semantic index and can be rebuilt from PostgreSQL and artifacts.
- Adaptive selection becomes harder with demonstrated mastery and schedules remediation for weak concepts.
- PenEcho is available as a resumable, graded, source-aware activity type.
- Low-confidence AI grading cannot silently update mastery.
- The approved UI layout projects confirmed state and recovers correctly after reload.
- Migration reconciliation passes without losing source records, questions, attempts, or artifacts.
- The MCP-driven Golden Path passes with fresh correlated evidence.
- The old prototype remains recoverable and is not deleted by this project.

## 20. Explicit Evidence Limits

This document is an approved design, not runtime evidence. It does not claim that forks, submodules, registry entries, MCP tools, services, migrations, Qdrant collections, or Golden Path execution currently exist. Those claims require implementation artifacts and fresh verification in the target checkout.
