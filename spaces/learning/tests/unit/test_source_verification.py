from __future__ import annotations

from uuid import uuid4

from spaces.learning.services.course_factory.schemas import (
    ActivityDraft,
    ChapterDraft,
    Citation,
    Claim,
    ConceptDraft,
    CourseDraft,
    LessonDraft,
    RubricCriterion,
    SourceProvenanceRef,
)
from spaces.learning.services.course_factory.verification import (
    ChunkEvidence,
    SourceSnapshot,
    verify_course_sources,
)


def _ids() -> dict[str, str]:
    return {name: str(uuid4()) for name in ("job", "source", "chunk", "citation")}


def _draft(ids: dict[str, str]) -> CourseDraft:
    return CourseDraft(
        schema_version="course-draft-v1",
        job_id=ids["job"],
        attempt_number=1,
        title="Grounded AI Operations",
        outcomes=["Operate an authorized AI workflow"],
        source_provenance=[
            SourceProvenanceRef(
                source_id=ids["source"], revision=2, content_hash="a" * 64
            )
        ],
        citations=[
            Citation(
                citation_id=ids["citation"],
                source_id=ids["source"],
                source_revision=2,
                chunk_id=ids["chunk"],
                content_hash="b" * 64,
                locator={"page": 4, "heading": "Authority"},
            )
        ],
        concepts=[ConceptDraft(concept_id="authority", title="Authority")],
        chapters=[
            ChapterDraft(
                chapter_id="foundations",
                title="Foundations",
                lesson_ids=["lesson-authority"],
                activity_ids=["activity-authority"],
            )
        ],
        lessons=[
            LessonDraft(
                lesson_id="lesson-authority",
                chapter_id="foundations",
                title="Authority boundaries",
                body="Provider execution stays behind the authority boundary.",
                concept_ids=["authority"],
                claims=[
                    Claim(
                        claim_id="claim-authority",
                        text="Provider execution stays behind OpenFang.",
                        citation_ids=[ids["citation"]],
                    )
                ],
            )
        ],
        activities=[
            ActivityDraft(
                activity_id="activity-authority",
                chapter_id="foundations",
                type="case",
                concept_ids=["authority"],
                prompt="Choose the authorized execution path.",
                expected_answer="Route the request through OpenFang.",
                citation_ids=[ids["citation"]],
                rubric=[
                    RubricCriterion(
                        criterion_id="routing",
                        description="Uses the authority chain",
                        points=2,
                    )
                ],
            )
        ],
    )


def _catalog(ids: dict[str, str]) -> tuple[SourceSnapshot, ...]:
    return (
        SourceSnapshot(
            source_id=ids["source"],
            revision=2,
            content_hash="a" * 64,
            chunks=(
                ChunkEvidence(
                    chunk_id=ids["chunk"],
                    content_hash="b" * 64,
                    locator={"page": 4, "heading": "Authority"},
                ),
            ),
        ),
    )


def test_all_claims_and_expected_answers_resolve_to_current_source_locators() -> None:
    ids = _ids()

    report = verify_course_sources(_draft(ids), _catalog(ids))

    assert report.approved is True
    assert report.unsupported_claim_ids == []
    assert report.issue_codes == []


def test_unsupported_claim_and_expected_answer_without_citations_are_visible() -> None:
    ids = _ids()
    draft = _draft(ids)
    draft.lessons[0].claims[0].citation_ids = []
    draft.activities[0].citation_ids = []

    report = verify_course_sources(draft, _catalog(ids))

    assert report.approved is False
    assert report.unsupported_claim_ids == ["claim-authority"]
    assert set(report.issue_codes) == {
        "claim_missing_citation",
        "expected_answer_missing_citation",
    }


def test_source_revision_hash_and_locator_mismatches_fail_closed() -> None:
    ids = _ids()
    draft = _draft(ids)
    draft.citations[0].source_revision = 1
    draft.citations[0].content_hash = "c" * 64
    draft.citations[0].locator = {"page": 99}

    report = verify_course_sources(draft, _catalog(ids))

    assert report.approved is False
    assert "source_revision_mismatch" in report.issue_codes
    assert "citation_content_hash_mismatch" in report.issue_codes
    assert "citation_locator_mismatch" in report.issue_codes
    assert report.unsupported_claim_ids == ["claim-authority"]


def test_stale_draft_provenance_is_rejected_even_when_citation_looks_current() -> None:
    ids = _ids()
    draft = _draft(ids)
    draft.source_provenance[0].content_hash = "d" * 64

    report = verify_course_sources(draft, _catalog(ids))

    assert report.approved is False
    assert report.issue_codes == ["source_provenance_mismatch"]


def test_unknown_citation_reference_marks_claim_unsupported() -> None:
    ids = _ids()
    draft = _draft(ids)
    draft.lessons[0].claims[0].citation_ids = [str(uuid4())]

    report = verify_course_sources(draft, _catalog(ids))

    assert report.approved is False
    assert report.unsupported_claim_ids == ["claim-authority"]
    assert report.issue_codes == ["claim_citation_unresolved"]
