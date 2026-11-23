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


# --- A1: ohne vertraegliche Reihenfolge zaehlt die Mindestzahl an Bruechen --


def _insel_set() -> list:
    """Drei Tracks, von denen einer auf keiner Seite passt: 2A ist weder zu
    8A noch zu 8B benachbart. Jede Reihenfolge hat mindestens einen Bruch."""
    return [
        make_analysis("a", camelot="8A", energy=0.3),
        make_analysis("b", camelot="8B", energy=0.5),
        make_analysis("c", camelot="2A", energy=0.7),
    ]


def test_ohne_vertraegliche_reihenfolge_reicht_das_minimum_an_bruechen(
    profile: Profile,
) -> None:
    """Vorher: drei Versuche, dann StageRulesViolated - bei vorhandenen Songs
    fast immer, weil es fuer sie selten eine bruchfreie Reihenfolge gibt."""
    ids = ["a", "b", "c"]
    client = FakeClient([_answer(ids)])

    setlist = arrange(client, _shortlist(ids), _insel_set(), profile, _budget())

    assert client.calls == 1
    assert setlist.order == ids
    # Der Architect bekommt die Obergrenze genannt.
    assert "hoechstens 1" in client.prompts[0]
    # Und der Bruch steht in der Setlist-Notiz, mit beiden Songs und Tonarten.
    assert "Fixture-Reihenfolge." in setlist.note
    assert "b (8B)" in setlist.note and "c (2A)" in setlist.note


def test_mehr_brueche_als_noetig_werden_neu_gefragt(profile: Profile) -> None:
    """a-c-b hat zwei Brueche (8A/2A und 2A/8B), noetig ist nur einer."""
    client = FakeClient([_answer(["a", "c", "b"]), _answer(["c", "a", "b"])])

    setlist = arrange(
        client, _shortlist(["a", "b", "c"]), _insel_set(), profile, _budget()
    )

    assert client.calls == 2
    assert "hoechstens 1" in client.prompts[1]
    assert setlist.order == ["c", "a", "b"]
    assert "c (2A)" in setlist.note and "a (8A)" in setlist.note


def test_mit_vertraeglicher_reihenfolge_bleibt_der_prompt_ohne_bruchregel(
    profile: Profile,
) -> None:
    tracks = many(4)
    ids = [t.id for t in tracks]
    client = FakeClient([_answer(ids)])

    setlist = arrange(client, _shortlist(ids), tracks, profile, _budget())

    assert "Tonart-Bruch" not in client.prompts[0]
    assert setlist.note == "Fixture-Reihenfolge."
