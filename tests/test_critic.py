from djset.agents.designer import TransitionChoice, build_mixplan
from djset.contracts import MixEntry, Profile, Setlist
from djset.critic import critique
from tests.fixtures.analyses import make_analysis, make_off_grid, many


def _plan(tracks, profile, bars: int = 16):
    setlist = Setlist(order=[t.id for t in tracks], note="")
    choices = [
        TransitionChoice(technique="blend", bars=bars) for _ in range(len(tracks) - 1)
    ]
    return build_mixplan(setlist, tracks, choices, profile)


def _kinds(verdict) -> set[str]:
    return {complaint.kind for complaint in verdict.complaints}


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
    assert "length" in _kinds(verdict)
    assert any("Laenge" in c.text for c in verdict.complaints)


def test_a_cue_point_off_the_grid_is_caught(profile: Profile) -> None:
    tracks = many(13)
    plan = _plan(tracks, profile)
    broken = plan.model_dump()
    broken["entries"][2]["play_from_s"] += 0.37

    verdict = critique(
        type(plan).model_validate(broken), tracks, profile
    )

    assert verdict.ok is False
    assert any("Downbeat" in c.text for c in verdict.complaints)


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
    assert "energy_arc" in _kinds(verdict)
    assert any("Energie" in c.text for c in verdict.complaints)


def test_the_critique_names_the_offending_track(profile: Profile) -> None:
    tracks = many(13)
    plan = _plan(tracks, profile)
    broken = plan.model_dump()
    broken["entries"][5]["play_from_s"] += 0.41

    verdict = critique(type(plan).model_validate(broken), tracks, profile)

    assert any(plan.entries[5].track_id in c.text for c in verdict.complaints)


def test_a_transition_off_the_predecessors_downbeat_is_caught(
    profile: Profile,
) -> None:
    """Die trackuebergreifende Haelfte der Downbeat-Regel.

    Jeder Einstieg sitzt hier auf dem eigenen Raster - Pruefung 3 schweigt.
    Trotzdem faellt der Uebergang neben den Takt des Vorgaengers, und genau
    dort bricht die Beat-Kontinuitaet, die Erfolgskriterium 4 verlangt.
    """
    tracks = many(13)
    plan = _plan(tracks, profile)
    broken = plan.model_dump()
    broken["transitions"][3]["start_at_s"] += 0.37

    verdict = critique(type(plan).model_validate(broken), tracks, profile)

    assert verdict.ok is False
    assert "phase" in _kinds(verdict)
    assert any(
        plan.transitions[3].from_track in c.text
        for c in verdict.complaints
        if c.kind == "phase"
    )


def test_a_butt_join_without_overlap_is_caught(profile: Profile) -> None:
    """Finding 3: gleichfoermig lautes Material liefert kein Outro.

    find_mix_regions dokumentiert das ausdruecklich - ohne erkennbaren Auf- und
    Abbau endet das Intro bei 0.0 und das Outro beginnt am Trackende. Der Plan
    deklariert dann 1 Takt Uebergang, tatsaechlich liegt nichts uebereinander.
    Ohne Beanstandung meldet die QA aus Plan 3 an jeder Naht einen Beatbruch,
    ohne dass jemand sagen kann, woran es lag.
    """
    # Die Laenge ist bewusst kein glattes Vielfaches der Taktlaenge - so faellt
    # eine echte Datei aus, und nur so ist der Rest hinter dem letzten Downbeat
    # das, was er im Betrieb ist: ein Bruchteil eines Takts.
    tracks = [
        make_off_grid(
            f"u{index}", bpm=174.0, duration_s=241.7, intro_end_s=0.0, outro_start_s=241.7
        )
        for index in range(4)
    ]
    plan = _plan(tracks, profile, bars=16)

    verdict = critique(plan, tracks, profile)

    assert verdict.ok is False
    overlap = [c for c in verdict.complaints if c.kind == "overlap"]
    assert len(overlap) == 3
    assert "u0" in overlap[0].text and "u1" in overlap[0].text


def test_only_reorderable_complaints_go_back_to_stage_five(profile: Profile) -> None:
    """Finding 5: eine Laengenbeanstandung darf keine Neuanordnung ausloesen.

    Stufe 5 kann die Reihenfolge aendern - mehr nicht. Ein zu kurzes Set wird
    durch Umsortieren nicht laenger, also waere die Runde verschenkt.
    """
    tracks = many(4)
    plan = _plan(tracks, profile)

    verdict = critique(plan, tracks, profile)

    assert "length" in _kinds(verdict)
    assert not any("Laenge" in text for text in verdict.reorderable)


def test_an_energy_complaint_does_go_back_to_stage_five(profile: Profile) -> None:
    tracks = [
        make_analysis(f"t{i}", camelot=k, energy=e, bpm=174.0)
        for i, (k, e) in enumerate(
            zip(["8A", "8B", "9B", "9A", "10A"], [0.9, 0.8, 0.6, 0.4, 0.2])
        )
    ]
    plan = _plan(tracks, profile)

    verdict = critique(plan, tracks, profile)

    assert any("Energie" in text for text in verdict.reorderable)


def test_a_stretch_outside_the_limit_is_caught(profile: Profile) -> None:
    """Eine ruhende Pruefung, scharfgestellt.

    Der Designer kann kein Verhaeltnis ausserhalb der Grenze erzeugen - er
    setzt in dem Fall auf 1.0 und schneidet. Der Wachposten gilt Plan 3, wo
    handgeschriebene Mixplaene durch dieses Gate laufen. Ein Wachposten, den
    nie jemand ausloest, verrottet, deshalb wird hier ein Plan direkt
    konstruiert statt einen erzeugen zu lassen.
    """
    tracks = many(13)
    plan = _plan(tracks, profile)
    broken = plan.model_dump()
    # Am Vertrag vorbei: MixEntry.timestretch_ratio wuerde 1.20 abweisen. Der
    # Critic sieht im Betrieb nur validierte Plaene, aber seine Pruefung ist
    # trotzdem die zweite Verteidigungslinie und muss ansprechen.
    entry = plan.entries[4]
    broken_entry = MixEntry.model_construct(
        **(entry.model_dump() | {"timestretch_ratio": 1.20})
    )
    patched = type(plan).model_construct(
        target_length_s=plan.target_length_s,
        target_bpm=plan.target_bpm,
        entries=list(plan.entries[:4]) + [broken_entry] + list(plan.entries[5:]),
        transitions=plan.transitions,
    )

    verdict = critique(patched, tracks, profile)

    assert "stretch" in _kinds(verdict)
    assert any(entry.track_id in c.text for c in verdict.complaints if c.kind == "stretch")
    assert broken["entries"][4]["track_id"] == entry.track_id


def test_a_track_without_an_analysis_is_caught(profile: Profile) -> None:
    """Auch das kann der Designer nicht erzeugen - ein handgeschriebener
    Mixplan aus Plan 3 sehr wohl."""
    tracks = many(13)
    plan = _plan(tracks, profile)

    verdict = critique(plan, tracks[:12], profile)

    assert "unknown_track" in _kinds(verdict)
