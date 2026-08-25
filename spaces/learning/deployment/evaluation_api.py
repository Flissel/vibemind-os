from __future__ import annotations

import base64
import binascii
import hmac
import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Annotated
from uuid import UUID

from fastapi import FastAPI, Header, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import sessionmaker

from spaces.learning.contracts.penecho import CanvasSubmissionV1, SourceReferenceV1
from spaces.learning.mcp.tools.canvas import CanvasAdaptiveSubmissionService
from spaces.learning.mcp.tools.tutor import TutorService
from spaces.learning.services.adaptive_engine.session_service import AdaptiveSessionService
from spaces.learning.services.course_factory.model_gateway import build_openfang_model_gateway
from spaces.learning.services.db.session import create_learning_engine
from spaces.learning.services.evaluation.schemas import RubricDecision, RubricEvaluationRequest
from spaces.learning.services.evaluation.session_rubric import SessionRubricEvaluator


KEY_HEADER = "X-VibeMind-Learning-Evaluation-Key"
KEY_ENV = "LEARNING_EVALUATION_SERVICE_KEY"


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CanvasSubmitRequest(_StrictModel):
    session_id: UUID
    actor_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")]
    expected_session_revision: Annotated[int, Field(ge=1)]
    submission_id: UUID
    submission: CanvasSubmissionV1
    snapshot_png_base64: Annotated[str, Field(min_length=12, max_length=27_000_000)]
    source_refs: Annotated[tuple[SourceReferenceV1, ...], Field(min_length=1, max_length=256)]


class CanvasHintRequest(_StrictModel):
    session_id: UUID
    actor_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")]
    question: Annotated[str, Field(min_length=1, max_length=10_000)]
    correlation_id: UUID


@dataclass(frozen=True)
class _EvaluationServices:
    canvas: CanvasAdaptiveSubmissionService
    tutor: TutorService
    rubric: SessionRubricEvaluator


@lru_cache(maxsize=1)
def _services() -> _EvaluationServices:
    database_url = os.environ.get("LEARNING_DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError("canvas evaluation database is not configured")
    engine = create_learning_engine(database_url)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    gateway = build_openfang_model_gateway()
    return _EvaluationServices(
        canvas=CanvasAdaptiveSubmissionService(
            session_factory=factory,
            gateway=gateway,
        ),
        tutor=TutorService(
            sessions=AdaptiveSessionService(
                factory, external_rubric_types=frozenset({"penecho_canvas"})
            ),
            gateway=gateway,
        ),
        rubric=SessionRubricEvaluator(gateway),
    )


def _require_key(supplied: str) -> None:
    configured = os.environ.get(KEY_ENV, "")
    if len(configured) < 32:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "evaluation service is not configured")
    if not hmac.compare_digest(supplied.encode(), configured.encode()):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "invalid evaluation credentials")


app = FastAPI(title="VibeMind Learning Evaluation", version="1.0.0")


@app.get("/health/ready")
def ready() -> dict[str, str]:
    _services()
    return {"status": "ok"}


@app.post("/internal/canvas/submit")
def submit_canvas(
    payload: CanvasSubmitRequest,
    key: Annotated[str, Header(alias=KEY_HEADER, max_length=512)] = "",
) -> dict[str, object]:
    _require_key(key)
    try:
        snapshot = base64.b64decode(payload.snapshot_png_base64, validate=True)
    except (binascii.Error, ValueError) as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid canvas snapshot") from error
    outcome = _services().canvas.submit(
        session_id=str(payload.session_id), actor_id=payload.actor_id,
        expected_session_revision=payload.expected_session_revision,
        submission_id=str(payload.submission_id), submission=payload.submission,
        snapshot_png=snapshot, source_refs=payload.source_refs,
    )
    return {
        "result": outcome.result.model_dump(mode="json"),
        "turn": outcome.turn.model_dump(mode="json"),
    }


@app.post("/internal/canvas/hint")
async def hint_canvas(
    payload: CanvasHintRequest,
    key: Annotated[str, Header(alias=KEY_HEADER, max_length=512)] = "",
) -> dict[str, object]:
    _require_key(key)
    answer = await _services().tutor.ask(
        session_id=str(payload.session_id), actor_id=payload.actor_id,
        question=payload.question, correlation_id=str(payload.correlation_id),
    )
    return {"tutor": answer.model_dump(mode="json")}


@app.post("/internal/rubric/evaluate")
def evaluate_rubric(
    payload: RubricEvaluationRequest,
    key: Annotated[str, Header(alias=KEY_HEADER, max_length=512)] = "",
) -> dict[str, object]:
    _require_key(key)
    decision: RubricDecision = _services().rubric.evaluate(payload)
    return {"decision": decision.model_dump(mode="json")}


@app.get("/internal/evaluations/{evaluation_id}")
def evaluation_exists(
    evaluation_id: UUID,
    key: Annotated[str, Header(alias=KEY_HEADER, max_length=512)] = "",
) -> dict[str, bool]:
    _require_key(key)
    return {"exists": _services().canvas.has_evaluation(str(evaluation_id))}


@app.get("/internal/evaluations/{evaluation_id}/review")
def evaluation_review(
    evaluation_id: UUID,
    key: Annotated[str, Header(alias=KEY_HEADER, max_length=512)] = "",
) -> dict[str, object]:
    _require_key(key)
    return {"review": _services().canvas.review(str(evaluation_id))}


@app.get("/internal/sessions/{session_id}/revisions/{revision}")
def session_revision_exists(
    session_id: UUID,
    revision: Annotated[int, Field(ge=1)],
    key: Annotated[str, Header(alias=KEY_HEADER, max_length=512)] = "",
) -> dict[str, bool]:
    _require_key(key)
    return {
        "exists": _services().canvas.has_session_revision(str(session_id), revision)
    }


def run() -> None:
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8092, log_level="warning")
