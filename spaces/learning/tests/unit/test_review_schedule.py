from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from spaces.learning.services.adaptive_engine.review_schedule import (
    ReviewState,
    schedule_review,
)


NOW = datetime(2026, 8, 25, 12, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    ("current_index", "days", "next_index", "maintenance"),
    [
        (None, 1, 0, False),
        (0, 3, 1, False),
        (1, 7, 2, False),
        (2, 14, 3, False),
        (3, 14, 4, True),
        (4, 14, 4, True),
    ],
)
def test_success_advances_exact_review_intervals(
    current_index, days, next_index, maintenance
) -> None:
    current = (
        None
        if current_index is None
        else ReviewState(
            interval_index=current_index,
            due_at=NOW,
            maintenance=current_index == 4,
        )
    )

    plan = schedule_review(current, successful=True, now=NOW)

    assert plan.interval_index == next_index
    assert plan.due_at == NOW + timedelta(days=days)
    assert plan.maintenance is maintenance
    assert plan.alternate_representation_required is False


def test_failure_resets_schedule_and_requires_alternate_representation() -> None:
    plan = schedule_review(
        ReviewState(interval_index=3, due_at=NOW, maintenance=False),
        successful=False,
        now=NOW,
    )

    assert plan.interval_index == 0
    assert plan.due_at == NOW + timedelta(days=1)
    assert plan.maintenance is False
    assert plan.alternate_representation_required is True


def test_review_clock_must_be_timezone_aware() -> None:
    with pytest.raises(ValueError, match="timezone"):
        schedule_review(None, successful=True, now=datetime(2026, 8, 25, 12))
