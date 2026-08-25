from __future__ import annotations

import math
import unicodedata
from collections.abc import Mapping

from spaces.learning.services.evaluation.schemas import (
    DeterministicItem,
    ScoringResult,
)


def score_response(
    item: DeterministicItem, answer: Mapping[str, object] | object
) -> ScoringResult:
    if not isinstance(answer, Mapping):
        return _result(0, "malformed_answer")
    scorer = {
        "single_choice": _score_single_choice,
        "multiple_choice": _score_multiple_choice,
        "matching": _score_matching,
        "ordering": _score_ordering,
        "numeric": _score_numeric,
        "fill_in": _score_fill_in,
    }[item.item_type]
    try:
        score, code = scorer(item.expected_answer, answer, item.scoring_config)
    except (KeyError, TypeError, ValueError):
        return _result(0, "malformed_answer")
    return _result(score, code)


def _score_single_choice(expected, answer, config) -> tuple[float, str]:
    del config
    expected_id = _token(expected.get("choice_id"))
    answer_id = _token(answer.get("choice_id"))
    return (1.0, "exact_match") if answer_id == expected_id else (
        0.0,
        "incorrect_choice",
    )


def _score_multiple_choice(expected, answer, config) -> tuple[float, str]:
    expected_ids = _unique_tokens(expected.get("choice_ids"))
    answer_ids = _unique_tokens(answer.get("choice_ids"))
    if not expected_ids:
        raise ValueError("empty expected choices")
    if answer_ids == expected_ids:
        return 1.0, "exact_match"
    if not _partial_credit(config):
        return 0.0, "incorrect_set"
    correct = len(answer_ids & expected_ids)
    recall = correct / len(expected_ids)
    precision = correct / len(answer_ids) if answer_ids else 0.0
    return recall * precision, "partial_match"


def _score_matching(expected, answer, config) -> tuple[float, str]:
    expected_pairs = _pairs(expected.get("pairs"))
    answer_pairs = _pairs(answer.get("pairs"))
    if not expected_pairs or set(answer_pairs) != set(expected_pairs):
        raise ValueError("matching keys differ")
    correct = sum(
        answer_pairs[key] == expected_value
        for key, expected_value in expected_pairs.items()
    )
    if correct == len(expected_pairs):
        return 1.0, "exact_match"
    if not _partial_credit(config):
        return 0.0, "incorrect_matching"
    return correct / len(expected_pairs), "partial_match"


def _score_ordering(expected, answer, config) -> tuple[float, str]:
    expected_order = _ordered_tokens(expected.get("order"))
    answer_order = _ordered_tokens(answer.get("order"))
    if set(answer_order) != set(expected_order) or len(answer_order) != len(
        expected_order
    ):
        raise ValueError("ordering members differ")
    correct = sum(left == right for left, right in zip(expected_order, answer_order))
    if correct == len(expected_order):
        return 1.0, "exact_match"
    if not _partial_credit(config):
        return 0.0, "incorrect_order"
    return correct / len(expected_order), "partial_match"


def _score_numeric(expected, answer, config) -> tuple[float, str]:
    expected_value = _finite_number(expected.get("value"))
    answer_value = _finite_number(answer.get("value"))
    absolute = _non_negative_number(config.get("absolute_tolerance", 0.0))
    relative = _non_negative_number(config.get("relative_tolerance", 0.0))
    tolerance = max(absolute, abs(expected_value) * relative)
    if abs(answer_value - expected_value) <= tolerance:
        return 1.0, "within_tolerance"
    return 0.0, "outside_tolerance"


def _score_fill_in(expected, answer, config) -> tuple[float, str]:
    expected_answers = expected.get("answers")
    text = answer.get("text")
    if (
        not isinstance(expected_answers, list)
        or not expected_answers
        or any(not isinstance(value, str) or not value for value in expected_answers)
        or not isinstance(text, str)
    ):
        raise ValueError("fill-in values are malformed")
    if config.get("normalize", True) is not True:
        return (
            (1.0, "exact_match")
            if text in expected_answers
            else (0.0, "incorrect_text")
        )
    normalized = _normalize(text)
    accepted = {_normalize(value) for value in expected_answers}
    return (
        (1.0, "normalized_match")
        if normalized and normalized in accepted
        else (0.0, "incorrect_text")
    )


def _token(value: object) -> str:
    if not isinstance(value, str) or not value or len(value) > 200:
        raise ValueError("invalid token")
    return value


def _unique_tokens(value: object) -> set[str]:
    if not isinstance(value, list) or len(value) > 500:
        raise ValueError("invalid token list")
    tokens = [_token(item) for item in value]
    if len(tokens) != len(set(tokens)):
        raise ValueError("duplicate tokens")
    return set(tokens)


def _ordered_tokens(value: object) -> list[str]:
    if not isinstance(value, list) or not value or len(value) > 500:
        raise ValueError("invalid ordered tokens")
    tokens = [_token(item) for item in value]
    if len(tokens) != len(set(tokens)):
        raise ValueError("duplicate ordered tokens")
    return tokens


def _pairs(value: object) -> dict[str, str]:
    if not isinstance(value, dict) or len(value) > 500:
        raise ValueError("invalid pairs")
    return {_token(key): _token(nested) for key, nested in value.items()}


def _finite_number(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError("invalid number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("invalid number")
    return result


def _non_negative_number(value: object) -> float:
    result = _finite_number(value)
    if result < 0:
        raise ValueError("negative tolerance")
    return result


def _partial_credit(config: Mapping[str, object]) -> bool:
    value = config.get("partial_credit", False)
    if not isinstance(value, bool):
        raise ValueError("partial_credit must be boolean")
    return value


def _normalize(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return "".join(character for character in normalized if character.isalnum())


def _result(score: float, code: str) -> ScoringResult:
    return ScoringResult(
        score=min(1.0, max(0.0, score)),
        confidence=1.0,
        rationale_codes=[code],
    )
