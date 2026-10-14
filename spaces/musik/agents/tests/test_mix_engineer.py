"""What the Mix-Engineer must get right, checked without an engine.

`plan()` is pure, so every rule below is a function call and an assertion. That
is the whole reason the module is split the way it is: the decisions are the
part that can be wrong in a way nobody notices, and they are also the part that
is cheapest to test.

What these tests do **not** show is that the choices are musically good. They
show that the choice follows from the numbers and that the reason says which
numbers. Whether the numbers themselves mean anything is Hard Rule 6, and the
answer today is: not yet.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO_ROOT))

from spaces.musik.agents.mix_engineer import (  # noqa: E402
    MAX_BLEND_BEATS,
    Grip,
    Plan,
    Refusal,
    drive,
    phrase_seconds,
    plan,
    room_needed,
)
from spaces.musik.api import Console, Deck, Master, Situation  # noqa: E402


def deck(name: str, **kw) -> Deck:
    """A deck with a plausible middle-of-the-track state.

    Defaults chosen so that nothing is a special case: a 128 BPM track with
    plenty left, four beats to the next phrase, an outro far off, and the
    playhead exactly on the entry point so no jump is needed.
    """
    base = dict(
        name=name,
        title=name.upper(),
        bpm=128.0,
        playing=False,
        finished=False,
        position=0.5,
        duration=300.0,
        entry=0.5,
        beats_left=480.0,
        beats_to_phrase=4.0,
        phrase_beats=16.0,
        section="main",
        section_beats_left=64.0,
        beats_to_outro=96.0,
        intro_beats=32.0,
        load_status="bereit",
    )
    base.update(kw)
    return Deck(**base)


def situation(*decks: Deck, repeats: int = 0, transitions: tuple = ()) -> Situation:
    return Situation(
        decks=tuple(decks),
        master=Master(
            crossfader=-1.0,
            recording=True,
            record_dropped=0.0,
            repeats=repeats,
            transitions=transitions,
            arc_target=None,
            arc_actual=None,
            arc_gap=None,
            room=None,
        ),
    )


# ── Refusals ─────────────────────────────────────────────────────────────


def test_nothing_playing_is_not_a_transition():
    got = plan(situation(deck("deck1"), deck("deck2")))
    assert isinstance(got, Refusal)
    assert "no deck is playing" in got.reason


def test_two_decks_playing_is_refused_before_anything_is_sent():
    """Hard Rule 4, and the engine says the same thing.

    Refusing here as well is not redundant. The engine's `err es laufen
    mehrere Decks` arrives after a round trip; the plan is meant to be the
    thing that already knew.
    """
    got = plan(situation(deck("deck1", playing=True), deck("deck2", playing=True)))
    assert isinstance(got, Refusal)
    assert "two decks are playing" in got.reason
    assert "deck1, deck2" in got.reason


def test_an_empty_other_deck_is_the_track_waehlers_job():
    got = plan(situation(deck("deck1", playing=True), deck("deck2", duration=0.0)))
    assert isinstance(got, Refusal)
    assert "nothing is loaded" in got.reason


def test_a_deck_that_is_still_loading_is_not_a_candidate():
    """A load in flight has a duration and plays silence.

    This is the one that would look like a working transition right up to the
    moment the crossfader arrives on an empty channel.
    """
    got = plan(situation(deck("deck1", playing=True), deck("deck2", load_status="laedt")))
    assert isinstance(got, Refusal)
    assert "nothing is loaded" in got.reason


def test_a_track_that_ends_before_the_next_phrase_gets_nothing():
    """Every grip starts on the next phrase boundary — even a cut.

    If that boundary lies past the end of the track, there is nothing to
    transition from, and pretending otherwise schedules an order that the
    engine will drop when the deck runs out.
    """
    got = plan(
        situation(
            deck("deck1", playing=True, beats_left=3.0, beats_to_phrase=8.0),
            deck("deck2"),
        )
    )
    assert isinstance(got, Refusal)
    assert "before the next phrase" in got.reason


# ── The choice ───────────────────────────────────────────────────────────


def test_an_outro_meeting_a_long_intro_gets_a_blend():
    got = plan(
        situation(
            deck("deck1", playing=True, beats_to_outro=0.0, beats_left=200.0),
            deck("deck2", intro_beats=64.0),
        )
    )
    assert isinstance(got, Plan)
    assert got.grip is Grip.BLENDE
    assert got.beats == 32.0
    assert got.outgoing == "deck1" and got.incoming == "deck2"
    assert "outro" in got.reason and "64-beat intro" in got.reason


def test_a_track_that_starts_cold_gets_a_bassswap():
    """No intro means the arrangement is there from bar one.

    Laying that over a running track stacks two kicks. The bass swap is the
    grip that overlaps without doing so.
    """
    got = plan(situation(deck("deck1", playing=True), deck("deck2", intro_beats=4.0)))
    assert isinstance(got, Plan)
    assert got.grip is Grip.BASSSWAP
    assert got.beats == 16.0
    assert "without an intro" in got.reason


def test_no_detected_outro_gets_a_filter():
    """The honest answer to a silent detector.

    The filter needs to know nothing about where the arrangement ends — it
    takes the floor away and the incoming track supplies a new one.
    """
    got = plan(
        situation(deck("deck1", playing=True, beats_to_outro=None), deck("deck2", intro_beats=16.0))
    )
    assert isinstance(got, Plan)
    assert got.grip is Grip.FILTER
    assert "no outro detected" in got.reason


def test_the_third_identical_grip_is_avoided():
    """`repeats` is the engine's own measure of a DJ repeating themselves.

    Its catalogue draws the line at three, so the turn has to happen at two:
    the grip to avoid is the one that would *become* the third.
    """
    lage = situation(
        deck("deck1", playing=True, beats_to_outro=0.0, beats_left=200.0),
        deck("deck2", intro_beats=64.0),
        repeats=2,
        transitions=("schnitt", "blende", "blende"),
    )
    got = plan(lage)
    assert isinstance(got, Plan)
    assert got.grip is not Grip.BLENDE
    assert "a third blende in a row would be audible" in got.reason

    # One repeat is not two: the material still wins.
    assert plan(
        situation(
            deck("deck1", playing=True, beats_to_outro=0.0, beats_left=200.0),
            deck("deck2", intro_beats=64.0),
            repeats=1,
            transitions=("schnitt", "blende"),
        )
    ).grip is Grip.BLENDE


def test_too_little_room_falls_to_the_loop():
    """The loop is the only grip that makes room instead of using it.

    A filter needs seventeen beats after the phrase boundary. With sixteen
    left, holding the outgoing track on the phrase is what a person would do.
    """
    got = plan(
        situation(
            deck("deck1", playing=True, beats_to_outro=None, beats_left=20.0, beats_to_phrase=4.0),
            deck("deck2", intro_beats=16.0),
        )
    )
    assert isinstance(got, Plan)
    assert got.grip is Grip.SCHLEIFE
    assert "filter needs 17 beats" in got.reason
    assert "16 are left" in got.reason


def test_a_cut_is_what_is_left_when_a_third_loop_would_show():
    """Two loops in a row happen; three are a tic.

    Track after track ending short is a real situation, and it is exactly the
    one where an automat starts sounding like an automat. With no room for
    anything longer and the loop ruled out, the cut is the answer.
    """
    got = plan(
        situation(
            deck("deck1", playing=True, beats_to_outro=None, beats_left=20.0, beats_to_phrase=4.0),
            deck("deck2", intro_beats=16.0),
            repeats=2,
            transitions=("schleife", "schleife"),
        )
    )
    assert isinstance(got, Plan)
    assert got.grip is Grip.SCHNITT
    assert got.beats == 0.0


# ── What the plan says about itself ──────────────────────────────────────


def test_the_plan_names_the_numbers_it_could_not_verify():
    """Hard Rule 6, made visible instead of assumed.

    Every choice above rests on `section`, `intro_beats` or `beats_to_outro`,
    and none of those has ever been checked against real music with heard
    ground truth. The note carries that, so a human reading the transcript
    afterwards can see what the agent believed.
    """
    got = plan(
        situation(
            deck("deck1", playing=True, beats_to_outro=0.0, beats_left=200.0),
            deck("deck2", intro_beats=64.0),
        )
    )
    assert got.unverified == ("beats_to_outro", "intro_beats")
    assert "[unverified: beats_to_outro, intro_beats]" in got.note()
    assert got.note().startswith("blende over 32 beats:")

    # The absence of an outro is an analysis result too.
    still = plan(
        situation(deck("deck1", playing=True, beats_to_outro=None), deck("deck2", intro_beats=16.0))
    )
    assert still.unverified == ("beats_to_outro",)


def test_a_blend_is_never_longer_than_the_critic_can_see():
    """N3: three of four blends at 64 beats were missed entirely.

    A transition the critic cannot find afterwards cannot be checked, and
    checking it is the whole point of this space. The engine's own default is
    32 today, so this cap is a fence around a future change, not a change.
    """
    assert MAX_BLEND_BEATS == 32.0
    got = plan(
        situation(
            deck("deck1", playing=True, beats_to_outro=0.0, beats_left=200.0),
            deck("deck2", intro_beats=64.0),
        )
    )
    assert got.beats <= MAX_BLEND_BEATS


# ── The calls ────────────────────────────────────────────────────────────


def test_the_incoming_deck_jumps_to_its_entry_first():
    """A deck parked at second 0 enters before its first downbeat.

    `schleife` says so outright — it assumes the incoming track sits on a
    phrase boundary — and the other grips are no different, they just fail
    less obviously.
    """
    got = plan(
        situation(deck("deck1", playing=True), deck("deck2", position=0.0, entry=1.4, intro_beats=4.0))
    )
    assert got.calls[0] == ("musik_do", {"aktion": "deck2.jump_entry"})
    assert got.calls[1] == ("musik_uebergang", {"griff": "bassswap", "beats": 16.0})

    # Already on the entry point: nothing to do, and one call fewer to go wrong.
    steht = plan(
        situation(deck("deck1", playing=True), deck("deck2", position=0.5, entry=0.5, intro_beats=4.0))
    )
    assert len(steht.calls) == 1
    assert steht.calls[0][0] == "musik_uebergang"


def test_the_crossfader_is_never_set():
    """Hard Rule 3, across every branch the planner has.

    A jump on the crossfader is audible. The repertoire exists so that nobody
    has to reach for `musik_set`, and this asserts nobody did.
    """
    lagen = [
        situation(
            deck("deck1", playing=True, beats_to_outro=0.0, beats_left=200.0),
            deck("deck2", intro_beats=64.0),
        ),
        situation(deck("deck1", playing=True), deck("deck2", intro_beats=4.0)),
        situation(deck("deck1", playing=True, beats_to_outro=None), deck("deck2")),
        situation(
            deck("deck1", playing=True, beats_to_outro=None, beats_left=20.0),
            deck("deck2"),
        ),
    ]
    for lage in lagen:
        got = plan(lage)
        assert isinstance(got, Plan), got
        for name, args in got.calls:
            assert name in ("musik_do", "musik_uebergang"), name
            assert "crossfader" not in repr(args)


def test_room_matches_the_engine():
    """The mirrored arithmetic, written out so drift shows up in a diff.

    Source: `zeilen()` and `NACHLAUF` in `crates/control/src/uebergang.rs`.
    Nothing checks this automatically — the engine lives in another repo — so
    the numbers are spelled out here rather than derived.
    """
    assert room_needed(Grip.BLENDE, 32.0) == 33.0  # ramp + one beat of follow-on
    assert room_needed(Grip.FILTER, 16.0) == 17.0  # same shape
    assert room_needed(Grip.SCHNITT, 0.0) == 0.0  # one instant
    assert room_needed(Grip.SCHLEIFE, 16.0) == 0.0  # makes room, does not use it
    # to the centre, basses over half, then across, then the follow-on
    assert room_needed(Grip.BASSSWAP, 16.0) == 16.0 + 8.0 + 16.0 + 1.0
    # `halb` is floored at one beat in the engine, so a one-beat swap is 4.
    assert room_needed(Grip.BASSSWAP, 1.0) == 4.0


def test_phrase_seconds_is_the_budget_for_hard_rule_7():
    """Sixteen beats at 128 BPM are seven and a half seconds.

    Whoever calls this agent has that long to decide before letting the
    running track run. Two decks playing has no single answer, and saying so
    is better than picking one.
    """
    lage = situation(deck("deck1", playing=True), deck("deck2"))
    assert phrase_seconds(lage) == 7.5
    assert phrase_seconds(situation(deck("deck1"), deck("deck2"))) is None
    assert phrase_seconds(situation(deck("deck1", playing=True, bpm=None), deck("deck2"))) is None


def test_drive_issues_the_planned_calls_and_nothing_else():
    seen: list[tuple[str, dict]] = []
    answers = {
        "musik_status": None,  # filled in below
        "musik_do": "ok deck2.jump_entry",
        "musik_uebergang": "ok plan 1 …\nok plan 2 …\nok plan 3 …",
    }

    import json

    lage = situation(
        deck("deck1", playing=True),
        deck("deck2", position=0.0, entry=1.4, intro_beats=4.0),
    )
    answers["musik_status"] = json.dumps(
        {
            "decks": [
                {
                    "deck": d.name,
                    "title": d.title,
                    "bpm": d.bpm,
                    "playing": d.playing,
                    "finished": d.finished,
                    "position": d.position,
                    "duration": d.duration,
                    "entry": d.entry,
                    "beats_left": d.beats_left,
                    "beats_to_phrase": d.beats_to_phrase,
                    "phrase_beats": d.phrase_beats,
                    "section": d.section,
                    "section_beats_left": d.section_beats_left,
                    "beats_to_outro": d.beats_to_outro,
                    "intro_beats": d.intro_beats,
                    "load_status": d.load_status,
                }
                for d in lage.decks
            ],
            "master": {"crossfader": -1.0, "recording": True, "repeats": 0, "transitions": None},
        }
    )

    async def call(name, args):
        seen.append((name, args))
        return answers[name]

    got = asyncio.run(drive(Console(call)))
    assert isinstance(got, Plan)
    assert got.grip is Grip.BASSSWAP
    assert [name for name, _ in seen] == ["musik_status", "musik_do", "musik_uebergang"]
    assert seen[1][1] == {"aktion": "deck2.jump_entry"}
    assert seen[2][1] == {"griff": "bassswap", "beats": 16.0}


def test_drive_sends_nothing_when_the_plan_refuses():
    """A refusal has to be silent on the wire.

    Sending a transition and then reporting a refusal would be the worst of
    both: the engine acts, the caller thinks it did not.
    """
    seen: list[str] = []

    async def call(name, _args):
        seen.append(name)
        return '{"decks": [], "master": {}}'

    got = asyncio.run(drive(Console(call)))
    assert isinstance(got, Refusal)
    assert seen == ["musik_status"]
