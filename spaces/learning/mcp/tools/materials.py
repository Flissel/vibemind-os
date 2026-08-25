from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping
from pathlib import Path, PurePosixPath
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from spaces.learning.bridge.dispatcher import ApplicationGateway, ApplicationOutcomeV1
from spaces.learning.bridge.truth_readback import verify_readback
from spaces.learning.contracts.events import LearningToolName
from spaces.learning.contracts.mcp_models import ToolRequestV1
from spaces.learning.contracts.outcomes import (
    AggregateRefV1,
    EvidenceRefV1,
    ToolErrorV1,
    TruthReadbackV1,
)
from spaces.learning.contracts.ui_intents import NavigateIntentV1
from spaces.learning.services.db.models import LearningArtifact
from spaces.learning.services.db.repository import PersistenceConflict
from spaces.learning.services.ingestion.models import (
    LearningSource,
    LearningSourceChunk,
    LearningSourceRevision,
)
from spaces.learning.services.ingestion.pipeline import IngestionPipeline


SessionFactory = Callable[[], Session]


class MaterialImportGateway:
    """Composes LearnHouse source ownership with Learning-owned ingestion."""

    def __init__(
        self,
        *,
        learnhouse: ApplicationGateway,
        pipeline: IngestionPipeline,
        session_factory: SessionFactory,
        artifact_root: Path,
    ) -> None:
        self._learnhouse = learnhouse
        self._pipeline = pipeline
        self._session_factory = session_factory
        self._artifact_root = artifact_root.resolve(strict=True)

    def execute(self, request: ToolRequestV1) -> ApplicationOutcomeV1:
        try:
            upload = self._upload(request)
            learnhouse_outcome = self._learnhouse.execute(request)
            if learnhouse_outcome.state != "completed":
                return learnhouse_outcome
            if learnhouse_outcome.aggregate is None:
                raise RuntimeError("LearnHouse source import omitted aggregate")
            readback = self._learnhouse.readback(request, learnhouse_outcome)
            if readback is None:
                raise RuntimeError("LearnHouse source import omitted readback")
            verify_readback(
                readback,
                invocation_id=request.event.invocation_id,
                correlation_id=request.event.correlation_id,
                aggregate=learnhouse_outcome.aggregate,
            )
            source_id = str(UUID(learnhouse_outcome.aggregate.aggregate_id))
            course_id = request.event.course_id
            if course_id is None or request.event.idempotency_key is None:
                raise ValueError("material import identity is incomplete")
            expected_revision = self._current_revision(source_id)
            record = self._pipeline.ingest(
                source_path=upload["path"],
                source_id=source_id,
                course_id=str(course_id),
                title=_text(request.event.payload, "title"),
                expected_revision=expected_revision,
                idempotency_key=_internal_key(request.event.idempotency_key, source_id),
                media_type=upload["artifact"].media_type,
            ).record
            if record is None or not hasattr(record, "chunk_ids"):
                return _rejected("material_parser_failed", "material could not be parsed")
            chunk_ids = tuple(record.chunk_ids)
            return ApplicationOutcomeV1(
                state="completed",
                aggregate=AggregateRefV1(
                    aggregate_type="source",
                    aggregate_id=source_id,
                    revision=record.revision,
                ),
                result={
                    "source": {
                        "source_id": source_id,
                        "title": _text(request.event.payload, "title"),
                        "revision": record.revision,
                        "artifact_id": record.artifact_id,
                        "locators": [f"source://{source_id}/chunks/{value}" for value in chunk_ids],
                    },
                    "learnhouse_revision": learnhouse_outcome.aggregate.revision,
                    "projection_state": "pending",
                    "qdrant_point_ids": list(chunk_ids),
                },
                ui_intent=NavigateIntentV1(
                    aggregate_id=source_id,
                    aggregate_revision=record.revision,
                    route="/learning",
                ),
            )
        except (PersistenceConflict, ValueError) as error:
            return _rejected("invalid_material_import", str(error))
        except Exception:
            return _unavailable("material_backend_unavailable")

    def readback(
        self, request: ToolRequestV1, outcome: ApplicationOutcomeV1
    ) -> TruthReadbackV1 | None:
        if outcome.aggregate is None or outcome.result is None:
            return None
        result = outcome.result
        point_ids = result.get("qdrant_point_ids")
        if not isinstance(point_ids, list) or not all(
            isinstance(value, str) for value in point_ids
        ):
            return None
        with self._session_factory() as session:
            source = session.get(LearningSource, outcome.aggregate.aggregate_id)
            revision = session.get(
                LearningSourceRevision,
                (outcome.aggregate.aggregate_id, outcome.aggregate.revision),
            )
            chunks = session.scalars(
                select(LearningSourceChunk).where(
                    LearningSourceChunk.source_id == outcome.aggregate.aggregate_id,
                    LearningSourceChunk.source_revision == outcome.aggregate.revision,
                )
            ).all()
            artifact = (
                session.get(LearningArtifact, revision.artifact_id)
                if revision is not None
                else None
            )
        if (
            source is None
            or source.current_revision != outcome.aggregate.revision
            or revision is None
            or revision.status not in {"stored", "indexed"}
            or artifact is None
            or {chunk.id for chunk in chunks} != set(point_ids)
            or not chunks
        ):
            return None
        return TruthReadbackV1(
            invocation_id=request.event.invocation_id,
            correlation_id=request.event.correlation_id,
            owner="learning-ingestion",
            terminal_state="completed",
            aggregate=outcome.aggregate,
            evidence=EvidenceRefV1(
                owner="learning-ingestion",
                evidence_id=f"source:{source.id}:{source.current_revision}",
                evidence_type="artifact_readback",
            ),
        )

    def _upload(self, request: ToolRequestV1) -> dict:
        artifact_id = str(UUID(_text(request.event.payload, "artifact_id")))
        with self._session_factory() as session:
            artifact = session.get(LearningArtifact, artifact_id)
        if artifact is None or artifact.aggregate_type != "source_upload":
            raise ValueError("material upload artifact is unavailable")
        relative = PurePosixPath(artifact.relative_path)
        if relative.is_absolute() or ".." in relative.parts or "\\" in artifact.relative_path:
            raise ValueError("material upload artifact path is invalid")
        path = (self._artifact_root / Path(*relative.parts)).resolve(strict=True)
        if not path.is_relative_to(self._artifact_root) or not path.is_file():
            raise ValueError("material upload artifact escaped its root")
        content = path.read_bytes()
        if (
            len(content) != artifact.size_bytes
            or hashlib.sha256(content).hexdigest() != artifact.content_hash
        ):
            raise ValueError("material upload artifact hash mismatch")
        return {"artifact": artifact, "path": path}

    def _current_revision(self, source_id: str) -> int:
        with self._session_factory() as session:
            source = session.get(LearningSource, source_id)
            return source.current_revision if source is not None else 0


def build_material_gateway(
    *,
    learnhouse: ApplicationGateway,
    pipeline: IngestionPipeline,
    session_factory: SessionFactory,
    artifact_root: Path,
) -> Mapping[LearningToolName, ApplicationGateway]:
    return {
        LearningToolName.MATERIAL_IMPORT: MaterialImportGateway(
            learnhouse=learnhouse,
            pipeline=pipeline,
            session_factory=session_factory,
            artifact_root=artifact_root,
        )
    }


def _text(payload: Mapping[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value or len(value) > 2048:
        raise ValueError(f"material {key} is invalid")
    return value


def _internal_key(public_key: str, source_id: str) -> str:
    digest = hashlib.sha256(f"{public_key}:{source_id}".encode()).hexdigest()
    return f"material-{digest}"


def _rejected(code: str, message: str) -> ApplicationOutcomeV1:
    return ApplicationOutcomeV1(
        state="rejected",
        error=ToolErrorV1(code=code, message=message[:500], retryable=False),
    )


def _unavailable(code: str) -> ApplicationOutcomeV1:
    return ApplicationOutcomeV1(
        state="unavailable",
        error=ToolErrorV1(
            code=code,
            message="the admitted material backend is unavailable",
            retryable=True,
        ),
    )
