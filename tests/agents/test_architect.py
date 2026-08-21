import json

import pytest

from djset.agents.architect import arrange
from djset.agents.base import Budget, StageRulesViolated
from djset.camelot import is_compatible
from djset.contracts import Profile, Shortlist
from tests.fixtures.analyses import make_analysis, many
from tests.fixtures.fake_client import FakeClient


def _budget() -> Budget:
    return Budget(max_tokens=50_000, max_seconds=60.0)


def _answer(order: list[str]) -> str:
    return json.dumps({"order": order, "note": "Fixture-Reihenfolge."})


def _shortlist(ids: list[str]) -> Shortlist:
    return Shortlist(accepted=ids, rejected=[], shortened=False, note="")


def test_the_architect_returns_the_order_it_chose(profile: Profile) -> None:
    tracks = many(6)
    ids = [t.id for t in tracks]
    client = FakeClient([_answer(ids)])

    setlist = arrange(client, _shortlist(ids), tracks, profile, _budget())

    assert setlist.order == ids


def test_a_harmonic_break_is_re_asked_with_the_specific_pair(
    profile: Profile,
) -> None:
    tracks = [
        make_analysis("a", camelot="8A", energy=0.3),
        make_analysis("b", camelot="9B", energy=0.5),
        make_analysis("c", camelot="8B", energy=0.7),
    ]
    ids = ["a", "b", "c"]
    assert not is_compatible("8A", "9B", "strict")
    client = FakeClient([_answer(ids), _answer(["a", "c", "b"])])

    setlist = arrange(client, _shortlist(ids), tracks, profile, _budget())

    assert client.calls == 2
    assert "8A" in client.prompts[1] and "9B" in client.prompts[1]
    assert setlist.order == ["a", "c", "b"]


def test_two_too_similar_tracks_may_not_touch(profile: Profile) -> None:
    """Gleiche Tonart, fast gleiches Tempo, fast gleiche Energie: bequem, aber Stillstand."""
    tracks = [
        make_analysis("a", camelot="8A", bpm=174.0, energy=0.50),
        make_analysis("b", camelot="8A", bpm=174.3, energy=0.52),
        make_analysis("c", camelot="8B", bpm=171.0, energy=0.80),
    ]
    client = FakeClient([_answer(["a", "b", "c"]), _answer(["a", "c", "b"])])

    setlist = arrange(client, _shortlist(["a", "b", "c"]), tracks, profile, _budget())

    assert client.calls == 2
    assert setlist.order == ["a", "c", "b"]


def test_an_id_outside_the_shortlist_is_re_asked(profile: Profile) -> None:
    tracks = many(4)
    ids = [t.id for t in tracks]
    client = FakeClient([_answer(ids[:3] + ["fremd"]), _answer(ids)])

    setlist = arrange(client, _shortlist(ids), tracks, profile, _budget())

    assert client.calls == 2
    assert "fremd" in client.prompts[1]
    assert setlist.order == ids


def test_critic_complaints_reach_the_prompt(profile: Profile) -> None:
    tracks = many(5)
    ids = [t.id for t in tracks]
    client = FakeClient([_answer(ids)])

    arrange(
        client,
        _shortlist(ids),
        tracks,
        profile,
        _budget(),
        complaints=("Die Energiekurve faellt in der Mitte ab.",),
    )

    assert "Energiekurve faellt in der Mitte ab" in client.prompts[0]


def test_a_harmonic_break_after_every_round_raises(profile: Profile) -> None:
    """Ruling R4: der Architect ist das Einzige zwischen einer harmonisch
    gebrochenen Setlist und dem Renderer.

    Ein schemagueltiger, aber harmonisch kaputter Mixplan faellt weiter unten
    niemandem mehr auf - der Critic prueft Tonarten nicht, das ist die Aufgabe
    dieser Stufe.
    """
    tracks = [
        make_analysis("a", camelot="8A", energy=0.3),
        make_analysis("b", camelot="9B", energy=0.5),
        make_analysis("c", camelot="8B", energy=0.7),
    ]
    ids = ["a", "b", "c"]
    assert not is_compatible("8A", "9B", "strict")
    bad = _answer(ids)
    client = FakeClient([bad, bad, bad])

    with pytest.raises(StageRulesViolated) as caught:
        arrange(client, _shortlist(ids), tracks, profile, _budget())

    assert client.calls == 3
    assert "8A" in str(caught.value) and "9B" in str(caught.value)


def test_a_missing_track_after_every_round_raises(profile: Profile) -> None:
    """Eine Reihenfolge, die einen Track der Shortlist unterschlaegt, kuerzt
    das Set stillschweigend - und niemand haette dafuer eine Begruendung."""
    tracks = many(5)
    ids = [t.id for t in tracks]
    bad = _answer(ids[:4])
    client = FakeClient([bad, bad, bad])

    with pytest.raises(StageRulesViolated) as caught:
        arrange(client, _shortlist(ids), tracks, profile, _budget())

    assert client.calls == 3
    assert ids[4] in str(caught.value)
