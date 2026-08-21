import json

import pytest

from djset.agents.base import Budget
from djset.agents.curator import NotEnoughTracks, curate
from djset.contracts import MIN_TRACKS, Profile, UnreadableTrack
from tests.fixtures.analyses import many
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
