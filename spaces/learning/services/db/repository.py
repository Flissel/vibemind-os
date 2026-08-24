from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Any, Callable
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from spaces.learning.bridge.dispatcher import Receipt, ReceiptStore
from spaces.learning.contracts.outcomes import ToolResultV1
from spaces.learning.services.db.models import (
    LearningAggregateRevision,
    LearningArtifact,
    LearningInvocationReceipt,
    LearningOutboxRecord,
    LearningTerminalEvidence,
)


SessionFactory = Callable[[], Session]
_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:")


class PersistenceConflict(RuntimeError):
    pass


class SqlReceiptStore(ReceiptStore):
    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    def get(self, idempotency_key: str) -> Receipt | None:
        with self._session_factory() as session:
            row = session.get(LearningInvocationReceipt, idempotency_key)
            if row is None:
                return None
            return Receipt(
                request_digest=row.request_digest,
                result=ToolResultV1.model_validate(row.result_json),
            )

    def put(self, idempotency_key: str, receipt: Receipt) -> None:
        with self._session_factory() as session, session.begin():
            existing = session.get(LearningInvocationReceipt, idempotency_key)
            result_json = receipt.result.model_dump(mode="json")
            if existing is not None:
                if (
                    existing.request_digest == receipt.request_digest
                    and existing.result_json == result_json
                ):
                    return
                raise PersistenceConflict("idempotency receipt is immutable")
            session.add(
                LearningInvocationReceipt(
                    idempotency_key=idempotency_key,
                    request_digest=receipt.request_digest,
                    result_json=result_json,
                )
            )


class SqlLearningRepository:
    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    def advance_revision(
        self, aggregate_type: str, aggregate_id: str, *, expected: int
    ) -> int:
        with self._session_factory() as session, session.begin():
            key = (aggregate_type, aggregate_id)
            row = session.get(LearningAggregateRevision, key)
            if row is None:
                if expected != 0:
                    raise PersistenceConflict("aggregate revision conflict")
                session.add(
                    LearningAggregateRevision(
                        aggregate_type=aggregate_type,
                        aggregate_id=aggregate_id,
                        revision=1,
                    )
                )
                return 1
            statement = (
                update(LearningAggregateRevision)
                .where(
                    LearningAggregateRevision.aggregate_type == aggregate_type,
                    LearningAggregateRevision.aggregate_id == aggregate_id,
                    LearningAggregateRevision.revision == expected,
                )
                .values(revision=expected + 1)
            )
            if session.execute(statement).rowcount != 1:
                raise PersistenceConflict("aggregate revision conflict")
            return expected + 1

    def enqueue_outbox(
        self,
        *,
        topic: str,
        idempotency_key: str,
        aggregate_type: str,
        aggregate_id: str,
        payload: dict[str, Any],
    ) -> str:
        with self._session_factory() as session, session.begin():
            existing = session.scalar(
                select(LearningOutboxRecord).where(
                    LearningOutboxRecord.idempotency_key == idempotency_key
                )
            )
            if existing is not None:
                if (
                    existing.topic == topic
                    and existing.aggregate_type == aggregate_type
                    and existing.aggregate_id == aggregate_id
                    and existing.payload == payload
                ):
                    return existing.id
                raise PersistenceConflict("outbox idempotency conflict")
            record = LearningOutboxRecord(
                id=str(uuid4()),
                topic=topic,
                idempotency_key=idempotency_key,
                aggregate_type=aggregate_type,
                aggregate_id=aggregate_id,
                payload=payload,
            )
            session.add(record)
            return record.id

    def record_artifact(
        self,
        *,
        relative_path: str,
        content_hash: str,
        media_type: str,
        size_bytes: int,
        aggregate_type: str,
        aggregate_id: str,
        revision: int,
    ) -> str:
        path = PurePosixPath(relative_path)
        if (
            path.is_absolute()
            or _WINDOWS_DRIVE.match(relative_path)
            or ".." in path.parts
        ):
            raise ValueError("relative_path must stay inside the Learning data root")
        artifact_id = str(uuid4())
        with self._session_factory() as session, session.begin():
            session.add(
                LearningArtifact(
                    id=artifact_id,
                    relative_path=relative_path,
                    content_hash=content_hash,
                    media_type=media_type,
                    size_bytes=size_bytes,
                    aggregate_type=aggregate_type,
                    aggregate_id=aggregate_id,
                    revision=revision,
                )
            )
        return artifact_id

    def read_artifact(self, artifact_id: str) -> dict[str, Any]:
        with self._session_factory() as session:
            row = session.get(LearningArtifact, artifact_id)
            if row is None:
                raise LookupError("artifact not found")
            return {
                "id": row.id,
                "relative_path": row.relative_path,
                "content_hash": row.content_hash,
                "media_type": row.media_type,
                "size_bytes": row.size_bytes,
                "aggregate_type": row.aggregate_type,
                "aggregate_id": row.aggregate_id,
                "revision": row.revision,
            }

    def record_terminal_evidence(self, **values: Any) -> str:
        invocation_id = str(values["invocation_id"])
        with self._session_factory() as session, session.begin():
            existing = session.scalar(
                select(LearningTerminalEvidence).where(
                    LearningTerminalEvidence.invocation_id == invocation_id
                )
            )
            comparable = {
                key: values[key]
                for key in (
                    "correlation_id",
                    "owner",
                    "aggregate_type",
                    "aggregate_id",
                    "aggregate_revision",
                    "terminal_state",
                    "evidence_type",
                    "payload",
                )
            }
            comparable["correlation_id"] = str(comparable["correlation_id"])
            if existing is not None:
                current = {key: getattr(existing, key) for key in comparable}
                if current == comparable:
                    return existing.id
                raise PersistenceConflict("terminal evidence is immutable")
            evidence_id = str(uuid4())
            session.add(
                LearningTerminalEvidence(
                    id=evidence_id,
                    invocation_id=invocation_id,
                    **comparable,
                )
            )
            return evidence_id

    def read_terminal_evidence(self, evidence_id: str) -> dict[str, Any]:
        with self._session_factory() as session:
            row = session.get(LearningTerminalEvidence, evidence_id)
            if row is None:
                raise LookupError("terminal evidence not found")
            return {
                "id": row.id,
                "invocation_id": row.invocation_id,
                "correlation_id": row.correlation_id,
                "owner": row.owner,
                "aggregate_type": row.aggregate_type,
                "aggregate_id": row.aggregate_id,
                "aggregate_revision": row.aggregate_revision,
                "terminal_state": row.terminal_state,
                "evidence_type": row.evidence_type,
                "payload": row.payload,
            }
