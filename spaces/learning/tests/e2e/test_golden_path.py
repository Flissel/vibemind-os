from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from spaces.learning.bridge.dispatcher import ApplicationOutcomeV1, LearningDispatcher
from spaces.learning.contracts.events import LearningToolName
from spaces.learning.contracts.mcp_models import ActorV1, EventEnvelopeV1
from spaces.learning.contracts.outcomes import AggregateRefV1, EvidenceRefV1, TruthReadbackV1
from spaces.learning.mcp import server
from spaces.learning.mcp.tools.materials import MaterialImportGateway
from spaces.learning.services.db.models import Base, LearningArtifact
from spaces.learning.services.db.repository import SqlReceiptStore
from spaces.learning.services.ingestion.models import (
    LearningSource,
    LearningSourceChunk,
    LearningSourceRevision,
)
from spaces.learning.services.ingestion.outbox_worker import QdrantOutboxWorker
from spaces.learning.services.ingestion.pipeline import IngestionPipeline
from spaces.learning.services.ingestion.qdrant_index import QdrantHit, QdrantPoint
from spaces.learning.services.ingestion.repository import SourceRepository
from spaces.learning.validation.golden_path import run_deterministic


CORRELATION_ID = UUID("11111111-1111-4111-8111-111111111111")


class _LearnHouseSourceGateway:
    def __init__(self, source_id: str) -> None:
        self.source_id = source_id
        self.calls: list[str] = []

    def execute(self, request) -> ApplicationOutcomeV1:
        self.calls.append("execute")
        return ApplicationOutcomeV1(
            state="completed",
            aggregate=AggregateRefV1(
                aggregate_type="source", aggregate_id=self.source_id, revision=2
            ),
            result={
                "source": {
                    "source_id": self.source_id,
                    "title": request.event.payload["title"],
                    "revision": 2,
                }
            },
        )

    def readback(self, request, outcome) -> TruthReadbackV1:
        self.calls.append("readback")
        return TruthReadbackV1(
            invocation_id=request.event.invocation_id,
            correlation_id=request.event.correlation_id,
            owner="learnhouse",
            terminal_state="completed",
            aggregate=outcome.aggregate,
            evidence=EvidenceRefV1(
                owner="learnhouse",
                evidence_id=f"source-{self.source_id}-r2",
                evidence_type="application_readback",
            ),
        )


class _Index:
    def __init__(self) -> None:
        self.points: dict[str, QdrantPoint] = {}

    def ensure_schema(self) -> None:
        return None

    def upsert(self, points: list[QdrantPoint]) -> None:
        self.points.update({point.point_id: point for point in points})

    def tombstone(self, point_ids, **kwargs) -> None:
        del kwargs
        for point_id in point_ids:
            self.points.pop(point_id, None)

    def retrieve(self, point_ids: list[str]) -> tuple[QdrantHit, ...]:
        return tuple(
            QdrantHit(point_id=value, score=1.0, payload=self.points[value].payload)
            for value in point_ids
            if value in self.points
        )


class _Embedder:
    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[float(len(text)), 1.0, 0.5] for text in texts]


def _mcp_call(
    dispatcher: LearningDispatcher,
    *,
    tool: LearningToolName,
    event: EventEnvelopeV1,
) -> dict:
    response = server.handle_message(
        {
            "jsonrpc": "2.0",
            "id": str(event.invocation_id),
            "method": "tools/call",
            "params": {
                "name": tool.value,
                "arguments": event.model_dump(mode="json"),
            },
        },
        dispatcher=dispatcher,
    )
    assert response is not None
    return json.loads(response["result"]["content"][0]["text"])


def test_material_import_persists_source_then_projects_qdrant(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'golden.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    artifact_root = tmp_path / "artifacts"
    upload_path = artifact_root / "uploads" / "authority.txt"
    upload_path.parent.mkdir(parents=True)
    content = "OpenFang authorizes provider execution. Terminal readback proves success."
    upload_path.write_text(content, encoding="utf-8")
    digest = hashlib.sha256(upload_path.read_bytes()).hexdigest()
    artifact_id, course_id, source_id = (str(uuid4()) for _ in range(3))
    with factory() as db, db.begin():
        db.add(LearningArtifact(
            id=artifact_id,
            relative_path="uploads/authority.txt",
            content_hash=digest,
            media_type="text/plain",
            size_bytes=len(upload_path.read_bytes()),
            aggregate_type="source_upload",
            aggregate_id=artifact_id,
            revision=1,
        ))
    learnhouse = _LearnHouseSourceGateway(source_id)
    gateway = MaterialImportGateway(
        learnhouse=learnhouse,
        pipeline=IngestionPipeline(
            SourceRepository(factory, artifact_root=artifact_root),
            artifact_root=artifact_root,
        ),
        session_factory=factory,
        artifact_root=artifact_root,
    )
    dispatcher = LearningDispatcher(
        gateways={LearningToolName.MATERIAL_IMPORT: gateway},
        receipts=SqlReceiptStore(factory),
    )
    event = EventEnvelopeV1(
        event_type="learning.material.import",
        invocation_id=uuid4(),
        correlation_id=CORRELATION_ID,
        actor=ActorV1(actor_id="golden-student", actor_type="local_user"),
        course_id=course_id,
        expected_revision=1,
        idempotency_key="golden-material-import-1",
        payload={
            "artifact_id": artifact_id,
            "title": "Authority and readback",
            "source_url": "https://materials.invalid/authority.txt",
        },
    )

    result = _mcp_call(
        dispatcher, tool=LearningToolName.MATERIAL_IMPORT, event=event
    )

    assert result["state"] == "completed"
    assert result["correlation_id"] == str(CORRELATION_ID)
    assert result["result"]["projection_state"] == "pending"
    assert result["result"]["source"]["source_id"] == source_id
    assert result["result"]["source"]["locators"]
    assert learnhouse.calls == ["execute", "readback"]
    with factory() as db:
        source = db.get(LearningSource, source_id)
        revision = db.get(LearningSourceRevision, (source_id, 1))
        chunks = db.scalars(
            select(LearningSourceChunk).where(LearningSourceChunk.source_id == source_id)
        ).all()
    assert source is not None and source.current_revision == 1
    assert revision is not None and revision.status == "stored"
    assert chunks and all(chunk.locator for chunk in chunks)

    index = _Index()
    worker = QdrantOutboxWorker(factory, index=index, embedder=_Embedder())
    assert worker.run_once() is True
    with factory() as db:
        indexed = db.get(LearningSourceRevision, (source_id, 1))
    assert indexed is not None and indexed.status == "indexed"
    hits = index.retrieve([chunk.id for chunk in chunks])
    assert len(hits) == len(chunks)
    assert all(hit.payload["course_id"] == course_id for hit in hits)
    engine.dispose()


def test_complete_semantic_mcp_golden_path(tmp_path: Path) -> None:
    fixture = (
        Path(__file__).parent / "fixtures" / "golden-course" / "authority.md"
    )

    report = run_deterministic(tmp_path / "run", fixture_path=fixture)

    expected_steps = [
        "status",
        "material_import",
        "rag_index",
        "course_generate",
        "course_review",
        "course_publish",
        "chapter_open",
        "session_start",
        "task_answer",
        "canvas_open",
        "canvas_save",
        "canvas_submit",
        "canvas_review",
        "task_next",
        "progress_show",
    ]
    assert [step["name"] for step in report["steps"]] == expected_steps
    assert all(
        step["result"]["correlation_id"] == report["correlation_id"]
        for step in report["steps"]
    )
    assert report["profile"] == "deterministic"
    assert report["live_provider_claim"] is False
    assert report["factory"]["state"] == "published"
    assert report["factory"]["roles"] == [
        "architect",
        "concept_mapper",
        "lesson_author",
        "assessment_designer",
        "source_verifier",
        "quality_reviewer",
    ]
    assert report["rag"]["point_count"] >= 1
    assert report["rag"]["source_locators"]
    assert report["adaptive"]["mastery_evidence_count"] == 2
    assert report["adaptive"]["evaluation_count"] == 2
    assert report["adaptive"]["mastery"] > 0.5
    assert report["adaptive"]["progress"]["completed"] == 2
    assert report["persistence"]["receipt_count"] >= 8
    assert (
        report["persistence"]["receipt_count"]
        == report["persistence"]["terminal_receipt_count"]
    )
    intent_kinds = {intent["kind"] for intent in report["ui_intents"]}
    assert {
        "navigate",
        "open_chapter",
        "open_task",
        "open_canvas",
        "show_result",
        "show_progress",
    } <= intent_kinds
