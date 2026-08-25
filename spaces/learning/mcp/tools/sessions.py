from __future__ import annotations

from collections.abc import Mapping
from uuid import UUID

from spaces.learning.bridge.dispatcher import ApplicationGateway, ApplicationOutcomeV1
from spaces.learning.contracts.events import LearningToolName
from spaces.learning.contracts.mcp_models import ToolRequestV1
from spaces.learning.contracts.outcomes import (
    AggregateRefV1,
    EvidenceRefV1,
    ToolErrorV1,
    TruthReadbackV1,
)
from spaces.learning.contracts.ui_intents import (
    OpenTaskIntentV1,
    ShowProgressIntentV1,
    ShowResultIntentV1,
)
from spaces.learning.services.adaptive_engine.session_service import (
    AdaptiveSessionService,
    AnswerCommand,
    SessionStartCommand,
    SessionTurn,
)
from spaces.learning.services.db.repository import PersistenceConflict


SESSION_TOOL_NAMES = frozenset(
    {
        LearningToolName.SESSION_START,
        LearningToolName.TASK_NEXT,
        LearningToolName.TASK_ANSWER,
        LearningToolName.HINT_REQUEST,
        LearningToolName.PROGRESS_SHOW,
    }
)


class AdaptiveSessionGateway:
    def __init__(self, service: AdaptiveSessionService) -> None:
        self._service = service

    def execute(self, request: ToolRequestV1) -> ApplicationOutcomeV1:
        try:
            if request.tool is LearningToolName.SESSION_START:
                turn = self._start(request)
                return self._turn_outcome(turn)
            if request.tool is LearningToolName.TASK_ANSWER:
                turn = self._answer(request)
                return self._turn_outcome(turn)
            if request.tool is LearningToolName.TASK_NEXT:
                return self._turn_outcome(self._current(request))
            if request.tool is LearningToolName.HINT_REQUEST:
                turn = self._current(request)
                hint = self._service.hint(
                    turn.session_id, actor_id=request.event.actor.actor_id
                )
                return self._turn_outcome(turn, extra={"hint": hint}, intent=False)
            if request.tool is LearningToolName.PROGRESS_SHOW:
                turn = self._current(request)
                return self._turn_outcome(
                    turn,
                    extra={"progress": turn.progress.model_dump(mode="json")},
                    progress_intent=True,
                )
        except PersistenceConflict as error:
            code = (
                "session_revision_conflict"
                if "revision" in str(error)
                else "session_state_conflict"
            )
            return _rejected(code, str(error))
        except PermissionError as error:
            return _rejected("session_access_denied", str(error))
        except (LookupError, TypeError, ValueError) as error:
            return _rejected("invalid_session_request", str(error))
        raise ValueError("unsupported adaptive session tool")

    def readback(
        self, request: ToolRequestV1, outcome: ApplicationOutcomeV1
    ) -> TruthReadbackV1 | None:
        if outcome.aggregate is None:
            return None
        if not self._service.has_revision(
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
                evidence_id=(
                    f"session:{outcome.aggregate.aggregate_id}:"
                    f"{outcome.aggregate.revision}"
                ),
                evidence_type="application_readback",
            ),
        )

    def _start(self, request: ToolRequestV1) -> SessionTurn:
        course_id = request.event.course_id
        if course_id is None:
            raise ValueError("session start requires course_id")
        payload = request.event.payload
        revision = payload.get("course_revision")
        mode = payload.get("mode")
        blueprint = payload.get("blueprint")
        if isinstance(revision, bool) or not isinstance(revision, int):
            raise ValueError("session start requires course_revision")
        if mode not in {"training", "exam"} or not isinstance(blueprint, dict):
            raise ValueError("session start mode or blueprint is invalid")
        return self._service.start(
            SessionStartCommand(
                course_id=str(course_id),
                course_revision=revision,
                actor_id=request.event.actor.actor_id,
                mode=mode,
                blueprint=blueprint,
            )
        )

    def _answer(self, request: ToolRequestV1) -> SessionTurn:
        session_id = request.event.session_id
        revision = request.event.expected_revision
        answer = request.event.payload.get("answer")
        if session_id is None or revision is None or not isinstance(answer, dict):
            raise ValueError("task answer requires session, revision, and answer")
        return self._service.answer(
            AnswerCommand(
                session_id=str(session_id),
                actor_id=request.event.actor.actor_id,
                expected_session_revision=revision,
                answer=answer,
            )
        )

    def _current(self, request: ToolRequestV1) -> SessionTurn:
        session_id = request.event.session_id
        if session_id is None:
            raise ValueError("session operation requires session_id")
        return self._service.current(
            str(session_id), actor_id=request.event.actor.actor_id
        )

    @staticmethod
    def _turn_outcome(
        turn: SessionTurn,
        *,
        extra: dict[str, object] | None = None,
        intent: bool = True,
        progress_intent: bool = False,
    ) -> ApplicationOutcomeV1:
        aggregate = AggregateRefV1(
            aggregate_type="session",
            aggregate_id=turn.session_id,
            revision=turn.session_revision,
        )
        result: dict[str, object] = {"session": turn.model_dump(mode="json")}
        if extra:
            result.update(extra)
        ui_intent = None
        if progress_intent:
            ui_intent = ShowProgressIntentV1(
                aggregate_id=turn.session_id,
                aggregate_revision=turn.session_revision,
                course_id=UUID(turn.course_id),
            )
        elif intent and turn.task is not None:
            ui_intent = OpenTaskIntentV1(
                aggregate_id=turn.session_id,
                aggregate_revision=turn.session_revision,
                session_id=UUID(turn.session_id),
                task_id=UUID(turn.task.id),
            )
        elif intent and turn.summary is not None:
            result_id = turn.audit.evaluation_id or turn.session_id
            ui_intent = ShowResultIntentV1(
                aggregate_id=turn.session_id,
                aggregate_revision=turn.session_revision,
                result_id=UUID(result_id),
            )
        return ApplicationOutcomeV1(
            state="completed",
            aggregate=aggregate,
            result=result,
            ui_intent=ui_intent,
        )


def build_session_gateways(
    service: AdaptiveSessionService,
) -> Mapping[LearningToolName, ApplicationGateway]:
    gateway = AdaptiveSessionGateway(service)
    return {tool: gateway for tool in SESSION_TOOL_NAMES}


def _rejected(code: str, message: str) -> ApplicationOutcomeV1:
    return ApplicationOutcomeV1(
        state="rejected",
        error=ToolErrorV1(code=code, message=message, retryable=False),
    )
