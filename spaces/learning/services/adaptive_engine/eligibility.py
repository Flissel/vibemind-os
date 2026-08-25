from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from collections.abc import Mapping


@dataclass(frozen=True)
class CandidateItem:
    item_id: str
    difficulty: float
    concept_ids: tuple[str, ...]
    quality_status: str
    remediation_tags: tuple[str, ...]
    representation: str

    def __post_init__(self) -> None:
        if not self.item_id or len(self.item_id) > 200:
            raise ValueError("candidate item ID is invalid")
        if self.difficulty < 0 or self.difficulty > 1:
            raise ValueError("candidate difficulty must be normalized")
        if not self.concept_ids or len(self.concept_ids) > 200:
            raise ValueError("candidate concepts are invalid")
        if len(self.concept_ids) != len(set(self.concept_ids)):
            raise ValueError("candidate concepts must be unique")
        if not self.representation or len(self.representation) > 100:
            raise ValueError("candidate representation is invalid")


@dataclass(frozen=True)
class SelectionContext:
    mastery: Mapping[str, float]
    review_due_at: Mapping[str, datetime]
    unresolved_misconceptions: Mapping[str, tuple[str, ...]]
    recent_item_ids: tuple[str, ...]
    alternate_representation_concepts: frozenset[str]
    recent_representations: Mapping[str, str]
    now: datetime

    def __post_init__(self) -> None:
        if self.now.tzinfo is None or self.now.utcoffset() is None:
            raise ValueError("selection clock must be timezone-aware")
        if any(value < 0 or value > 1 for value in self.mastery.values()):
            raise ValueError("selection mastery must be normalized")
        if len(self.recent_item_ids) > 100:
            raise ValueError("selection history is too large")
        for due_at in self.review_due_at.values():
            if due_at.tzinfo is None or due_at.utcoffset() is None:
                raise ValueError("review due dates must be timezone-aware")
        object.__setattr__(self, "mastery", MappingProxyType(dict(self.mastery)))
        object.__setattr__(
            self, "review_due_at", MappingProxyType(dict(self.review_due_at))
        )
        object.__setattr__(
            self,
            "unresolved_misconceptions",
            MappingProxyType(dict(self.unresolved_misconceptions)),
        )
        object.__setattr__(
            self,
            "recent_representations",
            MappingProxyType(dict(self.recent_representations)),
        )


def eligibility_reasons(
    candidate: CandidateItem, context: SelectionContext
) -> tuple[str, ...]:
    reasons: list[str] = []
    if candidate.quality_status != "approved":
        reasons.append("quality_not_approved")
    if any(concept_id not in context.mastery for concept_id in candidate.concept_ids):
        reasons.append("mastery_missing")
    alternate_concepts = (
        set(candidate.concept_ids) & context.alternate_representation_concepts
    )
    if alternate_concepts and all(
        context.recent_representations.get(concept_id) == candidate.representation
        for concept_id in alternate_concepts
    ):
        reasons.append("alternate_representation_required")
    return tuple(reasons)
