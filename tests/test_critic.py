from djset.agents.designer import TransitionChoice, build_mixplan
from djset.contracts import MixEntry, Profile, Setlist
from djset.critic import critique
from tests.fixtures.analyses import make_analysis, many


def _plan(tracks, profile, bars: int = 16):
    setlist = Setlist(order=[t.id for t in tracks], note="")
    choices = [
        TransitionChoice(technique="blend", bars=bars) for _ in range(len(tracks) - 1)
    ]
    return build_mixplan(setlist, tracks, choices, profile)


def test_a_sound_plan_passes(profile: Profile) -> None:
    tracks = many(13)
    plan = _plan(tracks, profile)
    # Das Profil bekommt die Laenge, die der Plan tatsaechlich hat. Dreizehn
    # Fixture-Tracks summieren sich nicht zufaellig auf 60 Minuten, und ein
    # Test, der die Fixtures auf eine Wunschzahl frisiert, prueft nicht mehr die
    # Arithmetik des Critics, sondern nur noch die Frisur.
    fitting = Profile.model_validate(
        profile.model_dump() | {"length_minutes": plan.target_length_s / 60.0}
    )

    verdict = critique(plan, tracks, fitting)

    assert verdict.ok is True
    assert verdict.complaints == []


def test_a_plan_that_is_far_too_short_is_complained_about(profile: Profile) -> None:
    tracks = many(4)
    plan = _plan(tracks, profile)

    verdict = critique(plan, tracks, profile)

    assert verdict.ok is False
    assert any("Laenge" in c for c in verdict.complaints)


def test_a_cue_point_off_the_grid_is_caught(profile: Profile) -> None:
    tracks = many(13)
    plan = _plan(tracks, profile)
    broken = plan.model_dump()
    broken["entries"][2]["play_from_s"] += 0.37

    verdict = critique(
        type(plan).model_validate(broken), tracks, profile
    )

    assert verdict.ok is False
    assert any("Downbeat" in c for c in verdict.complaints)


def test_a_falling_energy_arc_is_caught(profile: Profile) -> None:
    """Der Bogen steigt zur Mitte; eine absteigende Reihenfolge verletzt ihn."""
    tracks = [
        make_analysis(f"t{i}", camelot=k, energy=e, bpm=174.0)
        for i, (k, e) in enumerate(
            zip(["8A", "8B", "9B", "9A", "10A"], [0.9, 0.8, 0.6, 0.4, 0.2])
        )
    ]
    plan = _plan(tracks, profile)

    verdict = critique(plan, tracks, profile)

    assert verdict.ok is False
    assert any("Energie" in c for c in verdict.complaints)


def test_the_critique_names_the_offending_track(profile: Profile) -> None:
    tracks = many(13)
    plan = _plan(tracks, profile)
    broken = plan.model_dump()
    broken["entries"][5]["play_from_s"] += 0.41

    verdict = critique(type(plan).model_validate(broken), tracks, profile)

    assert any(plan.entries[5].track_id in c for c in verdict.complaints)
