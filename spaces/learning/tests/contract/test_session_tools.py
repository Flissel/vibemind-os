from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from spaces.learning.bridge.dispatcher import InMemoryReceiptStore, LearningDispatcher
from spaces.learning.contracts.events import LearningToolName
from spaces.learning.mcp import server
from spaces.learning.mcp.tools.sessions import build_session_gateways
from spaces.learning.services.adaptive_engine.models import (
    AdaptiveConcept,
    AdaptiveItem,
    ItemConcept,
)
from spaces.learning.services.adaptive_engine.session_service import AdaptiveSessionService
from spaces.learning.services.db.models import Base


def _store(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'mcp-session.db'}")

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, connection_record) -> None:
        del connection_record
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    course_id = str(uuid4())
    concept_id = str(uuid4())
    item_id = str(uuid4())
    with factory() as db, db.begin():
        db.add(
            AdaptiveConcept(
                id=concept_id,
                course_id=course_id,
                course_revision=1,
                concept_key="authority",
                title="Authority",
            )
        )
        db.add(
            AdaptiveItem(
                id=item_id,
                course_id=course_id,
                course_revision=1,
                activity_id="activity-1",
                item_type="single_choice",
                difficulty=0.3,
                quality_status="approved",
                expected_answer={"choice_id": "a"},
                scoring_config={
                    "prompt": "Welche Quelle ist autorisiert?",
                    "options": [
                        {"id": "a", "label": "Freigegebene Kursquelle"},
                        {"id": "b", "label": "Unbelegte Erinnerung"},
                    ],
                    "source_refs": ["source://course/1"],
                    "hints": ["Pruefe die Freigabe."],
                    "representation": "quiz",
                    "remediation_tags": [],
                },
                rubric=[],
            )
        )
        db.add(
            ItemConcept(
                item_id=item_id,
                concept_id=concept_id,
                course_id=course_id,
                course_revision=1,
            )
        )
    return engine, factory, course_id


def _event(event_type: str, **updates: object) -> dict[str, object]:
    value: dict[str, object] = {
        "event_type": event_type,
        "invocation_id": str(uuid4()),
        "correlation_id": str(uuid4()),
        "actor": {"actor_id": "student-mcp", "actor_type": "local_user"},
        "payload": {},
    }
    value.update(updates)
    return value


def _call(dispatcher: LearningDispatcher, name: str, arguments: dict[str, object]):
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
    return json.loads(response["result"]["content"][0]["text"])


def test_session_tools_emit_only_readback_verified_task_and_result_intents(
    tmp_path: Path,
) -> None:
    engine, factory, course_id = _store(tmp_path)
    service = AdaptiveSessionService(factory)
    dispatcher = LearningDispatcher(
        gateways=build_session_gateways(service),
        receipts=InMemoryReceiptStore(),
    )
    started = _call(
        dispatcher,
        "learning_session_start",
        _event(
            "learning.session.start",
            course_id=course_id,
            idempotency_key="session-start-1",
            payload={
                "course_revision": 1,
                "mode": "training",
                "blueprint": {"max_attempts": 1},
            },
        ),
    )

    assert started["state"] == "completed"
    assert started["evidence"]["evidence_type"] == "application_readback"
    assert started["ui_intent"]["kind"] == "open_task"
    assert started["ui_intent"]["aggregate_revision"] == started["aggregate"]["revision"]
    session_id = started["result"]["session"]["session_id"]
    revision = started["aggregate"]["revision"]

    hinted = _call(
        dispatcher,
        "learning_hint_request",
        _event("learning.hint.request", session_id=session_id),
    )
    assert hinted["state"] == "completed"
    assert hinted["result"]["hint"] == "Pruefe die Freigabe."

    answered = _call(
        dispatcher,
        "learning_task_answer",
        _event(
            "learning.task.answer",
            session_id=session_id,
            expected_revision=revision,
            idempotency_key="answer-1",
            payload={"answer": {"choice_id": "a"}},
        ),
    )
    assert answered["state"] == "completed"
    assert answered["result"]["session"]["state"] == "completed"
    assert answered["result"]["session"]["summary"]["correct"] == 1
    assert answered["ui_intent"]["kind"] == "show_result"
    assert answered["ui_intent"]["aggregate_revision"] == answered["aggregate"]["revision"]
    engine.dispose()


def test_session_tool_rejects_stale_revision_without_success_intent(tmp_path: Path) -> None:
    engine, factory, course_id = _store(tmp_path)
    service = AdaptiveSessionService(factory)
    gateways = build_session_gateways(service)
    dispatcher = LearningDispatcher(gateways=gateways, receipts=InMemoryReceiptStore())
    started = _call(
        dispatcher,
        LearningToolName.SESSION_START.value,
        _event(
            "learning.session.start",
            course_id=course_id,
            idempotency_key="session-start-stale",
            payload={"course_revision": 1, "mode": "training", "blueprint": {"max_attempts": 2}},
        ),
    )
    rejected = _call(
        dispatcher,
        LearningToolName.TASK_ANSWER.value,
        _event(
            "learning.task.answer",
            session_id=started["result"]["session"]["session_id"],
            expected_revision=1,
            idempotency_key="answer-stale",
            payload={"answer": {"choice_id": "a"}},
        ),
    )
    assert rejected["state"] == "rejected"
    assert rejected["error"]["code"] == "session_revision_conflict"
    assert "ui_intent" not in rejected
    engine.dispose()
