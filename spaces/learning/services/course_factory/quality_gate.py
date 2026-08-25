from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

from spaces.learning.services.course_factory.repository import (
    CourseFactoryRepository,
    FactoryJobRecord,
)
from spaces.learning.services.course_factory.roles.schemas import QualityReviewOutput
from spaces.learning.services.course_factory.schemas import CourseDraft
from spaces.learning.services.course_factory.state_machine import FactoryState
from spaces.learning.services.course_factory.verification import (
    SourceSnapshot,
    verify_course_sources,
)


_AI_PASS_THRESHOLD = 0.75


@dataclass(frozen=True)
class QualityIssue:
    code: str
    subject_id: str


@dataclass(frozen=True)
class QualityReport:
    issues: tuple[QualityIssue, ...]

    @property
    def approved(self) -> bool:
        return not self.issues

    @property
    def issue_codes(self) -> list[str]:
        return list(dict.fromkeys(issue.code for issue in self.issues))


@dataclass(frozen=True)
class QualityGateResult:
    job: FactoryJobRecord
    report: QualityReport


class QualityGate:
    def __init__(self, repository: CourseFactoryRepository) -> None:
        self._repository = repository

    def evaluate_and_promote(
        self,
        job_id: str,
        *,
        expected_revision: int,
        draft: CourseDraft,
        sources: tuple[SourceSnapshot, ...],
        ai_review: QualityReviewOutput,
    ) -> QualityGateResult:
        job = self._repository.get(job_id)
        if job.revision != expected_revision:
            raise ValueError("quality gate revision mismatch")
        if job.state is not FactoryState.QUALITY_GATE:
            raise ValueError("quality gate requires quality_gate state")

        issues = [
            QualityIssue(issue.code, issue.subject_id)
            for issue in verify_course_sources(draft, sources).issues
        ]
        if draft.job_id != job.id or draft.attempt_number != job.attempt_number:
            issues.append(QualityIssue("draft_job_mismatch", draft.job_id))
        issues.extend(_deterministic_quality_issues(draft))
        if ai_review.decision != "pass" or ai_review.score < _AI_PASS_THRESHOLD:
            issues.append(QualityIssue("ai_review_not_approved", draft.job_id))

        report = QualityReport(tuple(_deduplicate_issues(issues)))
        if report.approved:
            job = self._repository.advance(
                job_id,
                expected_revision=expected_revision,
                target=FactoryState.REVIEW_READY,
            )
        return QualityGateResult(job=job, report=report)


def _deterministic_quality_issues(draft: CourseDraft) -> list[QualityIssue]:
    issues: list[QualityIssue] = []
    for code, values in (
        ("duplicate_concept_id", [item.concept_id for item in draft.concepts]),
        ("duplicate_chapter_id", [item.chapter_id for item in draft.chapters]),
        ("duplicate_lesson_id", [item.lesson_id for item in draft.lessons]),
        ("duplicate_activity_id", [item.activity_id for item in draft.activities]),
        ("duplicate_citation_id", [item.citation_id for item in draft.citations]),
    ):
        for duplicate in _duplicates(values):
            issues.append(QualityIssue(code, duplicate))
    concept_ids = {concept.concept_id for concept in draft.concepts}
    chapter_ids = {chapter.chapter_id for chapter in draft.chapters}
    lesson_ids = {lesson.lesson_id for lesson in draft.lessons}
    activity_ids = {activity.activity_id for activity in draft.activities}

    for chapter in draft.chapters:
        for lesson_id in chapter.lesson_ids:
            if lesson_id not in lesson_ids:
                issues.append(QualityIssue("chapter_lesson_missing", lesson_id))
        for activity_id in chapter.activity_ids:
            if activity_id not in activity_ids:
                issues.append(QualityIssue("chapter_activity_missing", activity_id))

    normalized_lessons: dict[str, str] = {}
    for lesson in draft.lessons:
        if lesson.chapter_id not in chapter_ids:
            issues.append(QualityIssue("lesson_chapter_missing", lesson.lesson_id))
        normalized = _normalize(lesson.title + " " + lesson.body)
        if normalized in normalized_lessons:
            issues.append(QualityIssue("duplicate_lesson", lesson.lesson_id))
        else:
            normalized_lessons[normalized] = lesson.lesson_id
        for concept_id in lesson.concept_ids:
            if concept_id not in concept_ids:
                issues.append(QualityIssue("lesson_concept_missing", concept_id))

    for activity in draft.activities:
        if activity.chapter_id not in chapter_ids:
            issues.append(QualityIssue("activity_chapter_missing", activity.activity_id))
        for concept_id in activity.concept_ids:
            if concept_id not in concept_ids:
                issues.append(QualityIssue("activity_concept_missing", concept_id))
        if not activity.expected_answer.strip():
            issues.append(QualityIssue("expected_answer_missing", activity.activity_id))
        if activity.type != "quiz" and not activity.rubric:
            issues.append(QualityIssue("rubric_missing", activity.activity_id))

    lesson_coverage = {
        concept_id for lesson in draft.lessons for concept_id in lesson.concept_ids
    }
    activity_coverage = {
        concept_id
        for activity in draft.activities
        for concept_id in activity.concept_ids
    }
    for concept_id in sorted(concept_ids):
        if concept_id not in lesson_coverage:
            issues.append(QualityIssue("concept_missing_lesson", concept_id))
        if concept_id not in activity_coverage:
            issues.append(QualityIssue("concept_missing_activity", concept_id))

    if len(draft.activities) >= 4:
        counts = Counter(activity.type for activity in draft.activities)
        if len(counts) < 2 or max(counts.values()) / len(draft.activities) > 0.75:
            issues.append(QualityIssue("task_type_distribution", draft.job_id))
    return issues


def _normalize(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", value.lower()))


def _duplicates(values: list[str]) -> list[str]:
    counts = Counter(values)
    return sorted(value for value, count in counts.items() if count > 1)


def _deduplicate_issues(issues: list[QualityIssue]) -> list[QualityIssue]:
    seen: set[tuple[str, str]] = set()
    result: list[QualityIssue] = []
    for issue in issues:
        key = (issue.code, issue.subject_id)
        if key not in seen:
            seen.add(key)
            result.append(issue)
    return result
