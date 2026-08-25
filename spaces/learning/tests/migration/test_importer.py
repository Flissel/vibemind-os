from __future__ import annotations

import hashlib
import json
from pathlib import Path

import httpx
import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from spaces.learning.services.adaptive_engine.models import (
    AdaptiveConcept,
    AdaptiveItem,
    ConceptMastery,
    LearningEvaluation,
    LearningResponse,
    LearningSession,
    MasteryEvidence,
)
from spaces.learning.services.db.models import Base, LearningArtifact
from spaces.learning.services.db.repository import PersistenceConflict
from spaces.learning.services.ingestion.models import (
    LearningSource,
    LearningSourceChunk,
    LearningSourceRevision,
)
from spaces.learning.services.migration.importer import (
    LearnHouseImportReceipt,
    LearnHouseMigrationHttpGateway,
    MigrationImporter,
    confirmation_digest,
    main,
)
from spaces.learning.services.migration.exporter import LegacyExporter
from spaces.learning.services.migration.legacy_schema import LegacySnapshot
from spaces.learning.services.migration.models import LearningMigrationBatch
from spaces.learning.services.migration.schemas import (
    CanonicalImportEnvelope,
    TransformationResult,
)


def _id(index: int) -> str:
    return f"00000000-0000-0000-0000-{index:012d}"


def _envelope(record_type: str, index: int, payload: dict[str, object]):
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return CanonicalImportEnvelope(
        record_type=record_type,
        canonical_id=_id(index),
        source_system="learning_plattform_v1",
        source_entity="questions",
        source_id=f"legacy-{index}",
        migration_batch_id="batch-apply-1",
        content_hash=hashlib.sha256(encoded).hexdigest(),
        payload=payload,
    )


def import_fixture() -> TransformationResult:
    values = (
        _envelope("organization", 1, {"name": "Demo", "slug": "demo", "local_default": True}),
        _envelope("program", 2, {"organization_id": _id(1), "name": "AI", "slug": "ai"}),
        _envelope("course", 3, {
            "program_id": _id(2), "title": "Safety", "slug": "safety",
            "revision": 1, "status": "imported_draft",
        }),
        _envelope("chapter", 4, {
            "course_id": _id(3), "course_revision": 1, "title": "Authority",
            "slug": "authority", "description": None,
        }),
        _envelope("concept", 5, {
            "course_id": _id(3), "course_revision": 1, "title": "Authority",
            "slug": "authority", "description": None, "concept_key": "legacy.authority",
        }),
        _envelope("activity", 6, {
            "course_id": _id(3), "course_revision": 1, "chapter_id": _id(4),
            "activity_type": "open", "title": "Name authority",
        }),
        _envelope("adaptive_item", 7, {
            "course_id": _id(3), "course_revision": 1, "chapter_id": _id(4),
            "activity_id": _id(6), "concept_ids": [_id(5)], "item_type": "open",
            "difficulty": 0.6, "quality_status": "approved",
            "expected_answer": {"text": "OpenFang"},
            "scoring_config": {
                "prompt": "Name authority", "options": [],
                "source_refs": [f"source://{_id(8)}/revisions/1"],
                "representation": "open", "remediation_tags": [],
            },
            "rubric": [{
                "criterion_id": "authority", "description": "Names authority", "points": 1,
            }],
        }),
        _envelope("source", 8, {
            "course_id": _id(3), "chapter_id": _id(4),
            "title": "Authority Notes", "current_revision": 1,
        }),
        _envelope("source_revision", 9, {
            "source_id": _id(8), "revision": 1, "source_type": "manual",
            "content": "OpenFang authorizes provider execution.",
            "legacy_content_hash": "a" * 64, "media_type": "text/plain",
            "ingestion_spec_version": "legacy-import-v1",
        }),
        _envelope("source_chunk", 10, {
            "source_id": _id(8), "source_revision": 1, "ordinal": 0,
            "content": "OpenFang authorizes provider execution.",
            "content_hash": hashlib.sha256(b"OpenFang authorizes provider execution.").hexdigest(),
            "locator": {"legacy_chunk_id": "chunk-1"}, "rebuild_vector": True,
        }),
        _envelope("session", 11, {
            "course_id": _id(3), "course_revision": 1, "actor_id": "actor-1",
            "mode": "training", "state": "completed", "revision": 1,
            "blueprint": {"item_count": 1}, "started_at": "2026-01-01T00:00:00Z",
            "completed_at": "2026-01-01T00:01:00Z",
        }),
        _envelope("response", 12, {
            "session_id": _id(11), "item_id": _id(7), "ordinal": 1,
            "answer": {"text": "OpenFang"}, "answer_hash": "b" * 64,
            "response_revision": 1,
        }),
        _envelope("evaluation", 13, {
            "response_id": _id(12), "evaluator_type": "deterministic", "score": 1.0,
            "confidence": 1.0, "accepted": True,
            "rationale_codes": ["legacy_score_imported"],
            "evaluator_versions": {"legacy": "learning_plattform_v1"},
            "source_refs": [f"source://{_id(8)}/revisions/1"],
        }),
        _envelope("mastery_evidence", 14, {
            "evaluation_id": _id(13), "session_id": _id(11), "concept_id": _id(5),
            "actor_id": "actor-1", "score": 1.0, "accepted": True, "weight": 1.0,
        }),
    )
    return TransformationResult(
        migration_batch_id="batch-apply-1",
        envelopes=values,
        warnings=(),
        quarantines=(),
    )


class FakeLearnHouse:
    def __init__(self) -> None:
        self.stage_calls = 0
        self.rollback_calls = 0
        self.receipt: LearnHouseImportReceipt | None = None

    def stage_batch(self, *, batch_id, content_hash, envelopes):
        self.stage_calls += 1
        self.receipt = LearnHouseImportReceipt(
            batch_id=batch_id,
            revision=1,
            content_hash=content_hash,
            state="staged",
            imported_ids={item.canonical_id: item.canonical_id for item in envelopes},
            evidence_ref=f"learnhouse://migration/{batch_id}/revision/1",
        )
        return self.receipt

    def read_batch(self, *, batch_id):
        assert self.receipt and self.receipt.batch_id == batch_id
        return self.receipt

    def rollback_batch(self, *, batch_id, expected_revision):
        assert self.receipt and self.receipt.batch_id == batch_id
        assert expected_revision == self.receipt.revision
        self.rollback_calls += 1


@pytest.fixture()
def import_store(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'import.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir()
    yield factory, artifact_root
    engine.dispose()


def _importer(import_store, gateway, *, cls=MigrationImporter):
    factory, artifact_root = import_store
    return cls(
        factory,
        artifact_root=artifact_root,
        learnhouse=gateway,
        confirmation_sha256=confirmation_digest("confirm-batch-apply-1"),
    )


def test_import_defaults_to_plan_and_requires_batch_bound_confirmation(import_store) -> None:
    gateway = FakeLearnHouse()
    importer = _importer(import_store, gateway)
    result = import_fixture()

    planned = importer.run(result, batch_id="batch-apply-1")
    assert planned.state == "planned"
    assert gateway.stage_calls == 0
    with import_store[0]() as db:
        assert db.scalar(select(func.count()).select_from(LearningMigrationBatch)) == 0

    with pytest.raises(PermissionError, match="confirmation"):
        importer.run(result, batch_id="batch-apply-1", apply=True)
    with pytest.raises(ValueError, match="batch"):
        importer.run(
            result, batch_id="different-batch", apply=True,
            confirmation_token="confirm-batch-apply-1",
        )


def test_import_is_atomic_idempotent_and_populates_learning_owned_tables(import_store) -> None:
    gateway = FakeLearnHouse()
    importer = _importer(import_store, gateway)
    result = import_fixture()

    applied = importer.run(
        result,
        batch_id="batch-apply-1",
        apply=True,
        confirmation_token="confirm-batch-apply-1",
    )
    replay = importer.run(
        result,
        batch_id="batch-apply-1",
        apply=True,
        confirmation_token="confirm-batch-apply-1",
    )

    assert applied.state == "applied"
    assert replay == applied
    assert gateway.stage_calls == 1
    factory, artifact_root = import_store
    with factory() as db:
        for model in (
            AdaptiveConcept, AdaptiveItem, LearningSession, LearningResponse,
            LearningEvaluation, MasteryEvidence, ConceptMastery, LearningSource,
            LearningSourceRevision, LearningSourceChunk, LearningArtifact,
        ):
            assert db.scalar(select(func.count()).select_from(model)) == 1
        batch = db.get(LearningMigrationBatch, "batch-apply-1")
        assert batch is not None and batch.state == "applied"
    artifacts = list(artifact_root.rglob("*.txt"))
    assert len(artifacts) == 1
    assert hashlib.sha256(artifacts[0].read_bytes()).hexdigest() == (
        hashlib.sha256(b"OpenFang authorizes provider execution.").hexdigest()
    )

    changed_course = next(item for item in result.envelopes if item.record_type == "course")
    changed = _envelope("course", 3, {**changed_course.payload, "title": "Changed"})
    conflicting = result.model_copy(update={
        "envelopes": tuple(changed if item == changed_course else item for item in result.envelopes)
    })
    with pytest.raises(PersistenceConflict, match="batch"):
        importer.run(
            conflicting,
            batch_id="batch-apply-1",
            apply=True,
            confirmation_token="confirm-batch-apply-1",
        )


def test_import_rolls_back_learnhouse_and_artifacts_when_learning_transaction_fails(
    import_store,
) -> None:
    class FailingImporter(MigrationImporter):
        def _persist_learning_owned(self, *args, **kwargs):
            super()._persist_learning_owned(*args, **kwargs)
            raise RuntimeError("injected transaction failure")

    gateway = FakeLearnHouse()
    importer = _importer(import_store, gateway, cls=FailingImporter)

    with pytest.raises(RuntimeError, match="injected"):
        importer.run(
            import_fixture(),
            batch_id="batch-apply-1",
            apply=True,
            confirmation_token="confirm-batch-apply-1",
        )

    assert gateway.stage_calls == 1
    assert gateway.rollback_calls == 1
    with import_store[0]() as db:
        assert db.scalar(select(func.count()).select_from(LearningMigrationBatch)) == 0
    assert list(import_store[1].rglob("*.*")) == []


def test_import_rejects_published_legacy_revision_without_mutation(import_store) -> None:
    gateway = FakeLearnHouse()
    importer = _importer(import_store, gateway)
    result = import_fixture()
    course = next(item for item in result.envelopes if item.record_type == "course")
    published = _envelope("course", 3, {**course.payload, "status": "published"})
    result = result.model_copy(update={
        "envelopes": tuple(published if item == course else item for item in result.envelopes)
    })

    with pytest.raises(ValueError, match="published"):
        importer.run(
            result,
            batch_id="batch-apply-1",
            apply=True,
            confirmation_token="confirm-batch-apply-1",
        )
    assert gateway.stage_calls == 0


def test_learnhouse_http_gateway_authenticates_and_reads_strict_receipt() -> None:
    fixture = import_fixture()
    records = tuple(
        item
        for item in fixture.envelopes
        if item.record_type in {"organization", "program", "course", "chapter", "activity"}
    )
    content_hash = hashlib.sha256(json.dumps(
        [item.model_dump(mode="json") for item in records],
        sort_keys=True,
        separators=(",", ":"),
    ).encode()).hexdigest()
    receipt = {
        "batch_id": "batch-apply-1",
        "revision": 1,
        "content_hash": content_hash,
        "state": "staged",
        "imported_ids": {item.canonical_id: item.canonical_id for item in records},
        "evidence_ref": "learnhouse://learning-migration/batch-apply-1/1",
    }
    observed: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        observed.append(request)
        if request.url.path.endswith("/rollback"):
            return httpx.Response(204)
        return httpx.Response(200, json={"receipt": receipt})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    gateway = LearnHouseMigrationHttpGateway(
        base_url="http://learnhouse-api:9000/api/v1/learning",
        service_key="test-only-learning-service-key-32chars",
        client=client,
    )
    staged = gateway.stage_batch(
        batch_id="batch-apply-1", content_hash=content_hash, envelopes=records
    )
    assert gateway.read_batch(batch_id="batch-apply-1") == staged
    gateway.rollback_batch(batch_id="batch-apply-1", expected_revision=1)

    assert [request.method for request in observed] == ["PUT", "GET", "POST"]
    assert all(
        request.headers["X-LearnHouse-Learning-Service-Key"]
        == "test-only-learning-service-key-32chars"
        for request in observed
    )
    assert json.loads(observed[0].content)["content_hash"] == content_hash
    assert json.loads(observed[2].content) == {"expected_revision": 1}


def test_migration_cli_defaults_to_validated_plan_only(tmp_path: Path, capsys) -> None:
    export_dir = tmp_path / "export"
    LegacyExporter().export(
        LegacySnapshot(tables={
            "universities": ({"id": _id(1), "name": "Demo", "slug": "demo"},),
            "study_programs": ({
                "id": _id(2), "university_id": _id(1), "name": "AI", "slug": "ai",
            },),
            "modules": ({
                "id": _id(3), "study_program_id": _id(2), "name": "Safety", "slug": "safety",
            },),
            "topics": ({
                "id": _id(4), "module_id": _id(3), "name": "Authority", "slug": "authority",
            },),
        }),
        export_dir,
        batch_id="batch-cli-1",
    )

    assert main(["--export-dir", str(export_dir), "--batch", "batch-cli-1"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["state"] == "planned"
    assert output["batch_id"] == "batch-cli-1"
    assert not (tmp_path / "artifacts").exists()
