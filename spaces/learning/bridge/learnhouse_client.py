from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from ipaddress import ip_address
from typing import Any, Literal
from urllib.parse import urlsplit
from uuid import UUID

import httpx

from spaces.learning.bridge.dispatcher import ApplicationOutcomeV1
from spaces.learning.contracts.events import LearningToolName
from spaces.learning.contracts.mcp_models import ToolRequestV1
from spaces.learning.contracts.outcomes import AggregateRefV1, EvidenceRefV1, ToolErrorV1, TruthReadbackV1
from spaces.learning.contracts.ui_intents import OpenChapterIntentV1, OpenCourseIntentV1, OpenTaskIntentV1


class LearnHouseError(RuntimeError):
    """Base error for the admitted LearnHouse transport boundary."""


class LearnHouseTransportError(LearnHouseError):
    pass


class LearnHouseMalformedResponse(LearnHouseError):
    pass


class LearnHouseStaleRevision(LearnHouseError):
    pass


@dataclass(frozen=True)
class _Operation:
    method: Literal["GET", "POST", "PUT"]
    path: str
    payload: dict[str, object] | None = None
    params: dict[str, object] | None = None
    form: bool = False


def _validate_loopback_url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme != "http" or not parsed.hostname:
        raise ValueError("LearnHouse base_url must use an http loopback URL")
    try:
        if not ip_address(parsed.hostname).is_loopback:
            raise ValueError("LearnHouse base_url must use a loopback address")
    except ValueError as error:
        if str(error).startswith("LearnHouse"):
            raise
        raise ValueError("LearnHouse base_url must use a loopback address") from None
    return value.rstrip("/")


class LearnHouseClient:
    """Typed, loopback-only gateway from Learning tools to LearnHouse."""

    def __init__(
        self,
        *,
        base_url: str = "http://127.0.0.1:8080/api/v1",
        timeout_seconds: float = 2.0,
        read_attempts: int = 2,
        http_client: httpx.Client | None = None,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if read_attempts < 1 or read_attempts > 3:
            raise ValueError("read_attempts must be between 1 and 3")
        self._base_url = _validate_loopback_url(base_url)
        self._timeout_seconds = timeout_seconds
        self._read_attempts = read_attempts
        self._http_client = http_client or httpx.Client()

    def execute(self, request: ToolRequestV1) -> ApplicationOutcomeV1:
        operation = self._operation_for(request)
        response = self._request(operation, correlation_id=str(request.event.correlation_id))
        return self._map_response(request, response)

    def readback(
        self, request: ToolRequestV1, outcome: ApplicationOutcomeV1
    ) -> TruthReadbackV1 | None:
        if outcome.aggregate is None:
            return None
        try:
            operation = self._readback_operation_for(request, outcome.aggregate)
            response = self._request(operation, correlation_id=str(request.event.correlation_id))
            observed = self._map_response(request, response)
        except LearnHouseError:
            return None
        if observed.aggregate != outcome.aggregate:
            return None
        return TruthReadbackV1(
            invocation_id=request.event.invocation_id,
            correlation_id=request.event.correlation_id,
            owner="learnhouse",
            terminal_state="completed",
            aggregate=outcome.aggregate,
            evidence=EvidenceRefV1(
                owner="learnhouse",
                evidence_id=f"learnhouse:{outcome.aggregate.aggregate_type}:{outcome.aggregate.aggregate_id}:{outcome.aggregate.revision}",
                evidence_type="application_readback",
            ),
        )

    def _request(self, operation: _Operation, *, correlation_id: str) -> object:
        attempts = self._read_attempts if operation.method == "GET" else 1
        headers = {"Accept": "application/json", "X-Correlation-ID": correlation_id}
        for attempt in range(attempts):
            try:
                response = self._http_client.request(
                    operation.method,
                    f"{self._base_url}{operation.path}",
                    headers=headers,
                    params=operation.params,
                    data=operation.payload if operation.form else None,
                    json=None if operation.form else operation.payload,
                    timeout=self._timeout_seconds,
                )
            except httpx.HTTPError as error:
                if operation.method == "GET" and attempt + 1 < attempts:
                    continue
                raise LearnHouseTransportError(
                    f"LearnHouse {'read' if operation.method == 'GET' else 'write'} request failed"
                ) from error
            if response.status_code >= 500 and operation.method == "GET" and attempt + 1 < attempts:
                continue
            if response.is_error:
                raise LearnHouseTransportError(
                    f"LearnHouse {'read' if operation.method == 'GET' else 'write'} request failed"
                )
            try:
                return response.json()
            except ValueError as error:
                raise LearnHouseMalformedResponse("LearnHouse returned malformed JSON") from error
        raise LearnHouseTransportError("LearnHouse read request failed")

    def _operation_for(self, request: ToolRequestV1) -> _Operation:
        event = request.event
        payload = event.payload
        tool = request.tool
        if tool is LearningToolName.COURSE_LIST:
            slug = self._string(payload, "org_slug")
            limit = self._integer(payload, "limit", default=20)
            if limit < 1 or limit > 50:
                raise LearnHouseMalformedResponse("malformed course list request")
            return _Operation("GET", f"/courses/org_slug/{slug}/page/1/limit/{limit}")
        if tool is LearningToolName.COURSE_CREATE:
            return _Operation("POST", "/courses/", self._course_create_payload(payload), form=True)
        if tool is LearningToolName.COURSE_OPEN:
            return _Operation("GET", f"/courses/{self._course_id(event.course_id)}/meta", params={"slim": "true"})
        if tool is LearningToolName.CHAPTER_OPEN:
            return _Operation("GET", f"/chapters/{self._uuid_string(payload, 'chapter_id')}")
        if tool is LearningToolName.MATERIAL_IMPORT:
            return _Operation("POST", "/media/", self._source_import_payload(payload), form=True)
        if tool in {LearningToolName.COURSE_REVIEW, LearningToolName.COURSE_PUBLISH}:
            course_id = self._course_id(event.course_id)
            update: dict[str, object] = {"revision": self._expected_revision(event.expected_revision)}
            if tool is LearningToolName.COURSE_REVIEW:
                update["review_status"] = self._string(payload, "review")
            else:
                update["published"] = True
            return _Operation("PUT", f"/courses/{course_id}", update)
        if tool is LearningToolName.SESSION_START:
            return _Operation(
                "POST",
                "/trail/start",
                {"org_id": self._integer(payload, "org_id"), "course_uuid": self._course_id(event.course_id)},
            )
        if tool is LearningToolName.TASK_NEXT:
            return _Operation("GET", f"/activities/{self._uuid_string(payload, 'task_id')}")
        if tool is LearningToolName.PROGRESS_SHOW:
            return _Operation("GET", "/trail/")
        raise LearnHouseMalformedResponse("unsupported LearnHouse tool")

    def _readback_operation_for(
        self, request: ToolRequestV1, aggregate: AggregateRefV1
    ) -> _Operation:
        if request.tool is LearningToolName.COURSE_LIST:
            return self._operation_for(request)
        if request.tool in {
            LearningToolName.COURSE_CREATE,
            LearningToolName.COURSE_OPEN,
            LearningToolName.COURSE_REVIEW,
            LearningToolName.COURSE_PUBLISH,
        }:
            return _Operation("GET", f"/courses/{aggregate.aggregate_id}/meta", params={"slim": "true"})
        if request.tool is LearningToolName.CHAPTER_OPEN:
            return _Operation("GET", f"/chapters/{aggregate.aggregate_id}")
        if request.tool is LearningToolName.MATERIAL_IMPORT:
            return _Operation("GET", f"/media/{aggregate.aggregate_id}")
        if request.tool in {LearningToolName.SESSION_START, LearningToolName.PROGRESS_SHOW}:
            return _Operation("GET", "/trail/")
        if request.tool is LearningToolName.TASK_NEXT:
            return _Operation("GET", f"/activities/{aggregate.aggregate_id}")
        raise LearnHouseMalformedResponse("unsupported LearnHouse readback")

    def _map_response(self, request: ToolRequestV1, value: object) -> ApplicationOutcomeV1:
        if request.tool is LearningToolName.COURSE_LIST:
            return self._map_course_list(request, value)
        if request.tool in {
            LearningToolName.COURSE_CREATE,
            LearningToolName.COURSE_OPEN,
            LearningToolName.COURSE_REVIEW,
            LearningToolName.COURSE_PUBLISH,
        }:
            return self._map_course(request, value)
        if request.tool is LearningToolName.CHAPTER_OPEN:
            return self._map_chapter(request, value)
        if request.tool is LearningToolName.MATERIAL_IMPORT:
            return self._map_source(request, value)
        if request.tool is LearningToolName.SESSION_START:
            return self._map_session(request, value)
        if request.tool is LearningToolName.TASK_NEXT:
            return self._map_task(request, value)
        if request.tool is LearningToolName.PROGRESS_SHOW:
            return self._map_progress(request, value)
        raise LearnHouseMalformedResponse("unsupported LearnHouse tool")

    def _map_course_list(self, request: ToolRequestV1, value: object) -> ApplicationOutcomeV1:
        if not isinstance(value, list) or not value:
            raise LearnHouseMalformedResponse("LearnHouse returned malformed course list")
        courses = [self._course_value(item) for item in value]
        revision = max(course["revision"] for course in courses)
        return ApplicationOutcomeV1(
            state="completed",
            aggregate=AggregateRefV1(
                aggregate_type="course_collection",
                aggregate_id=self._string(request.event.payload, "org_slug"),
                revision=revision,
            ),
            result={"courses": courses},
        )

    def _map_course(self, request: ToolRequestV1, value: object) -> ApplicationOutcomeV1:
        course = self._course_value(value)
        self._assert_not_stale(request, course["revision"])
        intent = None
        if request.tool is LearningToolName.COURSE_OPEN:
            course_id = UUID(course["course_id"])
            intent = OpenCourseIntentV1(
                aggregate_id=course["course_id"],
                aggregate_revision=course["revision"],
                course_id=course_id,
            )
        return ApplicationOutcomeV1(
            state="completed",
            aggregate=AggregateRefV1(
                aggregate_type="course", aggregate_id=course["course_id"], revision=course["revision"]
            ),
            result={"course": course},
            ui_intent=intent,
        )

    def _map_chapter(self, request: ToolRequestV1, value: object) -> ApplicationOutcomeV1:
        item = self._mapping(value)
        chapter_id = self._uuid_from(item, "chapter_uuid")
        course_id = self._uuid_from(item, "course_uuid")
        revision = self._revision(item)
        self._assert_not_stale(request, revision)
        chapter = {"chapter_id": chapter_id, "course_id": course_id, "title": self._string(item, "name"), "revision": revision}
        return ApplicationOutcomeV1(
            state="completed",
            aggregate=AggregateRefV1(aggregate_type="chapter", aggregate_id=chapter_id, revision=revision),
            result={"chapter": chapter},
            ui_intent=OpenChapterIntentV1(
                aggregate_id=chapter_id,
                aggregate_revision=revision,
                course_id=UUID(course_id),
                chapter_id=UUID(chapter_id),
            ),
        )

    def _map_source(self, request: ToolRequestV1, value: object) -> ApplicationOutcomeV1:
        item = self._mapping(value)
        source_id = self._uuid_from(item, "media_uuid")
        revision = self._revision(item)
        self._assert_not_stale(request, revision)
        return ApplicationOutcomeV1(
            state="completed",
            aggregate=AggregateRefV1(aggregate_type="source", aggregate_id=source_id, revision=revision),
            result={"source": {"source_id": source_id, "title": self._string(item, "name"), "revision": revision}},
        )

    def _map_session(self, request: ToolRequestV1, value: object) -> ApplicationOutcomeV1:
        item = self._mapping(value)
        session_id = self._uuid_from(item, "trail_uuid")
        course_id = self._uuid_from(item, "course_uuid")
        revision = self._revision(item)
        return ApplicationOutcomeV1(
            state="completed",
            aggregate=AggregateRefV1(aggregate_type="session", aggregate_id=session_id, revision=revision),
            result={"session": {"session_id": session_id, "course_id": course_id, "revision": revision}},
        )

    def _map_task(self, request: ToolRequestV1, value: object) -> ApplicationOutcomeV1:
        item = self._mapping(value)
        task_id = self._uuid_from(item, "activity_uuid")
        session_id = self._uuid_from(item, "trail_uuid")
        revision = self._revision(item)
        task = {"task_id": task_id, "session_id": session_id, "title": self._string(item, "title"), "revision": revision}
        return ApplicationOutcomeV1(
            state="completed",
            aggregate=AggregateRefV1(aggregate_type="task", aggregate_id=task_id, revision=revision),
            result={"task": task},
            ui_intent=OpenTaskIntentV1(
                aggregate_id=task_id,
                aggregate_revision=revision,
                session_id=UUID(session_id),
                task_id=UUID(task_id),
            ),
        )

    def _map_progress(self, request: ToolRequestV1, value: object) -> ApplicationOutcomeV1:
        item = self._mapping(value)
        course_id = self._uuid_from(item, "course_uuid")
        revision = self._revision(item)
        progress = {"course_id": course_id, "completed": self._integer(item, "completed"), "total": self._integer(item, "total"), "revision": revision}
        return ApplicationOutcomeV1(
            state="completed",
            aggregate=AggregateRefV1(aggregate_type="progress", aggregate_id=course_id, revision=revision),
            result={"progress": progress},
        )

    def _course_value(self, value: object) -> dict[str, Any]:
        item = self._mapping(value)
        return {
            "course_id": self._uuid_from(item, "course_uuid"),
            "title": self._string(item, "name"),
            "description": self._string(item, "description"),
            "revision": self._revision(item),
        }

    @staticmethod
    def _mapping(value: object) -> Mapping[str, object]:
        if not isinstance(value, Mapping):
            raise LearnHouseMalformedResponse("LearnHouse returned malformed response")
        return value

    @staticmethod
    def _string(values: Mapping[str, object], key: str) -> str:
        value = values.get(key)
        if not isinstance(value, str) or not value:
            raise LearnHouseMalformedResponse("LearnHouse returned malformed response")
        return value

    @classmethod
    def _uuid_from(cls, values: Mapping[str, object], key: str) -> str:
        value = cls._string(values, key)
        try:
            return str(UUID(value))
        except ValueError as error:
            raise LearnHouseMalformedResponse("LearnHouse returned malformed response") from error

    @classmethod
    def _uuid_string(cls, values: Mapping[str, object], key: str) -> str:
        return cls._uuid_from(values, key)

    @staticmethod
    def _integer(values: Mapping[str, object], key: str, *, default: int | None = None) -> int:
        value = values.get(key, default)
        if isinstance(value, bool) or not isinstance(value, int):
            raise LearnHouseMalformedResponse("LearnHouse returned malformed response")
        return value

    @classmethod
    def _revision(cls, values: Mapping[str, object]) -> int:
        revision = cls._integer(values, "revision")
        if revision < 0:
            raise LearnHouseMalformedResponse("LearnHouse returned malformed response")
        return revision

    @staticmethod
    def _course_id(value: UUID | None) -> str:
        if value is None:
            raise LearnHouseMalformedResponse("malformed course request")
        return str(value)

    @staticmethod
    def _expected_revision(value: int | None) -> int:
        if value is None:
            raise LearnHouseMalformedResponse("malformed revisioned write request")
        return value

    def _assert_not_stale(self, request: ToolRequestV1, observed_revision: int) -> None:
        expected = request.event.expected_revision
        if expected is not None and observed_revision < expected:
            raise LearnHouseStaleRevision("LearnHouse returned a stale revision")

    def _course_create_payload(self, payload: Mapping[str, object]) -> dict[str, object]:
        return {
            "org_id": self._integer(payload, "org_id"),
            "name": self._string(payload, "title"),
            "description": self._string(payload, "description"),
            "about": self._string(payload, "about"),
            "public": "false",
        }

    def _source_import_payload(self, payload: Mapping[str, object]) -> dict[str, object]:
        source_url = self._string(payload, "source_url")
        parsed = urlsplit(source_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise LearnHouseMalformedResponse("malformed source import request")
        return {
            "org_id": self._integer(payload, "org_id"),
            "name": self._string(payload, "title"),
            "media_type": "EMBED",
            "url": source_url,
            "public": "false",
        }
