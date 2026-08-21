import pytest
from pydantic import ValidationError

from djset.contracts import (
    BpmRange, EnergyPoint, MixEntry, MixPlan, Profile, StructureRules,
    TrackCount, TransitionRules,
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
        MixPlan(target_length_s=180.0, entries=entries, transitions=[])
