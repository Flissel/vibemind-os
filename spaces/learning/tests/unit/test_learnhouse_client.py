from __future__ import annotations

from uuid import UUID, uuid4

import httpx
import pytest

from spaces.learning.bridge.dispatcher import ApplicationOutcomeV1
from spaces.learning.bridge.learnhouse_client import (
    LearnHouseClient,
    LearnHouseMalformedResponse,
    LearnHouseStaleRevision,
    LearnHouseTransportError,
)
from spaces.learning.contracts.events import LearningEventType, LearningToolName
from spaces.learning.contracts.mcp_models import (
    ActorV1,
    ConfirmationV1,
    EventEnvelopeV1,
    ToolRequestV1,
)


COURSE_ID = UUID("00000000-0000-0000-0000-000000000101")
CHAPTER_ID = UUID("00000000-0000-0000-0000-000000000102")
SOURCE_ID = UUID("00000000-0000-0000-0000-000000000103")
SESSION_ID = UUID("00000000-0000-0000-0000-000000000104")
TASK_ID = UUID("00000000-0000-0000-0000-000000000105")
OTHER_COURSE_ID = UUID("00000000-0000-0000-0000-000000000201")
OTHER_CHAPTER_ID = UUID("00000000-0000-0000-0000-000000000202")
OTHER_SESSION_ID = UUID("00000000-0000-0000-0000-000000000204")
OTHER_TASK_ID = UUID("00000000-0000-0000-0000-000000000205")


def _request(event_type: LearningEventType, **overrides: object) -> ToolRequestV1:
    values: dict[str, object] = {
        "event_type": event_type,
        "invocation_id": uuid4(),
        "correlation_id": uuid4(),
        "actor": ActorV1(actor_id="local-owner", actor_type="local_user"),
        "idempotency_key": "learning-adapter-test",
        "payload": {},
    }
    values.update(overrides)
    event = EventEnvelopeV1.model_validate(values)
    return ToolRequestV1(
        tool=LearningToolName(
            event_type.value.replace("learning.", "learning_").replace(".", "_")
        ),
        event=event,
    )


def _client(handler: httpx.MockTransport) -> LearnHouseClient:
    return LearnHouseClient(
        base_url="http://127.0.0.1:8080/api/v1",
        http_client=httpx.Client(transport=handler),
        read_attempts=2,
    )


def _course(*, revision: int = 4) -> dict[str, object]:
    return {
        "course_uuid": str(COURSE_ID),
        "org_id": 1,
        "name": "Typed learning",
        "description": "A course returned by LearnHouse.",
        "revision": revision,
        "internal_owner_id": "must-not-leak",
    }


def test_course_list_retries_read_and_maps_only_semantic_contract_fields() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        assert request.method == "GET"
        assert (
            request.url.path
            == "/api/v1/courses/org_slug/local-learning/page/1/limit/20"
        )
        assert request.headers["X-Correlation-ID"]
        if calls == 1:
            return httpx.Response(503, json={"detail": "try again"})
        return httpx.Response(200, json=[_course()])

    request = _request(
        LearningEventType.COURSE_LIST,
        payload={"org_slug": "local-learning", "limit": 20},
    )

    outcome = _client(httpx.MockTransport(handler)).execute(request)

    assert calls == 2
    assert outcome.state == "completed"
    assert outcome.aggregate and outcome.aggregate.revision == 4
    assert outcome.result == {
        "courses": [
            {
                "course_id": str(COURSE_ID),
                "title": "Typed learning",
                "description": "A course returned by LearnHouse.",
                "revision": 4,
            }
        ]
    }


@pytest.mark.parametrize(
    (
        "event_type",
        "overrides",
        "expected_method",
        "expected_path",
        "response",
        "expected_key",
    ),
    [
        (
            LearningEventType.COURSE_CREATE,
            {
                "payload": {
                    "org_id": 1,
                    "title": "New",
                    "description": "Course",
                    "about": "About",
                }
            },
            "POST",
            "/api/v1/courses/",
            _course(),
            "course",
        ),
        (
            LearningEventType.COURSE_OPEN,
            {"course_id": COURSE_ID},
            "GET",
            f"/api/v1/courses/{COURSE_ID}/meta",
            {**_course(), "chapters": [{"chapter_uuid": str(CHAPTER_ID)}]},
            "course",
        ),
        (
            LearningEventType.CHAPTER_OPEN,
            {"course_id": COURSE_ID, "payload": {"chapter_id": str(CHAPTER_ID)}},
            "GET",
            f"/api/v1/chapters/{CHAPTER_ID}",
            {
                "chapter_uuid": str(CHAPTER_ID),
                "course_uuid": str(COURSE_ID),
                "name": "One",
                "revision": 4,
            },
            "chapter",
        ),
        (
            LearningEventType.MATERIAL_IMPORT,
            {
                "payload": {
                    "org_id": 1,
                    "title": "Source",
                    "source_url": "https://example.invalid/source",
                }
            },
            "POST",
            "/api/v1/media/",
            {"media_uuid": str(SOURCE_ID), "name": "Source", "revision": 4},
            "source",
        ),
        (
            LearningEventType.COURSE_REVIEW,
            {
                "course_id": COURSE_ID,
                "expected_revision": 3,
                "payload": {"review": "approved"},
            },
            "PUT",
            f"/api/v1/courses/{COURSE_ID}",
            {**_course(), "revision": 4, "review_status": "reviewed"},
            "course",
        ),
        (
            LearningEventType.COURSE_PUBLISH,
            {
                "course_id": COURSE_ID,
                "expected_revision": 3,
                "confirmation": ConfirmationV1(
                    confirmed=True, approval_ref="publish-approved"
                ),
            },
            "PUT",
            f"/api/v1/courses/{COURSE_ID}",
            {**_course(), "revision": 4, "published": True},
            "course",
        ),
        (
            LearningEventType.SESSION_START,
            {"course_id": COURSE_ID, "payload": {"org_id": 1}},
            "POST",
            "/api/v1/trail/start",
            {
                "trail_uuid": str(SESSION_ID),
                "course_uuid": str(COURSE_ID),
                "revision": 4,
            },
            "session",
        ),
        (
            LearningEventType.TASK_NEXT,
            {"session_id": SESSION_ID, "payload": {"task_id": str(TASK_ID)}},
            "GET",
            f"/api/v1/activities/{TASK_ID}",
            {
                "activity_uuid": str(TASK_ID),
                "trail_uuid": str(SESSION_ID),
                "revision": 4,
                "title": "Exercise",
            },
            "task",
        ),
        (
            LearningEventType.PROGRESS_SHOW,
            {"course_id": COURSE_ID},
            "GET",
            "/api/v1/trail/",
            {"course_uuid": str(COURSE_ID), "revision": 4, "completed": 2, "total": 5},
            "progress",
        ),
    ],
)
def test_client_maps_supported_semantic_operations(
    event_type: LearningEventType,
    overrides: dict[str, object],
    expected_method: str,
    expected_path: str,
    response: dict[str, object],
    expected_key: str,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == expected_method
        assert request.url.path == expected_path
        assert request.headers["X-Correlation-ID"]
        return httpx.Response(200, json=response)

    outcome = _client(httpx.MockTransport(handler)).execute(
        _request(event_type, **overrides)
    )

    assert isinstance(outcome, ApplicationOutcomeV1)
    assert outcome.state == "completed"
    assert outcome.result and expected_key in outcome.result


def test_write_http_failure_is_not_retried_and_hides_upstream_detail() -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(500, json={"detail": "Bearer secret-value"})

    request = _request(
        LearningEventType.COURSE_CREATE,
        payload={
            "org_id": 1,
            "title": "New",
            "description": "Course",
            "about": "About",
        },
    )

    with pytest.raises(LearnHouseTransportError, match="write request failed") as error:
        _client(httpx.MockTransport(handler)).execute(request)

    assert calls == 1
    assert "secret-value" not in str(error.value)


def test_client_fails_closed_for_stale_or_malformed_upstream_responses() -> None:
    request = _request(
        LearningEventType.COURSE_OPEN, course_id=COURSE_ID, expected_revision=4
    )

    stale = _client(
        httpx.MockTransport(lambda _: httpx.Response(200, json=_course(revision=3)))
    )
    with pytest.raises(LearnHouseStaleRevision, match="revision"):
        stale.execute(request)

    malformed = _client(
        httpx.MockTransport(
            lambda _: httpx.Response(200, json={"name": "missing identity"})
        )
    )
    with pytest.raises(LearnHouseMalformedResponse, match="malformed"):
        malformed.execute(request)


@pytest.mark.parametrize(
    ("event_type", "overrides", "response"),
    [
        (
            LearningEventType.COURSE_OPEN,
            {"course_id": COURSE_ID},
            {**_course(), "course_uuid": str(OTHER_COURSE_ID)},
        ),
        (
            LearningEventType.COURSE_REVIEW,
            {
                "course_id": COURSE_ID,
                "expected_revision": 3,
                "payload": {"review": "approved"},
            },
            {**_course(), "course_uuid": str(OTHER_COURSE_ID)},
        ),
        (
            LearningEventType.COURSE_PUBLISH,
            {
                "course_id": COURSE_ID,
                "expected_revision": 3,
                "confirmation": ConfirmationV1(
                    confirmed=True, approval_ref="publish-approved"
                ),
            },
            {**_course(), "course_uuid": str(OTHER_COURSE_ID)},
        ),
        (
            LearningEventType.CHAPTER_OPEN,
            {"course_id": COURSE_ID, "payload": {"chapter_id": str(CHAPTER_ID)}},
            {
                "chapter_uuid": str(OTHER_CHAPTER_ID),
                "course_uuid": str(COURSE_ID),
                "name": "One",
                "revision": 4,
            },
        ),
        (
            LearningEventType.CHAPTER_OPEN,
            {"course_id": COURSE_ID, "payload": {"chapter_id": str(CHAPTER_ID)}},
            {
                "chapter_uuid": str(CHAPTER_ID),
                "course_uuid": str(OTHER_COURSE_ID),
                "name": "One",
                "revision": 4,
            },
        ),
        (
            LearningEventType.SESSION_START,
            {"course_id": COURSE_ID, "payload": {"org_id": 1}},
            {
                "trail_uuid": str(SESSION_ID),
                "course_uuid": str(OTHER_COURSE_ID),
                "revision": 4,
            },
        ),
        (
            LearningEventType.TASK_NEXT,
            {"session_id": SESSION_ID, "payload": {"task_id": str(TASK_ID)}},
            {
                "activity_uuid": str(OTHER_TASK_ID),
                "trail_uuid": str(SESSION_ID),
                "revision": 4,
                "title": "Exercise",
            },
        ),
        (
            LearningEventType.TASK_NEXT,
            {"session_id": SESSION_ID, "payload": {"task_id": str(TASK_ID)}},
            {
                "activity_uuid": str(TASK_ID),
                "trail_uuid": str(OTHER_SESSION_ID),
                "revision": 4,
                "title": "Exercise",
            },
        ),
        (
            LearningEventType.PROGRESS_SHOW,
            {"course_id": COURSE_ID},
            {
                "course_uuid": str(OTHER_COURSE_ID),
                "revision": 4,
                "completed": 2,
                "total": 5,
            },
        ),
    ],
)
def test_client_rejects_response_identity_not_bound_to_request(
    event_type: LearningEventType,
    overrides: dict[str, object],
    response: dict[str, object],
) -> None:
    client = _client(httpx.MockTransport(lambda _: httpx.Response(200, json=response)))

    with pytest.raises(LearnHouseMalformedResponse, match="identity"):
        client.execute(_request(event_type, **overrides))


@pytest.mark.parametrize(
    ("event_type", "overrides", "response"),
    [
        (
            LearningEventType.SESSION_START,
            {"course_id": COURSE_ID, "expected_revision": 4, "payload": {"org_id": 1}},
            {
                "trail_uuid": str(SESSION_ID),
                "course_uuid": str(COURSE_ID),
                "revision": 3,
            },
        ),
        (
            LearningEventType.TASK_NEXT,
            {
                "session_id": SESSION_ID,
                "expected_revision": 4,
                "payload": {"task_id": str(TASK_ID)},
            },
            {
                "activity_uuid": str(TASK_ID),
                "trail_uuid": str(SESSION_ID),
                "revision": 3,
                "title": "Exercise",
            },
        ),
        (
            LearningEventType.PROGRESS_SHOW,
            {"course_id": COURSE_ID, "expected_revision": 4},
            {"course_uuid": str(COURSE_ID), "revision": 3, "completed": 2, "total": 5},
        ),
    ],
)
def test_client_rejects_stale_session_task_and_progress_responses(
    event_type: LearningEventType,
    overrides: dict[str, object],
    response: dict[str, object],
) -> None:
    client = _client(httpx.MockTransport(lambda _: httpx.Response(200, json=response)))

    with pytest.raises(LearnHouseStaleRevision, match="revision"):
        client.execute(_request(event_type, **overrides))


@pytest.mark.parametrize(
    ("event_type", "overrides", "first_response", "second_response"),
    [
        (
            LearningEventType.COURSE_OPEN,
            {"course_id": COURSE_ID},
            _course(),
            {**_course(), "course_uuid": str(OTHER_COURSE_ID)},
        ),
        (
            LearningEventType.CHAPTER_OPEN,
            {"course_id": COURSE_ID, "payload": {"chapter_id": str(CHAPTER_ID)}},
            {
                "chapter_uuid": str(CHAPTER_ID),
                "course_uuid": str(COURSE_ID),
                "name": "One",
                "revision": 4,
            },
            {
                "chapter_uuid": str(CHAPTER_ID),
                "course_uuid": str(OTHER_COURSE_ID),
                "name": "One",
                "revision": 4,
            },
        ),
        (
            LearningEventType.SESSION_START,
            {"course_id": COURSE_ID, "payload": {"org_id": 1}},
            {
                "trail_uuid": str(SESSION_ID),
                "course_uuid": str(COURSE_ID),
                "revision": 4,
            },
            {
                "trail_uuid": str(SESSION_ID),
                "course_uuid": str(OTHER_COURSE_ID),
                "revision": 4,
            },
        ),
        (
            LearningEventType.TASK_NEXT,
            {"session_id": SESSION_ID, "payload": {"task_id": str(TASK_ID)}},
            {
                "activity_uuid": str(TASK_ID),
                "trail_uuid": str(SESSION_ID),
                "revision": 4,
                "title": "Exercise",
            },
            {
                "activity_uuid": str(TASK_ID),
                "trail_uuid": str(OTHER_SESSION_ID),
                "revision": 4,
                "title": "Exercise",
            },
        ),
        (
            LearningEventType.PROGRESS_SHOW,
            {"course_id": COURSE_ID},
            {"course_uuid": str(COURSE_ID), "revision": 4, "completed": 2, "total": 5},
            {
                "course_uuid": str(OTHER_COURSE_ID),
                "revision": 4,
                "completed": 2,
                "total": 5,
            },
        ),
    ],
)
def test_readback_rejects_identity_substitution(
    event_type: LearningEventType,
    overrides: dict[str, object],
    first_response: dict[str, object],
    second_response: dict[str, object],
) -> None:
    responses = iter((first_response, second_response))
    client = _client(
        httpx.MockTransport(lambda _: httpx.Response(200, json=next(responses)))
    )
    request = _request(event_type, **overrides)

    outcome = client.execute(request)

    assert client.readback(request, outcome) is None


def test_learnhouse_client_rejects_redirect_without_leaving_loopback() -> None:
    destinations: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        destinations.append(str(request.url))
        assert request.url.host == "127.0.0.1"
        return httpx.Response(
            307, headers={"location": "https://example.invalid/redirect"}
        )

    injected = httpx.Client(
        transport=httpx.MockTransport(handler), follow_redirects=True
    )
    client = LearnHouseClient(
        base_url="http://127.0.0.1:8080/api/v1", http_client=injected
    )

    with pytest.raises(LearnHouseTransportError, match="read request failed"):
        client.execute(_request(LearningEventType.COURSE_OPEN, course_id=COURSE_ID))

    assert destinations == [
        f"http://127.0.0.1:8080/api/v1/courses/{COURSE_ID}/meta?slim=true"
    ]


def test_client_rejects_non_loopback_base_url() -> None:
    with pytest.raises(ValueError, match="loopback"):
        LearnHouseClient(base_url="https://learnhouse.example.invalid/api/v1")


def test_client_uses_the_authenticated_internal_learning_service_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LEARNHOUSE_LEARNING_SERVICE_KEY", "k" * 32)

    def handler(request: httpx.Request) -> httpx.Response:
        assert (
            str(request.url)
            == "http://learnhouse-api:9000/api/v1/learning/courses?page=1&limit=20"
        )
        assert request.extensions["timeout"]["read"] == 5.0
        assert request.headers["X-LearnHouse-Learning-Service-Key"] == "k" * 32
        assert "k" * 32 not in request.content.decode("utf-8")
        return httpx.Response(
            200,
            json={
                "aggregate": {"aggregate_id": "default", "revision": 7},
                "courses": [
                    {
                        "course_id": str(COURSE_ID),
                        "title": "Controlled service course",
                        "description": "Decoded from the pinned service.",
                        "revision": 3,
                        "review_status": "pending",
                    }
                ],
            },
        )

    outcome = LearnHouseClient(
        http_client=httpx.Client(transport=httpx.MockTransport(handler))
    ).execute(
        _request(
            LearningEventType.COURSE_LIST,
            payload={"org_slug": "default", "limit": 20},
        )
    )

    assert outcome.state == "completed"
    assert outcome.aggregate and outcome.aggregate.aggregate_id == "default"


@pytest.mark.parametrize(
    ("status_code", "expected_error"),
    [
        (401, "learning_backend_unauthorized"),
        (403, "learning_backend_unauthorized"),
        (404, "learning_not_found"),
        (409, "revision_conflict"),
    ],
)
def test_client_decodes_controlled_service_terminal_statuses(
    monkeypatch: pytest.MonkeyPatch,
    status_code: int,
    expected_error: str,
) -> None:
    monkeypatch.setenv("LEARNHOUSE_LEARNING_SERVICE_KEY", "k" * 32)
    client = LearnHouseClient(
        http_client=httpx.Client(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(status_code, json={"error": "private-detail"})
            )
        )
    )

    outcome = client.execute(
        _request(LearningEventType.COURSE_OPEN, course_id=COURSE_ID)
    )

    assert outcome.state == ("rejected" if status_code == 409 else "unavailable")
    assert outcome.error and outcome.error.code == expected_error
    assert "private-detail" not in outcome.error.message


@pytest.mark.parametrize(
    ("event_type", "overrides", "path", "response", "result_key"),
    [
        (
            LearningEventType.COURSE_LIST,
            {"payload": {"org_slug": "default", "limit": 20}},
            "/api/v1/learning/courses",
            {"aggregate": {"aggregate_id": "default", "revision": 0}, "courses": []},
            "courses",
        ),
        (
            LearningEventType.COURSE_CREATE,
            {
                "payload": {
                    "org_id": 1,
                    "title": "New",
                    "description": "Course",
                    "about": "About",
                }
            },
            "/api/v1/learning/courses",
            {
                "course": {
                    "course_id": str(COURSE_ID),
                    "title": "New",
                    "description": "Course",
                    "revision": 1,
                    "review_status": "pending",
                    "published": False,
                }
            },
            "course",
        ),
        (
            LearningEventType.COURSE_OPEN,
            {"course_id": COURSE_ID},
            f"/api/v1/learning/courses/{COURSE_ID}",
            {
                "course": {
                    "course_id": str(COURSE_ID),
                    "title": "Course",
                    "description": "Description",
                    "revision": 3,
                    "review_status": "pending",
                    "published": False,
                }
            },
            "course",
        ),
        (
            LearningEventType.MATERIAL_IMPORT,
            {
                "course_id": COURSE_ID,
                "expected_revision": 3,
                "payload": {
                    "title": "Source",
                    "source_url": "https://example.invalid/source",
                },
            },
            f"/api/v1/learning/courses/{COURSE_ID}/sources",
            {"source": {"source_id": str(SOURCE_ID), "title": "Source", "revision": 4}},
            "source",
        ),
        (
            LearningEventType.COURSE_REVIEW,
            {
                "course_id": COURSE_ID,
                "expected_revision": 3,
                "payload": {"review": "approved"},
            },
            f"/api/v1/learning/courses/{COURSE_ID}/review",
            {
                "course": {
                    "course_id": str(COURSE_ID),
                    "title": "Course",
                    "description": "Description",
                    "revision": 4,
                    "review_status": "approved",
                    "published": False,
                }
            },
            "course",
        ),
        (
            LearningEventType.COURSE_PUBLISH,
            {
                "course_id": COURSE_ID,
                "expected_revision": 3,
                "confirmation": ConfirmationV1(
                    confirmed=True, approval_ref="publish-approved"
                ),
            },
            f"/api/v1/learning/courses/{COURSE_ID}/publish",
            {
                "course": {
                    "course_id": str(COURSE_ID),
                    "title": "Course",
                    "description": "Description",
                    "revision": 4,
                    "review_status": "approved",
                    "published": True,
                }
            },
            "course",
        ),
        (
            LearningEventType.CHAPTER_OPEN,
            {"course_id": COURSE_ID, "payload": {"chapter_id": str(CHAPTER_ID)}},
            f"/api/v1/learning/chapters/{CHAPTER_ID}",
            {
                "chapter": {
                    "chapter_id": str(CHAPTER_ID),
                    "course_id": str(COURSE_ID),
                    "title": "Chapter",
                    "revision": 3,
                }
            },
            "chapter",
        ),
        (
            LearningEventType.PROGRESS_SHOW,
            {"course_id": COURSE_ID},
            f"/api/v1/learning/courses/{COURSE_ID}/progress",
            {
                "progress": {
                    "course_id": str(COURSE_ID),
                    "completed": 1,
                    "total": 2,
                    "revision": 3,
                }
            },
            "progress",
        ),
    ],
)
def test_client_decodes_each_admitted_controlled_service_operation(
    monkeypatch: pytest.MonkeyPatch,
    event_type: LearningEventType,
    overrides: dict[str, object],
    path: str,
    response: dict[str, object],
    result_key: str,
) -> None:
    monkeypatch.setenv("LEARNHOUSE_LEARNING_SERVICE_KEY", "k" * 32)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == path
        assert request.headers["X-LearnHouse-Learning-Service-Key"] == "k" * 32
        return httpx.Response(200, json=response)

    outcome = LearnHouseClient(
        http_client=httpx.Client(transport=httpx.MockTransport(handler))
    ).execute(_request(event_type, **overrides))

    assert outcome.state == "completed"
    assert outcome.result and result_key in outcome.result


def test_client_rejects_a_write_response_that_skips_the_cas_successor_revision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LEARNHOUSE_LEARNING_SERVICE_KEY", "k" * 32)
    request = _request(
        LearningEventType.COURSE_REVIEW,
        course_id=COURSE_ID,
        expected_revision=3,
        payload={"review": "approved"},
    )
    client = LearnHouseClient(
        http_client=httpx.Client(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(
                    200,
                    json={
                        "course": {
                            "course_id": str(COURSE_ID),
                            "title": "Course",
                            "description": "Description",
                            "revision": 5,
                            "review_status": "approved",
                            "published": False,
                        }
                    },
                )
            )
        )
    )

    with pytest.raises(LearnHouseStaleRevision, match="revision"):
        client.execute(request)
