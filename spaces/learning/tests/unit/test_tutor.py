from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

import pytest

from spaces.learning.services.adaptive_engine.session_service import (
    AuditReceipt,
    PublicTask,
    SessionProgress,
    SessionTurn,
)
from spaces.learning.mcp.tools.tutor import TutorService
from spaces.learning.services.course_factory.model_gateway import GatewayResult
from spaces.learning.services.evaluation.schemas import ChoiceOption


@dataclass
class SessionStub:
    mode: str = "training"

    def current(self, session_id: str, *, actor_id: str) -> SessionTurn:
        return SessionTurn(
            session_id=session_id,
            course_id=str(uuid4()),
            session_revision=4,
            state="active",
            mode=self.mode,
            task=PublicTask(
                id=str(uuid4()),
                type="single_choice",
                prompt="Welche Quelle ist autorisiert?",
                options=(ChoiceOption(id="a", label="Kursquelle"),),
                difficulty=0.4,
                representation="quiz",
                source_refs=("source://course/4",),
                presentation={},
            ),
            progress=SessionProgress(completed=1, total=4),
            audit=AuditReceipt(readback_verified=True),
        )


class GatewayStub:
    def __init__(self, *, unsupported: bool = False) -> None:
        self.invocation = None
        self.unsupported = unsupported

    async def generate(self, invocation, output_model):
        self.invocation = invocation
        return GatewayResult(
            output=output_model(
                schema_version="learning-tutor-v1",
                answer="Vergleiche die Quelle mit der Freigabeliste.",
                source_refs=[
                    "source://outside/1" if self.unsupported else "source://course/4"
                ],
                next_step="Begruende deine Auswahl.",
            ),
            evidence_ref="openfang://completion/tutor-1",
        )


@pytest.mark.asyncio
async def test_tutor_uses_authorized_openfang_role_and_current_task_sources() -> None:
    gateway = GatewayStub()
    service = TutorService(sessions=SessionStub(), gateway=gateway)
    result = await service.ask(
        session_id=str(uuid4()),
        actor_id="student-1",
        question="Wie gehe ich vor?",
        correlation_id=str(uuid4()),
    )
    assert result.answer.startswith("Vergleiche")
    assert result.source_refs == ("source://course/4",)
    assert result.evidence_ref == "openfang://completion/tutor-1"
    assert gateway.invocation.role == "learning_tutor"
    assert gateway.invocation.input_payload["source_refs"] == ["source://course/4"]


@pytest.mark.asyncio
async def test_tutor_rejects_unsupported_sources_and_exam_queries() -> None:
    with pytest.raises(ValueError, match="unsupported"):
        await TutorService(sessions=SessionStub(), gateway=GatewayStub(unsupported=True)).ask(
            session_id=str(uuid4()),
            actor_id="student-1",
            question="Wie gehe ich vor?",
            correlation_id=str(uuid4()),
        )

    with pytest.raises(PermissionError, match="exam"):
        await TutorService(sessions=SessionStub(mode="exam"), gateway=GatewayStub()).ask(
            session_id=str(uuid4()),
            actor_id="student-1",
            question="Was ist die Loesung?",
            correlation_id=str(uuid4()),
        )
