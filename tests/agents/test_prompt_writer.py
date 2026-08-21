import json

import pytest

from djset.agents.base import Budget, SchemaViolation
from djset.agents.prompt_writer import render_markdown, write_prompts
from djset.contracts import (
    BpmRange, EnergyPoint, Profile, StructureRules, TrackCount, TransitionRules,
)
from tests.fixtures.fake_client import FakeClient


def _profile(request: int = 4) -> Profile:
    return Profile(
        genre="liquid dnb",
        length_minutes=20.0,
        bpm=BpmRange(target=174.0, min=170.0, max=178.0),
        transition=TransitionRules(bars_min=16, bars_max=32, techniques=["blend"]),
        harmonic_strictness="strict",
        energy_arc=[EnergyPoint(position=0.0, energy=0.3),
                    EnergyPoint(position=1.0, energy=0.9)],
        track_count=TrackCount(needed=3, request=request),
        style_vocab=["liquid", "rolling breaks"],
        structure=StructureRules(min_intro_bars=16, min_outro_bars=16),
    )


def _prompt_set(count: int, camelots: list[str] | None = None) -> str:
    keys = camelots or ["8A", "9A", "10A", "8B"]
    return json.dumps({
        "prompts": [
            {
                "index": i + 1,
                "title": f"Track {i + 1}",
                "style": "liquid dnb, rolling breaks",
                "target_bpm": 174,
                "target_camelot": keys[i % len(keys)],
                "notes": "warm, luftig",
            }
            for i in range(count)
        ]
    })


def test_returns_exactly_the_requested_number():
    client = FakeClient([_prompt_set(4)])
    result = write_prompts(client, _profile(request=4), Budget(max_tokens=100_000, max_seconds=60.0))
    assert len(result.prompts) == 4


def test_wrong_count_is_retried_then_fails():
    client = FakeClient([_prompt_set(2)] * 3)
    with pytest.raises(SchemaViolation):
        write_prompts(client, _profile(request=4), Budget(max_tokens=100_000, max_seconds=60.0))


def test_too_few_distinct_keys_is_rejected():
    same = ["8A", "8A", "8A", "8A"]
    client = FakeClient([_prompt_set(4, same)] * 3)
    with pytest.raises(SchemaViolation):
        write_prompts(client, _profile(request=4), Budget(max_tokens=100_000, max_seconds=60.0))


def test_markdown_lists_every_prompt():
    client = FakeClient([_prompt_set(4)])
    result = write_prompts(client, _profile(request=4), Budget(max_tokens=100_000, max_seconds=60.0))
    text = render_markdown(_profile(request=4), result)
    assert text.count("## Track") == 4
    assert "174" in text
    assert "liquid dnb" in text


def test_a_sentence_before_the_json_gets_a_second_chance():
    """Fund 6: die Schemaschleife hatte hier keinen einzigen Versuch frei.

    write_prompts rief run_agent mit retries=0 - ein Modell, das dem JSON
    einen hoeflichen Satz voranstellt, brachte damit das einzige
    Nutzerkommando beim ersten Lauf zu Fall. Der Spec verlangt fuer eine
    Schemaverletzung ausdruecklich "Retry mit der Fehlermeldung".
    """
    client = FakeClient([
        "Klar, hier sind die Prompts:\n" + _prompt_set(4),
        _prompt_set(4),
    ])

    result = write_prompts(
        client, _profile(request=4), Budget(max_tokens=100_000, max_seconds=60.0)
    )

    assert len(result.prompts) == 4
    assert client.calls == 2
    # Der zweite Anlauf traegt die Fehlermeldung, nicht nur den alten Prompt.
    assert "ungueltig" in client.prompts[1]
