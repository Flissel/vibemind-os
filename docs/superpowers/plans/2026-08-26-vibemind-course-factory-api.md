# VibeMind Course Factory API Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose the existing source-grounded Learning Course Factory to VibeMind OS through authenticated REST and the existing MCP tools, with one lifecycle implementation and OpenFang as the only AI execution boundary.

**Architecture:** Add typed HTTP contracts and a VibeMind request-context dependency, then move Course Factory command handling behind a transport-neutral application facade. REST routes and MCP gateways submit equivalent commands through the durable Learning dispatcher, while the existing worker, repository, quality gate, LearnHouse publisher, and OpenFang gateway retain their current ownership.

**Tech Stack:** Python 3.11+, FastAPI, Pydantic v2, SQLAlchemy, PostgreSQL, Redis, Qdrant, pytest, Docker Compose, MCP JSON-RPC, OpenFang.

**Spec:** `docs/superpowers/specs/2026-08-26-vibemind-course-factory-api-design.md`

## Global Constraints

- Canonical Space ID remains exactly `learning`; `config/space_agent_registry.yml` is the public Space/event authority.
- All generative AI calls use `OpenFangModelGateway`; no direct OpenAI/provider fallback is permitted.
- HTTP binds only to the existing loopback Learning API and requires `LEARNING_VIBEMIND_SERVICE_KEY` with at least 32 characters.
- Mutating requests require an idempotency key and reuse `learning_invocation_receipts` through `SqlReceiptStore`.
- Generated courses remain drafts until an authorized review and explicit publication confirmation succeed.
- Provider keys, hidden expected answers, raw reasoning, host paths, and unbounded source content never enter public responses.
- Existing MCP tool names and event names remain backward compatible.
- Fake deterministic provider evidence never supports a live OpenFang claim.

## File Map

- `spaces/learning/contracts/course_factory_api.py`: immutable HTTP request, response, authority, and error schemas.
- `spaces/learning/deployment/vibemind_auth.py`: loopback bearer-key validation and typed actor/role context.
- `spaces/learning/services/course_factory/application.py`: transport-neutral Course Factory commands and outcome/readback mapping.
- `spaces/learning/deployment/course_factory_routes.py`: FastAPI router and HTTP-to-application translation.
- `spaces/learning/services/ingestion/material_staging.py`: allowlisted local/HTTPS material staging into `LearningArtifact` records.
- `spaces/learning/mcp/tools/generation.py`: thin compatibility adapter over `CourseFactoryApplication`.
- `spaces/learning/mcp/server.py`: shared dispatcher construction for API and MCP service roles.
- `spaces/learning/deployment/runtime_api.py`: mount the new router and stable error handler.
- `spaces/learning/deployment/compose.yml` and `.env.example`: VibeMind service key, import root, and API dependencies.
- `config/space_agent_registry.yml`: document the REST adapter without creating a second Space identity.

---

### Task 1: Public Contracts and VibeMind Authority

**Files:**
- Create: `spaces/learning/contracts/course_factory_api.py`
- Create: `spaces/learning/deployment/vibemind_auth.py`
- Test: `spaces/learning/tests/contract/test_course_factory_api.py`
- Test: `spaces/learning/tests/unit/test_vibemind_auth.py`

**Interfaces:**
- Produces: `VibeMindRequestContext`, `CourseGenerationRequestV1`, `FactoryJobResponseV1`, `ReviewRequestV1`, `PublishRequestV1`, `MaterialImportRequestV1`, and `ApiErrorV1`.
- Consumes: canonical `learning` Space identity and existing `ActorV1` token rules.

- [ ] **Step 1: Write failing contract tests**

```python
def test_generation_request_rejects_empty_sources_and_provider_fields() -> None:
    with pytest.raises(ValidationError):
        CourseGenerationRequestV1.model_validate({
            "course_id": str(uuid4()), "title": "Safety", "audience": "Engineers",
            "level": "advanced", "locale": "de-DE",
            "learning_objectives": ["Assess risk"], "source_revision_ids": [],
            "provider": "openai",
        })

def test_publish_requires_current_revision_and_confirmation() -> None:
    value = PublishRequestV1(expected_revision=7, confirmed=True, approval_ref="approval-7")
    assert value.expected_revision == 7
```

- [ ] **Step 2: Run the contract tests and verify RED**

Run: `python -m pytest spaces/learning/tests/contract/test_course_factory_api.py -q`

Expected: collection fails because `course_factory_api` does not exist.

- [ ] **Step 3: Implement frozen Pydantic contracts**

```python
class CourseGenerationRequestV1(ContractModel):
    course_id: UUID
    title: Annotated[str, Field(min_length=1, max_length=300)]
    audience: Annotated[str, Field(min_length=1, max_length=1000)]
    level: Literal["beginner", "intermediate", "advanced", "expert"]
    locale: Annotated[str, Field(pattern=r"^[a-z]{2}(?:-[A-Z]{2})?$")]
    learning_objectives: Annotated[tuple[BoundedText, ...], Field(min_length=1, max_length=32)]
    source_revision_ids: Annotated[tuple[UUID, ...], Field(min_length=1, max_length=256)]

class PublishRequestV1(ContractModel):
    expected_revision: Annotated[int, Field(ge=0)]
    confirmed: Literal[True]
    approval_ref: SafeToken
```

Use discriminated `MaterialSourceV1` variants `local_import` with a safe relative POSIX path and `https` with a bounded URL. Add validators that reject duplicate sources, secret/provider keys, absolute paths, credentials in URLs, fragments, and non-HTTPS remote schemes.

- [ ] **Step 4: Write failing authority tests**

```python
def test_request_context_requires_valid_key_learning_space_and_role(monkeypatch) -> None:
    monkeypatch.setenv("LEARNING_VIBEMIND_SERVICE_KEY", "k" * 32)
    context = require_vibemind_context(
        authorization="Bearer " + "k" * 32,
        actor_id="local-owner", space_id="learning",
        roles="learning.author,learning.reviewer", correlation_id=str(uuid4()),
    )
    context.require("learning.author")
    with pytest.raises(HTTPException) as denied:
        context.require("learning.publisher")
    assert denied.value.status_code == 403
```

- [ ] **Step 5: Implement fail-closed authority parsing**

```python
@dataclass(frozen=True)
class VibeMindRequestContext:
    actor_id: str
    space_id: Literal["learning"]
    roles: frozenset[str]
    correlation_id: UUID

    def require(self, role: str) -> None:
        if role not in self.roles:
            raise HTTPException(status_code=403, detail="Missing Learning role")
```

Compare the bearer key with `hmac.compare_digest`; return `503` for insecure server configuration, `401` for invalid credentials, and `403` unless normalized Space is exactly `learning`. Accept only the three roles declared by the spec.

- [ ] **Step 6: Run Task 1 tests and commit**

Run: `python -m pytest spaces/learning/tests/contract/test_course_factory_api.py spaces/learning/tests/unit/test_vibemind_auth.py -q`

Expected: PASS.

```bash
git add spaces/learning/contracts/course_factory_api.py spaces/learning/deployment/vibemind_auth.py spaces/learning/tests/contract/test_course_factory_api.py spaces/learning/tests/unit/test_vibemind_auth.py
git commit -m "feat(learning): define VibeMind course API authority"
```

### Task 2: Transport-Neutral Course Factory Application

**Files:**
- Create: `spaces/learning/services/course_factory/application.py`
- Modify: `spaces/learning/mcp/tools/generation.py`
- Modify: `spaces/learning/mcp/server.py`
- Test: `spaces/learning/tests/unit/test_course_factory_application.py`
- Test: `spaces/learning/tests/integration/test_generation_mcp.py`

**Interfaces:**
- Consumes: `CourseFactoryRepository`, `CourseDraftPublisher`, `CourseFactoryArtifactStore`, `SqlReceiptStore`, and existing `ToolRequestV1` contracts.
- Produces: `CourseFactoryApplication.execute(ToolRequestV1) -> ApplicationOutcomeV1` and `readback(ToolRequestV1, ApplicationOutcomeV1) -> TruthReadbackV1 | None`.

- [ ] **Step 1: Write failing application tests for create, retry, status, review, and publish**

```python
def test_application_creates_source_grounded_queued_job(factory_application, source_revision) -> None:
    result = factory_application.execute(generation_request(source_revision))
    assert result.state == "completed"
    assert result.result["state"] == "queued"
    assert result.aggregate.aggregate_type == "course_factory_job"

def test_application_rejects_unindexed_or_hash_mismatched_source(factory_application) -> None:
    result = factory_application.execute(generation_request_for_unready_source())
    assert result.state == "rejected"
    assert result.error.code == "invalid_generation_request"
```

- [ ] **Step 2: Run RED application tests**

Run: `python -m pytest spaces/learning/tests/unit/test_course_factory_application.py -q`

Expected: import failure for `CourseFactoryApplication`.

- [ ] **Step 3: Move lifecycle methods from the MCP gateway into the application facade**

```python
class CourseFactoryApplication:
    def execute(self, request: ToolRequestV1) -> ApplicationOutcomeV1:
        handlers = {
            LearningToolName.COURSE_GENERATE: self._generate,
            LearningToolName.GENERATION_STATUS: self._status,
            LearningToolName.COURSE_REVIEW: self._review,
            LearningToolName.COURSE_PUBLISH: self._publish,
        }
        try:
            return handlers[request.tool](request)
        except PersistenceConflict as error:
            return rejected("revision_conflict", str(error))
        except (LookupError, ValueError) as error:
            return rejected("invalid_generation_request", str(error))
```

Keep `_MAX_ATTEMPTS = 3`, source hash/projection checks, aggregate revisions, publish confirmation, and readback evidence behavior unchanged. Do not catch provider failures in this layer; the worker persists them through the existing runner.

- [ ] **Step 4: Replace `CourseFactoryGateway` internals with a thin compatibility adapter**

```python
class CourseFactoryGateway:
    def __init__(self, application: CourseFactoryApplication) -> None:
        self._application = application

    def execute(self, request: ToolRequestV1) -> ApplicationOutcomeV1:
        return self._application.execute(request)

    def readback(self, request, outcome):
        return self._application.readback(request, outcome)
```

Update `build_generation_gateways` and `build_default_dispatcher` to construct one application instance. Allow the generation/material subset to be built for both `LEARNING_SERVICE_ROLE=api` and `mcp`; keep canvas/session-only dependencies on the MCP service role.

- [ ] **Step 5: Run application and MCP regression suites**

Run: `python -m pytest spaces/learning/tests/unit/test_course_factory_application.py spaces/learning/tests/integration/test_generation_mcp.py spaces/learning/tests/integration/test_generation_flow.py spaces/learning/tests/integration/test_publish_confirmation.py -q`

Expected: PASS with unchanged MCP tool names and outcome shapes.

- [ ] **Step 6: Commit Task 2**

```bash
git add spaces/learning/services/course_factory/application.py spaces/learning/mcp/tools/generation.py spaces/learning/mcp/server.py spaces/learning/tests/unit/test_course_factory_application.py spaces/learning/tests/integration/test_generation_mcp.py
git commit -m "refactor(learning): share course factory application logic"
```

### Task 3: Authenticated Course Factory HTTP Routes

**Files:**
- Create: `spaces/learning/deployment/course_factory_routes.py`
- Modify: `spaces/learning/deployment/runtime_api.py`
- Test: `spaces/learning/tests/integration/test_course_factory_api.py`

**Interfaces:**
- Consumes: Task 1 contracts/context and Task 2 shared `LearningDispatcher`/application path.
- Produces: `/api/v1/course-factory/jobs`, job readback, review, publish, and `/api/v1/courses/{course_id}`.

- [ ] **Step 1: Write failing API tests with injected dispatcher**

```python
def test_create_job_returns_202_and_persisted_revision(api_client, ready_source) -> None:
    response = api_client.post(
        "/api/v1/course-factory/jobs",
        headers=author_headers("learning.author", idempotency_key="job-1"),
        json=generation_payload(ready_source),
    )
    assert response.status_code == 202
    assert response.json()["state"] == "queued"
    assert response.json()["revision"] == 0

def test_idempotency_conflict_maps_to_409(api_client, ready_source) -> None:
    first = generation_payload(ready_source)
    api_client.post("/api/v1/course-factory/jobs", headers=author_headers("learning.author", "same"), json=first)
    changed = {**first, "title": "Different"}
    assert api_client.post("/api/v1/course-factory/jobs", headers=author_headers("learning.author", "same"), json=changed).status_code == 409
```

Also cover `401`, `403`, missing idempotency key, stale review/publish revision, missing confirmation, `404`, and stable redacted error envelopes.

- [ ] **Step 2: Run RED API tests**

Run: `python -m pytest spaces/learning/tests/integration/test_course_factory_api.py -q`

Expected: `404` for all new endpoints.

- [ ] **Step 3: Implement the router using one event builder**

```python
def _event(context, event_type, *, idempotency_key=None, course_id=None,
           expected_revision=None, confirmation=None, payload=None) -> EventEnvelopeV1:
    return EventEnvelopeV1(
        event_type=event_type,
        invocation_id=uuid4(), correlation_id=context.correlation_id,
        actor=ActorV1(actor_id=context.actor_id, actor_type="local_user"),
        course_id=course_id, idempotency_key=idempotency_key,
        expected_revision=expected_revision, confirmation=confirmation,
        payload=payload or {},
    )
```

Dispatch through the durable API-role dispatcher. Map queued creation to `202`; completed status/review/publish to `200`; `idempotency_conflict` and `revision_conflict` to `409`; source/quality rejection to `422`; unavailable outcomes to `503`. Return only `FactoryJobResponseV1` or `ApiErrorV1`.

- [ ] **Step 4: Mount the router and stable exception handler**

```python
app = FastAPI(title="VibeMind Learning Runtime", version="1.1.0")
app.include_router(course_factory_router)

@app.exception_handler(RequestValidationError)
async def validation_error(request, error):
    return JSONResponse(ApiErrorV1(code="invalid_request", message="Request validation failed", retryable=False, correlation_id=request.state.correlation_id).model_dump(mode="json"), status_code=400)
```

Do not expose Pydantic input echoes or FastAPI internal exception strings.

- [ ] **Step 5: Run API, MCP, retrieval, and health tests**

Run: `python -m pytest spaces/learning/tests/integration/test_course_factory_api.py spaces/learning/tests/integration/test_generation_mcp.py spaces/learning/tests/integration/test_retrieval_api.py spaces/learning/tests/integration/test_health.py -q`

Expected: PASS.

- [ ] **Step 6: Commit Task 3**

```bash
git add spaces/learning/deployment/course_factory_routes.py spaces/learning/deployment/runtime_api.py spaces/learning/tests/integration/test_course_factory_api.py
git commit -m "feat(learning): expose authenticated course factory API"
```

### Task 4: Safe Programmatic Material Import

**Files:**
- Create: `spaces/learning/services/ingestion/material_staging.py`
- Modify: `spaces/learning/deployment/course_factory_routes.py`
- Test: `spaces/learning/tests/unit/test_material_staging.py`
- Test: `spaces/learning/tests/integration/test_material_import_api.py`

**Interfaces:**
- Consumes: `MaterialImportRequestV1`, `LearningArtifact`, existing `learning_material_import` dispatcher path, and `LEARNING_IMPORT_ROOT`.
- Produces: `MaterialStagingService.stage(...) -> StagedMaterial` and `POST /api/v1/materials/import`.

- [ ] **Step 1: Write failing staging security tests**

```python
def test_local_import_stays_beneath_allowlisted_root(stager, import_root) -> None:
    (import_root / "manual.md").write_text("# Safety", encoding="utf-8")
    staged = stager.stage(LocalImportSource(relative_path="manual.md"))
    assert staged.media_type == "text/markdown"
    assert staged.content_hash == hashlib.sha256(b"# Safety").hexdigest()

@pytest.mark.parametrize("url", [
    "http://example.com/a.pdf", "https://127.0.0.1/a.pdf",
    "https://169.254.169.254/latest/meta-data", "https://user:pass@example.com/a.pdf",
])
def test_remote_import_rejects_unsafe_targets(stager, url) -> None:
    with pytest.raises(MaterialStageRejected):
        stager.stage(HttpsImportSource(url=url))
```

Cover traversal, symlinks/reparse points, DNS resolution to private/link-local/loopback ranges, redirects to blocked ranges, unsupported media type, more than 50 MB, hash changes, timeout, and partial-file cleanup.

- [ ] **Step 2: Run RED staging tests**

Run: `python -m pytest spaces/learning/tests/unit/test_material_staging.py -q`

Expected: import failure for `material_staging`.

- [ ] **Step 3: Implement bounded staging and immutable artifact registration**

```python
@dataclass(frozen=True)
class StagedMaterial:
    artifact_id: str
    media_type: str
    content_hash: str
    size_bytes: int

class MaterialStagingService:
    def stage(self, source: MaterialSourceV1) -> StagedMaterial:
        path = self._copy_local(source) if source.kind == "local_import" else self._download_https(source)
        return self._register_verified_artifact(path)
```

Resolve local paths strictly beneath `LEARNING_IMPORT_ROOT`. For HTTPS, inject the resolver/client in tests, resolve every hop, allow public addresses only, cap redirects at three, stream at most 50 MB, and accept only parser-supported media types. Store the final bytes below the Learning artifact root and insert `LearningArtifact(aggregate_type="source_upload")` in one transaction.

- [ ] **Step 4: Write and implement the material import API flow**

```python
def test_material_import_stages_then_dispatches(api_client, import_root) -> None:
    response = api_client.post(
        "/api/v1/materials/import",
        headers=author_headers("learning.author", "material-1"),
        json={"course_id": str(uuid4()), "title": "Manual", "source": {"kind": "local_import", "relative_path": "manual.md"}},
    )
    assert response.status_code == 202
    assert response.json()["projection_state"] == "pending"
```

The route stages bytes, inserts the generated `artifact_id` into an internal `learning.material.import` event, and dispatches through the same durable receipt store. Client input never contains the artifact-root host path.

- [ ] **Step 5: Run material and existing ingestion tests**

Run: `python -m pytest spaces/learning/tests/unit/test_material_staging.py spaces/learning/tests/integration/test_material_import_api.py spaces/learning/tests/unit/test_ingestion.py spaces/learning/tests/integration/test_source_repository.py -q`

Expected: PASS.

- [ ] **Step 6: Commit Task 4**

```bash
git add spaces/learning/services/ingestion/material_staging.py spaces/learning/deployment/course_factory_routes.py spaces/learning/tests/unit/test_material_staging.py spaces/learning/tests/integration/test_material_import_api.py
git commit -m "feat(learning): add safe programmatic material import"
```

### Task 5: VibeMind Space and Docker Wiring

**Files:**
- Modify: `spaces/learning/deployment/compose.yml`
- Modify: `spaces/learning/deployment/.env.example`
- Modify: `spaces/learning/deployment/test_compose.py`
- Modify: `config/space_agent_registry.yml`
- Modify: `spaces/learning/tests/contract/test_registry.py`
- Modify: `spaces/learning/README.md`

**Interfaces:**
- Consumes: `LEARNING_VIBEMIND_SERVICE_KEY`, `LEARNING_IMPORT_ROOT`, existing LearnHouse internal key, and the `learning` registry entry.
- Produces: a reproducible loopback deployment with the HTTP Course Factory adapter enabled.

- [ ] **Step 1: Write failing Compose and registry assertions**

```python
def test_learning_api_has_vibemind_course_factory_secrets_and_import_volume(compose) -> None:
    api = compose["services"]["learning-api"]
    assert api["environment"]["LEARNING_VIBEMIND_SERVICE_KEY"] == "${LEARNING_VIBEMIND_SERVICE_KEY:?set LEARNING_VIBEMIND_SERVICE_KEY}"
    assert api["environment"]["LEARNHOUSE_LEARNING_SERVICE_KEY"] == "${LEARNHOUSE_LEARNING_SERVICE_KEY:?set LEARNHOUSE_LEARNING_SERVICE_KEY}"
    assert api["environment"]["LEARNING_IMPORT_ROOT"] == "/var/lib/vibemind-learning/imports"

def test_learning_registry_declares_http_adapter_without_new_space(registry) -> None:
    assert registry["spaces"]["learning"]["http_api"] == "http://127.0.0.1:8090/api/v1"
    assert "course-factory" not in registry["spaces"]
```

- [ ] **Step 2: Run RED deployment tests**

Run: `python -m pytest spaces/learning/deployment/test_compose.py spaces/learning/tests/contract/test_registry.py -q`

Expected: assertions fail because the environment and HTTP adapter metadata are absent.

- [ ] **Step 3: Wire secrets, import storage, service dependencies, and registry metadata**

Add placeholders only to `.env.example`; never add real values. Mount a named `learning-imports` volume read-only or bind an explicitly configured local import directory. Keep host ports bound to `127.0.0.1`. Give only `learning-api` and `learning-mcp` the VibeMind service key; give OpenFang keys only to worker/evaluation processes. Add `http_api` metadata to the existing `learning` registry entry without changing canonical events or MCP tools.

- [ ] **Step 4: Document request flow and a redacted PowerShell example**

```powershell
$headers = @{
  Authorization = "Bearer $env:LEARNING_VIBEMIND_SERVICE_KEY"
  "X-VibeMind-Actor-ID" = "local-owner"
  "X-VibeMind-Space-ID" = "learning"
  "X-VibeMind-Roles" = "learning.author"
  "X-VibeMind-Correlation-ID" = [guid]::NewGuid().ToString()
  "Idempotency-Key" = [guid]::NewGuid().ToString()
}
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8090/api/v1/course-factory/jobs -Headers $headers -ContentType application/json -Body $body
```

- [ ] **Step 5: Run deployment contracts and Compose config validation**

Run: `python -m pytest spaces/learning/deployment/test_compose.py spaces/learning/tests/contract/test_registry.py -q`

Run: `docker compose --env-file spaces/learning/deployment/.env -f spaces/learning/deployment/compose.yml config --quiet`

Expected: tests PASS and Compose config exits `0` without printing secrets.

- [ ] **Step 6: Commit Task 5**

```bash
git add spaces/learning/deployment/compose.yml spaces/learning/deployment/.env.example spaces/learning/deployment/test_compose.py config/space_agent_registry.yml spaces/learning/tests/contract/test_registry.py spaces/learning/README.md
git commit -m "feat(learning): wire Course Factory into VibeMind Space"
```

### Task 6: Transport Equivalence, OpenFang Boundary, and Golden Path

**Files:**
- Create: `spaces/learning/tests/e2e/test_course_factory_transport_equivalence.py`
- Modify: `spaces/learning/tests/contract/test_openfang_scope.py`
- Modify: `spaces/learning/tests/e2e/test_secret_boundary.py`
- Modify: `spaces/learning/validation/golden_path.py`
- Modify: `spaces/learning/tests/e2e/test_golden_path.py`
- Modify: `docs/learning/acceptance-report.md`

**Interfaces:**
- Consumes: completed HTTP/MCP routes, deterministic fake OpenFang gateway, Docker runtime, and terminal readbacks.
- Produces: acceptance evidence that both transports share state and that AI remains OpenFang-owned.

- [ ] **Step 1: Write failing transport-equivalence and OpenFang-boundary tests**

```python
def test_http_create_and_mcp_status_observe_same_job(runtime) -> None:
    created = runtime.http_create_job()
    observed = runtime.mcp_generation_status(created["job_id"])
    assert observed["aggregate"] == created["aggregate"]
    assert observed["result"]["revision"] == created["revision"]

def test_no_direct_provider_credentials_reach_public_services(compose) -> None:
    for name in ("learning-api", "learning-mcp", "learnhouse-web"):
        env = compose["services"][name].get("environment", {})
        assert "OPENAI_API_KEY" not in env
        assert "LEARNING_OPENFANG_API_KEY" not in env
```

Also prove HTTP replay returns the same persisted receipt after API restart, HTTP and MCP conflicts agree, publish requires confirmation, and a schema-invalid fake OpenFang response cannot reach `review_ready`.

- [ ] **Step 2: Run RED focused acceptance tests**

Run: `python -m pytest spaces/learning/tests/e2e/test_course_factory_transport_equivalence.py spaces/learning/tests/contract/test_openfang_scope.py spaces/learning/tests/e2e/test_secret_boundary.py -q`

Expected: new transport tests fail until the Golden Path exposes HTTP helpers and equivalent readback.

- [ ] **Step 3: Extend the deterministic Golden Path**

Add HTTP material import and job creation before the existing MCP status/review/publish sequence. Record transport, correlation ID, aggregate revision, receipt ID, and evidence owner for every step. Keep `live_provider_claim=False` when using the fake gateway.

- [ ] **Step 4: Run all focused Learning suites**

Run: `python -m pytest spaces/learning/tests/contract spaces/learning/tests/unit spaces/learning/tests/integration spaces/learning/tests/e2e -q`

Expected: all Learning-owned tests PASS; pre-existing upstream LearnHouse or PenEcho baseline failures remain separately reported.

- [ ] **Step 5: Rebuild and smoke-test Docker**

Run: `docker compose --env-file spaces/learning/deployment/.env -f spaces/learning/deployment/compose.yml up -d --build`

Run: `python -m spaces.learning.deployment.runtime_smoke --compose-file spaces/learning/deployment/compose.yml`

Run: `python scripts/run_learning_golden_path.py`

Expected: all Learning services healthy; receipt replay survives restart; HTTP-to-MCP Golden Path publishes one reviewed course with deterministic OpenFang-owned evidence and no live-provider claim.

- [ ] **Step 6: Update acceptance evidence and commit**

Document exact pass counts, Docker service health, known upstream failures, and explicit non-claims. Do not claim live OpenFang execution unless separately authorized and evidenced.

```bash
git add spaces/learning/tests/e2e/test_course_factory_transport_equivalence.py spaces/learning/tests/contract/test_openfang_scope.py spaces/learning/tests/e2e/test_secret_boundary.py spaces/learning/validation/golden_path.py spaces/learning/tests/e2e/test_golden_path.py docs/learning/acceptance-report.md
git commit -m "test(learning): prove VibeMind Course Factory integration"
```

## Final Verification

- [ ] Run `git status --short --branch` and confirm only intended tracked changes exist.
- [ ] Run `git diff --check HEAD~6..HEAD` and confirm no whitespace errors.
- [ ] Run the complete Learning-owned pytest command from Task 6.
- [ ] Confirm `docker compose ps` reports every Learning service healthy.
- [ ] Confirm HTTP and MCP show the same job ID, revision, publication state, and terminal evidence.
- [ ] Confirm tracked files and rendered responses contain no configured secret values.
- [ ] Report direct-provider execution, public SaaS, route cutover, and live OpenFang validation as non-claims unless separately proven.
