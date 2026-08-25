from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID, uuid4

from spaces.learning.bridge.dispatcher import ApplicationOutcomeV1, LearningDispatcher
from spaces.learning.contracts.events import LearningToolName
from spaces.learning.contracts.mcp_models import ToolRequestV1
from spaces.learning.contracts.outcomes import (
    AggregateRefV1,
    EvidenceRefV1,
    TruthReadbackV1,
)
from spaces.learning.mcp import server


COURSE_ID = UUID("00000000-0000-0000-0000-000000000101")
CHAPTER_ID = UUID("00000000-0000-0000-0000-000000000102")


def _event(event_type: str, **overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "event_type": event_type,
        "invocation_id": str(uuid4()),
        "correlation_id": str(uuid4()),
        "actor": {"actor_id": "local-owner", "actor_type": "local_user"},
        "payload": {},
    }
    values.update(overrides)
    return values


def _call(
    dispatcher: LearningDispatcher, name: str, arguments: dict[str, object]
) -> dict:
    response = server.handle_message(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        },
        dispatcher=dispatcher,
    )
    assert response is not None
    return response


def _payload(response: dict) -> dict:
    import json

    return json.loads(response["result"]["content"][0]["text"])


@dataclass
class RecordingNavigationGateway:
    readback_failure: bool = False
    backend_failure: bool = False
    execute_calls: int = 0
    readback_calls: int = 0
    call_order: list[str] = field(default_factory=list)

    def execute(self, request: ToolRequestV1) -> ApplicationOutcomeV1:
        self.execute_calls += 1
        self.call_order.append("execute")
        if self.backend_failure:
            raise RuntimeError("private upstream detail")
        aggregate, result = self._success(request.tool)
        return ApplicationOutcomeV1(
            state="completed", aggregate=aggregate, result=result
        )

    def readback(
        self, request: ToolRequestV1, outcome: ApplicationOutcomeV1
    ) -> TruthReadbackV1 | None:
        self.readback_calls += 1
        self.call_order.append("readback")
        if self.readback_failure or outcome.aggregate is None:
            return None
        return TruthReadbackV1(
            invocation_id=request.event.invocation_id,
            correlation_id=request.event.correlation_id,
            owner="learnhouse",
            terminal_state="completed",
            aggregate=outcome.aggregate,
            evidence=EvidenceRefV1(
                owner="learnhouse",
                evidence_id=f"readback-{outcome.aggregate.aggregate_id}",
                evidence_type="application_readback",
            ),
        )

    @staticmethod
    def _success(tool: LearningToolName) -> tuple[AggregateRefV1, dict[str, object]]:
        if tool is LearningToolName.CHAPTER_OPEN:
            return (
                AggregateRefV1(
                    aggregate_type="chapter", aggregate_id=str(CHAPTER_ID), revision=5
                ),
                {
                    "chapter": {
                        "chapter_id": str(CHAPTER_ID),
                        "course_id": str(COURSE_ID),
                        "title": "Chapter one",
                        "revision": 5,
                    }
                },
            )
        if tool is LearningToolName.PROGRESS_SHOW:
            return (
                AggregateRefV1(
                    aggregate_type="progress", aggregate_id=str(COURSE_ID), revision=5
                ),
                {
                    "progress": {
                        "course_id": str(COURSE_ID),
                        "completed": 2,
                        "total": 5,
                        "revision": 5,
                    }
                },
            )
        return (
            AggregateRefV1(
                aggregate_type="course", aggregate_id=str(COURSE_ID), revision=5
            ),
            {
                "course": {
                    "course_id": str(COURSE_ID),
                    "title": "Typed learning",
                    "description": "A semantic course.",
                    "revision": 5,
                }
            },
        )


def _dispatcher(monkeypatch, gateway: RecordingNavigationGateway) -> LearningDispatcher:
    monkeypatch.setattr(server, "LearnHouseClient", lambda: gateway, raising=False)
    return server.build_default_dispatcher()


def test_navigation_tools_emit_their_intents_only_after_exact_readback(
    monkeypatch,
) -> None:
    cases = [
        (
            "learning_course_open",
            _event("learning.course.open", course_id=str(COURSE_ID)),
            "open_course",
        ),
        (
            "learning_chapter_open",
            _event(
                "learning.chapter.open",
                course_id=str(COURSE_ID),
                payload={"chapter_id": str(CHAPTER_ID)},
            ),
            "open_chapter",
        ),
        (
            "learning_progress_show",
            _event("learning.progress.show", course_id=str(COURSE_ID)),
            "show_progress",
        ),
    ]

    for tool, event, expected_intent in cases:
        gateway = RecordingNavigationGateway()
        response = _call(_dispatcher(monkeypatch, gateway), tool, event)
        payload = _payload(response)

        assert response["result"]["isError"] is False
        assert payload["state"] == "completed"
        assert payload["evidence"]["evidence_type"] == "application_readback"
        assert payload["ui_intent"]["kind"] == expected_intent
        assert gateway.call_order == ["execute", "readback"]


def test_navigation_readback_failure_returns_no_success_or_intent(monkeypatch) -> None:
    gateway = RecordingNavigationGateway(readback_failure=True)
    response = _call(
        _dispatcher(monkeypatch, gateway),
        "learning_chapter_open",
        _event(
            "learning.chapter.open",
            course_id=str(COURSE_ID),
            payload={"chapter_id": str(CHAPTER_ID)},
        ),
    )
    payload = _payload(response)

    assert response["result"]["isError"] is True
    assert payload["state"] == "unavailable"
    assert payload["error"]["code"] == "truth_readback_unverified"
    assert "ui_intent" not in payload
    assert gateway.call_order == ["execute", "readback"]


def test_navigation_backend_failure_is_distinct_from_a_completed_backend_result(
    monkeypatch,
) -> None:
    gateway = RecordingNavigationGateway(backend_failure=True)
    response = _call(
        _dispatcher(monkeypatch, gateway),
        "learning_progress_show",
        _event("learning.progress.show", course_id=str(COURSE_ID)),
    )
    payload = _payload(response)

    assert response["result"]["isError"] is True
    assert payload["state"] == "unavailable"
    assert payload["error"]["code"] == "learning_backend_unavailable"
    assert "private upstream detail" not in str(payload)
    assert "ui_intent" not in payload
    assert gateway.call_order == ["execute"]
