from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

from spaces.learning.bridge.dispatcher import ApplicationGateway, ApplicationOutcomeV1
from spaces.learning.contracts.events import LearningToolName
from spaces.learning.contracts.mcp_models import ToolRequestV1
from spaces.learning.contracts.outcomes import (
    AggregateRefV1,
    EvidenceRefV1,
    ToolErrorV1,
    TruthReadbackV1,
)
from spaces.learning.services.adaptive_engine.session_service import AdaptiveSessionService
from spaces.learning.services.course_factory.model_gateway import (
    GatewayUnavailable,
    ModelGateway,
    ModelInvocation,
)


class TutorModelOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["learning-tutor-v1"]
    answer: str = Field(min_length=1, max_length=10_000)
    source_refs: list[str] = Field(min_length=1, max_length=100)
    next_step: str = Field(min_length=1, max_length=2_000)


class TutorAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    answer: str
    source_refs: tuple[str, ...]
    next_step: str
    evidence_ref: str


class TutorService:
    def __init__(
        self, *, sessions: AdaptiveSessionService, gateway: ModelGateway
    ) -> None:
        self._sessions = sessions
        self._gateway = gateway

    async def ask(
        self,
        *,
        session_id: str,
        actor_id: str,
        question: str,
        correlation_id: str,
    ) -> TutorAnswer:
        if not question.strip() or len(question) > 10_000:
            raise ValueError("tutor question is invalid")
        turn = self._sessions.current(session_id, actor_id=actor_id)
        if turn.mode == "exam":
            raise PermissionError("exam sessions do not expose tutor guidance")
        if turn.task is None or not turn.task.source_refs:
            raise LookupError("current task has no tutor sources")
        result = await self._gateway.generate(
            ModelInvocation(
                role="learning_tutor",
                stage="tutoring",
                correlation_id=correlation_id,
                prompt_version="learning-tutor-prompt-v1",
                output_schema_version="learning-tutor-v1",
                input_payload={
                    "question": question.strip(),
                    "task": {
                        "prompt": turn.task.prompt,
                        "type": turn.task.type,
                        "difficulty": turn.task.difficulty,
                    },
                    "source_refs": list(turn.task.source_refs),
                    "policy": "guide_without_revealing_expected_answer",
                },
            ),
            TutorModelOutput,
        )
        allowed = set(turn.task.source_refs)
        if not result.output.source_refs or not set(result.output.source_refs) <= allowed:
            raise ValueError("tutor returned unsupported source references")
        return TutorAnswer(
            answer=result.output.answer,
            source_refs=tuple(result.output.source_refs),
            next_step=result.output.next_step,
            evidence_ref=result.evidence_ref,
        )


class TutorGateway:
    def __init__(
        self, *, sessions: AdaptiveSessionService, tutor: TutorService
    ) -> None:
        self._sessions = sessions
        self._tutor = tutor

    def execute(self, request: ToolRequestV1) -> ApplicationOutcomeV1:
        session_id = request.event.session_id
        question = request.event.payload.get("question")
        if session_id is None or not isinstance(question, str):
            return _rejected("invalid_tutor_request", "tutor requires session and question")
        try:
            answer = asyncio.run(
                self._tutor.ask(
                    session_id=str(session_id),
                    actor_id=request.event.actor.actor_id,
                    question=question,
                    correlation_id=str(request.event.correlation_id),
                )
            )
            turn = self._sessions.current(
                str(session_id), actor_id=request.event.actor.actor_id
            )
        except PermissionError as error:
            return _rejected("tutor_policy_denied", str(error))
        except (LookupError, TypeError, ValueError) as error:
            return _rejected("invalid_tutor_request", str(error))
        except GatewayUnavailable:
            return ApplicationOutcomeV1(
                state="unavailable",
                error=ToolErrorV1(
                    code="tutor_unavailable",
                    message="the admitted tutor backend is unavailable",
                    retryable=True,
                ),
            )
        return ApplicationOutcomeV1(
            state="completed",
            aggregate=AggregateRefV1(
                aggregate_type="session",
                aggregate_id=turn.session_id,
                revision=turn.session_revision,
            ),
            result={"tutor": answer.model_dump(mode="json")},
        )

    def readback(
        self, request: ToolRequestV1, outcome: ApplicationOutcomeV1
    ) -> TruthReadbackV1 | None:
        if outcome.aggregate is None or not self._sessions.has_revision(
            outcome.aggregate.aggregate_id, outcome.aggregate.revision
        ):
            return None
        return TruthReadbackV1(
            invocation_id=request.event.invocation_id,
            correlation_id=request.event.correlation_id,
            owner="adaptive-engine",
            terminal_state="completed",
            aggregate=outcome.aggregate,
            evidence=EvidenceRefV1(
                owner="adaptive-engine",
                evidence_id=f"tutor:{outcome.aggregate.aggregate_id}:{outcome.aggregate.revision}",
                evidence_type="application_readback",
            ),
        )


def build_tutor_gateways(
    *, sessions: AdaptiveSessionService, gateway: ModelGateway
) -> Mapping[LearningToolName, ApplicationGateway]:
    return {
        LearningToolName.TUTOR_ASK: TutorGateway(
            sessions=sessions,
            tutor=TutorService(sessions=sessions, gateway=gateway),
        )
    }


def _rejected(code: str, message: str) -> ApplicationOutcomeV1:
    return ApplicationOutcomeV1(
        state="rejected",
        error=ToolErrorV1(code=code, message=message, retryable=False),
    )
