"""The Mix-Engineer: choose a transition, say why, and drive it.

This is the first role in the space, and it is first for one reason: it is the
only one whose work can be checked the same evening. It drives a transition,
`musik-kritik` reads the recording back, and the two are compared — duration,
phrase position, level curve. Everything before this was wiring.

## What it does and does not decide

The engine deliberately does **not** choose a grip (`crates/control/src/
uebergang.rs`: "Die Anlage wählt nicht aus"). It offers five and refuses to
pick, because the choice depends on the material and on what was played three
transitions ago — and whoever makes it should be able to justify it.

This module makes that choice, and every `Plan` carries the justification and,
just as importantly, **which numbers it trusted**. Hard Rule 6 says a value
from `section`, `intro_beats` or `beats_to_outro` is not verified: the analysis
has never run against real music with heard ground truth. So the plan names
them. A human reading the note afterwards can see exactly what the agent
believed, and whether the belief was the problem.

## The split

`plan()` is pure: a `Situation` goes in, a `Plan` or a `Refusal` comes out. No
I/O, no clock, no engine. Every rule below is therefore testable without a
running mixer, and all of them are tested that way.

`drive()` is the thin async part: read status, plan, issue the calls.

## Hard Rules, and where each one lives

| Rule | Here |
| --- | --- |
| 1 no shell/filesystem | nothing in this module touches either |
| 3 no `set` where `ramp` fits | only `musik_uebergang` and `musik_do` are issued; the crossfader is never set |
| 4 no transition while two decks play | `plan()` refuses before any call is made |
| 5 never rewrite `master.arc` | the arc is read, never written |
| 6 unverified numbers stay marked | `Plan.unverified` |
| 7 never wait longer than a phrase | `plan()` cannot block; `phrase_seconds()` gives the caller its budget |

Rule 4 is also enforced by the engine (`err es laufen mehrere Decks`). Refusing
here as well is not redundant: the engine's answer arrives after a socket round
trip, and the plan is supposed to be the thing that knows better.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Sequence, Union

from ..api import Console, Deck, Situation

__all__ = [
    "Grip",
    "MAX_BLEND_BEATS",
    "Plan",
    "Refusal",
    "drive",
    "phrase_seconds",
    "plan",
    "room_needed",
]


class Grip(str, Enum):
    """The five named transitions. The values are the engine's wire tokens."""

    BLENDE = "blende"
    BASSSWAP = "bassswap"
    SCHNITT = "schnitt"
    FILTER = "filter"
    SCHLEIFE = "schleife"

    def default_beats(self) -> float:
        """Mirrors `Griff::beats` in `crates/control/src/uebergang.rs`."""
        return {
            Grip.BLENDE: 32.0,
            Grip.BASSSWAP: 16.0,
            Grip.SCHNITT: 0.0,
            Grip.FILTER: 16.0,
            Grip.SCHLEIFE: 16.0,
        }[self]


#: One beat between the end of a movement and the outgoing deck stopping.
#: `NACHLAUF` in `crates/control/src/uebergang.rs`, and it is there for a
#: reason: a ramp that ends in the same bar its timing deck stops never gets
#: its last step, and the order sits in the plan forever.
FOLLOW_ON = 1.0

#: A blend longer than this is invisible to the critic, so the planner will not
#: ask for one. Measured, not guessed: over 24 sets, 3 of 4 blends at 64 beats
#: were missed entirely (engine `docs/FAHRPLAN.md`, N3). A transition nobody can
#: check afterwards is not what this space is for.
MAX_BLEND_BEATS = 32.0


def room_needed(grip: Grip, beats: float) -> float:
    """How many beats of the outgoing track a grip consumes, after its start.

    **This mirrors the engine and can drift from it.** The numbers come from
    `zeilen()` in `crates/control/src/uebergang.rs`; if that function changes
    shape, this one is wrong and nothing will say so. `test_room_matches_the_
    engine` writes the mirrored arithmetic out line by line so the drift is at
    least visible in a diff.

    Two grips need no room at all, for different reasons: a cut happens in one
    instant, and a loop *makes* room rather than using it.
    """
    if grip is Grip.SCHNITT:
        return 0.0
    if grip is Grip.SCHLEIFE:
        return 0.0
    if grip is Grip.BASSSWAP:
        # ramp to centre (beats), basses swap over half, ramp across (beats).
        half = max(beats / 2.0, 1.0)
        return beats + half + beats + FOLLOW_ON
    return beats + FOLLOW_ON


@dataclass(frozen=True)
class Refusal:
    """No transition, and the reason.

    A refusal is an answer, not a failure. Three of the four cases below are
    the correct thing to do, and the fourth (nothing loaded) is a job for the
    Track-Wähler, which does not exist yet.
    """

    reason: str

    def __str__(self) -> str:  # pragma: no cover - convenience
        return f"no transition: {self.reason}"


@dataclass(frozen=True)
class Plan:
    """A chosen transition, with its justification and its blind spots."""

    grip: Grip
    beats: float
    outgoing: str
    incoming: str
    reason: str
    #: Which of the numbers behind `reason` have never been checked against
    #: real music. Hard Rule 6. Empty is possible and means the choice rested
    #: only on tempo, position and the transition history.
    unverified: tuple[str, ...] = ()
    calls: tuple[tuple[str, dict], ...] = field(default_factory=tuple)

    def note(self) -> str:
        """One line for the queue entry or the transcript.

        `musik_uebergang` asks for exactly this: the grip is yours, and so is
        the reason — put it where the next person will find it.
        """
        line = f"{self.grip.value} over {self.beats:g} beats: {self.reason}"
        if self.unverified:
            line += " [unverified: " + ", ".join(self.unverified) + "]"
        return line


def phrase_seconds(situation: Situation) -> Optional[float]:
    """How long one phrase lasts on the outgoing deck — Hard Rule 7's budget.

    At 128 BPM sixteen bars are thirty seconds. A caller that has not decided
    by then should let the running track run: a missed transition beats one in
    the wrong place.
    """
    playing = situation.playing()
    if len(playing) != 1:
        return None
    deck = playing[0]
    if not deck.bpm or deck.bpm <= 0 or not deck.phrase_beats:
        return None
    return deck.phrase_beats * 60.0 / deck.bpm


def _entry_needed(incoming: Deck) -> bool:
    """Whether the incoming deck has to jump to its entry point first.

    Every grip starts the incoming deck where its playhead happens to be, and
    `schleife` says so outright: it assumes the incoming sits on a phrase
    boundary. A deck parked at second 0 of a track whose first downbeat is at
    1.4 s enters a beat and a half early, and the whole grip is off.
    """
    if incoming.entry is None:
        return False
    tolerance = 60.0 / incoming.bpm if incoming.bpm else 0.5
    return abs(incoming.position - incoming.entry) > tolerance


def _preference(outgoing: Deck, incoming: Deck) -> list[tuple[Grip, str, tuple[str, ...]]]:
    """Grips in the order the material argues for them.

    Each entry carries its reason and the unverified numbers it leaned on.
    Nothing here is proven right — these are defaults a human would recognise,
    and the point of writing them down is that they can be argued with.
    """
    to_phrase = outgoing.beats_to_phrase or 0.0
    outro = outgoing.beats_to_outro
    intro = incoming.intro_beats

    if outro is not None and outro <= to_phrase and intro is not None and intro >= 32.0:
        return [
            (
                Grip.BLENDE,
                f"the outgoing track is in its outro and {incoming.name} has a "
                f"{intro:g}-beat intro — there is room for two phrases of overlap",
                ("beats_to_outro", "intro_beats"),
            )
        ]

    if intro is not None and intro < 8.0:
        return [
            (
                Grip.BASSSWAP,
                f"{incoming.name} starts almost without an intro ({intro:g} beats); "
                "swapping the basses keeps two kicks from stacking",
                ("intro_beats",),
            )
        ]

    if outro is None:
        return [
            (
                Grip.FILTER,
                "no outro detected on the outgoing track — the filter pulls its "
                "floor away without needing to know where the arrangement ends",
                # The *absence* of an outro is an analysis result too, and just
                # as unchecked as a number would be. A silent detector and a
                # track without an outro look identical from here.
                ("beats_to_outro",),
            )
        ]

    if intro is None:
        reason = f"{outro:g} beats to the outro — an ordinary blend fits"
        used = ("beats_to_outro",)
    else:
        reason = (
            f"{outro:g} beats to the outro and a {intro:g}-beat intro — "
            "an ordinary blend fits"
        )
        used = ("beats_to_outro", "intro_beats")
    return [(Grip.BLENDE, reason, used)]


#: What to reach for when the material's first choice is out — either because
#: it does not fit, or because playing it again would be the third in a row.
#:
#: Two of these are not in `_preference` at all. A cut is here because nothing
#: about the material ever asks for one, but it is the grip that sounds least
#: like any of the others. The loop is here, ahead of the cut, because it is
#: the answer to *no room*: a track with four beats left gets held on the
#: phrase rather than chopped off. Both need no room, so the walk down this
#: list always ends somewhere.
FALLBACK: tuple[Grip, ...] = (
    Grip.FILTER,
    Grip.BASSSWAP,
    Grip.SCHLEIFE,
    Grip.SCHNITT,
    Grip.BLENDE,
)


def _beats_for(grip: Grip) -> float:
    beats = grip.default_beats()
    if grip is Grip.BLENDE:
        return min(beats, MAX_BLEND_BEATS)
    return beats


def _fits(grip: Grip, outgoing: Deck) -> bool:
    """Whether the outgoing track lasts long enough for this grip."""
    left = outgoing.beats_left
    if left is None:
        # Without a beat grid there is no arithmetic to do. The engine will
        # still refuse or not; this side does not invent a number.
        return True
    to_phrase = outgoing.beats_to_phrase or 0.0
    return to_phrase + room_needed(grip, _beats_for(grip)) <= left


def plan(situation: Situation) -> Union[Plan, Refusal]:
    """Choose the next transition. Pure — no engine, no clock.

    The order is: who is going out, is there room, what does the material
    argue for, and would that be the third of its kind in a row.
    """
    playing = situation.playing()
    if not playing:
        return Refusal("no deck is playing")
    if len(playing) > 1:
        # Hard Rule 4. Which of the two is going out cannot be guessed, and
        # guessing wrong means fading up the track that was already leaving.
        names = ", ".join(d.name for d in playing)
        return Refusal(f"two decks are playing ({names}) — the transition is already under way")

    outgoing = playing[0]
    candidates = [d for d in situation.decks if d.name != outgoing.name and d.loaded]
    if not candidates:
        return Refusal("nothing is loaded on the other deck")
    incoming = candidates[0]

    left = outgoing.beats_left
    to_phrase = outgoing.beats_to_phrase or 0.0
    if left is not None and to_phrase >= left:
        # Even a cut happens on the next phrase boundary, and that boundary is
        # past the end of the track. There is nothing left to transition from.
        return Refusal(
            f"{outgoing.name} ends in {left:g} beats, before the next phrase in {to_phrase:g}"
        )

    ranked = _preference(outgoing, incoming)
    last = situation.master.transitions[-1] if situation.master.transitions else None
    repeats = situation.master.repeats

    ordered: list[tuple[Grip, str, tuple[str, ...]]] = list(ranked)
    seen = {g for g, _, _ in ordered}
    for grip in FALLBACK:
        if grip not in seen:
            ordered.append((grip, "", ()))
            seen.add(grip)

    def repetitive(grip: Grip) -> bool:
        """Would this grip be the third of its kind in a row?

        The engine counts the run and its own catalogue draws the line: "Ab 3
        wiederholt sich jemand hörbar." The one to avoid is the third, so the
        turn has to happen at two.
        """
        return last == grip.value and repeats >= 2

    # Why the material's first choice was dropped, if it was. This is what the
    # reason has to say — "a blend does not fit" and "a third blend in a row"
    # are different sentences, and a human reading the transcript later needs
    # the right one.
    top = ordered[0][0]
    dropped: list[str] = []
    if not _fits(top, outgoing):
        need = room_needed(top, _beats_for(top))
        have = "unknown" if left is None else f"{left - to_phrase:g}"
        dropped.append(f"{top.value} needs {need:g} beats after the phrase and {have} are left")
    if repetitive(top):
        dropped.append(f"a third {top.value} in a row would be audible")

    chosen: Optional[tuple[Grip, str, tuple[str, ...]]] = None
    for grip, reason, unverified in ordered:
        if not _fits(grip, outgoing) or repetitive(grip):
            continue
        chosen = (grip, reason, unverified)
        break

    if chosen is None:
        # Unreachable as the list stands: the loop and the cut both need no
        # room, and restraint can only rule out one grip. Kept because it is
        # cheap, and because a future sixth grip could make it reachable.
        return Refusal("no grip in the repertoire fits this situation")

    grip, reason, unverified = chosen
    if not reason:
        because = "; ".join(dropped) if dropped else "nothing in the material argued for a grip"
        reason = f"{because} — {grip.value} is the next one that fits"
    return _build(grip, outgoing, incoming, reason, unverified)


def _build(
    grip: Grip,
    outgoing: Deck,
    incoming: Deck,
    reason: str,
    unverified: Sequence[str],
) -> Plan:
    beats = _beats_for(grip)
    calls: list[tuple[str, dict]] = []
    if _entry_needed(incoming):
        calls.append(("musik_do", {"aktion": f"{incoming.name}.jump_entry"}))
    calls.append(("musik_uebergang", {"griff": grip.value, "beats": beats}))
    return Plan(
        grip=grip,
        beats=beats,
        outgoing=outgoing.name,
        incoming=incoming.name,
        reason=reason,
        unverified=tuple(unverified),
        calls=tuple(calls),
    )


async def drive(console: Console) -> Union[Plan, Refusal]:
    """Read the situation, plan, and issue the calls.

    Everything that can go wrong on the way — a refused approval, an engine
    that is not running — raises out of `api`, unchanged. Swallowing those
    here would leave a caller believing a transition is under way when
    nothing was sent.
    """
    decision = plan(await console.status())
    if isinstance(decision, Refusal):
        return decision
    for name, args in decision.calls:
        if name == "musik_do":
            await console.do(args["aktion"])
        elif name == "musik_uebergang":
            await console.transition(args["griff"], args["beats"])
        else:  # pragma: no cover - the planner emits no other call
            raise ValueError(f"the planner produced an unknown call: {name}")
    return decision
