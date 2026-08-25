from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
from collections import Counter
from collections.abc import Callable, Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Protocol
from urllib.parse import urlparse
from uuid import NAMESPACE_URL, uuid5

import httpx
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from spaces.learning.services.adaptive_engine.models import (
    AdaptiveConcept,
    AdaptiveItem,
    ConceptMastery,
    ItemConcept,
    LearningAttempt,
    LearningEvaluation,
    LearningResponse,
    LearningSession,
    MasteryEvidence,
    Misconception,
    ReviewSchedule,
)
from spaces.learning.services.db.models import LearningArtifact
from spaces.learning.services.db.repository import PersistenceConflict
from spaces.learning.services.db.session import build_session_factory, create_learning_engine
from spaces.learning.deployment.migrate import migrate
from spaces.learning.services.ingestion.models import (
    LearningSource,
    LearningSourceChunk,
    LearningSourceRevision,
)
from spaces.learning.services.migration.models import (
    LearningMigrationBatch,
    LearningMigrationRecord,
)
from spaces.learning.services.migration.schemas import (
    CanonicalImportEnvelope,
    TransformationResult,
)
from spaces.learning.services.migration.report import write_validation_report
from spaces.learning.services.migration.transformer import transform_legacy_records
from spaces.learning.services.migration.validator import load_export, validate_dry_run


SessionFactory = Callable[[], Session]
_LEARNHOUSE_TYPES = frozenset({"organization", "program", "course", "chapter", "activity"})


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class LearnHouseImportReceipt(_StrictModel):
    batch_id: str = Field(min_length=1, max_length=128)
    revision: int = Field(ge=1)
    content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    state: Literal["staged"]
    imported_ids: dict[str, str]
    evidence_ref: str = Field(min_length=1, max_length=512)


class MigrationImportResult(_StrictModel):
    version: Literal["learning-migration-import-v1"] = "learning-migration-import-v1"
    batch_id: str
    state: Literal["planned", "applied"]
    content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    counts: dict[str, int]
    learnhouse_revision: int | None = None
    evidence_ref: str | None = None


class LearnHouseMigrationBoundary(Protocol):
    def stage_batch(
        self,
        *,
        batch_id: str,
        content_hash: str,
        envelopes: tuple[CanonicalImportEnvelope, ...],
    ) -> LearnHouseImportReceipt: ...

    def read_batch(self, *, batch_id: str) -> LearnHouseImportReceipt: ...

    def rollback_batch(self, *, batch_id: str, expected_revision: int) -> None: ...


class LearnHouseMigrationHttpGateway:
    def __init__(
        self,
        *,
        base_url: str,
        service_key: str,
        timeout_seconds: float = 30.0,
        client: httpx.Client | None = None,
    ) -> None:
        parsed = urlparse(base_url)
        if (
            parsed.scheme != "http"
            or parsed.hostname not in {"127.0.0.1", "localhost", "learnhouse-api"}
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("LearnHouse migration URL must be internal or loopback")
        if len(service_key) < 32 or service_key.strip() != service_key:
            raise ValueError("LearnHouse migration service key is invalid")
        if timeout_seconds <= 0 or timeout_seconds > 120:
            raise ValueError("LearnHouse migration timeout is invalid")
        self._base_url = base_url.rstrip("/") + "/"
        self._service_key = service_key
        self._timeout = timeout_seconds
        self._client = client or httpx.Client()

    def stage_batch(
        self,
        *,
        batch_id: str,
        content_hash: str,
        envelopes: tuple[CanonicalImportEnvelope, ...],
    ) -> LearnHouseImportReceipt:
        value = self._request(
            "PUT",
            f"migration/batches/{batch_id}",
            json={
                "content_hash": content_hash,
                "envelopes": [item.model_dump(mode="json") for item in envelopes],
            },
        )
        return LearnHouseImportReceipt.model_validate(value["receipt"])

    def read_batch(self, *, batch_id: str) -> LearnHouseImportReceipt:
        value = self._request("GET", f"migration/batches/{batch_id}")
        return LearnHouseImportReceipt.model_validate(value["receipt"])

    def rollback_batch(self, *, batch_id: str, expected_revision: int) -> None:
        self._request(
            "POST",
            f"migration/batches/{batch_id}/rollback",
            json={"expected_revision": expected_revision},
        )

    def close(self) -> None:
        self._client.close()

    def _request(self, method: str, path: str, **kwargs: object) -> dict[str, object]:
        try:
            response = self._client.request(
                method,
                self._base_url + path,
                headers={
                    "X-LearnHouse-Learning-Service-Key": self._service_key,
                },
                timeout=self._timeout,
                follow_redirects=False,
                **kwargs,
            )
        except httpx.HTTPError as error:
            raise RuntimeError("LearnHouse migration service is unavailable") from error
        if not 200 <= response.status_code < 300:
            raise RuntimeError("LearnHouse migration service rejected the request")
        if response.status_code == 204:
            return {}
        try:
            value = response.json()
        except ValueError as error:
            raise RuntimeError("LearnHouse migration service returned malformed JSON") from error
        if not isinstance(value, dict):
            raise RuntimeError("LearnHouse migration service returned malformed data")
        return value


def confirmation_digest(token: str) -> str:
    if len(token) < 16 or token.strip() != token:
        raise ValueError("migration confirmation token is invalid")
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _batch_hash(result: TransformationResult) -> str:
    return _envelope_hash(result.envelopes)


def _envelope_hash(envelopes: Iterable[CanonicalImportEnvelope]) -> str:
    return hashlib.sha256(_canonical([
        item.model_dump(mode="json") for item in envelopes
    ])).hexdigest()


def _text(payload: dict, key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"migration payload {key} is invalid")
    return value


def _integer(payload: dict, key: str) -> int:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"migration payload {key} is invalid")
    return value


def _number(payload: dict, key: str) -> float:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"migration payload {key} is invalid")
    return float(value)


def _timestamp(value: object) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("migration timestamp is invalid")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


class MigrationImporter:
    def __init__(
        self,
        session_factory: SessionFactory,
        *,
        artifact_root: Path,
        learnhouse: LearnHouseMigrationBoundary,
        confirmation_sha256: str,
    ) -> None:
        if len(confirmation_sha256) != 64:
            raise ValueError("migration confirmation digest is invalid")
        self._session_factory = session_factory
        self._artifact_root = artifact_root
        self._learnhouse = learnhouse
        self._confirmation_sha256 = confirmation_sha256

    def run(
        self,
        result: TransformationResult,
        *,
        batch_id: str,
        apply: bool = False,
        confirmation_token: str | None = None,
    ) -> MigrationImportResult:
        self._validate(result, batch_id=batch_id)
        content_hash = _batch_hash(result)
        counts = dict(sorted(Counter(item.record_type for item in result.envelopes).items()))
        planned = MigrationImportResult(
            batch_id=batch_id,
            state="planned",
            content_hash=content_hash,
            counts=counts,
        )
        if not apply:
            return planned
        self._authorize(confirmation_token)
        existing = self._existing(batch_id, content_hash=content_hash)
        if existing is not None:
            return existing

        learnhouse_records = tuple(
            item for item in result.envelopes if item.record_type in _LEARNHOUSE_TYPES
        )
        learnhouse_content_hash = _envelope_hash(learnhouse_records)
        receipt = self._learnhouse.stage_batch(
            batch_id=batch_id,
            content_hash=learnhouse_content_hash,
            envelopes=learnhouse_records,
        )
        self._verify_learnhouse_receipt(
            receipt,
            batch_id=batch_id,
            content_hash=learnhouse_content_hash,
            expected_ids={item.canonical_id for item in learnhouse_records},
        )
        readback = self._learnhouse.read_batch(batch_id=batch_id)
        if readback != receipt:
            self._learnhouse.rollback_batch(
                batch_id=batch_id,
                expected_revision=receipt.revision,
            )
            raise RuntimeError("LearnHouse migration readback did not match stage receipt")

        applied = MigrationImportResult(
            batch_id=batch_id,
            state="applied",
            content_hash=content_hash,
            counts=counts,
            learnhouse_revision=receipt.revision,
            evidence_ref=receipt.evidence_ref,
        )
        created_paths: list[Path] = []
        try:
            with self._session_factory() as session, session.begin():
                concurrent = session.get(LearningMigrationBatch, batch_id)
                if concurrent is not None:
                    if concurrent.content_hash != content_hash:
                        raise PersistenceConflict("migration batch content conflict")
                    return MigrationImportResult.model_validate(concurrent.result_json)
                session.add(LearningMigrationBatch(
                    id=batch_id,
                    source_system="learning_plattform_v1",
                    content_hash=content_hash,
                    state="applied",
                    learnhouse_receipt=receipt.model_dump(mode="json"),
                    result_json=applied.model_dump(mode="json"),
                ))
                self._persist_learning_owned(
                    session,
                    self._map_learnhouse_references(
                        result.envelopes,
                        receipt.imported_ids,
                    ),
                    created_paths=created_paths,
                )
                for item in result.envelopes:
                    session.add(LearningMigrationRecord(
                        batch_id=batch_id,
                        canonical_id=item.canonical_id,
                        record_type=item.record_type,
                        source_system=item.source_system,
                        source_entity=item.source_entity,
                        source_id=item.source_id,
                        content_hash=item.content_hash,
                        target_id=receipt.imported_ids.get(
                            item.canonical_id, item.canonical_id
                        ),
                    ))
        except Exception:
            self._remove_created_paths(created_paths)
            self._learnhouse.rollback_batch(
                batch_id=batch_id,
                expected_revision=receipt.revision,
            )
            raise
        return applied

    def _validate(self, result: TransformationResult, *, batch_id: str) -> None:
        if result.migration_batch_id != batch_id:
            raise ValueError("migration batch does not match transformed records")
        if result.quarantines:
            raise ValueError("migration batch contains quarantined records")
        for item in result.envelopes:
            if item.migration_batch_id != batch_id:
                raise ValueError("migration envelope batch mismatch")
            if item.record_type == "course" and item.payload.get("status") != "imported_draft":
                raise ValueError("published legacy course revisions cannot be imported")

    def _authorize(self, token: str | None) -> None:
        if token is None:
            raise PermissionError("migration apply requires confirmation")
        try:
            observed = confirmation_digest(token)
        except ValueError as error:
            raise PermissionError("migration apply requires confirmation") from error
        if not hmac.compare_digest(observed, self._confirmation_sha256):
            raise PermissionError("migration apply requires confirmation")

    def _existing(
        self, batch_id: str, *, content_hash: str
    ) -> MigrationImportResult | None:
        with self._session_factory() as session:
            row = session.get(LearningMigrationBatch, batch_id)
            if row is None:
                return None
            if row.content_hash != content_hash:
                raise PersistenceConflict("migration batch content conflict")
            if row.state != "applied":
                raise PersistenceConflict("migration batch is not applied")
            return MigrationImportResult.model_validate(row.result_json)

    @staticmethod
    def _map_learnhouse_references(
        envelopes: tuple[CanonicalImportEnvelope, ...],
        target_ids: dict[str, str],
    ) -> tuple[CanonicalImportEnvelope, ...]:
        reference_fields = {"program_id", "course_id", "chapter_id", "activity_id"}
        mapped: list[CanonicalImportEnvelope] = []
        for item in envelopes:
            payload = dict(item.payload)
            for field in reference_fields:
                value = payload.get(field)
                if isinstance(value, str) and value in target_ids:
                    payload[field] = target_ids[value]
            mapped.append(item.model_copy(update={"payload": payload}))
        return tuple(mapped)

    @staticmethod
    def _verify_learnhouse_receipt(
        receipt: LearnHouseImportReceipt,
        *,
        batch_id: str,
        content_hash: str,
        expected_ids: set[str],
    ) -> None:
        if (
            receipt.batch_id != batch_id
            or receipt.content_hash != content_hash
            or receipt.state != "staged"
            or set(receipt.imported_ids) != expected_ids
        ):
            raise RuntimeError("LearnHouse migration receipt did not match request")

    def _persist_learning_owned(
        self,
        session: Session,
        envelopes: Iterable[CanonicalImportEnvelope],
        *,
        created_paths: list[Path],
    ) -> None:
        by_type: dict[str, list[CanonicalImportEnvelope]] = {}
        for item in envelopes:
            by_type.setdefault(item.record_type, []).append(item)
        self._persist_concepts(session, by_type.get("concept", ()))
        self._persist_items(session, by_type.get("adaptive_item", ()))
        self._persist_sources(
            session,
            by_type,
            created_paths=created_paths,
        )
        self._persist_sessions(session, by_type.get("session", ()))
        self._persist_responses(session, by_type.get("response", ()))
        self._persist_evaluations(session, by_type.get("evaluation", ()))
        self._persist_mastery(session, by_type.get("mastery_evidence", ()))
        self._persist_mistakes(
            session,
            by_type.get("misconception", ()),
            by_type.get("review_schedule", ()),
        )

    @staticmethod
    def _persist_concepts(
        session: Session, records: Iterable[CanonicalImportEnvelope]
    ) -> None:
        for item in records:
            payload = item.payload
            session.add(AdaptiveConcept(
                id=item.canonical_id,
                course_id=_text(payload, "course_id"),
                course_revision=_integer(payload, "course_revision"),
                concept_key=_text(payload, "concept_key"),
                title=_text(payload, "title"),
            ))
        session.flush()

    @staticmethod
    def _persist_items(
        session: Session, records: Iterable[CanonicalImportEnvelope]
    ) -> None:
        for item in records:
            payload = item.payload
            item_row = AdaptiveItem(
                id=item.canonical_id,
                course_id=_text(payload, "course_id"),
                course_revision=_integer(payload, "course_revision"),
                activity_id=_text(payload, "activity_id"),
                item_type=_text(payload, "item_type"),
                difficulty=_number(payload, "difficulty"),
                quality_status=_text(payload, "quality_status"),
                expected_answer=dict(payload.get("expected_answer", {})),
                scoring_config=dict(payload.get("scoring_config", {})),
                rubric=list(payload.get("rubric", [])),
            )
            session.add(item_row)
            concepts = payload.get("concept_ids")
            if not isinstance(concepts, list) or not concepts:
                raise ValueError("migration adaptive item has no concepts")
            for concept_id in concepts:
                if not isinstance(concept_id, str):
                    raise ValueError("migration adaptive item concept is invalid")
                session.add(ItemConcept(
                    item_id=item.canonical_id,
                    concept_id=concept_id,
                    course_id=item_row.course_id,
                    course_revision=item_row.course_revision,
                ))
        session.flush()

    def _persist_sources(
        self,
        session: Session,
        by_type: dict[str, list[CanonicalImportEnvelope]],
        *,
        created_paths: list[Path],
    ) -> None:
        for item in by_type.get("source", ()):
            session.add(LearningSource(
                id=item.canonical_id,
                course_id=_text(item.payload, "course_id"),
                title=_text(item.payload, "title"),
                current_revision=_integer(item.payload, "current_revision"),
            ))
        session.flush()
        for item in by_type.get("source_revision", ()):
            payload = item.payload
            source_id = _text(payload, "source_id")
            revision = _integer(payload, "revision")
            content = _text(payload, "content")
            encoded = content.encode("utf-8")
            content_hash = hashlib.sha256(encoded).hexdigest()
            relative = Path("migration") / item.migration_batch_id / source_id / f"r{revision}.txt"
            path = self._artifact_root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists():
                if hashlib.sha256(path.read_bytes()).hexdigest() != content_hash:
                    raise PersistenceConflict("migration artifact content conflict")
            else:
                path.write_bytes(encoded)
                created_paths.append(path)
            artifact_id = str(uuid5(NAMESPACE_URL, f"migration-artifact:{item.canonical_id}"))
            session.add(LearningArtifact(
                id=artifact_id,
                relative_path=relative.as_posix(),
                content_hash=content_hash,
                media_type=_text(payload, "media_type"),
                size_bytes=len(encoded),
                aggregate_type="source",
                aggregate_id=source_id,
                revision=revision,
            ))
            session.add(LearningSourceRevision(
                source_id=source_id,
                revision=revision,
                artifact_id=artifact_id,
                content_hash=content_hash,
                ingestion_spec_version=_text(payload, "ingestion_spec_version"),
                media_type=_text(payload, "media_type"),
                size_bytes=len(encoded),
                status="stored",
            ))
        session.flush()
        for item in by_type.get("source_chunk", ()):
            payload = item.payload
            locator = payload.get("locator")
            if not isinstance(locator, dict) or not locator:
                raise ValueError("migration source chunk locator is invalid")
            content = _text(payload, "content")
            expected_hash = _text(payload, "content_hash")
            if hashlib.sha256(content.encode("utf-8")).hexdigest() != expected_hash:
                raise ValueError("migration source chunk hash mismatch")
            locator_hash = hashlib.sha256(_canonical(locator)).hexdigest()
            session.add(LearningSourceChunk(
                id=item.canonical_id,
                source_id=_text(payload, "source_id"),
                source_revision=_integer(payload, "source_revision"),
                ordinal=_integer(payload, "ordinal"),
                content=content,
                content_hash=expected_hash,
                locator=locator,
                locator_hash=locator_hash,
                metadata_json={
                    "migration_batch_id": item.migration_batch_id,
                    "source_system": item.source_system,
                },
            ))
        session.flush()

    @staticmethod
    def _persist_sessions(
        session: Session, records: Iterable[CanonicalImportEnvelope]
    ) -> None:
        for item in records:
            payload = item.payload
            mode = _text(payload, "mode")
            raw_blueprint = payload.get("blueprint")
            if not isinstance(raw_blueprint, dict):
                raise ValueError("migration session blueprint is invalid")
            item_count = raw_blueprint.get("item_count")
            if isinstance(item_count, bool) or not isinstance(item_count, int) or item_count < 1:
                raise ValueError("migration session item count is invalid")
            blueprint = (
                {"item_count": item_count}
                if mode == "exam"
                else {"max_attempts": item_count}
            )
            session.add(LearningSession(
                id=item.canonical_id,
                course_id=_text(payload, "course_id"),
                course_revision=_integer(payload, "course_revision"),
                actor_id=_text(payload, "actor_id"),
                mode=mode,
                state=_text(payload, "state"),
                revision=_integer(payload, "revision"),
                blueprint=blueprint,
                created_at=_timestamp(payload.get("started_at")) or datetime.now(timezone.utc),
                updated_at=_timestamp(payload.get("completed_at"))
                or _timestamp(payload.get("started_at"))
                or datetime.now(timezone.utc),
                completed_at=_timestamp(payload.get("completed_at")),
            ))
        session.flush()

    @staticmethod
    def _persist_responses(
        session: Session, records: Iterable[CanonicalImportEnvelope]
    ) -> None:
        for item in records:
            payload = item.payload
            session_id = _text(payload, "session_id")
            item_id = _text(payload, "item_id")
            session_row = session.get(LearningSession, session_id)
            item_row = session.get(AdaptiveItem, item_id)
            if session_row is None or item_row is None:
                raise ValueError("migration response parent is missing")
            attempt_id = str(uuid5(NAMESPACE_URL, f"migration-attempt:{item.canonical_id}"))
            session.add(LearningAttempt(
                id=attempt_id,
                session_id=session_id,
                item_id=item_id,
                course_id=session_row.course_id,
                course_revision=session_row.course_revision,
                ordinal=_integer(payload, "ordinal"),
                selection_reason={"reason_codes": ["legacy_import"]},
            ))
            answer = payload.get("answer")
            if not isinstance(answer, dict):
                raise ValueError("migration response answer is invalid")
            session.add(LearningResponse(
                id=item.canonical_id,
                attempt_id=attempt_id,
                response_revision=_integer(payload, "response_revision"),
                answer=answer,
                answer_hash=_text(payload, "answer_hash"),
            ))
        session.flush()

    @staticmethod
    def _persist_evaluations(
        session: Session, records: Iterable[CanonicalImportEnvelope]
    ) -> None:
        for item in records:
            payload = item.payload
            session.add(LearningEvaluation(
                id=item.canonical_id,
                response_id=_text(payload, "response_id"),
                evaluator_type=_text(payload, "evaluator_type"),
                score=_number(payload, "score"),
                confidence=_number(payload, "confidence"),
                accepted=payload.get("accepted") is True,
                rationale_codes=list(payload.get("rationale_codes", [])),
                evaluator_versions=dict(payload.get("evaluator_versions", {})),
                source_refs=list(payload.get("source_refs", [])),
            ))
        session.flush()

    @staticmethod
    def _persist_mastery(
        session: Session, records: Iterable[CanonicalImportEnvelope]
    ) -> None:
        for item in records:
            payload = item.payload
            if payload.get("accepted") is not True:
                continue
            actor_id = _text(payload, "actor_id")
            concept_id = _text(payload, "concept_id")
            score = _number(payload, "score")
            weight = _number(payload, "weight")
            alpha_before = 1.0
            beta_before = 1.0
            mastery = session.get(ConceptMastery, (actor_id, concept_id))
            if mastery is not None:
                alpha_before, beta_before = mastery.alpha, mastery.beta
            alpha_after = alpha_before + score * weight
            beta_after = beta_before + (1 - score) * weight
            if mastery is None:
                session.add(ConceptMastery(
                    actor_id=actor_id,
                    concept_id=concept_id,
                    alpha=alpha_after,
                    beta=beta_after,
                    revision=1,
                ))
            else:
                mastery.alpha = alpha_after
                mastery.beta = beta_after
                mastery.revision += 1
            session.add(MasteryEvidence(
                id=item.canonical_id,
                evaluation_id=_text(payload, "evaluation_id"),
                concept_id=concept_id,
                actor_id=actor_id,
                applied_weight=weight,
                applied_score=score,
                alpha_before=alpha_before,
                beta_before=beta_before,
                alpha_after=alpha_after,
                beta_after=beta_after,
            ))
        session.flush()

    @staticmethod
    def _persist_mistakes(
        session: Session,
        misconceptions: Iterable[CanonicalImportEnvelope],
        schedules: Iterable[CanonicalImportEnvelope],
    ) -> None:
        evidence = list(session.query(MasteryEvidence).order_by(MasteryEvidence.created_at))
        latest = {(row.actor_id, row.concept_id): row.evaluation_id for row in evidence}
        for item in misconceptions:
            payload = item.payload
            key = (_text(payload, "actor_id"), _text(payload, "concept_id"))
            evaluation_id = latest.get(key)
            if evaluation_id is None:
                raise ValueError("migration misconception has no evaluation evidence")
            status = _text(payload, "status")
            status = "resolved" if status == "resolved" else "unresolved"
            session.add(Misconception(
                id=item.canonical_id,
                actor_id=key[0],
                concept_id=key[1],
                tag=_text(payload, "tag")[:128],
                status=status,
                first_evaluation_id=evaluation_id,
                latest_evaluation_id=evaluation_id,
                resolved_at=datetime.now(timezone.utc) if status == "resolved" else None,
            ))
        for item in schedules:
            payload = item.payload
            key = (_text(payload, "actor_id"), _text(payload, "concept_id"))
            evaluation_id = latest.get(key)
            if evaluation_id is None:
                raise ValueError("migration review schedule has no evaluation evidence")
            session.add(ReviewSchedule(
                actor_id=key[0],
                concept_id=key[1],
                interval_index=_integer(payload, "interval_index"),
                due_at=datetime.now(timezone.utc),
                maintenance=payload.get("maintenance") is True,
                alternate_representation_required=(
                    payload.get("alternate_representation_required") is True
                ),
                last_evaluation_id=evaluation_id,
                revision=1,
            ))
        session.flush()

    def _remove_created_paths(self, paths: list[Path]) -> None:
        for path in reversed(paths):
            path.unlink(missing_ok=True)
            parent = path.parent
            while parent != self._artifact_root and parent.is_relative_to(self._artifact_root):
                try:
                    parent.rmdir()
                except OSError:
                    break
                parent = parent.parent


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Plan or apply a guarded Learning migration")
    parser.add_argument("--export-dir", type=Path, required=True)
    parser.add_argument("--batch", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirmation-token")
    parser.add_argument("--report-json", type=Path)
    parser.add_argument("--report-markdown", type=Path)
    args = parser.parse_args(argv)
    if args.apply and not args.confirmation_token:
        parser.error("--apply requires --confirmation-token")
    manifest, records = load_export(args.export_dir)
    if manifest.batch_id != args.batch:
        raise ValueError("migration CLI batch does not match export manifest")
    transformed = transform_legacy_records(records)
    report = validate_dry_run(
        args.export_dir,
        transformed,
        legacy_artifact_root=(
            Path(os.environ["LEARNING_LEGACY_ARTIFACT_ROOT"])
            if os.environ.get("LEARNING_LEGACY_ARTIFACT_ROOT")
            else None
        ),
    )
    if args.report_json and args.report_markdown:
        write_validation_report(
            report,
            json_path=args.report_json,
            markdown_path=args.report_markdown,
        )
    elif args.report_json or args.report_markdown:
        parser.error("report JSON and Markdown paths must be supplied together")
    if not report.ready:
        print(report.model_dump_json())
        return 2
    if not args.apply:
        print(MigrationImportResult(
            batch_id=args.batch,
            state="planned",
            content_hash=_batch_hash(transformed),
            counts=dict(sorted(Counter(item.record_type for item in transformed.envelopes).items())),
        ).model_dump_json())
        return 0

    database_url = os.environ.get("LEARNING_DATABASE_URL", "").strip()
    artifact_root = os.environ.get("LEARNING_ARTIFACT_ROOT", "").strip()
    confirmation_sha256 = os.environ.get("LEARNING_MIGRATION_CONFIRMATION_SHA256", "")
    learnhouse_url = os.environ.get("LEARNHOUSE_LEARNING_SERVICE_URL", "").strip()
    learnhouse_key = os.environ.get("LEARNHOUSE_LEARNING_SERVICE_KEY", "")
    if not all((database_url, artifact_root, confirmation_sha256, learnhouse_url, learnhouse_key)):
        raise RuntimeError("migration apply runtime is not configured")
    migrate(database_url)
    engine = create_learning_engine(database_url)
    gateway = LearnHouseMigrationHttpGateway(
        base_url=learnhouse_url,
        service_key=learnhouse_key,
    )
    try:
        result = MigrationImporter(
            build_session_factory(engine),
            artifact_root=Path(artifact_root),
            learnhouse=gateway,
            confirmation_sha256=confirmation_sha256,
        ).run(
            transformed,
            batch_id=args.batch,
            apply=True,
            confirmation_token=args.confirmation_token,
        )
        print(result.model_dump_json())
        return 0
    finally:
        gateway.close()
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
