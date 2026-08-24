from __future__ import annotations

from core.space_contract import load_space_contract, registry_health


def test_learning_registry_owns_the_complete_event_namespace() -> None:
    contract = load_space_contract()
    learning_events = {
        event for event, space in contract.event_space_map.items() if space == "learning"
    }

    assert learning_events
    assert all(event.startswith("learning.") for event in learning_events)
    assert "learning.status" in learning_events
    assert "learning.course.publish" in learning_events
    assert "learning.canvas.review" in learning_events


def test_learning_is_present_in_structural_registry_health() -> None:
    contract = load_space_contract()
    health = registry_health(contract, capabilities=[])

    assert "learning" in contract.space_ids
    assert not any(
        issue.get("kind") == "navigator_missing_spaces"
        and "learning" in issue.get("spaces", [])
        for issue in health["issues"]
    )
