from __future__ import annotations

import pytest

from spaces.learning.services.adaptive_engine.mastery import (
    MasteryState,
    calculate_mastery_update,
)


def test_neutral_beta_prior_has_half_mastery() -> None:
    state = MasteryState()
    assert state.alpha == 2
    assert state.beta == 2
    assert state.posterior_mean == 0.5


def test_accepted_update_uses_confidence_divided_by_concept_count() -> None:
    update = calculate_mastery_update(
        MasteryState(),
        score=0.8,
        confidence=0.9,
        concept_count=3,
        accepted=True,
    )

    assert update is not None
    assert update.weight == pytest.approx(0.3)
    assert update.before == MasteryState(alpha=2, beta=2)
    assert update.after.alpha == pytest.approx(2.24)
    assert update.after.beta == pytest.approx(2.06)
    assert update.after.posterior_mean == pytest.approx(2.24 / 4.3)


@pytest.mark.parametrize(
    ("confidence", "accepted"), [(0.649, True), (1.0, False)]
)
def test_low_confidence_or_unaccepted_evaluation_has_no_update(
    confidence, accepted
) -> None:
    assert (
        calculate_mastery_update(
            MasteryState(),
            score=1,
            confidence=confidence,
            concept_count=1,
            accepted=accepted,
        )
        is None
    )


def test_mastery_inputs_are_bounded() -> None:
    with pytest.raises(ValueError, match="score"):
        calculate_mastery_update(
            MasteryState(),
            score=1.1,
            confidence=1,
            concept_count=1,
            accepted=True,
        )
    with pytest.raises(ValueError, match="concept_count"):
        calculate_mastery_update(
            MasteryState(),
            score=1,
            confidence=1,
            concept_count=0,
            accepted=True,
        )
