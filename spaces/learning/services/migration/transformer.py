from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from collections.abc import Iterable
from uuid import NAMESPACE_URL, uuid5

from spaces.learning.services.migration.legacy_schema import LegacyExportRecord
from spaces.learning.services.migration.schemas import (
    CanonicalImportEnvelope,
    TransformationIssue,
    TransformationResult,
)


_FORMAT_MAP = {
    "cloze": "fill_in",
    "multiple_choice": "multiple_choice",
    "drag_drop": "ordering",
    "matching": "matching",
    "definition": "open",
    "programming": "code",
}


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _target_id(record: LegacyExportRecord, record_type: str) -> str:
    identity = (
        f"vibemind-learning:{record.source_system}:{record.entity}:"
        f"{record.source_id}:{record_type}"
    )
    return str(uuid5(NAMESPACE_URL, identity))


def _envelope(
    record: LegacyExportRecord,
    record_type: str,
    payload: dict[str, object],
) -> CanonicalImportEnvelope:
    return CanonicalImportEnvelope(
        record_type=record_type,
        canonical_id=_target_id(record, record_type),
        source_system=record.source_system,
        source_entity=record.entity,
        source_id=record.source_id,
        migration_batch_id=record.migration_batch_id,
        content_hash=hashlib.sha256(_canonical(payload)).hexdigest(),
        payload=payload,
    )


def _issue(
    record: LegacyExportRecord,
    *,
    severity: str,
    code: str,
    details: dict[str, object] | None = None,
) -> TransformationIssue:
    return TransformationIssue(
        severity=severity,
        code=code,
        source_system=record.source_system,
        source_entity=record.entity,
        source_id=record.source_id,
        migration_batch_id=record.migration_batch_id,
        source_content_hash=record.content_hash,
        details=details or {},
    )


class _Transformer:
    def __init__(self, records: Iterable[LegacyExportRecord]) -> None:
        values = tuple(records)
        if not values:
            raise ValueError("legacy transformation requires at least one record")
        batches = {record.migration_batch_id for record in values}
        sources = {record.source_system for record in values}
        if len(batches) != 1 or len(sources) != 1:
            raise ValueError("legacy transformation requires one source and batch")
        self.batch_id = next(iter(batches))
        self.records, self.warnings, self.quarantines = self._deduplicate(values)
        self.index = {(record.entity, record.source_id): record for record in self.records}
        self.envelopes: list[CanonicalImportEnvelope] = []

    @staticmethod
    def _deduplicate(
        records: tuple[LegacyExportRecord, ...],
    ) -> tuple[
        tuple[LegacyExportRecord, ...],
        list[TransformationIssue],
        list[TransformationIssue],
    ]:
        groups: dict[tuple[str, str], list[LegacyExportRecord]] = defaultdict(list)
        for record in records:
            groups[(record.entity, record.source_id)].append(record)
        admitted: list[LegacyExportRecord] = []
        warnings: list[TransformationIssue] = []
        quarantines: list[TransformationIssue] = []
        for key in sorted(groups):
            group = groups[key]
            hashes = {record.content_hash for record in group}
            if len(hashes) > 1:
                quarantines.append(_issue(
                    group[0],
                    severity="quarantine",
                    code="duplicate_source_id",
                    details={"content_hashes": sorted(hashes)},
                ))
                continue
            admitted.append(group[0])
            if len(group) > 1:
                warnings.append(_issue(
                    group[0],
                    severity="warning",
                    code="duplicate_identical_record",
                    details={"duplicate_count": len(group)},
                ))
        return tuple(admitted), warnings, quarantines

    def run(self) -> TransformationResult:
        handlers = {
            "universities": self._organization,
            "study_programs": self._program,
            "modules": self._course,
            "topics": self._topic,
            "knowledge_documents": self._source,
            "knowledge_chunks": self._chunk,
            "task_catalog_items": self._task,
            "quiz_attempts": self._session,
            "quiz_answer_submissions": self._submission,
            "evaluations": self._mastery,
            "artifacts": self._artifact,
            "mistake_journal": self._mistake,
        }
        for record in self.records:
            handler = handlers.get(record.entity)
            if handler is not None:
                handler(record)
        referenced_questions = {
            str(record.payload.get("source_question_id"))
            for record in self.records
            if record.entity == "task_catalog_items"
            and record.payload.get("source_question_id") is not None
        }
        for record in self.records:
            if record.entity == "questions" and record.source_id not in referenced_questions:
                self._standalone_question(record)
        self.envelopes.sort(
            key=lambda item: (item.record_type, item.canonical_id)
        )
        self.warnings.sort(key=lambda item: (item.code, item.source_entity, item.source_id))
        self.quarantines.sort(key=lambda item: (item.code, item.source_entity, item.source_id))
        return TransformationResult(
            migration_batch_id=self.batch_id,
            envelopes=tuple(self.envelopes),
            warnings=tuple(self.warnings),
            quarantines=tuple(self.quarantines),
        )

    def _get(self, entity: str, source_id: object) -> LegacyExportRecord | None:
        return self.index.get((entity, str(source_id))) if source_id is not None else None

    def _missing(self, record: LegacyExportRecord, entity: str, source_id: object) -> None:
        self.quarantines.append(_issue(
            record,
            severity="quarantine",
            code="missing_parent",
            details={"parent_entity": entity, "parent_source_id": str(source_id)},
        ))

    def _course_for_topic(
        self, record: LegacyExportRecord,
    ) -> tuple[LegacyExportRecord, LegacyExportRecord] | None:
        topic = self._get("topics", record.payload.get("topic_id"))
        if topic is None:
            self._missing(record, "topics", record.payload.get("topic_id"))
            return None
        module = self._get("modules", topic.payload.get("module_id"))
        if module is None:
            self._missing(record, "modules", topic.payload.get("module_id"))
            return None
        return topic, module

    def _organization(self, record: LegacyExportRecord) -> None:
        self.envelopes.append(_envelope(record, "organization", {
            "name": record.payload.get("name"),
            "slug": record.payload.get("slug"),
            "local_default": True,
        }))

    def _program(self, record: LegacyExportRecord) -> None:
        parent = self._get("universities", record.payload.get("university_id"))
        if parent is None:
            self._missing(record, "universities", record.payload.get("university_id"))
            return
        self.envelopes.append(_envelope(record, "program", {
            "organization_id": _target_id(parent, "organization"),
            "name": record.payload.get("name"),
            "slug": record.payload.get("slug"),
        }))

    def _course(self, record: LegacyExportRecord) -> None:
        parent = self._get("study_programs", record.payload.get("study_program_id"))
        if parent is None:
            self._missing(record, "study_programs", record.payload.get("study_program_id"))
            return
        self.envelopes.append(_envelope(record, "course", {
            "program_id": _target_id(parent, "program"),
            "title": record.payload.get("name"),
            "slug": record.payload.get("slug"),
            "revision": 1,
            "status": "imported_draft",
        }))

    def _topic(self, record: LegacyExportRecord) -> None:
        module = self._get("modules", record.payload.get("module_id"))
        if module is None:
            self._missing(record, "modules", record.payload.get("module_id"))
            return
        base = {
            "course_id": _target_id(module, "course"),
            "course_revision": 1,
            "title": record.payload.get("name"),
            "slug": record.payload.get("slug"),
            "description": record.payload.get("description"),
        }
        self.envelopes.append(_envelope(record, "chapter", base))
        self.envelopes.append(_envelope(record, "concept", {
            **base,
            "concept_key": f"legacy.topic.{record.source_id}",
        }))

    def _source(self, record: LegacyExportRecord) -> None:
        relationship = self._course_for_topic(record)
        if relationship is None:
            return
        topic, module = relationship
        source_id = _target_id(record, "source")
        self.envelopes.append(_envelope(record, "source", {
            "course_id": _target_id(module, "course"),
            "chapter_id": _target_id(topic, "chapter"),
            "title": record.payload.get("title"),
            "current_revision": 1,
        }))
        self.envelopes.append(_envelope(record, "source_revision", {
            "source_id": source_id,
            "revision": 1,
            "source_type": record.payload.get("source_type"),
            "content": record.payload.get("content"),
            "legacy_content_hash": record.payload.get("content_hash"),
            "media_type": "text/plain",
            "ingestion_spec_version": "legacy-import-v1",
        }))

    def _chunk(self, record: LegacyExportRecord) -> None:
        document = self._get("knowledge_documents", record.payload.get("document_id"))
        if document is None:
            self._missing(record, "knowledge_documents", record.payload.get("document_id"))
            return
        ordinal = record.payload.get("chunk_index")
        if isinstance(ordinal, bool) or not isinstance(ordinal, int) or ordinal < 0:
            self.quarantines.append(_issue(
                record, severity="quarantine", code="invalid_chunk_ordinal"
            ))
            return
        self.envelopes.append(_envelope(record, "source_chunk", {
            "source_id": _target_id(document, "source"),
            "source_revision": 1,
            "ordinal": ordinal,
            "content": record.payload.get("content"),
            "content_hash": record.payload.get("content_hash"),
            "locator": {"legacy_chunk_id": record.source_id},
            "rebuild_vector": True,
        }))

    def _task(self, record: LegacyExportRecord) -> None:
        task_format = record.payload.get("format")
        item_type = _FORMAT_MAP.get(str(task_format))
        if item_type is None:
            self.quarantines.append(_issue(
                record,
                severity="quarantine",
                code="unsupported_task_format",
                details={"format": str(task_format)},
            ))
            return
        relationship = self._course_for_topic(record)
        if relationship is None:
            return
        topic, module = relationship
        difficulty = self._difficulty(record)
        if difficulty is None:
            return
        question = self._get("questions", record.payload.get("source_question_id"))
        answers = tuple(
            candidate for candidate in self.records
            if candidate.entity == "answers"
            and question is not None
            and candidate.payload.get("question_id") == question.source_id
        )
        expected = record.payload.get("expected_answer")
        if expected is None:
            expected = next(
                (answer.payload.get("text") for answer in answers if answer.payload.get("is_correct") is True),
                "Manual review required",
            )
        source_refs = self._source_refs(topic, question)
        activity_id = _target_id(record, "activity")
        common = {
            "course_id": _target_id(module, "course"),
            "course_revision": 1,
            "chapter_id": _target_id(topic, "chapter"),
        }
        self.envelopes.append(_envelope(record, "activity", {
            **common,
            "activity_type": item_type,
            "title": record.payload.get("prompt"),
        }))
        self.envelopes.append(_envelope(record, "adaptive_item", {
            **common,
            "activity_id": activity_id,
            "concept_ids": [_target_id(topic, "concept")],
            "item_type": item_type,
            "difficulty": difficulty,
            "quality_status": "approved",
            "expected_answer": {"text": expected},
            "scoring_config": {
                "prompt": record.payload.get("prompt"),
                "options": [
                    {"id": answer.source_id, "label": answer.payload.get("text")}
                    for answer in answers
                ],
                "source_refs": source_refs,
                "representation": item_type,
                "remediation_tags": [],
            },
            "rubric": [{
                "criterion_id": "legacy_expected_answer",
                "description": "Matches the verified legacy expected answer.",
                "points": 1,
            }] if item_type in {"open", "case", "code"} else [],
        }))

    def _standalone_question(self, record: LegacyExportRecord) -> None:
        relationship = self._course_for_topic(record)
        if relationship is None:
            return
        topic, module = relationship
        difficulty = self._difficulty(record)
        if difficulty is None:
            return
        answers = tuple(
            candidate for candidate in self.records
            if candidate.entity == "answers"
            and candidate.payload.get("question_id") == record.source_id
        )
        expected = next(
            (answer.payload.get("text") for answer in answers if answer.payload.get("is_correct") is True),
            "Manual review required",
        )
        common = {
            "course_id": _target_id(module, "course"),
            "course_revision": 1,
            "chapter_id": _target_id(topic, "chapter"),
        }
        self.envelopes.append(_envelope(record, "activity", {
            **common, "activity_type": "open", "title": record.payload.get("text"),
        }))
        self.envelopes.append(_envelope(record, "adaptive_item", {
            **common,
            "activity_id": _target_id(record, "activity"),
            "concept_ids": [_target_id(topic, "concept")],
            "item_type": "open",
            "difficulty": difficulty,
            "quality_status": "approved",
            "expected_answer": {"text": expected},
            "scoring_config": {
                "prompt": record.payload.get("text"),
                "options": [],
                "source_refs": self._source_refs(topic, record),
                "representation": "open",
                "remediation_tags": [],
            },
            "rubric": [{
                "criterion_id": "legacy_expected_answer",
                "description": "Matches the verified legacy expected answer.",
                "points": 1,
            }],
        }))

    def _session(self, record: LegacyExportRecord) -> None:
        quiz = self._get("quizzes", record.payload.get("quiz_id"))
        if quiz is None:
            self._missing(record, "quizzes", record.payload.get("quiz_id"))
            return
        module = self._get("modules", quiz.payload.get("module_id"))
        if module is None and quiz.payload.get("topic_id") is not None:
            topic = self._get("topics", quiz.payload.get("topic_id"))
            module = self._get("modules", topic.payload.get("module_id")) if topic else None
        if module is None:
            self._missing(record, "modules", quiz.payload.get("module_id"))
            return
        self.envelopes.append(_envelope(record, "session", {
            "course_id": _target_id(module, "course"),
            "course_revision": 1,
            "actor_id": str(record.payload.get("user_id")),
            "mode": "exam" if quiz.payload.get("quiz_type") == "exam_preparation" else "training",
            "state": "completed" if record.payload.get("finished_at") else "cancelled",
            "revision": 1,
            "blueprint": {"item_count": quiz.payload.get("question_count")},
            "started_at": record.payload.get("started_at"),
            "completed_at": record.payload.get("finished_at"),
        }))

    def _submission(self, record: LegacyExportRecord) -> None:
        score = self._score(record, "score")
        if score is None:
            return
        attempt = self._get("quiz_attempts", record.payload.get("attempt_id"))
        quiz_question = self._get("quiz_questions", record.payload.get("quiz_question_id"))
        if attempt is None:
            self._missing(record, "quiz_attempts", record.payload.get("attempt_id"))
            return
        if quiz_question is None:
            self._missing(record, "quiz_questions", record.payload.get("quiz_question_id"))
            return
        task = self._get("task_catalog_items", quiz_question.payload.get("task_catalog_item_id"))
        if task is None:
            self._missing(record, "task_catalog_items", quiz_question.payload.get("task_catalog_item_id"))
            return
        answer = record.payload.get("submitted_answer")
        response_id = _target_id(record, "response")
        self.envelopes.append(_envelope(record, "response", {
            "session_id": _target_id(attempt, "session"),
            "item_id": _target_id(task, "adaptive_item"),
            "ordinal": quiz_question.payload.get("position"),
            "answer": answer,
            "answer_hash": hashlib.sha256(_canonical(answer)).hexdigest(),
            "response_revision": 1,
        }))
        needs_review = record.payload.get("needs_review") is True
        self.envelopes.append(_envelope(record, "evaluation", {
            "response_id": response_id,
            "evaluator_type": "deterministic",
            "score": score,
            "confidence": 1.0,
            "accepted": not needs_review,
            "rationale_codes": ["legacy_score_imported"],
            "evaluator_versions": {"legacy": "learning_plattform_v1"},
            "source_refs": self._task_source_refs(task),
        }))

    def _mastery(self, record: LegacyExportRecord) -> None:
        score = self._score(record, "topic_progress_percent")
        if score is None:
            return
        attempt = self._get("quiz_attempts", record.payload.get("attempt_id"))
        quiz = self._get("quizzes", record.payload.get("quiz_id"))
        if attempt is None:
            self._missing(record, "quiz_attempts", record.payload.get("attempt_id"))
            return
        if quiz is None:
            self._missing(record, "quizzes", record.payload.get("quiz_id"))
            return
        topic = self._get("topics", quiz.payload.get("topic_id"))
        if topic is None:
            self._missing(record, "topics", quiz.payload.get("topic_id"))
            return
        submissions = sorted(
            (
                candidate for candidate in self.records
                if candidate.entity == "quiz_answer_submissions"
                and candidate.payload.get("attempt_id") == attempt.source_id
            ),
            key=lambda candidate: candidate.source_id,
        )
        if not submissions:
            self._missing(record, "quiz_answer_submissions", attempt.source_id)
            return
        self.envelopes.append(_envelope(record, "mastery_evidence", {
            "evaluation_id": _target_id(submissions[-1], "evaluation"),
            "session_id": _target_id(attempt, "session"),
            "concept_id": _target_id(topic, "concept"),
            "actor_id": str(record.payload.get("user_id")),
            "score": score,
            "accepted": record.payload.get("needs_review") is not True,
            "weight": 1.0,
        }))

    def _artifact(self, record: LegacyExportRecord) -> None:
        owner_entity = record.payload.get("owner_entity")
        owner_source_id = record.payload.get("owner_source_id")
        owner = self._get(str(owner_entity), owner_source_id)
        if owner is None:
            self.quarantines.append(_issue(
                record,
                severity="quarantine",
                code="orphan_artifact",
                details={
                    "owner_entity": str(owner_entity),
                    "owner_source_id": str(owner_source_id),
                },
            ))
            return
        self.envelopes.append(_envelope(record, "artifact", {
            **record.payload,
            "owner_canonical_id": _target_id(owner, "source_revision"),
        }))

    def _mistake(self, record: LegacyExportRecord) -> None:
        topic = self._get("topics", record.payload.get("topic_id"))
        if topic is None:
            self._missing(record, "topics", record.payload.get("topic_id"))
            return
        common = {
            "actor_id": str(record.payload.get("actor_id")),
            "concept_id": _target_id(topic, "concept"),
            "status": record.payload.get("status", "unresolved"),
        }
        self.envelopes.append(_envelope(record, "misconception", {
            **common,
            "tag": record.payload.get("misconception") or "legacy_mistake",
            "notes": record.payload.get("notes"),
        }))
        self.envelopes.append(_envelope(record, "review_schedule", {
            **common,
            "interval_index": 0,
            "maintenance": False,
            "alternate_representation_required": True,
        }))

    def _difficulty(self, record: LegacyExportRecord) -> float | None:
        value = record.payload.get("difficulty", 1)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 1 <= value <= 5:
            self.quarantines.append(_issue(
                record, severity="quarantine", code="invalid_difficulty"
            ))
            return None
        return float(value) / 5.0

    def _score(self, record: LegacyExportRecord, key: str) -> float | None:
        value = record.payload.get(key)
        if value is None and key == "score":
            value = 100 if record.payload.get("is_correct") is True else 0
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 100:
            self.quarantines.append(_issue(
                record,
                severity="quarantine",
                code="invalid_score",
                details={"field": key},
            ))
            return None
        return float(value) / 100.0

    def _source_refs(
        self,
        topic: LegacyExportRecord,
        question: LegacyExportRecord | None,
    ) -> list[str]:
        documents = sorted(
            (
                candidate for candidate in self.records
                if candidate.entity == "knowledge_documents"
                and candidate.payload.get("topic_id") == topic.source_id
            ),
            key=lambda candidate: candidate.source_id,
        )
        if documents:
            return [
                f"source://{_target_id(document, 'source')}/revisions/1"
                for document in documents
            ]
        suffix = question.source_id if question is not None else topic.source_id
        return [f"legacy-question://{suffix}"]

    def _task_source_refs(self, task: LegacyExportRecord) -> list[str]:
        topic = self._get("topics", task.payload.get("topic_id"))
        question = self._get("questions", task.payload.get("source_question_id"))
        return self._source_refs(topic, question) if topic is not None else []


def transform_legacy_records(
    records: Iterable[LegacyExportRecord],
) -> TransformationResult:
    return _Transformer(records).run()
