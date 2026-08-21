import json

import pytest

from djset.agents.base import Budget, StageRulesViolated
from djset.agents.designer import MAX_GAIN_DB, design, snap_to_downbeat
from djset.contracts import (
    MAX_RATIO,
    MIN_RATIO,
    BeatGrid,
    MixPlan,
    Profile,
    Setlist,
    TrackAnalysis,
)
from tests.fixtures.analyses import make_analysis, make_off_grid, many
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


def _off_grid_set() -> list[TrackAnalysis]:
    """Vier Tracks, bei denen NICHTS auf dem Raster liegt.

    Weder der erste Downbeat noch Intro noch Outro. Genau das liefert die
    Messung im Betrieb: find_mix_regions zaehlt seine 4-Takt-Bloecke ab Sample
    0, nicht ab first_downbeat_s, also ist outro_start_s ein beliebiger
    Bruchteil eines Takts. make_analysis kann das nicht nachstellen - dort ist
    first_downbeat_s immer 0.0 und das Outro sitzt exakt auf einer Taktgrenze.
    """
    return [
        make_off_grid("o0", bpm=172.0, first_downbeat_s=0.30, intro_end_s=20.0, outro_start_s=200.0),
        make_off_grid("o1", bpm=173.0, first_downbeat_s=0.70, intro_end_s=21.0, outro_start_s=201.0),
        make_off_grid("o2", bpm=174.0, first_downbeat_s=0.45, intro_end_s=22.0, outro_start_s=202.0),
        make_off_grid("o3", bpm=175.0, first_downbeat_s=0.15, intro_end_s=23.0, outro_start_s=203.0),
    ]


def test_every_cue_point_sits_on_a_downbeat(profile: Profile) -> None:
    """Jedes Raster hier ist bewusst schief: erster Downbeat ungleich 0.0 und
    Intro/Outro nicht auf Taktgrenzen. Mit many() (first_downbeat_s == 0.0 und
    Intro/Outro exakt auf 16 Takten) waere play_from_s immer 0.0 und der Test
    bestuende auch mit einer Identitaetsfunktion statt snap_to_downbeat."""
    tracks = _off_grid_set()
    setlist = Setlist(order=[t.id for t in tracks], note="")
    plan = design(FakeClient([_choices(3)]), setlist, tracks, profile, _budget())
    by_id = {t.id: t for t in tracks}

    for entry in plan.entries:
        grid = by_id[entry.track_id].beatgrid
        offset = entry.play_from_s - grid.first_downbeat_s
        bars = offset / grid.bar_length_s
        assert abs(bars - round(bars)) < 1e-6


def test_every_transition_sits_on_a_downbeat_of_the_outgoing_track(
    profile: Profile,
) -> None:
    """Die andere Haelfte der Downbeat-Regel: nicht nur der Einstieg jedes
    Tracks in sein eigenes Raster, sondern der Uebergang in das Raster seines
    Vorgaengers.

    Der eingehende Track startet bei start_at_s mit seinem eigenen Downbeat.
    Liegt start_at_s nicht auch auf einem Downbeat des Vorgaengers, laufen die
    beiden Raster gegeneinander - und das ist genau die Beat-Kontinuitaet, die
    Erfolgskriterium 4 an jeder Naht verlangt.

    Gemessen wird in Takten der AUSGABE: die Quelltaktlaenge geteilt durch das
    Stretch-Verhaeltnis.
    """
    tracks = _off_grid_set()
    setlist = Setlist(order=[t.id for t in tracks], note="")
    plan = design(FakeClient([_choices(3)]), setlist, tracks, profile, _budget())
    by_id = {t.id: t for t in tracks}
    entries = {e.track_id: e for e in plan.entries}

    out_start = 0.0
    for transition in plan.transitions:
        entry = entries[transition.from_track]
        grid = by_id[transition.from_track].beatgrid
        out_bar_s = grid.bar_length_s / entry.timestretch_ratio
        elapsed_bars = (transition.start_at_s - out_start) / out_bar_s
        drift_bars = elapsed_bars - round(elapsed_bars)
        assert abs(drift_bars) < 1e-6, (
            f"{transition.from_track} nach {transition.to_track}: "
            f"{elapsed_bars:.6f} Takte bis zum Uebergang, "
            f"Phasenfehler {abs(drift_bars) * out_bar_s * 1000:.0f} ms"
        )
        out_start = transition.start_at_s


def test_the_geometric_overlap_equals_the_declared_bars(profile: Profile) -> None:
    """`bars` ist keine Deklaration neben der Geometrie, sondern deren Mass.

    Der Renderer blendet zwischen start_at_s und dem Ende des ausgehenden
    Abschnitts. Was dort tatsaechlich uebereinanderliegt, muss die Taktzahl
    sein, die der Uebergang angibt - sonst blendet er ueber eine andere Strecke
    als der Plan behauptet.
    """
    tracks = _off_grid_set()
    setlist = Setlist(order=[t.id for t in tracks], note="")
    plan = design(FakeClient([_choices(3)]), setlist, tracks, profile, _budget())
    by_id = {t.id: t for t in tracks}
    entries = {e.track_id: e for e in plan.entries}

    out_start = 0.0
    for transition in plan.transitions:
        entry = entries[transition.from_track]
        grid = by_id[transition.from_track].beatgrid
        out_bar_s = grid.bar_length_s / entry.timestretch_ratio
        ends_at = out_start + (entry.play_to_s - entry.play_from_s) / entry.timestretch_ratio
        overlap_bars = (ends_at - transition.start_at_s) / out_bar_s
        assert abs(overlap_bars - transition.bars) < 1e-6
        out_start = transition.start_at_s


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


def test_the_gain_pulls_every_track_to_the_median_loudness(profile: Profile) -> None:
    """Finding 8: gain_db war fest 0.0, obwohl lufs_integrated gemessen dasteht.

    Der Renderer darf laut Spec nichts entscheiden, der Abgleich muss also hier
    passieren. Referenz ist der Median des Sets.
    """
    tracks = many(5)
    for track, lufs in zip(tracks, [-12.0, -9.0, -8.0, -7.0, -6.0]):
        track.loudness.lufs_integrated = lufs
    setlist = Setlist(order=[t.id for t in tracks], note="")

    plan = design(FakeClient([_choices(4)]), setlist, tracks, profile, _budget())

    gains = {e.track_id: e.gain_db for e in plan.entries}
    # Median der fuenf Werte ist -8.0.
    assert gains[tracks[2].id] == 0.0
    assert gains[tracks[0].id] == pytest.approx(4.0)
    assert gains[tracks[4].id] == pytest.approx(-2.0)


def test_a_broken_loudness_measurement_cannot_blow_up_the_mix(
    profile: Profile,
) -> None:
    """Der Bodenwert -120 dB fuer digitale Stille steht so im Vertrag. Ohne
    Klammer verlangte er eine Anhebung um ueber 110 dB."""
    tracks = many(5)
    tracks[1].loudness.lufs_integrated = -120.0
    setlist = Setlist(order=[t.id for t in tracks], note="")

    plan = design(FakeClient([_choices(4)]), setlist, tracks, profile, _budget())

    gains = {e.track_id: e.gain_db for e in plan.entries}
    assert gains[tracks[1].id] == MAX_GAIN_DB
    assert all(abs(gain) <= MAX_GAIN_DB for gain in gains.values())


def test_a_cut_switches_at_the_transition_start(profile: Profile) -> None:
    """Finding 4: ein Schnitt schneidet, er blendet nicht spaet.

    Die alte Vorlage hielt den eingehenden Track bis Position 0,9 auf -60 dB.
    Bei 16 Takten spielte er damit 14,4 Takte lang unhoerbar mit, und der
    Wechsel fiel auf Takt 14,4 - unter keiner Rasterung ein Downbeat. Beide
    Kurven stehen jetzt von Anfang bis Ende auf ihrem Endwert, die Umschaltung
    liegt also bei start_at_s, und das ist ein Downbeat beider Tracks.
    """
    tracks = [
        make_analysis("a", bpm=174.0, camelot="8A", energy=0.3),
        make_analysis("b", bpm=140.0, camelot="8B", energy=0.6),
        make_analysis("c", bpm=174.0, camelot="9B", energy=0.8),
    ]
    setlist = Setlist(order=["a", "b", "c"], note="")

    plan = design(FakeClient([_choices(2)]), setlist, tracks, profile, _budget())

    transition = plan.transitions[0]
    assert transition.technique == "cut"
    for band in ("low", "mid", "high"):
        out_points = getattr(transition.eq_out, band)
        in_points = getattr(transition.eq_in, band)
        assert [p.gain_db for p in out_points] == [-60.0, -60.0]
        assert [p.gain_db for p in in_points] == [0.0, 0.0]
        assert [p.position for p in out_points] == [0.0, 1.0]
        assert [p.position for p in in_points] == [0.0, 1.0]


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
    b = make_off_grid("b", bpm=174.0, intro_end_s=0.0, outro_start_s=240.0, camelot="8A")
    c = make_analysis("c", bpm=175.0, camelot="8A")
    setlist = Setlist(order=["a", "b", "c"], note="")

    plan = design(FakeClient([_choices(2)]), setlist, [a, b, c], profile, _budget())

    assert plan.transitions[0].bars == 1
    assert plan.transitions[1].bars == 1


def test_bars_larger_than_the_available_outro_are_reduced(profile: Profile) -> None:
    """Finding 2: 32 Takte (im Profilkorridor erlaubt) gegen einen Track, der
    nur 8 Takte Outro hat, muss auf die verfuegbaren 8 Takte gekuerzt werden."""
    bar_length = 4 * 60.0 / 174.0
    x = make_off_grid(
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
