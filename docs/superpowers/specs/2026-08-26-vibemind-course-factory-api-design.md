# VibeMind Course Factory API Design

**Date:** 2026-08-26

**Status:** User-approved direction, written review pending

**Canonical Space ID:** `learning`

## 1. Purpose

Expose the existing source-grounded Course Factory as a stable VibeMind OS
interface. VibeMind applications use HTTP for deterministic software
integration and MCP for agent-driven operation. Both transports call one
application service and therefore enforce the same identity, idempotency,
revision, review, publication, and evidence rules.

All generative AI execution stays behind OpenFang. The Learning API, MCP
server, LearnHouse web application, and browser clients must not call model
providers directly or receive provider credentials.

## 2. Scope

This increment provides:

- authenticated material import for local files and bounded HTTPS sources;
- asynchronous source-grounded course generation;
- durable job status and evidence readback;
- explicit review and publication commands;
- stable HTTP and MCP projections of the same application operations;
- VibeMind actor, Space, and correlation context on every mutation;
- OpenFang-only AI execution for course generation and evaluation roles.

This increment does not provide:

- automatic publication;
- anonymous course creation;
- arbitrary callback URLs or outbound webhooks;
- direct provider selection or provider keys in requests;
- public SaaS multi-tenancy;
- a replacement for LearnHouse course persistence or rendering.

## 3. Architectural Decision

The implementation uses a transport-neutral `CourseFactoryApplication` in
`spaces/learning/services/course_factory/application.py`.

```text
VibeMind caller
  |-- HTTP /api/v1/materials and /api/v1/course-factory
  `-- MCP learning_material_import / learning_course_*
                       |
                       v
             CourseFactoryApplication
              |       |        |
              |       |        `-- LearnHouse publication adapter
              |       `-- durable Learning database and artifact store
              `-- CourseAgentTeam -> OpenFangModelGateway -> OpenFang
```

HTTP routers and MCP gateways validate transport envelopes, then construct
the same typed commands. They contain no independent lifecycle decisions.
Domain transitions remain owned by the existing Course Factory repository,
runner, quality gate, and publisher.

## 4. Public HTTP Contract

The Learning runtime exposes the following loopback endpoints under `8090`:

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/api/v1/materials/import` | Import a local artifact reference or bounded HTTPS source |
| `GET` | `/api/v1/materials/{source_id}` | Read source state, revision, hash, and projection state |
| `POST` | `/api/v1/course-factory/jobs` | Create an asynchronous generation job |
| `GET` | `/api/v1/course-factory/jobs/{job_id}` | Read lifecycle, revision, attempt, and evidence |
| `POST` | `/api/v1/course-factory/jobs/{job_id}/review` | Approve or reject the current review-ready revision |
| `POST` | `/api/v1/course-factory/jobs/{job_id}/publish` | Publish one approved revision after confirmation |
| `GET` | `/api/v1/courses/{course_id}` | Read the canonical course projection and publication state |

All successful mutations return `202 Accepted` for queued work or `200 OK`
for completed state transitions. Responses include `correlation_id`, the
current aggregate revision, lifecycle state, and evidence references. They
never include prompts, provider credentials, hidden expected answers, or raw
model reasoning.

### 4.1 Create Job

`POST /api/v1/course-factory/jobs` accepts:

```json
{
  "course_id": "UUID",
  "title": "Industrial AI Safety",
  "audience": "Maintenance engineers",
  "level": "advanced",
  "locale": "de-DE",
  "learning_objectives": ["Assess AI-assisted maintenance risks"],
  "source_revision_ids": ["UUID"]
}
```

At least one immutable, successfully projected source revision is required.
The service rejects unknown, stale, unprojected, or cross-Space sources.
Generation always creates a draft and never implies publication.

### 4.2 Review and Publish

Review requires `expected_revision` and a decision of `approve` or `reject`.
Publication requires the approved `expected_revision` plus an explicit
`confirmation` value defined by the existing publisher contract. A stale
revision returns `409 Conflict`. Repeating an already completed command with
the same idempotency key returns the original result without a second effect.

## 5. MCP Contract

The existing public tool names remain unchanged:

- `learning_material_import`
- `learning_course_create`
- `learning_course_generate`
- `learning_generation_status`
- `learning_course_review`
- `learning_course_publish`

MCP handlers call `CourseFactoryApplication`; they no longer own lifecycle
logic that differs from HTTP. Existing event envelopes, aggregate readback,
and UI intents stay backward compatible. Application readback remains the
only success proof exposed to Brain.

## 6. Identity and Authorization

V1 accepts a VibeMind service bearer token only on loopback. The token is
validated server-side and mapped to a typed request context containing:

- `actor_id`;
- `space_id`, which must normalize to canonical `learning`;
- `roles`;
- `correlation_id`;
- optional local organization context.

Course creation and material import require `learning.author`. Review requires
`learning.reviewer`. Publication requires `learning.publisher`. The local
Personal Mode may assign all three roles to the local owner, but it does not
bypass authorization internally.

Browser session cookies are not accepted as service credentials. Provider
keys and the LearnHouse internal service key remain separate from the
VibeMind caller credential.

## 7. Idempotency and Concurrency

Every mutating HTTP request requires `Idempotency-Key`. MCP continues to use
its invocation and correlation identifiers, projected into the same
idempotency record. The durable record binds:

- actor and canonical Space;
- operation name;
- canonical request hash;
- aggregate identity and result;
- terminal evidence reference.

Reusing a key with a different request returns `409 Conflict`. Review and
publication also use optimistic aggregate revisions. At most one worker may
claim a queued generation attempt; retries retain immutable attempt history
and the existing maximum-attempt rule.

## 8. OpenFang Authority Boundary

Course roles use the existing authorized role contracts: architect, concept
mapper, lesson author, assessment designer, source verifier, and quality
reviewer. Tutor, rubric, and PenEcho evaluation continue through their
existing OpenFang role contracts.

The application layer supplies source-bounded typed inputs. The
`OpenFangModelGateway` supplies the configured model alias and server-side
credential. A successful result requires schema validation and an
`openfang://completion/...` evidence reference.

If OpenFang is unavailable, rejects the request, or returns schema-invalid
output, the operation fails closed. There is no direct OpenAI fallback and no
claim that generation completed without terminal OpenFang evidence.

## 9. Material Ingestion

The import endpoint stores immutable source metadata and content hashes before
queuing parsing, normalization, chunking, embedding, and Qdrant projection.
Course generation may reference only source revisions whose durable outbox
projection is complete.

Local imports use server-resolved artifact references beneath an allowlisted
Learning import root; clients do not submit arbitrary host filesystem paths.
Remote imports allow HTTPS only, enforce size and content-type limits, reject
private or link-local destinations, and preserve the final source locator and
hash. Secrets and authorization headers are not accepted in source payloads.

## 10. Error Contract

Errors use a stable envelope with `code`, bounded `message`, `retryable`, and
`correlation_id`.

| HTTP | Code family | Meaning |
| --- | --- | --- |
| `400` | `invalid_request` | Schema or bounded-input failure |
| `401` | `unauthenticated` | Missing or invalid VibeMind identity |
| `403` | `forbidden` | Missing role or wrong canonical Space |
| `404` | `not_found` | Aggregate or source does not exist |
| `409` | `revision_conflict`, `idempotency_conflict` | Stale or conflicting mutation |
| `422` | `source_not_ready`, `quality_gate_failed` | Valid request cannot advance |
| `503` | `openfang_unavailable`, `dependency_unavailable` | Retryable dependency failure |

No error includes secrets, raw provider responses, internal filesystem paths,
or unbounded source content.

## 11. Repository Changes

The implementation remains inside canonical VibeMind OS ownership:

```text
spaces/learning/
  contracts/course_factory_api.py
  services/course_factory/application.py
  services/course_factory/idempotency.py
  deployment/course_factory_routes.py
  deployment/runtime_api.py
  mcp/tools/generation.py
  mcp/tools/courses.py
  tests/contract/
  tests/integration/
  tests/e2e/
config/space_agent_registry.yml
```

The registry receives no new canonical Space. It continues to map `learning.*`
events to the existing `spaces-learning` MCP server. Documentation records the
HTTP interface as an additional adapter, not a second authority source.

## 12. Verification

Implementation follows TDD and proves:

1. HTTP and MCP produce equivalent typed application commands and outcomes.
2. Authentication, roles, canonical Space, and idempotency fail closed.
3. Source readiness and immutable revision checks prevent ungrounded jobs.
4. Concurrent or repeated requests create no duplicate side effects.
5. Review and publication require current revisions and explicit authority.
6. OpenFang failure or invalid output cannot produce review-ready content.
7. Provider secrets never appear in tracked files, API responses, UI payloads,
   logs, or MCP results.
8. Docker health and the deterministic Golden Path remain green.

Focused suites run before the complete Learning test suite. Docker validation
uses fake deterministic provider evidence unless a separately authorized live
OpenFang run is performed. Fake evidence never supports a live-provider claim.

## 13. Acceptance Criteria

The increment is accepted when a VibeMind caller can import material, create a
course job, observe progress, approve the current draft, and explicitly
publish it through HTTP; the equivalent MCP Golden Path yields the same
aggregate revisions and terminal readbacks; all AI stages are evidenced as
OpenFang-owned; and no direct provider route or credential exposure exists.
