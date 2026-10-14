"""What the shell over the MCP tools must get right.

Three things, and each of them has bitten this project somewhere else:

1. **`None` is not zero.** A track without a detected outro and a track whose
   outro starts right now report the same thing to a careless parser.
2. **A refusal is not an error.** `freigabe verweigert` means the gate did its
   job; an agent that treats it as a crash learns nothing from it.
3. **The parameter names are the engine's, not ours.** They were read out of
   the bridge's input models, and a typo here is invisible until a live set.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO_ROOT))

from spaces.musik.api import (  # noqa: E402
    Console,
    Deck,
    EngineError,
    Refused,
    Situation,
)


def status_json(**overrides) -> str:
    """A `musik_status` answer in the shape the bridge actually produces.

    Copied from `_status()` in `mcp/musik/server.py`, trimmed to the fields
    this space reads. The point of the fixture is the *shape*: nulls where the
    engine reports nothing, strings where it reports text.
    """
    deck1 = {
        "deck": "deck1",
        "title": "Erster",
        "artist": "A",
        "bpm": 128.0,
        "position": 60.0,
        "duration": 300.0,
        "beat": 128.0,
        "beats_left": 480.0,
        "beats_to_phrase": 4.0,
        "phrase_beats": 16.0,
        "section": "main",
        "section_beats_left": 64.0,
        "beats_to_outro": 96.0,
        "intro_beats": 32.0,
        "entry": 0.5,
        "playing": True,
        "finished": False,
        "load_status": "bereit",
        "stems": [],
    }
    deck2 = dict(deck1, deck="deck2", title="Zweiter", playing=False, position=0.0)
    data = {
        "decks": [deck1, deck2],
        "channels": [],
        "master": {
            "crossfader": -1.0,
            "recording": True,
            "record_seconds": 12.0,
            "record_dropped": 0.0,
            "repeats": 1,
            "transitions": "blende, schnitt",
            "arc_target": 0.6,
            "arc_actual": 0.55,
            "arc_gap": -0.05,
            "room": None,
        },
        "plan": [],
        "queue": {"count": 0, "next": None},
    }
    for key, value in overrides.items():
        if key.startswith("deck1_"):
            deck1[key[6:]] = value
        elif key.startswith("deck2_"):
            deck2[key[6:]] = value
        else:
            data["master"][key] = value
    return json.dumps(data)


def test_a_missing_number_stays_missing():
    """`None` must survive the parse, because zero means something else.

    `beats_to_outro` is the one that matters: `null` says the analysis found
    no outro, `0` says the outro starts on this beat. A parser that folds them
    together turns "I do not know" into "now".
    """
    lage = Situation.from_json(status_json(deck1_beats_to_outro=None, deck1_intro_beats=None))
    deck = lage.deck("deck1")
    assert deck is not None
    assert deck.beats_to_outro is None
    assert deck.intro_beats is None

    lage = Situation.from_json(status_json(deck1_beats_to_outro=0.0))
    assert lage.deck("deck1").beats_to_outro == 0.0


def test_the_transition_history_becomes_a_list():
    lage = Situation.from_json(status_json())
    assert lage.master.transitions == ("blende", "schnitt")
    assert lage.master.repeats == 1

    # Empty is the normal state at the start of a set, and also whenever
    # somebody mixes on the channel faders alone.
    leer = Situation.from_json(status_json(transitions=None))
    assert leer.master.transitions == ()


def test_only_a_real_load_counts_as_loaded():
    """Three states share a duration and are not the same thing.

    `bereit` is loaded. `laedt` is a load still running — starting that deck
    plays silence. `fehler: …` is a load that failed. And `grid nicht
    gespeichert: …` is a failed *save*: the track is there and plays fine, so
    it must stay usable.
    """

    def deck(status: str, duration: float = 300.0) -> Deck:
        raw = json.loads(status_json())["decks"][1]
        raw["load_status"] = status
        raw["duration"] = duration
        return Deck.from_json(raw)

    assert deck("bereit").loaded
    assert deck("grid nicht gespeichert: disk voll").loaded
    assert not deck("laedt").loaded
    assert not deck("fehler: Ladeschlange voll").loaded
    assert not deck("bereit", duration=0.0).loaded


def test_a_refusal_is_told_apart_from_a_fault():
    """The gate, an unreachable engine and a refusing engine are three answers."""

    async def gate(_name, _args):
        return (
            "freigabe verweigert für: do master.uebergang blende\n"
            "  Klasse:  zeit\n"
            "  Grund:   keine Freigabe-Datei\n"
            "  Erlaubt: `klassen` in MUSIK_FREIGABE_DATEI um zeit ergänzen"
        )

    async def broken(_name, _args):
        return "Fehler: Musik ist unter /run/user/1000/musik.sock nicht erreichbar"

    async def refused_by_engine(_name, _args):
        return "err es laufen mehrere Decks — dann sind die Zeilen von Hand zu setzen"

    with pytest.raises(Refused):
        asyncio.run(Console(gate).transition("blende"))
    with pytest.raises(EngineError):
        asyncio.run(Console(broken).status())
    with pytest.raises(EngineError):
        asyncio.run(Console(refused_by_engine).transition("blende"))


def test_the_parameter_names_are_the_engines():
    """Read out of the bridge's input models, not invented here.

    `RampeEingabe` calls them `ziel`, `ueber_beats`, `form`, `taktgeber_deck`;
    `StreichEingabe` calls it `plan_id`; `StatusEingabe` calls it
    `response_format`. Getting one wrong fails at the pydantic boundary during
    a set, which is the worst possible moment to find out.
    """
    seen: list[tuple[str, dict]] = []

    async def record(name, args):
        seen.append((name, args))
        return status_json() if name == "musik_status" else "ok"

    console = Console(record)
    asyncio.run(console.status())
    asyncio.run(console.transition("blende", 32.0))
    asyncio.run(console.ramp("master.crossfader", 1.0, 32.0, shape="weich", timing_deck="deck1"))
    asyncio.run(console.cancel(7))
    asyncio.run(console.queue_add("/musik/a.mp3", "harmonisch zu 8A"))

    assert seen[0] == ("musik_status", {"response_format": "json"})
    assert seen[1] == ("musik_uebergang", {"griff": "blende", "beats": 32.0})
    assert seen[2] == (
        "musik_ramp",
        {
            "control": "master.crossfader",
            "ziel": 1.0,
            "ueber_beats": 32.0,
            "form": "weich",
            "taktgeber_deck": "deck1",
        },
    )
    assert seen[3] == ("musik_cancel", {"plan_id": 7})
    assert seen[4] == ("musik_queue_add", {"pfad": "/musik/a.mp3", "notiz": "harmonisch zu 8A"})


def test_unset_optionals_are_left_out_entirely():
    """A `None` is worse than absent.

    The bridge's models default these; sending `null` would override a default
    with nothing rather than leaving it alone.
    """
    seen: list[dict] = []

    async def record(_name, args):
        seen.append(args)
        return "ok"

    asyncio.run(Console(record).transition("schnitt"))
    assert seen[0] == {"griff": "schnitt"}
