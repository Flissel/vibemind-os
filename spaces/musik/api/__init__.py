"""Thin shell over the sixteen MCP tools of the music engine.

This package adds **no capability**. Every method here is one MCP call, with
the arguments named and the answer parsed. Anything that cannot be said over
the socket cannot be said here either — see `docs/SPACE.md` in the engine
repo, "Kein neues Werkzeug für den Space".

Why it exists at all: the engine answers in a line protocol, and
`musik_status` answers in JSON with about forty fields per deck. An agent that
re-parses that at every call re-parses it slightly differently each time. The
parsing lives here once, and the Mix-Engineer works on `Situation`.

The tool caller is injected. In production it is the agent runtime's MCP
client; in tests it is a dict. That keeps this package free of an MCP
dependency, which is what lets it run in the repo's dependency-light
`root-checks` job.

Calling convention, measured against the bridge's own client test
(`mcp/musik/tests/test_server.py`): every tool takes a single object argument
named `params`, so a call is `call("musik_status", {"response_format":
"json"})` and the caller wraps it in `{"params": …}` — or, more simply, this
module does the wrapping and the injected callable receives the inner dict.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional, Sequence

__all__ = [
    "Console",
    "Deck",
    "EngineError",
    "Master",
    "Refused",
    "Situation",
    "ToolCall",
]

#: What the injected caller looks like: tool name, the inner `params` object,
#: and the tool's answer as text.
ToolCall = Callable[[str, dict], Awaitable[str]]


class Refused(RuntimeError):
    """The approval gate said no.

    Not a fault. It means the class was not granted, or the window has run
    out. The message repeats the engine's answer verbatim, including the way
    to allow it.
    """


class EngineError(RuntimeError):
    """The engine refused, or is not reachable.

    Covers `Fehler: …` from the bridge and any `err …` line from the protocol.
    Some of these are answers rather than faults — `err es laufen mehrere
    Decks` is the engine enforcing Hard Rule 4.
    """


def _check(answer: str) -> str:
    """Raise on a refusal or an error, otherwise hand the answer back."""
    if answer.startswith("freigabe verweigert"):
        raise Refused(answer)
    if answer.startswith("Fehler:"):
        raise EngineError(answer)
    for line in answer.splitlines():
        if line.startswith("err "):
            raise EngineError(answer)
    return answer


def _number(value: Any) -> Optional[float]:
    return float(value) if isinstance(value, (int, float)) else None


def _text(value: Any) -> str:
    return value if isinstance(value, str) else ""


@dataclass(frozen=True)
class Deck:
    """One deck, as `musik_status` reports it.

    Only the fields a transition needs. `None` means the engine had nothing to
    say — an unloaded deck has no tempo, and a track without a detected outro
    has no `beats_to_outro`. `None` is not zero, and the distinction matters:
    zero beats to the outro means *now*.
    """

    name: str
    title: str
    bpm: Optional[float]
    playing: bool
    finished: bool
    position: float
    duration: float
    entry: Optional[float]
    beats_left: Optional[float]
    beats_to_phrase: Optional[float]
    phrase_beats: Optional[float]
    section: Optional[str]
    section_beats_left: Optional[float]
    beats_to_outro: Optional[float]
    intro_beats: Optional[float]
    load_status: str

    @property
    def loaded(self) -> bool:
        """Something usable lies on this deck.

        Mirrors what the engine's `uebergang::paar` asks — it takes any deck
        with `frames > 0` — and adds the two states where a duration exists
        but the track does not: a load still running, and a load that failed.

        `grid nicht gespeichert: …` is deliberately *not* excluded. That is a
        failed **save**; the track is loaded and plays fine.
        """
        if self.duration <= 0:
            return False
        return not (
            self.load_status.startswith("laedt") or self.load_status.startswith("fehler")
        )

    @classmethod
    def from_json(cls, raw: dict) -> "Deck":
        return cls(
            name=_text(raw.get("deck")),
            title=_text(raw.get("title")),
            bpm=_number(raw.get("bpm")),
            playing=bool(raw.get("playing")),
            finished=bool(raw.get("finished")),
            position=_number(raw.get("position")) or 0.0,
            duration=_number(raw.get("duration")) or 0.0,
            entry=_number(raw.get("entry")),
            beats_left=_number(raw.get("beats_left")),
            beats_to_phrase=_number(raw.get("beats_to_phrase")),
            phrase_beats=_number(raw.get("phrase_beats")),
            section=raw.get("section") if isinstance(raw.get("section"), str) else None,
            section_beats_left=_number(raw.get("section_beats_left")),
            beats_to_outro=_number(raw.get("beats_to_outro")),
            intro_beats=_number(raw.get("intro_beats")),
            load_status=_text(raw.get("load_status")),
        )


@dataclass(frozen=True)
class Master:
    """The master section, as far as a transition cares."""

    crossfader: float
    recording: bool
    record_dropped: float
    #: How many of the last transitions in a row were the same grip. The
    #: engine's catalogue: "Ab 3 wiederholt sich jemand hörbar."
    repeats: int
    #: The last transitions, oldest first. Empty when none has completed —
    #: which also happens when somebody mixes on the channel faders alone.
    transitions: tuple[str, ...]
    arc_target: Optional[float]
    arc_actual: Optional[float]
    arc_gap: Optional[float]
    room: Optional[str]

    @classmethod
    def from_json(cls, raw: dict) -> "Master":
        history = raw.get("transitions")
        parts: tuple[str, ...] = ()
        if isinstance(history, str) and history.strip():
            parts = tuple(p.strip() for p in history.split(",") if p.strip())
        return cls(
            crossfader=_number(raw.get("crossfader")) or 0.0,
            recording=bool(raw.get("recording")),
            record_dropped=_number(raw.get("record_dropped")) or 0.0,
            repeats=int(_number(raw.get("repeats")) or 0),
            transitions=parts,
            arc_target=_number(raw.get("arc_target")),
            arc_actual=_number(raw.get("arc_actual")),
            arc_gap=_number(raw.get("arc_gap")),
            room=raw.get("room") if isinstance(raw.get("room"), str) else None,
        )


@dataclass(frozen=True)
class Situation:
    """One `musik_status` answer, parsed.

    A snapshot and nothing more. By the time a decision made from it reaches
    the engine, the beat has moved on — which is why every grip schedules
    itself on the *next phrase boundary* rather than on a beat this side
    counted.
    """

    decks: tuple[Deck, ...]
    master: Master

    @classmethod
    def from_json(cls, text: str) -> "Situation":
        raw = json.loads(text)
        decks = tuple(Deck.from_json(d) for d in raw.get("decks", []))
        return cls(decks=decks, master=Master.from_json(raw.get("master", {})))

    def playing(self) -> tuple[Deck, ...]:
        return tuple(d for d in self.decks if d.playing)

    def deck(self, name: str) -> Optional[Deck]:
        for d in self.decks:
            if d.name == name:
                return d
        return None


class Console:
    """The engine, reachable through the injected MCP caller.

    Async because every real MCP client is. The decisions are not made here —
    they are made in `agents.mix_engineer.plan`, which is pure and sync, and
    therefore testable without any of this.
    """

    def __init__(self, call: ToolCall) -> None:
        self._call = call

    async def _tool(self, name: str, **params: Any) -> str:
        return _check(await self._call(name, {k: v for k, v in params.items() if v is not None}))

    async def status(self) -> Situation:
        return Situation.from_json(await self._tool("musik_status", response_format="json"))

    async def get(self, control: str) -> str:
        return await self._tool("musik_get", control=control)

    async def do(self, action: str, argument: Optional[str] = None) -> str:
        return await self._tool("musik_do", aktion=action, argument=argument)

    async def transition(self, grip: str, beats: Optional[float] = None) -> str:
        return await self._tool("musik_uebergang", griff=grip, beats=beats)

    async def ramp(
        self,
        control: str,
        target: float,
        over_beats: float,
        *,
        in_beats: Optional[float] = None,
        from_phrase: Optional[bool] = None,
        shape: Optional[str] = None,
        timing_deck: Optional[str] = None,
    ) -> str:
        """A movement instead of a jump — Hard Rule 3.

        The keyword names follow `RampeEingabe` in the bridge: `ziel`,
        `ueber_beats`, `in_beats`, `ab_phrase`, `form`, `taktgeber_deck`.
        """
        return await self._tool(
            "musik_ramp",
            control=control,
            ziel=target,
            ueber_beats=over_beats,
            in_beats=in_beats,
            ab_phrase=from_phrase,
            form=shape,
            taktgeber_deck=timing_deck,
        )

    async def queue_add(self, path: str, note: str = "", *, next_up: bool = False) -> str:
        """Put a track in the queue, with the reason it is there.

        The note is not decoration. `musik_uebergang` says it outright: the
        choice of grip belongs to whoever can justify it, and the
        justification goes on the queue entry.
        """
        return await self._tool(
            "musik_queue_add", pfad=path, notiz=note, als_naechstes=next_up or None
        )

    async def cancel(self, plan_id: int) -> str:
        return await self._tool("musik_cancel", plan_id=plan_id)


def sequence(calls: Sequence[tuple[str, dict]]) -> str:
    """Render a plan's calls the way they read in a note. Test and log helper."""
    return "; ".join(
        name + "(" + ", ".join(f"{k}={v}" for k, v in args.items()) + ")" for name, args in calls
    )
