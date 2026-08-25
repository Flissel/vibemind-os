from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from spaces.learning.services.adaptive_engine.eligibility import (
    CandidateItem,
    SelectionContext,
)
from spaces.learning.services.adaptive_engine.selector import (
    predicted_success,
    select_next_item,
)


NOW = datetime(2026, 8, 25, 12, tzinfo=timezone.utc)


def _candidate(
    item_id: str,
    difficulty: float,
    *,
    quality: str = "approved",
    concepts: tuple[str, ...] = ("concept-a",),
    remediation_tags: tuple[str, ...] = (),
    representation: str = "quiz",
) -> CandidateItem:
    return CandidateItem(
        item_id=item_id,
        difficulty=difficulty,
        concept_ids=concepts,
        quality_status=quality,
        remediation_tags=remediation_tags,
        representation=representation,
    )


def _context(**updates) -> SelectionContext:
    values = {
        "mastery": {"concept-a": 0.5},
        "review_due_at": {},
        "unresolved_misconceptions": {},
        "recent_item_ids": (),
        "alternate_representation_concepts": frozenset(),
        "recent_representations": {},
        "now": NOW,
    }
    values.update(updates)
    return SelectionContext(**values)


def test_prediction_uses_exact_formula_and_mastery_clamp() -> None:
    assert predicted_success(0.5, 0.5) == pytest.approx(0.5)
    assert predicted_success(0, 0.5) == pytest.approx(0.01)
    assert predicted_success(1, 0.5) == pytest.approx(0.99)
    assert predicted_success(0.8, 0.6) == pytest.approx(0.728355368, abs=1e-9)


def test_selection_targets_success_band_and_is_deterministic() -> None:
    candidates = (
        _candidate("too-easy", 0.0),
        _candidate("target-b", 0.26),
        _candidate("target-a", 0.26),
        _candidate("too-hard", 0.9),
    )

    result = select_next_item(candidates, _context())

    assert result.selected is not None
    assert result.selected.item_id == "target-a"
    assert 0.65 <= result.selected.predicted_success <= 0.80
    assert len(result.explanations) == len(candidates)
    assert {item.item_id for item in result.explanations} == {
        item.item_id for item in candidates
    }


def test_weakest_tagged_concept_controls_item_prediction() -> None:
    result = select_next_item(
        (_candidate("multi", 0.4, concepts=("strong", "weak")),),
        _context(mastery={"strong": 0.9, "weak": 0.2}),
    )
    assert result.selected is not None
    assert result.selected.weakest_concept_id == "weak"
    assert result.selected.weakest_mastery == 0.2


def test_quality_is_excluded_and_recent_item_is_penalized() -> None:
    result = select_next_item(
        (
            _candidate("rejected", 0.26, quality="review"),
            _candidate("recent", 0.26),
            _candidate("fresh", 0.26),
        ),
        _context(recent_item_ids=("recent",)),
    )
    assert result.selected is not None
    assert result.selected.item_id == "fresh"
    rejected = next(item for item in result.explanations if item.item_id == "rejected")
    assert rejected.eligible is False
    assert "quality_not_approved" in rejected.reason_codes


def test_immediate_repeat_never_beats_an_available_fresh_item() -> None:
    result = select_next_item(
        (
            _candidate("recent-perfect", 0.26),
            _candidate("fresh-harder", 0.9),
        ),
        _context(recent_item_ids=("recent-perfect",)),
    )

    assert result.selected is not None
    assert result.selected.item_id == "fresh-harder"


def test_overdue_review_and_misconception_break_equal_quality_ties() -> None:
    overdue = select_next_item(
        (
            _candidate("normal", 0.26, concepts=("concept-a",)),
            _candidate("overdue", 0.26, concepts=("concept-b",)),
        ),
        _context(
            mastery={"concept-a": 0.5, "concept-b": 0.5},
            review_due_at={"concept-b": NOW - timedelta(days=1)},
        ),
    )
    assert overdue.selected is not None
    assert overdue.selected.item_id == "overdue"

    remediation = select_next_item(
        (
            _candidate("normal", 0.26),
            _candidate(
                "remediation", 0.26, remediation_tags=("authority_bypass",)
            ),
        ),
        _context(
            unresolved_misconceptions={"concept-a": ("authority_bypass",)}
        ),
    )
    assert remediation.selected is not None
    assert remediation.selected.item_id == "remediation"


def test_failure_requires_an_alternate_representation() -> None:
    result = select_next_item(
        (
            _candidate("same", 0.26, representation="quiz"),
            _candidate("alternate", 0.26, representation="worked_example"),
        ),
        _context(
            alternate_representation_concepts=frozenset({"concept-a"}),
            recent_representations={"concept-a": "quiz"},
        ),
    )
    assert result.selected is not None
    assert result.selected.item_id == "alternate"


def test_stronger_mastery_selects_harder_items_over_trajectory() -> None:
    candidates = tuple(
        _candidate(f"difficulty-{index:02d}", index / 20)
        for index in range(1, 20)
    )
    selected_difficulties = []
    for step in range(30):
        mastery = 0.2 + (0.7 * step / 29)
        result = select_next_item(candidates, _context(mastery={"concept-a": mastery}))
        assert result.selected is not None
        selected_difficulties.append(result.selected.difficulty)

    assert selected_difficulties[-1] > selected_difficulties[0]
    assert all(
        later >= earlier
        for earlier, later in zip(selected_difficulties, selected_difficulties[1:])
    )
