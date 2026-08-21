import pytest
from pydantic import ValidationError

from djset.contracts import (
    BeatGrid, Bpm, BpmRange, EnergyPoint, EqCurve, EqPoint, Key, Loudness,
    MixEntry, MixPlan, MixRegions, Profile, Rejection, StructureRules,
    TrackCount, TrackMeasurements, Transition, TransitionRules,
)


def _profile(**overrides) -> dict:
    """Ein gueltiges Profil als Bausatz; einzelne Felder ueberschreibbar."""
    base = dict(
        genre="liquid dnb",
        length_minutes=60.0,
        bpm=BpmRange(target=174.0, min=170.0, max=178.0),
        transition=TransitionRules(bars_min=16, bars_max=32, techniques=["blend"]),
        harmonic_strictness="strict",
        energy_arc=[EnergyPoint(position=0.0, energy=0.3),
                    EnergyPoint(position=1.0, energy=0.8)],
        track_count=TrackCount(needed=12, request=18),
        style_vocab=["liquid", "rolling breaks"],
        structure=StructureRules(min_intro_bars=16, min_outro_bars=16),
    )
    base.update(overrides)
    return base


def test_profile_accepts_valid_input():
    profile = Profile(**_profile())
    assert profile.bpm.target == 174.0


def test_profile_rejects_target_outside_range():
    with pytest.raises(ValidationError, match="zwischen min und max"):
        Profile(**_profile(bpm=BpmRange(target=190.0, min=170.0, max=178.0)))


def test_profile_rejects_unsorted_energy_arc():
    unsorted_arc = [EnergyPoint(position=1.0, energy=0.8),
                    EnergyPoint(position=0.0, energy=0.3)]
    with pytest.raises(ValidationError, match="aufsteigend"):
        Profile(**_profile(energy_arc=unsorted_arc))


def test_track_count_rejects_request_below_needed():
    with pytest.raises(ValidationError, match="mindestens needed"):
        TrackCount(needed=12, request=8)


def test_mix_entry_rejects_timestretch_beyond_six_percent():
    with pytest.raises(ValidationError):
        MixEntry(track_id="a", play_from_s=0.0, play_to_s=60.0,
                 timestretch_ratio=1.2, gain_db=0.0)


def test_mix_plan_requires_one_transition_less_than_entries():
    entries = [MixEntry(track_id=f"t{i}", play_from_s=0.0, play_to_s=60.0,
                        timestretch_ratio=1.0, gain_db=0.0) for i in range(3)]
    with pytest.raises(ValidationError, match="genau einen Uebergang"):
        MixPlan(
            target_length_s=180.0,
            target_bpm=174.0,
            entries=entries,
            transitions=[],
        )


# --- Harte Regeln, die bisher kein Test festgehalten hat -------------------
# Der Plan wirbt damit, dass die harten Regeln im Typ stecken. Ohne Test
# faellt niemandem auf, wenn ein @model_validator bei einem Refactoring
# verschwindet.


def test_transition_rules_reject_bars_out_of_order():
    with pytest.raises(ValidationError, match="bars_min darf nicht groesser"):
        TransitionRules(bars_min=32, bars_max=16, techniques=["blend"])


def test_mix_regions_reject_intro_after_outro():
    with pytest.raises(ValidationError, match="intro_end_s muss vor"):
        MixRegions(intro_end_s=90.0, outro_start_s=30.0)


def test_mix_regions_reject_zero_length_window():
    """Gleichstand ist kein gueltiger Mix-Bereich: dazwischen liegt nichts."""
    with pytest.raises(ValidationError, match="intro_end_s muss vor"):
        MixRegions(intro_end_s=30.0, outro_start_s=30.0)


def _measurements(**overrides) -> dict:
    base = dict(
        id="t1",
        path="watch/t1.wav",
        duration_s=300.0,
        bpm=Bpm(value=174.0, confidence=0.9),
        beatgrid=BeatGrid(first_downbeat_s=0.0, bar_length_s=1.38, confidence=0.9),
        key=Key(camelot="8A", confidence=0.7),
        energy=[0.2, 0.8, 0.4],
        mix_regions=MixRegions(intro_end_s=10.0, outro_start_s=280.0),
        loudness=Loudness(lufs_integrated=-9.5, true_peak=-1.2),
    )
    base.update(overrides)
    return base


def test_track_measurements_reject_outro_behind_track_end():
    with pytest.raises(ValidationError, match="hinter dem Trackende"):
        TrackMeasurements(
            **_measurements(
                duration_s=120.0,
                mix_regions=MixRegions(intro_end_s=10.0, outro_start_s=200.0),
            )
        )


def test_mix_entry_rejects_timestretch_below_six_percent():
    """Die Grenze gilt in beide Richtungen - bisher war nur oben getestet."""
    with pytest.raises(ValidationError):
        MixEntry(track_id="a", play_from_s=0.0, play_to_s=60.0,
                 timestretch_ratio=0.90, gain_db=0.0)


def test_mix_entry_accepts_the_exact_stretch_limits():
    for ratio in (0.94, 1.06):
        entry = MixEntry(track_id="a", play_from_s=0.0, play_to_s=60.0,
                         timestretch_ratio=ratio, gain_db=0.0)
        assert entry.timestretch_ratio == ratio


def test_camelot_pattern_accepts_the_whole_wheel():
    for number in range(1, 13):
        for letter in ("A", "B"):
            assert Key(camelot=f"{number}{letter}", confidence=0.5).camelot


def test_camelot_pattern_rejects_everything_off_the_wheel():
    for bad in ("0A", "13A", "8C", "8", "A8", "", "8a", " 8A"):
        with pytest.raises(ValidationError):
            Key(camelot=bad, confidence=0.5)


def test_rejection_needs_a_track_id():
    with pytest.raises(ValidationError):
        Rejection(track_id="", reason="zu kurz")


# --- Loudness: -inf darf gar nicht erst in den Vertrag ---------------------


def test_loudness_rejects_infinite_values():
    """-inf ueberlebt den eigenen JSON-Roundtrip nicht, siehe Loudness."""
    with pytest.raises(ValidationError):
        Loudness(lufs_integrated=float("-inf"), true_peak=-1.0)
    with pytest.raises(ValidationError):
        Loudness(lufs_integrated=-9.0, true_peak=float("-inf"))


def test_loudness_round_trips_through_json():
    original = Loudness(lufs_integrated=-120.0, true_peak=-120.0)
    assert Loudness.model_validate_json(original.model_dump_json()) == original


# --- MixPlan: der Renderer muss der Paarung trauen koennen -----------------


def _eq_curve() -> EqCurve:
    flat = [EqPoint(position=0.0, gain_db=0.0), EqPoint(position=1.0, gain_db=0.0)]
    return EqCurve(low=list(flat), mid=list(flat), high=list(flat))


def _entries(ids: list[str]) -> list[MixEntry]:
    return [
        MixEntry(track_id=track_id, play_from_s=0.0, play_to_s=60.0,
                 timestretch_ratio=1.0, gain_db=0.0)
        for track_id in ids
    ]


def _transition(from_track: str, to_track: str) -> Transition:
    return Transition(
        from_track=from_track,
        to_track=to_track,
        start_at_s=50.0,
        bars=16,
        technique="blend",
        eq_out=_eq_curve(),
        eq_in=_eq_curve(),
    )


def test_mix_plan_accepts_transitions_that_match_their_neighbours():
    plan = MixPlan(
        target_length_s=180.0,
        target_bpm=174.0,
        entries=_entries(["a", "b", "c"]),
        transitions=[_transition("a", "b"), _transition("b", "c")],
    )
    assert plan.transitions[1].from_track == plan.entries[1].track_id


def test_mix_plan_rejects_transition_with_the_wrong_from_track():
    with pytest.raises(ValidationError, match="from_track"):
        MixPlan(
            target_length_s=180.0,
            target_bpm=174.0,
            entries=_entries(["a", "b", "c"]),
            transitions=[_transition("a", "b"), _transition("a", "c")],
        )


def test_mix_plan_rejects_transition_with_the_wrong_to_track():
    with pytest.raises(ValidationError, match="to_track"):
        MixPlan(
            target_length_s=180.0,
            target_bpm=174.0,
            entries=_entries(["a", "b", "c"]),
            transitions=[_transition("a", "c"), _transition("b", "c")],
        )


def test_mix_plan_rejects_transitions_in_the_wrong_order():
    """Richtige Menge, falsche Reihenfolge - Zaehlen allein faengt das nicht."""
    with pytest.raises(ValidationError, match="from_track"):
        MixPlan(
            target_length_s=180.0,
            target_bpm=174.0,
            entries=_entries(["a", "b", "c"]),
            transitions=[_transition("b", "c"), _transition("a", "b")],
        )


def test_mix_plan_rejects_duplicate_track_ids():
    with pytest.raises(ValidationError, match="nur einmal"):
        MixPlan(
            target_length_s=180.0,
            target_bpm=174.0,
            entries=_entries(["a", "b", "a"]),
            transitions=[_transition("a", "b"), _transition("b", "a")],
        )
