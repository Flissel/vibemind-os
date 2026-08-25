from __future__ import annotations

import asyncio
import base64
import binascii
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

import httpx
from pydantic import ValidationError

from spaces.learning.bridge.dispatcher import ApplicationGateway, ApplicationOutcomeV1
from spaces.learning.bridge.penecho_client import (
    PenEchoClient, PenEchoError, PenEchoRevisionConflict, PenEchoSessionV1,
)
from spaces.learning.contracts.events import LearningToolName
from spaces.learning.contracts.mcp_models import ToolRequestV1
from spaces.learning.contracts.outcomes import AggregateRefV1, EvidenceRefV1, ToolErrorV1, TruthReadbackV1
from spaces.learning.contracts.penecho import CanvasRubricResultV1, CanvasSaveV1
from spaces.learning.contracts.ui_intents import OpenCanvasIntentV1, ShowResultIntentV1
from spaces.learning.mcp.tools.tutor import TutorAnswer
from spaces.learning.services.adaptive_engine.models import LearningEvaluation, LearningReviewItem
from spaces.learning.services.adaptive_engine.session_service import (
    AdaptiveSessionService, AnswerCommand, SessionTurn,
)
from spaces.learning.services.course_factory.model_gateway import ModelGateway
from spaces.learning.services.course_factory.repository import SessionFactory
from spaces.learning.services.evaluation.penecho import (
    CanvasEvaluationRequest, SessionPenEchoEvaluator,
)
from spaces.learning.services.evaluation.schemas import RubricDecision, RubricEvaluationRequest
from sqlalchemy import select


CANVAS_TOOL_NAMES = frozenset({
    LearningToolName.CANVAS_OPEN,
    LearningToolName.CANVAS_SAVE,
    LearningToolName.CANVAS_HINT,
    LearningToolName.CANVAS_SUBMIT,
    LearningToolName.CANVAS_REVIEW,
})


class CanvasArtifactImporter(Protocol):
    def import_export(self, export): ...


class CanvasEvaluationBoundary(Protocol):
    def submit(
        self,
        *,
        session_id: str,
        actor_id: str,
        expected_session_revision: int,
        submission_id: str,
        submission,
        snapshot_png: bytes,
        source_refs,
    ) -> "CanvasSubmissionOutcome": ...

    def has_evaluation(self, evaluation_id: str) -> bool: ...

    def has_session_revision(self, session_id: str, revision: int) -> bool: ...

    def review(self, evaluation_id: str) -> dict[str, object] | None: ...


class CanvasTutor(Protocol):
    async def ask(self, **kwargs): ...


@dataclass(frozen=True)
class CanvasSubmissionOutcome:
    result: CanvasRubricResultV1
    turn: SessionTurn


class _SessionCanvasRubric:
    def __init__(
        self, *, gateway: ModelGateway, actor_id: str, submission_id: str,
        submission, snapshot_png: bytes, source_refs,
    ) -> None:
        self._evaluator = SessionPenEchoEvaluator(gateway)
        self._actor_id = actor_id
        self._submission_id = submission_id
        self._submission = submission
        self._snapshot_png = snapshot_png
        self._source_refs = tuple(source_refs)
        self.result: CanvasRubricResultV1 | None = None

    def evaluate(self, request: RubricEvaluationRequest) -> RubricDecision:
        declared = {item.locator for item in self._source_refs}
        if set(request.source_refs) != declared:
            raise ValueError("canvas session sources do not match PenEcho launch sources")
        canvas_request = CanvasEvaluationRequest(
            evaluation_id=request.evaluation_id,
            response_id=request.response_id,
            submission_id=self._submission_id,
            actor_id=self._actor_id,
            submission=self._submission,
            expected_answer=request.expected_answer,
            rubric=tuple(request.rubric),
            source_refs=self._source_refs,
            model_version=request.model_version,
            prompt_version=request.prompt_version,
            source_version=request.source_version,
        )
        self.result, decision = asyncio.run(self._evaluator.evaluate(
            canvas_request, snapshot_png=self._snapshot_png
        ))
        return decision


class CanvasAdaptiveSubmissionService:
    def __init__(self, *, session_factory: SessionFactory, gateway: ModelGateway) -> None:
        self._session_factory = session_factory
        self._gateway = gateway

    def submit(
        self,
        *,
        session_id: str,
        actor_id: str,
        expected_session_revision: int,
        submission_id: str,
        submission,
        snapshot_png: bytes,
        source_refs,
    ) -> CanvasSubmissionOutcome:
        boundary = _SessionCanvasRubric(
            gateway=self._gateway, actor_id=actor_id, submission_id=submission_id,
            submission=submission, snapshot_png=snapshot_png, source_refs=source_refs,
        )
        service = AdaptiveSessionService(
            self._session_factory, rubric_evaluator=boundary
        )
        turn = service.answer(AnswerCommand(
            session_id=session_id,
            actor_id=actor_id,
            expected_session_revision=expected_session_revision,
            answer={
                "kind": "penecho_canvas",
                "submission_id": submission_id,
                "structured_artifact": submission.structured_artifact.model_dump(mode="json"),
                "rendered_snapshot": submission.rendered_snapshot.model_dump(mode="json"),
            },
        ))
        if boundary.result is None:
            raise RuntimeError("canvas session evaluation produced no result")
        return CanvasSubmissionOutcome(result=boundary.result, turn=turn)

    def has_evaluation(self, evaluation_id: str) -> bool:
        with self._session_factory() as session:
            return session.get(LearningEvaluation, str(UUID(evaluation_id))) is not None

    def has_session_revision(self, session_id: str, revision: int) -> bool:
        return AdaptiveSessionService(self._session_factory).has_revision(
            session_id, revision
        )

    def review(self, evaluation_id: str) -> dict[str, object] | None:
        evaluation_id = str(UUID(evaluation_id))
        with self._session_factory() as session:
            evaluation = session.get(LearningEvaluation, evaluation_id)
            if evaluation is None:
                return None
            review = session.scalar(select(LearningReviewItem).where(
                LearningReviewItem.evaluation_id == evaluation_id
            ))
            return {
                "evaluation_id": evaluation_id,
                "score": evaluation.score,
                "confidence": evaluation.confidence,
                "accepted": evaluation.accepted,
                "rationale_codes": evaluation.rationale_codes,
                "review_status": review.status if review is not None else "not_required",
            }


class CanvasEvaluationClient:
    """Authenticated internal client; provider authority remains in learning-worker."""

    def __init__(
        self,
        *,
        base_url: str,
        service_key: str,
        timeout_seconds: float = 90.0,
        http_client: httpx.Client | None = None,
    ) -> None:
        parsed = httpx.URL(base_url)
        if (
            parsed.scheme != "http"
            or parsed.host not in {"learning-worker", "127.0.0.1", "localhost"}
            or parsed.port is None
            or parsed.userinfo
            or parsed.path not in {"", "/"}
        ):
            raise ValueError("canvas evaluation URL must be an admitted internal origin")
        if len(service_key) < 32 or service_key.strip() != service_key:
            raise ValueError("canvas evaluation service key is not configured securely")
        if timeout_seconds <= 0:
            raise ValueError("canvas evaluation timeout must be positive")
        self._base_url = base_url.rstrip("/")
        self._service_key = service_key
        self._timeout = timeout_seconds
        self._client = http_client or httpx.Client()

    def submit(self, **values) -> CanvasSubmissionOutcome:
        payload = {
            "session_id": values["session_id"],
            "actor_id": values["actor_id"],
            "expected_session_revision": values["expected_session_revision"],
            "submission_id": values["submission_id"],
            "submission": values["submission"].model_dump(mode="json"),
            "snapshot_png_base64": base64.b64encode(values["snapshot_png"]).decode(),
            "source_refs": [item.model_dump(mode="json") for item in values["source_refs"]],
        }
        response = self._request("POST", "/internal/canvas/submit", json=payload)
        return CanvasSubmissionOutcome(
            result=CanvasRubricResultV1.model_validate(response["result"]),
            turn=SessionTurn.model_validate(response["turn"]),
        )

    async def ask(self, **values):
        response = await asyncio.to_thread(
            self._request, "POST", "/internal/canvas/hint", json=values
        )
        return TutorAnswer.model_validate(response["tutor"])

    def evaluate(self, request: RubricEvaluationRequest) -> RubricDecision:
        response = self._request(
            "POST",
            "/internal/rubric/evaluate",
            json=request.model_dump(mode="json"),
        )
        return RubricDecision.model_validate(response["decision"])

    def has_evaluation(self, evaluation_id: str) -> bool:
        response = self._request("GET", f"/internal/evaluations/{UUID(evaluation_id)}")
        return bool(response.get("exists"))

    def has_session_revision(self, session_id: str, revision: int) -> bool:
        response = self._request(
            "GET", f"/internal/sessions/{UUID(session_id)}/revisions/{revision}"
        )
        return bool(response.get("exists"))

    def review(self, evaluation_id: str) -> dict[str, object] | None:
        response = self._request(
            "GET", f"/internal/evaluations/{UUID(evaluation_id)}/review"
        )
        value = response.get("review")
        return value if isinstance(value, dict) else None

    def _request(self, method: str, path: str, *, json=None) -> dict[str, object]:
        try:
            response = self._client.request(
                method,
                self._base_url + path,
                headers={"X-VibeMind-Learning-Evaluation-Key": self._service_key},
                json=json,
                timeout=self._timeout,
                follow_redirects=False,
            )
        except httpx.HTTPError as error:
            raise RuntimeError("canvas evaluation service is unavailable") from error
        if not 200 <= response.status_code < 300:
            raise RuntimeError("canvas evaluation service rejected the request")
        try:
            value = response.json()
        except ValueError as error:
            raise RuntimeError("canvas evaluation service returned malformed JSON") from error
        if not isinstance(value, dict):
            raise RuntimeError("canvas evaluation service returned malformed data")
        return value


class CanvasGateway:
    def __init__(
        self,
        *,
        penecho: PenEchoClient,
        artifacts: CanvasArtifactImporter,
        evaluations: CanvasEvaluationBoundary,
        tutor: CanvasTutor,
    ) -> None:
        self._penecho = penecho
        self._artifacts = artifacts
        self._evaluations = evaluations
        self._tutor = tutor

    def execute(self, request: ToolRequestV1) -> ApplicationOutcomeV1:
        try:
            if request.tool is LearningToolName.CANVAS_OPEN:
                return self._open(request)
            if request.tool is LearningToolName.CANVAS_SAVE:
                return self._save(request)
            if request.tool is LearningToolName.CANVAS_HINT:
                return self._hint(request)
            if request.tool is LearningToolName.CANVAS_SUBMIT:
                return self._submit(request)
            if request.tool is LearningToolName.CANVAS_REVIEW:
                return self._review(request)
        except PenEchoRevisionConflict as error:
            return _rejected("canvas_revision_conflict", str(error))
        except PermissionError as error:
            return _rejected("canvas_help_denied", str(error))
        except (ValidationError, LookupError, TypeError, ValueError) as error:
            return _rejected("invalid_canvas_request", str(error))
        except (PenEchoError, RuntimeError):
            return _unavailable("canvas_backend_unavailable")
        raise ValueError("unsupported canvas tool")

    def readback(
        self, request: ToolRequestV1, outcome: ApplicationOutcomeV1
    ) -> TruthReadbackV1 | None:
        if outcome.aggregate is None:
            return None
        session = self._penecho.open_session(
            self._session_id(request), self._session_ref(request)
        )
        result = outcome.result or {}
        if outcome.aggregate.aggregate_type == "session":
            canvas_revision = result.get("canvas_revision")
            if (
                not isinstance(canvas_revision, int)
                or session.revision != canvas_revision
                or not self._evaluations.has_session_revision(
                    outcome.aggregate.aggregate_id, outcome.aggregate.revision
                )
            ):
                return None
        elif (
            str(session.id) != outcome.aggregate.aggregate_id
            or session.revision != outcome.aggregate.revision
        ):
            return None
        evaluation_id = result.get("evaluation_id")
        if isinstance(evaluation_id, str) and not self._evaluations.has_evaluation(
            evaluation_id
        ):
            return None
        return TruthReadbackV1(
            invocation_id=request.event.invocation_id,
            correlation_id=request.event.correlation_id,
            owner="penecho",
            terminal_state="completed",
            aggregate=outcome.aggregate,
            evidence=EvidenceRefV1(
                owner="penecho",
                evidence_id=f"canvas:{session.id}:{session.revision}",
                evidence_type="application_readback",
            ),
        )

    def _open(self, request: ToolRequestV1) -> ApplicationOutcomeV1:
        session = self._current(request)
        activity_id = UUID(_required_text(request, "activity_id"))
        if session.launch is None or session.launch.activity_id != activity_id:
            raise ValueError("canvas activity identity does not match")
        return self._outcome(
            session,
            operation="open",
            ui_intent=OpenCanvasIntentV1(
                aggregate_id=str(session.id), aggregate_revision=session.revision,
                activity_id=activity_id, canvas_revision=session.revision,
            ),
        )

    def _save(self, request: ToolRequestV1) -> ApplicationOutcomeV1:
        revision = _expected_revision(request)
        self._current(request, check_revision=True)
        document = request.event.payload.get("document")
        encoded_snapshot = _required_text(request, "snapshot_png_base64")
        save = CanvasSaveV1.model_validate({
            "expected_revision": revision,
            "document": document,
        })
        session = self._penecho.save_session(
            self._session_id(request), self._session_ref(request), save,
            snapshot_png_base64=encoded_snapshot,
        )
        return self._outcome(session, operation="save")

    def _hint(self, request: ToolRequestV1) -> ApplicationOutcomeV1:
        session = self._current(request, check_revision=True)
        launch = session.launch
        if launch is None or launch.actor_id != request.event.actor.actor_id:
            raise PermissionError("canvas actor is not authorized")
        policy = launch.help_policy
        if launch.mode == "exam" or not policy.tutor_allowed or policy.hint_mode == "none":
            raise PermissionError("canvas help is disabled")
        selection = request.event.payload.get("selection")
        if not isinstance(selection, dict) or set(selection) != {"object_ids"}:
            raise ValueError("canvas hint selection is invalid")
        object_ids = selection["object_ids"]
        if (
            not isinstance(object_ids, list) or not 1 <= len(object_ids) <= 500
            or not all(isinstance(value, str) and 0 < len(value) <= 128 for value in object_ids)
        ):
            raise ValueError("canvas hint selection is invalid")
        answer = asyncio.run(self._tutor.ask(
            session_id=str(session.id), actor_id=launch.actor_id,
            question="Guide the learner on selected canvas objects: " + ", ".join(object_ids),
            correlation_id=str(request.event.correlation_id),
        ))
        value = answer.model_dump(mode="json") if hasattr(answer, "model_dump") else answer
        if not isinstance(value, dict):
            raise ValueError("canvas tutor response is invalid")
        allowed = {item.locator for item in launch.source_refs}
        source_refs = value.get("source_refs")
        if not isinstance(source_refs, (list, tuple)) or not set(source_refs) <= allowed:
            raise ValueError("canvas tutor response is not source grounded")
        return self._outcome(session, operation="hint", extra={"hint": value})

    def _submit(self, request: ToolRequestV1) -> ApplicationOutcomeV1:
        session = self._current(request, check_revision=True)
        if session.launch is None or session.launch.actor_id != request.event.actor.actor_id:
            raise PermissionError("canvas actor is not authorized")
        exported = self._penecho.export_session(
            session.id, self._session_ref(request)
        )
        submission = self._artifacts.import_export(exported)
        try:
            snapshot = base64.b64decode(exported.snapshot_png_base64, validate=True)
        except (binascii.Error, ValueError) as error:
            raise ValueError("canvas snapshot encoding is invalid") from error
        submission_id = _required_text(request, "submission_id")
        UUID(submission_id)
        adaptive_revision = request.event.payload.get("adaptive_session_revision")
        if isinstance(adaptive_revision, bool) or not isinstance(adaptive_revision, int):
            raise ValueError("canvas adaptive session revision is invalid")
        submitted = self._evaluations.submit(
            session_id=str(session.id),
            actor_id=request.event.actor.actor_id,
            expected_session_revision=adaptive_revision,
            submission_id=submission_id,
            submission=submission,
            snapshot_png=snapshot,
            source_refs=session.launch.source_refs,
        )
        result = submitted.result
        turn = submitted.turn
        return ApplicationOutcomeV1(
            state="completed",
            aggregate=AggregateRefV1(
                aggregate_type="session",
                aggregate_id=turn.session_id,
                revision=turn.session_revision,
            ),
            result={
                "operation": "submit",
                "canvas_revision": session.revision,
                "evaluation_id": str(result.evaluation_id),
                "evaluation": result.model_dump(mode="json"),
                "session": turn.model_dump(mode="json"),
            },
            ui_intent=ShowResultIntentV1(
                aggregate_id=turn.session_id,
                aggregate_revision=turn.session_revision,
                result_id=result.evaluation_id,
            ),
        )

    def _review(self, request: ToolRequestV1) -> ApplicationOutcomeV1:
        session = self._current(request)
        evaluation_id = _required_text(request, "evaluation_id")
        UUID(evaluation_id)
        review = self._evaluations.review(evaluation_id)
        if review is None:
            raise LookupError("canvas evaluation review was not found")
        return self._outcome(
            session, operation="review",
            extra={"evaluation_id": evaluation_id, "review": review},
            ui_intent=ShowResultIntentV1(
                aggregate_id=str(session.id), aggregate_revision=session.revision,
                result_id=UUID(evaluation_id),
            ),
        )

    def _current(
        self, request: ToolRequestV1, *, check_revision: bool = False
    ) -> PenEchoSessionV1:
        session = self._penecho.open_session(
            self._session_id(request), self._session_ref(request)
        )
        if session.launch is None or session.launch.actor_id != request.event.actor.actor_id:
            raise PermissionError("canvas actor is not authorized")
        if check_revision and session.revision != _expected_revision(request):
            raise PenEchoRevisionConflict("PenEcho learning canvas revision conflict")
        return session

    @staticmethod
    def _session_id(request: ToolRequestV1) -> UUID:
        if request.event.session_id is None:
            raise ValueError("canvas operation requires session_id")
        return request.event.session_id

    @staticmethod
    def _session_ref(request: ToolRequestV1) -> str:
        return _required_text(request, "session_ref")

    @staticmethod
    def _outcome(
        session: PenEchoSessionV1,
        *,
        operation: str,
        extra: dict[str, object] | None = None,
        ui_intent=None,
    ) -> ApplicationOutcomeV1:
        result: dict[str, object] = {
            "operation": operation,
            "canvas": session.model_dump(mode="json"),
        }
        if extra:
            result.update(extra)
        return ApplicationOutcomeV1(
            state="completed",
            aggregate=AggregateRefV1(
                aggregate_type="canvas", aggregate_id=str(session.id),
                revision=session.revision,
            ),
            result=result,
            ui_intent=ui_intent,
        )


def build_canvas_gateways(
    gateway: CanvasGateway,
) -> Mapping[LearningToolName, ApplicationGateway]:
    return {tool: gateway for tool in CANVAS_TOOL_NAMES}


def _required_text(request: ToolRequestV1, key: str) -> str:
    value = request.event.payload.get(key)
    if not isinstance(value, str) or not value or len(value) > 27_000_000:
        raise ValueError(f"canvas {key} is invalid")
    return value


def _expected_revision(request: ToolRequestV1) -> int:
    value = request.event.expected_revision
    if value is None:
        raise ValueError("canvas operation requires expected_revision")
    return value


def _rejected(code: str, message: str) -> ApplicationOutcomeV1:
    return ApplicationOutcomeV1(
        state="rejected",
        error=ToolErrorV1(code=code, message=message, retryable=False),
    )


def _unavailable(code: str) -> ApplicationOutcomeV1:
    return ApplicationOutcomeV1(
        state="unavailable",
        error=ToolErrorV1(
            code=code, message="the admitted canvas backend is unavailable", retryable=True,
        ),
    )
