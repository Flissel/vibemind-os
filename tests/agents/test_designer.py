import json

from djset.agents.base import Budget
from djset.agents.designer import MAX_RATIO, MIN_RATIO, design, snap_to_downbeat
from djset.contracts import BeatGrid, MixPlan, Profile, Setlist
from tests.fixtures.analyses import make_analysis, many
from tests.fixtures.fake_client import FakeClient


def _budget() -> Budget:
    return Budget(max_tokens=50_000, max_seconds=60.0)


def _choices(count: int, technique: str = "blend", bars: int = 16) -> str:
    return json.dumps(
        {"choices": [{"technique": technique, "bars": bars} for _ in range(count)]}
    )


def test_snap_to_downbeat_lands_on_the_grid() -> None:
    grid = BeatGrid(first_downbeat_s=0.5, bar_length_s=2.0, confidence=1.0)

    assert snap_to_downbeat(0.1, grid) == 0.5
    assert snap_to_downbeat(4.4, grid) == 4.5
    assert snap_to_downbeat(5.6, grid) == 6.5


def test_the_plan_has_one_transition_between_each_pair(profile: Profile) -> None:
    tracks = many(4)
    setlist = Setlist(order=[t.id for t in tracks], note="")
    client = FakeClient([_choices(3)])

    plan = design(client, setlist, tracks, profile, _budget())

    assert isinstance(plan, MixPlan)
    assert len(plan.entries) == 4
    assert len(plan.transitions) == 3
    assert [e.track_id for e in plan.entries] == setlist.order


def test_every_cue_point_sits_on_a_downbeat(profile: Profile) -> None:
    tracks = many(4)
    setlist = Setlist(order=[t.id for t in tracks], note="")
    plan = design(FakeClient([_choices(3)]), setlist, tracks, profile, _budget())
    by_id = {t.id: t for t in tracks}

    for entry in plan.entries:
        grid = by_id[entry.track_id].beatgrid
        offset = entry.play_from_s - grid.first_downbeat_s
        bars = offset / grid.bar_length_s
        assert abs(bars - round(bars)) < 1e-6


def test_every_stretch_stays_inside_the_limit(profile: Profile) -> None:
    tracks = many(5)
    setlist = Setlist(order=[t.id for t in tracks], note="")

    plan = design(FakeClient([_choices(4)]), setlist, tracks, profile, _budget())

    for entry in plan.entries:
        assert MIN_RATIO <= entry.timestretch_ratio <= MAX_RATIO


def test_an_unstretchable_track_gets_a_cut_and_no_stretch(profile: Profile) -> None:
    """Spec: ist der Stretch zu gross, loest der Designer den Uebergang anders."""
    tracks = [
        make_analysis("a", bpm=174.0, camelot="8A", energy=0.3),
        make_analysis("b", bpm=140.0, camelot="8B", energy=0.6),
        make_analysis("c", bpm=174.0, camelot="9B", energy=0.8),
    ]
    setlist = Setlist(order=["a", "b", "c"], note="")

    plan = design(FakeClient([_choices(2)]), setlist, tracks, profile, _budget())

    by_id = {e.track_id: e for e in plan.entries}
    assert by_id["b"].timestretch_ratio == 1.0
    assert plan.transitions[0].technique == "cut"


def test_the_timeline_runs_forward_without_gaps(profile: Profile) -> None:
    tracks = many(4)
    setlist = Setlist(order=[t.id for t in tracks], note="")

    plan = design(FakeClient([_choices(3)]), setlist, tracks, profile, _budget())

    starts = [t.start_at_s for t in plan.transitions]
    assert starts == sorted(starts)
    assert starts[0] > 0.0


def test_a_technique_outside_the_profile_is_re_asked(profile: Profile) -> None:
    tracks = many(4)
    setlist = Setlist(order=[t.id for t in tracks], note="")
    bad = json.dumps(
        {"choices": [{"technique": "double_drop", "bars": 16} for _ in range(3)]}
    )
    client = FakeClient([bad, _choices(3)])

    design(client, setlist, tracks, profile, _budget())

    assert client.calls == 2
    assert "double_drop" in client.prompts[1]
