import json

import pytest

from djset.agents.base import Budget, StageRulesViolated
from djset.agents.designer import (
    MAX_GAIN_DB,
    TransitionChoice,
    build_mixplan,
    design,
    snap_to_downbeat,
)
from djset.contracts import (
    MAX_RATIO,
    MIN_RATIO,
    BeatGrid,
    MixPlan,
    Profile,
    Setlist,
    TrackAnalysis,
)
from tests.fixtures.analyses import (
    make_analysis,
    make_off_grid,
    many,
    mit_lokalem_raster,
)
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


def test_a_track_without_own_intro_or_outro_is_mixed_over_16_bars(
    profile: Profile,
) -> None:
    """Fund 2 der Abnahme (Felix 23.11. "Ja, so bauen"). Eine gleichfoermig
    laute Spur - ein fertiger Song - liefert laut find_mix_regions 0 Takte
    Intro und 0 Takte Outro. Frueher wurde der Uebergang dann zum Stoss auf
    einem Takt. Jetzt blendet die Kette wie ein DJ ueber die letzten 16 Takte
    des einen und die ersten 16 Takte des anderen Songs."""
    a = make_analysis("a", bpm=173.0, camelot="8A")
    b = make_off_grid("b", bpm=174.0, intro_end_s=0.0, outro_start_s=240.0, camelot="8A")
    c = make_analysis("c", bpm=175.0, camelot="8A")
    setlist = Setlist(order=["a", "b", "c"], note="")

    plan = design(FakeClient([_choices(2)]), setlist, [a, b, c], profile, _budget())

    sechzehn_takte = 16 * 4 * 60.0 / plan.target_bpm
    for transition in plan.transitions:
        assert transition.bars == 16
        assert transition.overlap_s == pytest.approx(sechzehn_takte, rel=1e-6)


def test_mischbereiche_werden_mit_ihrem_lokalen_tempo_gestreckt(
    profile: Profile,
) -> None:
    """Fund 3 der Abnahme (Felix 23.11. "Stolpert hoerbar", dann
    "Abschnittsweise strecken"). farewell-kiss lief im Intro 1,1 % langsamer
    als im Mittel. Mit dem Gesamttempo gestreckt liefen die beiden Spuren
    ueber 16 Takte um 0,7 Schlaege auseinander. Jetzt bekommt jeder
    Mischbereich sein eigenes Verhaeltnis, und beide Seiten der Naht laufen im
    Zieltempo."""
    target = profile.bpm.target
    a = mit_lokalem_raster(
        make_off_grid("a", bpm=174.0, camelot="8A"), outro=(200.03, 173.0)
    )
    b = mit_lokalem_raster(
        make_off_grid("b", bpm=174.0, camelot="8A"),
        intro=(0.05, 172.0), outro=(201.0, 175.0),
    )
    c = make_analysis("c", bpm=175.0, camelot="8A")
    setlist = Setlist(order=["a", "b", "c"], note="")

    plan = design(FakeClient([_choices(2)]), setlist, [a, b, c], profile, _budget())

    eintrag_a, eintrag_b, eintrag_c = plan.entries
    # a: Koerper mit Gesamttempo, Outro mit lokalem Tempo.
    assert [s[2] for s in eintrag_a.stuecke()] == pytest.approx(
        [target / 174.0, target / 173.0]
    )
    # b: Intro lokal, Koerper gesamt, Outro lokal.
    assert [s[2] for s in eintrag_b.stuecke()] == pytest.approx(
        [target / 172.0, target / 174.0, target / 175.0]
    )
    # Einstieg von b und Outro-Einsatz von a liegen auf den lokalen Rastern.
    in_bar = 4 * 60.0 / 172.0
    takte_b = (eintrag_b.play_from_s - 0.05) / in_bar
    assert abs(takte_b - round(takte_b)) < 1e-6
    out_bar = 4 * 60.0 / 173.0
    cue_a = eintrag_a.stuecke()[1][0]
    takte_a = (cue_a - 200.03) / out_bar
    assert abs(takte_a - round(takte_a)) < 1e-6
    # Beide Seiten der Naht sind 16 Takte im Zieltempo lang.
    sechzehn = 16 * 4 * 60.0 / target
    assert plan.transitions[0].overlap_s == pytest.approx(sechzehn, rel=1e-9)
    assert eintrag_a.ausgabe_dauer_s - eintrag_a.ausgabe_s(cue_a) == pytest.approx(
        sechzehn, rel=1e-9
    )
    assert plan.transitions[0].start_at_s == pytest.approx(eintrag_a.ausgabe_s(cue_a))
    # c hat kein lokales Raster: ein Verhaeltnis wie bisher.
    assert eintrag_c.abschnitte == []
    assert plan.target_length_s == pytest.approx(
        plan.transitions[1].start_at_s + eintrag_c.ausgabe_dauer_s
    )


def test_bars_larger_than_the_available_outro_are_reduced(profile: Profile) -> None:
    """Finding 2: 32 Takte (im Profilkorridor erlaubt) gegen einen Track ohne
    eigenes Outro. Der Mischbereich gibt 16 Takte her, also wird auf 16
    gekuerzt - nicht auf das gemessene Nichts und nicht auf erfundene 32."""
    x = make_off_grid("x", bpm=174.0, intro_end_s=0.0, outro_start_s=240.0, camelot="8A")
    y = make_analysis("y", bpm=173.0, camelot="8A")
    z = make_analysis("z", bpm=175.0, camelot="8A")
    setlist = Setlist(order=["x", "y", "z"], note="")

    plan = design(
        FakeClient([_choices(2, bars=32)]), setlist, [x, y, z], profile, _budget()
    )

    assert plan.transitions[0].bars == 16


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


# --- A1: ein Tonart-Bruch bekommt nur cut oder filter_sweep -----------------


def _bruch_set() -> list[TrackAnalysis]:
    """b (8B) nach c (2A) passt harmonisch nicht - die zweite Naht ist ein Bruch."""
    return [
        make_analysis("a", camelot="8A", energy=0.3),
        make_analysis("b", camelot="8B", energy=0.5),
        make_analysis("c", camelot="2A", energy=0.7),
    ]


def _zwei_wahlen(erste: str, zweite: str) -> str:
    return json.dumps(
        {"choices": [{"technique": erste, "bars": 16}, {"technique": zweite, "bars": 16}]}
    )


def test_ein_blend_am_tonart_bruch_wird_neu_gefragt(profile: Profile) -> None:
    setlist = Setlist(order=["a", "b", "c"], note="")
    client = FakeClient(
        [_zwei_wahlen("blend", "blend"), _zwei_wahlen("blend", "filter_sweep")]
    )

    plan = design(client, setlist, _bruch_set(), profile, _budget())

    assert client.calls == 2
    assert "Tonart-Bruch" in client.prompts[0]
    assert "cut" in client.prompts[1] and "filter_sweep" in client.prompts[1]
    assert [t.technique for t in plan.transitions] == ["blend", "filter_sweep"]


def test_cut_am_tonart_bruch_ist_erlaubt_auch_wenn_das_profil_ihn_nicht_nennt(
    profile: Profile,
) -> None:
    assert "cut" not in profile.transition.techniques
    setlist = Setlist(order=["a", "b", "c"], note="")
    client = FakeClient([_zwei_wahlen("eq_swap", "cut")])

    plan = design(client, setlist, _bruch_set(), profile, _budget())

    assert client.calls == 1
    assert [t.technique for t in plan.transitions] == ["eq_swap", "cut"]


def test_build_mixplan_macht_aus_einem_blend_am_bruch_einen_schnitt(
    profile: Profile,
) -> None:
    """Die Nachbedingung haengt nicht am Modell: wer build_mixplan direkt mit
    einem blend am Bruch ruft, bekommt einen Schnitt."""
    setlist = Setlist(order=["a", "b", "c"], note="")
    choices = [
        TransitionChoice(technique="blend", bars=16),
        TransitionChoice(technique="blend", bars=16),
    ]

    plan = build_mixplan(setlist, _bruch_set(), choices, profile)

    assert [t.technique for t in plan.transitions] == ["blend", "cut"]


def test_ohne_tonart_bruch_bleibt_der_prompt_wie_er_war(profile: Profile) -> None:
    tracks = many(4)
    setlist = Setlist(order=[t.id for t in tracks], note="")
    client = FakeClient([_choices(3)])

    design(client, setlist, tracks, profile, _budget())

    assert "Tonart-Bruch" not in client.prompts[0]
