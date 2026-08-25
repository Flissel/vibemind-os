from __future__ import annotations

import pytest

from spaces.learning.services.evaluation.deterministic import score_response
from spaces.learning.services.evaluation.schemas import DeterministicItem


@pytest.mark.parametrize(
    ("item_type", "expected", "answer", "config", "score", "code"),
    [
        (
            "single_choice",
            {"choice_id": "b"},
            {"choice_id": "b"},
            {},
            1.0,
            "exact_match",
        ),
        (
            "single_choice",
            {"choice_id": "b"},
            {"choice_id": "a"},
            {},
            0.0,
            "incorrect_choice",
        ),
        (
            "multiple_choice",
            {"choice_ids": ["a", "b", "c"]},
            {"choice_ids": ["a", "b"]},
            {"partial_credit": True},
            2 / 3,
            "partial_match",
        ),
        (
            "multiple_choice",
            {"choice_ids": ["a", "b"]},
            {"choice_ids": ["a", "x"]},
            {"partial_credit": True},
            0.25,
            "partial_match",
        ),
        (
            "matching",
            {"pairs": {"a": "1", "b": "2"}},
            {"pairs": {"a": "1", "b": "9"}},
            {"partial_credit": True},
            0.5,
            "partial_match",
        ),
        (
            "ordering",
            {"order": ["a", "b", "c"]},
            {"order": ["a", "c", "b"]},
            {"partial_credit": True},
            1 / 3,
            "partial_match",
        ),
        (
            "numeric",
            {"value": 100.0},
            {"value": 100.49},
            {"absolute_tolerance": 0.5},
            1.0,
            "within_tolerance",
        ),
        (
            "numeric",
            {"value": 100.0},
            {"value": 101.1},
            {"relative_tolerance": 0.01},
            0.0,
            "outside_tolerance",
        ),
        (
            "fill_in",
            {"answers": ["OpenFang", "Open Fang"]},
            {"text": "  open-fang!  "},
            {"normalize": True},
            1.0,
            "normalized_match",
        ),
    ],
)
def test_supported_formats_score_deterministically(
    item_type, expected, answer, config, score, code
) -> None:
    result = score_response(
        DeterministicItem(
            item_id="item-1",
            item_type=item_type,
            prompt="Solve the task",
            options=[{"id": "a", "label": "A"}],
            expected_answer=expected,
            scoring_config=config,
        ),
        answer,
    )

    assert result.score == pytest.approx(score)
    assert result.confidence == 1.0
    assert code in result.rationale_codes
    assert 0 <= result.score <= 1


@pytest.mark.parametrize(
    ("item_type", "expected", "answer"),
    [
        ("single_choice", {"choice_id": "a"}, {"choice_id": ["a"]}),
        ("multiple_choice", {"choice_ids": ["a"]}, {"choice_ids": ["a", "a"]}),
        ("matching", {"pairs": {"a": "1"}}, {"pairs": ["a", "1"]}),
        ("ordering", {"order": ["a", "b"]}, {"order": ["a", "a"]}),
        ("numeric", {"value": 1}, {"value": "NaN"}),
        ("fill_in", {"answers": ["answer"]}, {"text": 123}),
    ],
)
def test_malformed_answers_fail_closed_without_throwing(
    item_type, expected, answer
) -> None:
    result = score_response(
        DeterministicItem(
            item_id="item-malformed",
            item_type=item_type,
            prompt="Prompt",
            options=[],
            expected_answer=expected,
            scoring_config={"partial_credit": True},
        ),
        answer,
    )

    assert result.score == 0
    assert result.confidence == 1
    assert result.rationale_codes == ["malformed_answer"]


def test_exact_mode_withholds_partial_credit() -> None:
    result = score_response(
        DeterministicItem(
            item_id="item-exact",
            item_type="multiple_choice",
            prompt="Select all",
            options=[],
            expected_answer={"choice_ids": ["a", "b"]},
            scoring_config={"partial_credit": False},
        ),
        {"choice_ids": ["a"]},
    )
    assert result.score == 0
    assert result.rationale_codes == ["incorrect_set"]


def test_student_payload_cannot_expose_expected_answer_or_scoring_config() -> None:
    item = DeterministicItem(
        item_id="item-public",
        item_type="single_choice",
        prompt="Choose",
        options=[{"id": "a", "label": "Safe"}],
        expected_answer={"choice_id": "a"},
        scoring_config={},
    )

    payload = item.student_view().model_dump(mode="json")

    assert payload == {
        "item_id": "item-public",
        "item_type": "single_choice",
        "prompt": "Choose",
        "options": [{"id": "a", "label": "Safe"}],
    }
    assert "expected_answer" not in payload
    assert "scoring_config" not in payload
