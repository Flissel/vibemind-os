from __future__ import annotations

import math
from dataclasses import dataclass, replace

from spaces.learning.services.adaptive_engine.eligibility import (
    CandidateItem,
    SelectionContext,
    eligibility_reasons,
)


_TARGET_SUCCESS = 0.72
_TARGET_BAND = (0.65, 0.80)


@dataclass(frozen=True)
class SelectionExplanation:
    item_id: str
    eligible: bool
    selected: bool
    reason_codes: tuple[str, ...]
    difficulty: float
    predicted_success: float | None
    weakest_concept_id: str | None
    weakest_mastery: float | None
    target_distance: float | None
    overdue_concept_count: int
    misconception_match_count: int
    repetition_penalty: int


@dataclass(frozen=True)
class SelectionResult:
    selected: SelectionExplanation | None
    explanations: tuple[SelectionExplanation, ...]


def predicted_success(mastery: float, difficulty: float) -> float:
    if mastery < 0 or mastery > 1:
        raise ValueError("mastery must be normalized")
    if difficulty < 0 or difficulty > 1:
        raise ValueError("difficulty must be normalized")
    clamped = min(0.99, max(0.01, mastery))
    logit = math.log(clamped / (1 - clamped))
    return 1 / (1 + math.exp(-(logit - 4 * (difficulty - 0.5))))


def select_next_item(
    candidates: tuple[CandidateItem, ...], context: SelectionContext
) -> SelectionResult:
    explanations: list[SelectionExplanation] = []
    ranked: list[tuple[tuple[object, ...], int]] = []
    for candidate in candidates:
        reasons = eligibility_reasons(candidate, context)
        if reasons:
            explanations.append(
                SelectionExplanation(
                    item_id=candidate.item_id,
                    eligible=False,
                    selected=False,
                    reason_codes=reasons,
                    difficulty=candidate.difficulty,
                    predicted_success=None,
                    weakest_concept_id=None,
                    weakest_mastery=None,
                    target_distance=None,
                    overdue_concept_count=0,
                    misconception_match_count=0,
                    repetition_penalty=_repetition_penalty(candidate, context),
                )
            )
            continue
        weakest_concept, weakest_mastery = min(
            (
                (concept_id, context.mastery[concept_id])
                for concept_id in candidate.concept_ids
            ),
            key=lambda item: (item[1], item[0]),
        )
        probability = predicted_success(weakest_mastery, candidate.difficulty)
        distance = abs(probability - _TARGET_SUCCESS)
        overdue_count = sum(
            context.review_due_at.get(concept_id, context.now) < context.now
            for concept_id in candidate.concept_ids
            if concept_id in context.review_due_at
        )
        unresolved = {
            tag
            for concept_id in candidate.concept_ids
            for tag in context.unresolved_misconceptions.get(concept_id, ())
        }
        misconception_count = len(unresolved & set(candidate.remediation_tags))
        repetition = _repetition_penalty(candidate, context)
        explanation = SelectionExplanation(
            item_id=candidate.item_id,
            eligible=True,
            selected=False,
            reason_codes=("eligible",),
            difficulty=candidate.difficulty,
            predicted_success=probability,
            weakest_concept_id=weakest_concept,
            weakest_mastery=weakest_mastery,
            target_distance=distance,
            overdue_concept_count=overdue_count,
            misconception_match_count=misconception_count,
            repetition_penalty=repetition,
        )
        explanations.append(explanation)
        rank = (
            0 if _TARGET_BAND[0] <= probability <= _TARGET_BAND[1] else 1,
            distance,
            -overdue_count,
            -misconception_count,
            repetition,
            candidate.item_id,
        )
        ranked.append((rank, len(explanations) - 1))
    if not ranked:
        return SelectionResult(selected=None, explanations=tuple(explanations))
    _, selected_index = min(ranked, key=lambda item: item[0])
    selected = replace(
        explanations[selected_index],
        selected=True,
        reason_codes=("selected",),
    )
    explanations[selected_index] = selected
    return SelectionResult(selected=selected, explanations=tuple(explanations))


def _repetition_penalty(
    candidate: CandidateItem, context: SelectionContext
) -> int:
    return sum(item_id == candidate.item_id for item_id in context.recent_item_ids)
