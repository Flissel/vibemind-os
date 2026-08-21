import json

import pytest

from djset.agents.base import Budget, SchemaViolation
from djset.agents.profiler import build_profile
from tests.fixtures.fake_client import FakeClient

DRAFT = {
    "genre": "liquid dnb",
    "length_minutes": 1.0,
    "bpm": {"target": 174.0, "min": 170.0, "max": 178.0},
    "transition": {"bars_min": 16, "bars_max": 32, "techniques": ["blend"]},
    "harmonic_strictness": "strict",
    "energy_arc": [
        {"position": 0.0, "energy": 0.3},
        {"position": 0.6, "energy": 0.9},
        {"position": 1.0, "energy": 0.6},
    ],
    "track_count": {"needed": 12, "request": 12},
    "style_vocab": ["liquid", "rolling breaks", "warm pads"],
    "structure": {"min_intro_bars": 16, "min_outro_bars": 16},
}


def test_returns_a_valid_profile():
    client = FakeClient([json.dumps(DRAFT)])
    profile = build_profile(client, "liquid dnb", 60.0, Budget(max_tokens=100_000))
    assert profile.bpm.target == 174.0


def test_length_comes_from_the_caller_not_the_model():
    client = FakeClient([json.dumps(DRAFT)])
    profile = build_profile(client, "liquid dnb", 60.0, Budget(max_tokens=100_000))
    assert profile.length_minutes == 60.0


def test_overshoot_is_applied_by_code():
    client = FakeClient([json.dumps(DRAFT)])
    profile = build_profile(client, "liquid dnb", 60.0, Budget(max_tokens=100_000))
    assert profile.track_count.needed == 12
    assert profile.track_count.request == 18


def test_draft_below_the_track_floor_never_parses():
    """Die Untergrenze von drei Tracks steckt im Vertrag: ein Entwurf darunter
    kommt gar nicht erst durch die Validierung, statt still hochgesetzt zu werden."""
    draft = json.loads(json.dumps(DRAFT))
    draft["track_count"] = {"needed": 1, "request": 1}
    client = FakeClient([json.dumps(draft)] * 3)
    with pytest.raises(SchemaViolation):
        build_profile(client, "liquid dnb", 5.0, Budget(max_tokens=100_000))


def test_the_genre_string_reaches_the_prompt():
    client = FakeClient([json.dumps(DRAFT)])
    build_profile(client, "hard techno", 60.0, Budget(max_tokens=100_000))
    assert "hard techno" in client.prompts[0]


def test_invalid_draft_is_retried_then_fails():
    broken = json.loads(json.dumps(DRAFT))
    broken["bpm"] = {"target": 300.0, "min": 170.0, "max": 178.0}
    client = FakeClient([json.dumps(broken)] * 3)
    with pytest.raises(SchemaViolation):
        build_profile(client, "liquid dnb", 60.0, Budget(max_tokens=100_000))
