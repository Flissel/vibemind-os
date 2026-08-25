from __future__ import annotations

from pathlib import Path
from datetime import datetime, timezone
import os
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event, select, update
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import sessionmaker

from spaces.learning.services.adaptive_engine.models import (
    AdaptiveConcept,
    AdaptiveItem,
    ConceptDependency,
    ConceptMastery,
    ItemConcept,
    LearningAttempt,
    LearningEvaluation,
    LearningResponse,
    MasteryEvidence,
    Misconception,
    ReviewSchedule,
)
from spaces.learning.services.adaptive_engine.repository import (
    AdaptiveRepository,
    AttemptInput,
    SessionInput,
)
from spaces.learning.services.db.models import Base
from spaces.learning.services.db.repository import PersistenceConflict
from spaces.learning.deployment.migrate import migrate


@pytest.fixture()
def adaptive_store(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'adaptive.db'}")

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, connection_record) -> None:
        del connection_record
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture()
def postgres_adaptive_store():
    database_url = os.environ.get("TEST_LEARNING_DATABASE_URL", "").strip()
    if not database_url:
        pytest.skip("TEST_LEARNING_DATABASE_URL is required for PostgreSQL evidence")
    if not database_url.startswith("postgresql+psycopg://") or "_test" not in database_url:
        raise RuntimeError("PostgreSQL integration requires a dedicated _test database")
    migrate(database_url)
    engine = create_engine(database_url, pool_pre_ping=True)
    try:
        yield sessionmaker(bind=engine, expire_on_commit=False)
    finally:
        engine.dispose()


def _catalog(factory):
    course_id = str(uuid4())
    concept_a = str(uuid4())
    concept_b = str(uuid4())
    item_id = str(uuid4())
    with factory() as session, session.begin():
        session.add_all(
            [
                AdaptiveConcept(
                    id=concept_a,
                    course_id=course_id,
                    course_revision=3,
                    concept_key="authority.boundary",
                    title="Authority boundary",
                ),
                AdaptiveConcept(
                    id=concept_b,
                    course_id=course_id,
                    course_revision=3,
                    concept_key="authority.readback",
                    title="Terminal readback",
                ),
                ConceptDependency(
                    id=str(uuid4()),
                    course_id=course_id,
                    course_revision=3,
                    prerequisite_concept_id=concept_a,
                    dependent_concept_id=concept_b,
                ),
                AdaptiveItem(
                    id=item_id,
                    course_id=course_id,
                    course_revision=3,
                    activity_id="activity-authority-case",
                    item_type="case",
                    difficulty=0.45,
                    quality_status="approved",
                    expected_answer={"text": "Use the authorized path."},
                    scoring_config={"mode": "rubric"},
                    rubric=[{"criterion_id": "authority", "points": 2}],
                ),
                ItemConcept(
                    item_id=item_id,
                    concept_id=concept_a,
                    course_id=course_id,
                    course_revision=3,
                ),
            ]
        )
    return course_id, concept_a, concept_b, item_id


def test_adaptive_evidence_graph_persists_immutable_links(adaptive_store) -> None:
    course_id, concept_id, _, item_id = _catalog(adaptive_store)
    repository = AdaptiveRepository(adaptive_store)
    learning_session = repository.start_session(
        SessionInput(
            course_id=course_id,
            course_revision=3,
            actor_id="local-owner",
            mode="training",
            blueprint={},
        )
    )
    attempt = repository.append_attempt(
        learning_session.id,
        expected_session_revision=1,
        value=AttemptInput(item_id=item_id, selection_reason={"target": 0.72}),
    )
    response_id = str(uuid4())
    evaluation_id = str(uuid4())
    with adaptive_store() as session, session.begin():
        session.add(
            LearningResponse(
                id=response_id,
                attempt_id=attempt.id,
                response_revision=1,
                answer={"text": "Use the authorized path."},
                answer_hash="a" * 64,
            )
        )
        session.flush()
        session.add(
            LearningEvaluation(
                id=evaluation_id,
                response_id=response_id,
                evaluator_type="rubric",
                score=0.8,
                confidence=0.9,
                accepted=True,
                rationale_codes=["rubric_pass"],
                evaluator_versions={"model": "openfang", "prompt": "rubric-v1"},
                source_refs=["source://authority/3"],
            )
        )
        session.flush()
        session.add(
            ConceptMastery(
                actor_id="local-owner",
                concept_id=concept_id,
                alpha=2.72,
                beta=2.18,
                revision=2,
            )
        )
        session.flush()
        session.add(
            MasteryEvidence(
                id=str(uuid4()),
                evaluation_id=evaluation_id,
                concept_id=concept_id,
                actor_id="local-owner",
                applied_weight=0.9,
                applied_score=0.8,
                alpha_before=2.0,
                beta_before=2.0,
                alpha_after=2.72,
                beta_after=2.18,
            )
        )
        session.add(
            ReviewSchedule(
                actor_id="local-owner",
                concept_id=concept_id,
                interval_index=1,
                due_at=datetime(2026, 8, 26, 12, tzinfo=timezone.utc),
                maintenance=False,
                last_evaluation_id=evaluation_id,
                revision=1,
            )
        )
        session.add(
            Misconception(
                id=str(uuid4()),
                actor_id="local-owner",
                concept_id=concept_id,
                tag="authority_bypass",
                status="unresolved",
                first_evaluation_id=evaluation_id,
                latest_evaluation_id=evaluation_id,
            )
        )

    with adaptive_store() as session:
        stored_attempt = session.get(LearningAttempt, attempt.id)
        evidence = session.scalar(select(MasteryEvidence))
        review = session.scalar(select(ReviewSchedule))
    assert stored_attempt is not None and stored_attempt.ordinal == 1
    assert evidence is not None and evidence.evaluation_id == evaluation_id
    assert review is not None and review.last_evaluation_id == evaluation_id

    with pytest.raises(IntegrityError):
        with adaptive_store() as session, session.begin():
            session.add(
                MasteryEvidence(
                    id=str(uuid4()),
                    evaluation_id=evaluation_id,
                    concept_id=concept_id,
                    actor_id="local-owner",
                    applied_weight=0.9,
                    applied_score=0.8,
                    alpha_before=2.0,
                    beta_before=2.0,
                    alpha_after=2.72,
                    beta_after=2.18,
                )
            )


def test_attempt_order_and_session_revision_are_compare_and_swap(
    adaptive_store,
) -> None:
    course_id, _, _, item_id = _catalog(adaptive_store)
    repository = AdaptiveRepository(adaptive_store)
    learning_session = repository.start_session(
        SessionInput(
            course_id=course_id,
            course_revision=3,
            actor_id="local-owner",
            mode="exam",
            blueprint={"items": 20},
        )
    )

    first = repository.append_attempt(
        learning_session.id,
        expected_session_revision=1,
        value=AttemptInput(item_id=item_id, selection_reason={"blueprint": "case"}),
    )
    assert first.ordinal == 1
    assert first.session_revision == 2
    with pytest.raises(PersistenceConflict, match="revision"):
        repository.append_attempt(
            learning_session.id,
            expected_session_revision=1,
            value=AttemptInput(item_id=item_id, selection_reason={}),
        )


def test_item_concept_mapping_cannot_cross_course_revision(adaptive_store) -> None:
    course_id, _, foreign_concept_id, item_id = _catalog(adaptive_store)
    with pytest.raises(IntegrityError):
        with adaptive_store() as session, session.begin():
            session.add(
                ItemConcept(
                    item_id=item_id,
                    concept_id=foreign_concept_id,
                    course_id=course_id,
                    course_revision=4,
                )
            )


def test_postgres_rejects_mutation_and_unaccepted_mastery_evidence(
    postgres_adaptive_store,
) -> None:
    factory = postgres_adaptive_store
    course_id, concept_id, _, item_id = _catalog(factory)
    repository = AdaptiveRepository(factory)
    learning_session = repository.start_session(
        SessionInput(
            course_id=course_id,
            course_revision=3,
            actor_id="local-owner",
            mode="training",
            blueprint={},
        )
    )
    attempt = repository.append_attempt(
        learning_session.id,
        expected_session_revision=1,
        value=AttemptInput(item_id=item_id, selection_reason={}),
    )
    response_id = str(uuid4())
    evaluation_id = str(uuid4())
    with factory() as session, session.begin():
        session.add(
            LearningResponse(
                id=response_id,
                attempt_id=attempt.id,
                response_revision=1,
                answer={"text": "uncertain"},
                answer_hash="b" * 64,
            )
        )
    with factory() as session, session.begin():
        session.add(
            LearningEvaluation(
                id=evaluation_id,
                response_id=response_id,
                evaluator_type="rubric",
                score=0.5,
                confidence=0.4,
                accepted=False,
                rationale_codes=["low_confidence"],
                evaluator_versions={"prompt": "rubric-v1"},
                source_refs=["source://authority/3"],
            )
        )

    with pytest.raises(DBAPIError, match="accepted evaluation"):
        with factory() as session, session.begin():
            session.add(
                MasteryEvidence(
                    id=str(uuid4()),
                    evaluation_id=evaluation_id,
                    concept_id=concept_id,
                    actor_id="local-owner",
                    applied_weight=0.4,
                    applied_score=0.5,
                    alpha_before=2.0,
                    beta_before=2.0,
                    alpha_after=2.2,
                    beta_after=2.2,
                )
            )
    with pytest.raises(DBAPIError, match="append-only"):
        with factory() as session, session.begin():
            session.execute(
                update(AdaptiveItem)
                .where(AdaptiveItem.id == item_id)
                .values(difficulty=0.9)
            )
