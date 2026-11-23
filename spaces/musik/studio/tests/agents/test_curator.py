import json

import pytest

from djset.agents.base import Budget, StageRulesViolated
from djset.agents.curator import NotEnoughTracks, curate
from djset.contracts import MIN_TRACKS, Profile, UnreadableTrack
from tests.fixtures.analyses import make_off_grid, many
from tests.fixtures.fake_client import FakeClient


def _budget() -> Budget:
    return Budget(max_tokens=50_000, max_seconds=60.0)


def _answer(accepted: list[str], rejected: list[tuple[str, str]]) -> str:
    return json.dumps(
        {
            "accepted": accepted,
            "rejected": [{"track_id": i, "reason": r} for i, r in rejected],
            "note": "Fixture-Urteil.",
        }
    )


def test_the_curator_keeps_what_it_accepts(profile: Profile) -> None:
    tracks = many(14)
    ids = [t.id for t in tracks]
    client = FakeClient([_answer(ids[:13], [(ids[13], "am Genre vorbei")])])

    shortlist = curate(client, tracks, [], profile, _budget())

    assert shortlist.accepted == ids[:13]
    assert [r.track_id for r in shortlist.rejected] == [ids[13]]
    assert shortlist.shortened is False


def test_ein_song_ohne_outro_gilt_dem_curator_nicht_als_defekt(
    profile: Profile,
) -> None:
    """Fund 2 der Abnahme: der Prompt nannte "kein brauchbares Intro oder
    Outro" als Aussortiergrund und zeigte das gemessene Outro auf dem
    Trackende. Jetzt zeigt er den Bereich, in dem die Kette mischt."""
    tracks = many(3) + [
        make_off_grid("laut", bpm=120.0, duration_s=232.9,
                      intro_end_s=0.0, outro_start_s=232.9)
    ]
    ids = [t.id for t in tracks]
    client = FakeClient([_answer(ids, [])])

    curate(client, tracks, [], profile, _budget())

    prompt = " ".join(client.prompts[0].split())
    assert "kein brauchbares Intro oder Outro" not in prompt
    assert "laut: 120.00 BPM" in prompt
    assert "gemischt bis 32 s und ab 200 s" in prompt


def test_tempo_und_korridor_stehen_gleich_genau_im_curator_prompt(
    profile: Profile,
) -> None:
    """Fund 4: gerundetes Tempo neben ungerundeter Grenze sah aus wie
    ausserhalb des Korridors."""
    tracks = many(3)
    client = FakeClient([_answer([t.id for t in tracks], [])])

    curate(client, tracks, [], profile, _budget())

    prompt = " ".join(client.prompts[0].split())
    assert f"erlaubt {profile.bpm.min:.2f} bis {profile.bpm.max:.2f}" in prompt


def test_unreadable_files_are_rejected_without_asking_the_model(
    profile: Profile,
) -> None:
    """Eine unlesbare Datei ist kein Urteilsfall - sie ist einfach nicht da."""
    tracks = many(13)
    broken = UnreadableTrack(id="kaputt", path="/w/k.wav", error="kein wav")
    client = FakeClient([_answer([t.id for t in tracks], [])])

    shortlist = curate(client, tracks, [broken], profile, _budget())

    rejected = {r.track_id: r.reason for r in shortlist.rejected}
    assert "kaputt" in rejected
    assert "kein wav" in rejected["kaputt"]
    assert "kaputt" not in client.prompts[0]


def test_fewer_than_needed_shortens_the_set_with_a_note(profile: Profile) -> None:
    tracks = many(8)
    ids = [t.id for t in tracks]
    client = FakeClient([_answer(ids, [])])

    shortlist = curate(client, tracks, [], profile, _budget())

    assert shortlist.shortened is True
    assert shortlist.note


def test_below_the_floor_is_a_hard_abort(profile: Profile) -> None:
    tracks = many(5)
    ids = [t.id for t in tracks]
    client = FakeClient([_answer(ids[:2], [(i, "zu schwach") for i in ids[2:]])])

    with pytest.raises(NotEnoughTracks) as caught:
        curate(client, tracks, [], profile, _budget())

    assert str(MIN_TRACKS) in str(caught.value)


def test_an_invented_track_id_is_re_asked(profile: Profile) -> None:
    """Der Curator darf nur ueber Tracks urteilen, die es gibt."""
    tracks = many(13)
    ids = [t.id for t in tracks]
    client = FakeClient([_answer(ids + ["erfunden"], []), _answer(ids, [])])

    shortlist = curate(client, tracks, [], profile, _budget())

    assert shortlist.accepted == ids
    assert client.calls == 2
    assert "erfunden" in client.prompts[1]


def test_a_missing_track_is_re_asked(profile: Profile) -> None:
    """Ein Track, den das Modell stillschweigend vergisst, muss auffallen."""
    tracks = many(13)
    ids = [t.id for t in tracks]
    client = FakeClient([_answer(ids[:12], []), _answer(ids, [])])

    shortlist = curate(client, tracks, [], profile, _budget())

    assert shortlist.accepted == ids
    assert client.calls == 2
    assert ids[12] in client.prompts[1]


def test_an_id_in_both_lists_is_re_asked(profile: Profile) -> None:
    """Eine track_id darf nicht gleichzeitig angenommen und abgelehnt sein."""
    tracks = many(13)
    ids = [t.id for t in tracks]
    client = FakeClient(
        [
            _answer(ids, [(ids[0], "Doppelurteil")]),
            _answer(ids[:12], [(ids[12], "am Genre vorbei")]),
        ]
    )

    shortlist = curate(client, tracks, [], profile, _budget())

    assert shortlist.accepted == ids[:12]
    assert client.calls == 2
    assert ids[0] in client.prompts[1]


def test_more_accepted_than_needed_is_re_asked(profile: Profile) -> None:
    """Mehr Tracks anzunehmen als gebraucht, verletzt die Regel im Prompt."""
    tracks = many(14)
    ids = [t.id for t in tracks]
    client = FakeClient(
        [
            _answer(ids, []),
            _answer(ids[:13], [(ids[13], "am Genre vorbei")]),
        ]
    )

    shortlist = curate(client, tracks, [], profile, _budget())

    assert shortlist.accepted == ids[:13]
    assert client.calls == 2
    assert "Du hast 14 Tracks angenommen" in client.prompts[1]


def test_a_duplicate_inside_accepted_is_re_asked(profile: Profile) -> None:
    """Dieselbe track_id darf nicht zweimal in accepted stehen."""
    tracks = many(13)
    ids = [t.id for t in tracks]
    client = FakeClient([_answer(ids + [ids[0]], []), _answer(ids, [])])

    shortlist = curate(client, tracks, [], profile, _budget())

    assert shortlist.accepted == ids
    assert client.calls == 2
    assert "In accepted steht eine ID mehrfach." in client.prompts[1]


def test_a_duplicate_inside_rejected_is_re_asked(profile: Profile) -> None:
    """Dieselbe track_id zweimal abzulehnen verschluckt eine Begruendung.

    `rejected = {r.track_id: r.reason ...}` (siehe Report-Aufbau und den Test
    fuer unlesbare Dateien oben) baut aus der Shortlist eine Zuordnung - bei
    einer doppelten track_id geht eine der beiden Begruendungen dabei
    stillschweigend verloren.
    """
    tracks = many(13)
    ids = [t.id for t in tracks]
    client = FakeClient(
        [
            _answer(ids[:12], [(ids[12], "zu kurz"), (ids[12], "am Genre vorbei")]),
            _answer(ids[:12], [(ids[12], "zu kurz")]),
        ]
    )

    shortlist = curate(client, tracks, [], profile, _budget())

    assert shortlist.accepted == ids[:12]
    assert client.calls == 2
    assert "In rejected steht eine ID mehrfach." in client.prompts[1]


def test_the_models_note_survives_the_shortened_append(profile: Profile) -> None:
    """Die eigene Begruendung des Modells wird ergaenzt, nicht ersetzt."""
    tracks = many(8)
    ids = [t.id for t in tracks]
    payload = json.dumps(
        {
            "accepted": ids,
            "rejected": [],
            "note": "Individuelle Urteilsbegruendung.",
        }
    )
    client = FakeClient([payload])

    shortlist = curate(client, tracks, [], profile, _budget())

    assert shortlist.note.startswith("Individuelle Urteilsbegruendung.")
    assert "gekuerzt" in shortlist.note


def test_an_invented_id_after_every_round_raises(profile: Profile) -> None:
    """Ruling R4 gilt auch hier: nach der letzten Runde nicht durchwinken.

    Eine erfundene track_id ist keine Geschmacksfrage - der Architect wuerde
    sie in die Reihenfolge nehmen und der Designer bei by_id[...] mit einem
    KeyError abstuerzen, mitten in einem unbeaufsichtigten Lauf.
    """
    tracks = many(13)
    ids = [t.id for t in tracks]
    bad = _answer(ids[:12] + ["gibtsnicht"], [(ids[12], "zu kurz")])
    client = FakeClient([bad, bad, bad])

    with pytest.raises(StageRulesViolated) as caught:
        curate(client, tracks, [], profile, _budget())

    assert client.calls == 3
    assert "gibtsnicht" in str(caught.value)


def test_too_many_accepted_after_every_round_raises(profile: Profile) -> None:
    """Mehr Tracks anzunehmen als gebraucht sprengt die Ziellaenge."""
    tracks = many(20)
    ids = [t.id for t in tracks]
    bad = _answer(ids, [])
    client = FakeClient([bad, bad, bad])

    with pytest.raises(StageRulesViolated):
        curate(client, tracks, [], profile, _budget())

    assert client.calls == 3
