from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import sessionmaker

from spaces.learning.services.adaptive_engine.models import (
    AdaptiveConcept,
    AdaptiveItem,
    ConceptMastery,
    ItemConcept,
    LearningAttempt,
    LearningEvaluation,
    LearningResponse,
    LearningReviewItem,
    MasteryEvidence,
    ReviewSchedule,
)
from spaces.learning.services.adaptive_engine.session_service import (
    AdaptiveSessionService,
    AnswerCommand,
    SessionStartCommand,
)
from spaces.learning.services.db.models import Base
from spaces.learning.deployment.migrate import migrate
from spaces.learning.services.evaluation.schemas import (
    CriterionEvaluation,
    RubricDecision,
)


NOW = datetime(2026, 8, 25, 12, tzinfo=timezone.utc)


@pytest.fixture()
def adaptive_session_store(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'session.db'}")

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, connection_record) -> None:
        del connection_record
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    course_id, concept_id = _seed_catalog(factory)
    try:
        yield factory, course_id, concept_id
    finally:
        engine.dispose()


@pytest.fixture()
def postgres_adaptive_session_store():
    database_url = os.environ.get("TEST_LEARNING_DATABASE_URL", "").strip()
    if not database_url:
        pytest.skip("TEST_LEARNING_DATABASE_URL is required for PostgreSQL evidence")
    if not database_url.startswith("postgresql+psycopg://") or "_test" not in database_url:
        raise RuntimeError("PostgreSQL e2e requires a dedicated _test database")
    migrate(database_url)
    engine = create_engine(database_url, pool_pre_ping=True)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    course_id, concept_id = _seed_catalog(factory)
    try:
        yield factory, course_id, concept_id
    finally:
        engine.dispose()


def _seed_catalog(factory):
    course_id = str(uuid4())
    concept_id = str(uuid4())
    with factory() as session, session.begin():
        session.add(
            AdaptiveConcept(
                id=concept_id,
                course_id=course_id,
                course_revision=1,
                concept_key="rag.authority",
                title="Quellenautoritaet",
            )
        )
        for index in range(1, 20):
            item_id = str(uuid4())
            choice_id = f"correct-{index}"
            session.add(
                AdaptiveItem(
                    id=item_id,
                    course_id=course_id,
                    course_revision=1,
                    activity_id=f"activity-{index}",
                    item_type="single_choice",
                    difficulty=index / 20,
                    quality_status="approved",
                    expected_answer={"choice_id": choice_id},
                    scoring_config={
                        "prompt": f"Welche Aussage gilt auf Stufe {index}?",
                        "options": [
                            {"id": choice_id, "label": "Quellen zuerst pruefen"},
                            {"id": f"wrong-{index}", "label": "Antwort frei erfinden"},
                        ],
                        "source_refs": [f"source://rag/{index}"],
                        "hints": ["Pruefe zuerst die freigegebene Quelle."],
                        "representation": "quiz" if index % 2 else "worked_example",
                        "remediation_tags": ["authority_bypass"] if index % 2 == 0 else [],
                    },
                    rubric=[],
                )
            )
            session.add(
                ItemConcept(
                    item_id=item_id,
                    concept_id=concept_id,
                    course_id=course_id,
                    course_revision=1,
                )
            )
    return course_id, concept_id


def _service(store) -> AdaptiveSessionService:
    factory, _, _ = store
    return AdaptiveSessionService(factory, clock=lambda: NOW)


class _RubricStub:
    def __init__(self, *, accepted: bool) -> None:
        self.accepted = accepted

    def evaluate(self, request) -> RubricDecision:
        return RubricDecision(
            evaluation_id=request.evaluation_id,
            response_id=request.response_id,
            score=0.8,
            confidence=0.9 if self.accepted else 0.4,
            accepted=self.accepted,
            rationale_codes=["rubric_accepted" if self.accepted else "low_confidence"],
            criterion_results=[
                CriterionEvaluation(
                    criterion_id="authority",
                    awarded_points=0.8,
                    feedback="Die Antwort nutzt den autorisierten Pfad.",
                    source_refs=["source://authority/1"],
                )
            ],
            misconception_tags=[],
            evaluator_versions={
                "model": "stub-v1",
                "prompt": "rubric-prompt-v1",
                "sources": "course-1",
            },
            source_refs=["source://authority/1"],
            evidence_ref="openfang://completion/stub-1",
        )


def _open_item_store(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'open-session.db'}")

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
                concept_key="authority.case",
                title="Authority case",
            )
        )
        db.add(
            AdaptiveItem(
                id=item_id,
                course_id=course_id,
                course_revision=1,
                activity_id="authority-case",
                item_type="case",
                difficulty=0.5,
                quality_status="approved",
                expected_answer={"text": "Use the authorized path."},
                scoring_config={
                    "prompt": "Begruende den autorisierten Ausfuehrungspfad.",
                    "options": [],
                    "source_refs": ["source://authority/1"],
                    "hints": ["Pruefe die Autoritaetskette."],
                    "representation": "case",
                    "remediation_tags": [],
                    "model_version": "stub-v1",
                    "source_version": "course-1",
                },
                rubric=[
                    {
                        "criterion_id": "authority",
                        "description": "Uses the authorized execution path.",
                        "points": 1,
                    }
                ],
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


def test_training_session_resumes_and_persists_audited_feedback(
    adaptive_session_store,
) -> None:
    _, course_id, concept_id = adaptive_session_store
    service = _service(adaptive_session_store)
    turn = service.start(
        SessionStartCommand(
            course_id=course_id,
            course_revision=1,
            actor_id="student-1",
            mode="training",
            blueprint={"max_attempts": 4},
        )
    )

    assert turn.task is not None
    assert turn.progress.completed == 0
    assert turn.progress.total == 4
    assert turn.task.expected_answer is None
    assert service.hint(turn.session_id, actor_id="student-1") == (
        "Pruefe zuerst die freigegebene Quelle."
    )

    answered = service.answer(
        AnswerCommand(
            session_id=turn.session_id,
            actor_id="student-1",
            expected_session_revision=turn.session_revision,
            answer={"choice_id": turn.task.options[0].id},
        )
    )

    assert answered.feedback is not None
    assert answered.feedback.score == 1
    assert answered.feedback.state == "correct"
    assert answered.task is not None
    assert answered.task.id != turn.task.id
    assert answered.progress.completed == 1
    assert answered.audit.evaluation_id is not None
    assert answered.audit.mastery_applied is True
    assert answered.audit.readback_verified is True

    resumed = service.resume(
        course_id=course_id, course_revision=1, actor_id="student-1"
    )
    assert resumed is not None
    assert resumed.session_id == turn.session_id
    assert resumed.task == answered.task
    assert resumed.session_revision == answered.session_revision

    factory, _, _ = adaptive_session_store
    with factory() as session:
        assert session.scalar(select(func.count(LearningResponse.id))) == 1
        assert session.scalar(select(func.count(LearningEvaluation.id))) == 1
        assert session.scalar(select(func.count(MasteryEvidence.id))) == 1
        mastery = session.get(
            ConceptMastery,
            {"actor_id": "student-1", "concept_id": concept_id},
        )
        review = session.get(
            ReviewSchedule,
            {"actor_id": "student-1", "concept_id": concept_id},
        )
    assert mastery is not None and mastery.alpha > 2
    assert review is not None and review.last_evaluation_id == answered.audit.evaluation_id


def test_exam_uses_frozen_blueprint_and_withholds_feedback_until_summary(
    adaptive_session_store,
) -> None:
    _, course_id, _ = adaptive_session_store
    service = _service(adaptive_session_store)
    turn = service.start(
        SessionStartCommand(
            course_id=course_id,
            course_revision=1,
            actor_id="student-exam",
            mode="exam",
            blueprint={
                "item_count": 3,
                "item_types": ["single_choice"],
                "difficulty_bands": [[0.0, 0.4], [0.4, 0.7], [0.7, 1.0]],
            },
        )
    )
    selected = []
    for index in range(3):
        assert turn.task is not None
        selected.append(turn.task.id)
        with pytest.raises(PermissionError, match="exam"):
            service.hint(turn.session_id, actor_id="student-exam")
        turn = service.answer(
            AnswerCommand(
                session_id=turn.session_id,
                actor_id="student-exam",
                expected_session_revision=turn.session_revision,
                answer={"choice_id": turn.task.options[0].id},
            )
        )
        if index < 2:
            assert turn.feedback is None
            assert turn.summary is None

    assert len(set(selected)) == 3
    assert turn.task is None
    assert turn.state == "completed"
    assert turn.summary is not None
    assert turn.summary.correct == 3
    assert turn.summary.score == 1
    assert len(turn.summary.results) == 3


def test_synthetic_learner_progresses_and_failure_schedules_remediation(
    adaptive_session_store,
) -> None:
    factory, course_id, concept_id = adaptive_session_store
    service = _service(adaptive_session_store)
    turn = service.start(
        SessionStartCommand(
            course_id=course_id,
            course_revision=1,
            actor_id="student-trajectory",
            mode="training",
            blueprint={"max_attempts": 30},
        )
    )
    difficulties = []
    remediation_seen = False
    for index in range(30):
        assert turn.task is not None
        difficulties.append(turn.task.difficulty)
        correct = index != 8
        answer_id = turn.task.options[0 if correct else 1].id
        turn = service.answer(
            AnswerCommand(
                session_id=turn.session_id,
                actor_id="student-trajectory",
                expected_session_revision=turn.session_revision,
                answer={"choice_id": answer_id},
            )
        )
        if index == 8:
            with factory() as session:
                review = session.get(
                    ReviewSchedule,
                    {"actor_id": "student-trajectory", "concept_id": concept_id},
                )
            assert review is not None
            assert review.alternate_representation_required is True
        if turn.task is not None and turn.task.representation == "worked_example":
            remediation_seen = True

    assert turn.state == "completed"
    assert difficulties[-1] > difficulties[0]
    assert remediation_seen is True
    with factory() as session:
        assert session.scalar(select(func.count(LearningAttempt.id))) == 30
        assert session.scalar(select(func.count(LearningEvaluation.id))) == 30


def test_postgres_session_transaction_has_terminal_readback_and_audit(
    postgres_adaptive_session_store,
) -> None:
    factory, course_id, _ = postgres_adaptive_session_store
    service = AdaptiveSessionService(factory, clock=lambda: NOW)
    turn = service.start(
        SessionStartCommand(
            course_id=course_id,
            course_revision=1,
            actor_id="postgres-student",
            mode="training",
            blueprint={"max_attempts": 1},
        )
    )
    assert turn.task is not None
    completed = service.answer(
        AnswerCommand(
            session_id=turn.session_id,
            actor_id="postgres-student",
            expected_session_revision=turn.session_revision,
            answer={"choice_id": turn.task.options[0].id},
        )
    )
    assert completed.state == "completed"
    assert completed.audit.readback_verified is True
    with factory() as db:
        response = db.scalar(select(LearningResponse))
        evaluation = db.scalar(select(LearningEvaluation))
        evidence = db.scalar(select(MasteryEvidence))
    assert response is not None
    assert evaluation is not None and evaluation.response_id == response.id
    assert evidence is not None and evidence.evaluation_id == evaluation.id


@pytest.mark.parametrize("accepted", [True, False])
def test_open_response_uses_rubric_boundary_for_mastery_or_review(
    tmp_path: Path, accepted: bool
) -> None:
    engine, factory, course_id = _open_item_store(tmp_path)
    service = AdaptiveSessionService(
        factory,
        clock=lambda: NOW,
        rubric_evaluator=_RubricStub(accepted=accepted),
    )
    turn = service.start(
        SessionStartCommand(
            course_id=course_id,
            course_revision=1,
            actor_id=f"rubric-{accepted}",
            mode="training",
            blueprint={"max_attempts": 1},
        )
    )
    assert turn.task is not None and turn.task.type == "case"
    completed = service.answer(
        AnswerCommand(
            session_id=turn.session_id,
            actor_id=f"rubric-{accepted}",
            expected_session_revision=turn.session_revision,
            answer={"text": "Use the authorized path because it is auditable."},
        )
    )
    assert completed.state == "completed"
    assert completed.audit.mastery_applied is accepted
    with factory() as db:
        reviews = db.scalar(select(func.count(LearningReviewItem.id)))
        evidence = db.scalar(select(func.count(MasteryEvidence.id)))
    assert reviews == (0 if accepted else 1)
    assert evidence == (1 if accepted else 0)
    engine.dispose()
