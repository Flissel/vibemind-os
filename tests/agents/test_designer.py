import json

import pytest

from djset.agents.base import Budget, StageRulesViolated
from djset.agents.designer import design, snap_to_downbeat
from djset.contracts import (
    MAX_RATIO,
    MIN_RATIO,
    BeatGrid,
    Bpm,
    Key,
    Loudness,
    MixPlan,
    MixRegions,
    Profile,
    Setlist,
    TrackAnalysis,
    Verdict,
)
from tests.fixtures.analyses import make_analysis, many
from tests.fixtures.fake_client import FakeClient


def _budget() -> Budget:
    return Budget(max_tokens=50_000, max_seconds=60.0)


def _choices(count: int, technique: str = "blend", bars: int = 16) -> str:
    return json.dumps(
        {"choices": [{"technique": technique, "bars": bars} for _ in range(count)]}
    )


def _custom_track(
    track_id: str,
    *,
    bpm: float,
    duration_s: float = 240.0,
    first_downbeat_s: float = 0.0,
    intro_end_s: float | None = None,
    outro_start_s: float | None = None,
    camelot: str = "8A",
) -> TrackAnalysis:
    """Ein Track mit voll kontrollierbarem Raster und Mix-Regionen.

    `tests.fixtures.analyses.make_analysis` bindet first_downbeat_s fest auf
    0.0 und Intro/Outro immer an genau 16 Takte - genau die zwei Faelle, die
    hier absichtlich anders sein sollen: ein schiefes Raster (Finding 4) und
    Mix-Regionen ohne nutzbares Material (Finding 2).
    """
    bar_length = 4 * 60.0 / bpm
    if intro_end_s is None:
        intro_end_s = bar_length * 16
    if outro_start_s is None:
        outro_start_s = duration_s - bar_length * 16
    return TrackAnalysis(
        id=track_id,
        path=f"/watch/{track_id}.wav",
        duration_s=duration_s,
        bpm=Bpm(value=bpm, confidence=0.95),
        beatgrid=BeatGrid(
            first_downbeat_s=first_downbeat_s, bar_length_s=bar_length, confidence=0.95
        ),
        key=Key(camelot=camelot, confidence=0.9),
        energy=[0.80, 0.84, 0.86, 0.85, 0.82],
        mix_regions=MixRegions(intro_end_s=intro_end_s, outro_start_s=outro_start_s),
        loudness=Loudness(lufs_integrated=-9.5, true_peak=-1.2),
        verdict=Verdict(usable=True, notes="Fixture-Track.", energy_level=0.5),
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
    """Jedes Raster hier ist bewusst schief: erster Downbeat ungleich 0.0 und
    Intro/Outro nicht auf Taktgrenzen. Mit many() (first_downbeat_s == 0.0 und
    Intro/Outro exakt auf 16 Takten) waere play_from_s immer 0.0 und der Test
    bestuende auch mit einer Identitaetsfunktion statt snap_to_downbeat."""
    tracks = [
        _custom_track("o0", bpm=172.0, first_downbeat_s=0.30, intro_end_s=20.0, outro_start_s=200.0),
        _custom_track("o1", bpm=173.0, first_downbeat_s=0.70, intro_end_s=21.0, outro_start_s=201.0),
        _custom_track("o2", bpm=174.0, first_downbeat_s=0.45, intro_end_s=22.0, outro_start_s=202.0),
        _custom_track("o3", bpm=175.0, first_downbeat_s=0.15, intro_end_s=23.0, outro_start_s=203.0),
    ]
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


def test_a_track_with_no_intro_or_outro_material_gets_a_one_bar_transition(
    profile: Profile,
) -> None:
    """Finding 2: ein Uebergang darf nicht mehr Takte deklarieren, als das
    Material hergibt. Eine gleichfoermig laute Spur liefert 0 Takte Intro und
    0 Takte Outro (find_mix_regions: intro_end_s == 0.0, outro_start_s ==
    duration_s) - dann wird der Uebergang zum Stoss auf dem Downbeat (1 Takt),
    nicht zu 16 Sekunden erfundenen Materials."""
    a = make_analysis("a", bpm=173.0, camelot="8A")
    b = _custom_track("b", bpm=174.0, intro_end_s=0.0, outro_start_s=240.0, camelot="8A")
    c = make_analysis("c", bpm=175.0, camelot="8A")
    setlist = Setlist(order=["a", "b", "c"], note="")

    plan = design(FakeClient([_choices(2)]), setlist, [a, b, c], profile, _budget())

    assert plan.transitions[0].bars == 1
    assert plan.transitions[1].bars == 1


def test_bars_larger_than_the_available_outro_are_reduced(profile: Profile) -> None:
    """Finding 2: 32 Takte (im Profilkorridor erlaubt) gegen einen Track, der
    nur 8 Takte Outro hat, muss auf die verfuegbaren 8 Takte gekuerzt werden."""
    bar_length = 4 * 60.0 / 174.0
    x = _custom_track(
        "x", bpm=174.0, outro_start_s=240.0 - 8 * bar_length, camelot="8A"
    )
    y = make_analysis("y", bpm=173.0, camelot="8A")
    z = make_analysis("z", bpm=175.0, camelot="8A")
    setlist = Setlist(order=["x", "y", "z"], note="")

    plan = design(
        FakeClient([_choices(2, bars=32)]), setlist, [x, y, z], profile, _budget()
    )

    assert plan.transitions[0].bars == 8


def test_an_unstretchable_middle_track_forces_the_cut_on_both_sides(
    profile: Profile,
) -> None:
    """Finding 3: der alte Code schrieb den Schnitt nur in den Uebergang
    hinein, nicht auch aus dem unangleichbaren Track heraus."""
    tracks = [
        make_analysis("a", bpm=174.0, camelot="8A", energy=0.3),
        make_analysis("b", bpm=140.0, camelot="8B", energy=0.6),
        make_analysis("c", bpm=174.0, camelot="9B", energy=0.8),
    ]
    setlist = Setlist(order=["a", "b", "c"], note="")

    plan = design(FakeClient([_choices(2)]), setlist, tracks, profile, _budget())

    assert plan.transitions[0].technique == "cut"
    assert plan.transitions[1].technique == "cut"


def test_an_unstretchable_first_track_forces_the_cut_out_of_it(
    profile: Profile,
) -> None:
    """Finding 3: ein unangleichbarer erster Track hat keinen Uebergang
    hinein, nur einen heraus - auch der muss zum Schnitt werden."""
    tracks = [
        make_analysis("a", bpm=140.0, camelot="8A", energy=0.3),
        make_analysis("b", bpm=174.0, camelot="8B", energy=0.6),
        make_analysis("c", bpm=174.0, camelot="9B", energy=0.8),
    ]
    setlist = Setlist(order=["a", "b", "c"], note="")

    plan = design(FakeClient([_choices(2)]), setlist, tracks, profile, _budget())

    by_id = {e.track_id: e for e in plan.entries}
    assert by_id["a"].timestretch_ratio == 1.0
    assert plan.transitions[0].technique == "cut"


def test_a_technique_outside_the_profile_after_every_round_raises(
    profile: Profile,
) -> None:
    """Ruling R4: nach der letzten Runde nicht stillschweigend durchwinken,
    wenn die Technik das Profil verletzt."""
    tracks = many(4)
    setlist = Setlist(order=[t.id for t in tracks], note="")
    bad = json.dumps(
        {"choices": [{"technique": "double_drop", "bars": 16} for _ in range(3)]}
    )
    client = FakeClient([bad, bad, bad])

    with pytest.raises(StageRulesViolated):
        design(client, setlist, tracks, profile, _budget())

    assert client.calls == 3


def test_bars_outside_the_corridor_after_every_round_raises(profile: Profile) -> None:
    """Ruling R4 gilt auch fuer die Taktzahl, nicht nur fuer die Technik."""
    tracks = many(4)
    setlist = Setlist(order=[t.id for t in tracks], note="")
    bad = json.dumps({"choices": [{"technique": "blend", "bars": 4} for _ in range(3)]})
    client = FakeClient([bad, bad, bad])

    with pytest.raises(StageRulesViolated):
        design(client, setlist, tracks, profile, _budget())

    assert client.calls == 3
