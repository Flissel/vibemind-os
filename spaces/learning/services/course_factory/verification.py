from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from spaces.learning.services.course_factory.schemas import CourseDraft


Locator = Mapping[str, str | int]


@dataclass(frozen=True)
class ChunkEvidence:
    chunk_id: str
    content_hash: str
    locator: Locator


@dataclass(frozen=True)
class SourceSnapshot:
    source_id: str
    revision: int
    content_hash: str
    chunks: tuple[ChunkEvidence, ...]


@dataclass(frozen=True)
class VerificationIssue:
    code: str
    subject_id: str


@dataclass(frozen=True)
class SourceVerificationReport:
    issues: tuple[VerificationIssue, ...]
    unsupported_claim_ids: list[str]

    @property
    def approved(self) -> bool:
        return not self.issues

    @property
    def issue_codes(self) -> list[str]:
        return list(dict.fromkeys(issue.code for issue in self.issues))


def verify_course_sources(
    draft: CourseDraft, sources: tuple[SourceSnapshot, ...]
) -> SourceVerificationReport:
    issues: list[VerificationIssue] = []
    source_by_id = {source.source_id: source for source in sources}

    for provenance in draft.source_provenance:
        current = source_by_id.get(provenance.source_id)
        if current is None or (
            provenance.revision,
            provenance.content_hash,
        ) != (current.revision, current.content_hash):
            issues.append(
                VerificationIssue("source_provenance_mismatch", provenance.source_id)
            )

    citation_validity: dict[str, bool] = {}
    for citation in draft.citations:
        current = source_by_id.get(citation.source_id)
        valid = current is not None
        if current is None:
            issues.append(VerificationIssue("source_missing", citation.citation_id))
        else:
            if citation.source_revision != current.revision:
                issues.append(
                    VerificationIssue(
                        "source_revision_mismatch", citation.citation_id
                    )
                )
                valid = False
            chunk = next(
                (
                    item
                    for item in current.chunks
                    if item.chunk_id == citation.chunk_id
                ),
                None,
            )
            if chunk is None:
                issues.append(VerificationIssue("citation_chunk_missing", citation.citation_id))
                valid = False
            else:
                if citation.content_hash != chunk.content_hash:
                    issues.append(
                        VerificationIssue(
                            "citation_content_hash_mismatch", citation.citation_id
                        )
                    )
                    valid = False
                if dict(citation.locator) != dict(chunk.locator):
                    issues.append(
                        VerificationIssue(
                            "citation_locator_mismatch", citation.citation_id
                        )
                    )
                    valid = False
        citation_validity[citation.citation_id] = valid

    unsupported: list[str] = []
    for lesson in draft.lessons:
        for claim in lesson.claims:
            if not claim.citation_ids:
                issues.append(VerificationIssue("claim_missing_citation", claim.claim_id))
                unsupported.append(claim.claim_id)
            elif not all(citation_validity.get(item, False) for item in claim.citation_ids):
                issues.append(
                    VerificationIssue("claim_citation_unresolved", claim.claim_id)
                )
                unsupported.append(claim.claim_id)

    for activity in draft.activities:
        if not activity.citation_ids:
            issues.append(
                VerificationIssue(
                    "expected_answer_missing_citation", activity.activity_id
                )
            )
        elif not all(
            citation_validity.get(item, False) for item in activity.citation_ids
        ):
            issues.append(
                VerificationIssue(
                    "expected_answer_unsupported", activity.activity_id
                )
            )

    return SourceVerificationReport(
        issues=tuple(_deduplicate_issues(issues)),
        unsupported_claim_ids=list(dict.fromkeys(unsupported)),
    )


def _deduplicate_issues(
    issues: list[VerificationIssue],
) -> list[VerificationIssue]:
    seen: set[tuple[str, str]] = set()
    result: list[VerificationIssue] = []
    for issue in issues:
        key = (issue.code, issue.subject_id)
        if key not in seen:
            seen.add(key)
            result.append(issue)
    return result
