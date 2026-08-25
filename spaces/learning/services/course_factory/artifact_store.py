from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import UUID, uuid5

from sqlalchemy import select

from spaces.learning.services.course_factory.models import CourseFactoryStageArtifact
from spaces.learning.services.course_factory.repository import (
    FactoryJobRecord,
    OutputArtifactInput,
    SessionFactory,
)
from spaces.learning.services.course_factory.state_machine import FactoryState
from spaces.learning.services.db.models import LearningArtifact


_ARTIFACT_NAMESPACE = UUID("4c23fe6a-3a57-56ef-a5a0-3ad7f7e7712a")
_MAX_STAGE_OUTPUT_BYTES = 5 * 1024 * 1024
_OUTPUT_STAGES = frozenset(
    {
        FactoryState.INGESTING,
        FactoryState.STRUCTURING,
        FactoryState.AUTHORING,
        FactoryState.ASSESSING,
        FactoryState.VERIFYING,
        FactoryState.QUALITY_GATE,
    }
)


class CourseFactoryArtifactStore:
    def __init__(self, session_factory: SessionFactory, *, artifact_root: Path) -> None:
        self._session_factory = session_factory
        self._artifact_root = artifact_root.resolve(strict=True)
        if not self._artifact_root.is_dir():
            raise ValueError("Learning artifact root must be a directory")

    def write_stage_output(
        self,
        job: FactoryJobRecord,
        *,
        stage: FactoryState,
        payload: dict[str, object],
    ) -> OutputArtifactInput:
        UUID(job.id)
        if stage not in _OUTPUT_STAGES:
            raise ValueError("factory output stage is invalid")
        encoded = _canonical_json(payload).encode("utf-8")
        if len(encoded) > _MAX_STAGE_OUTPUT_BYTES:
            raise ValueError("factory stage output exceeds its size limit")
        digest = hashlib.sha256(encoded).hexdigest()
        relative = f"course-factory/{job.id}/attempt-{job.attempt_number}/{stage.value}.json"
        destination = (self._artifact_root / Path(*relative.split("/"))).resolve(
            strict=False
        )
        if not destination.is_relative_to(self._artifact_root):
            raise ValueError("factory artifact path escaped its root")
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            with destination.open("xb") as handle:
                handle.write(encoded)
        except FileExistsError:
            if destination.read_bytes() != encoded:
                raise RuntimeError("factory stage output is immutable") from None
        artifact_id = str(
            uuid5(
                _ARTIFACT_NAMESPACE,
                f"{job.id}:{job.attempt_number}:{stage.value}",
            )
        )
        return OutputArtifactInput(
            artifact_id=artifact_id,
            relative_path=relative,
            content_hash=digest,
            media_type="application/json",
            size_bytes=len(encoded),
        )

    def read_stage_output(
        self,
        job_id: str,
        *,
        attempt_number: int,
        stage: FactoryState,
    ) -> dict[str, object]:
        job_id = str(UUID(job_id))
        with self._session_factory() as session:
            result = session.execute(
                select(CourseFactoryStageArtifact, LearningArtifact)
                .join(
                    LearningArtifact,
                    LearningArtifact.id
                    == CourseFactoryStageArtifact.output_artifact_id,
                )
                .where(
                    CourseFactoryStageArtifact.job_id == job_id,
                    CourseFactoryStageArtifact.stage == stage.value,
                    LearningArtifact.revision == attempt_number,
                )
            ).first()
            if result is None:
                raise LookupError("factory stage output artifact not found")
            row, artifact = result
        path = (self._artifact_root / Path(*artifact.relative_path.split("/"))).resolve(
            strict=True
        )
        if not path.is_relative_to(self._artifact_root) or not path.is_file():
            raise ValueError("factory stage output escaped its root")
        encoded = path.read_bytes()
        if (
            len(encoded) != artifact.size_bytes
            or hashlib.sha256(encoded).hexdigest() != artifact.content_hash
            or row.output_hash != artifact.content_hash
        ):
            raise ValueError("factory stage output failed integrity verification")
        value = json.loads(encoded)
        if not isinstance(value, dict):
            raise ValueError("factory stage output must be an object")
        return value


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
