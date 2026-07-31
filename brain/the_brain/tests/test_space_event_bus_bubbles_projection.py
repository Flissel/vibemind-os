from __future__ import annotations

import pytest

from core.space_event_bus import SpaceEventBus


def _operation_event(**params: object) -> dict[str, object]:
    return {
        "event_id": "bubbles.operation_projection",
        "params": {
            "canonical_space_id": "bubbles",
            "bubble_id": "a4b83e77-9069-4bad-9bb1-c6d5d83a7992",
            "operation_id": "operation-42",
            "lifecycle": "completed",
            "score_snapshot": {"score": 87},
            "evidence_refs": ["evidence:receipt-42"],
            **params,
        },
    }


def test_projects_contract_valid_bubbles_operation_read_only() -> None:
    bus = SpaceEventBus()

    outcome = bus.publish(_operation_event())

    projection = bus.recent(1)[0]
    assert outcome["ok"] is True
    assert projection["event_id"] == "bubbles.operation_projection"
    assert projection["ok"] is True
    assert projection["params"] == {
        "canonical_space_id": "bubbles",
        "bubble_id": "a4b83e77-9069-4bad-9bb1-c6d5d83a7992",
        "operation_id": "operation-42",
        "lifecycle": "completed",
        "score_snapshot": {"score": 87},
        "evidence_refs": ["evidence:receipt-42"],
        "verified": True,
        "verification_reason": "contract_valid",
    }


@pytest.mark.parametrize(
    ("params", "reason"),
    [
        ({"bubble_id": 42}, "invalid_bubble_id"),
        ({"canonical_space_id": "shuttles"}, "foreign_canonical_space_id"),
        ({"operation_id": ""}, "missing_operation_id"),
    ],
)
def test_fails_closed_for_missing_or_foreign_bubbles_identifiers(
    params: dict[str, object], reason: str
) -> None:
    bus = SpaceEventBus()

    outcome = bus.publish(_operation_event(**params))

    projection = bus.recent(1)[0]
    assert outcome["ok"] is False
    assert projection["ok"] is False
    assert projection["params"]["verified"] is False
    assert projection["params"]["verification_reason"] == reason


def test_completed_bubbles_operation_without_evidence_is_unverified() -> None:
    bus = SpaceEventBus()

    outcome = bus.publish(_operation_event(evidence_refs=[]))

    projection = bus.recent(1)[0]
    assert outcome["ok"] is False
    assert projection["ok"] is False
    assert projection["params"]["verified"] is False
    assert projection["params"]["verification_reason"] == "completed_requires_evidence"


def test_keeps_shuttle_events_as_workflow_events() -> None:
    bus = SpaceEventBus()

    outcome = bus.publish(
        {
            "event_id": "shuttle.completed",
            "params": {"bubble_id": "a4b83e77-9069-4bad-9bb1-c6d5d83a7992"},
            "ok": True,
        }
    )

    event = bus.recent(1)[0]
    assert outcome["ok"] is True
    assert event["event_id"] == "shuttle.completed"
    assert event["params"] == {
        "bubble_id": "a4b83e77-9069-4bad-9bb1-c6d5d83a7992"
    }


def test_preserves_existing_bubble_mutation_refresh() -> None:
    bus = SpaceEventBus()

    outcome = bus.publish({"event_id": "bubble.update", "params": {"bubble_id": "db-uuid"}})

    assert outcome["ok"] is True
    assert [event["event_id"] for event in bus.recent(2)] == [
        "bubble.update",
        "ui.refresh_bubbles",
    ]
