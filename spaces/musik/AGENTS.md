# Musik-Space — Agent + Developer Guide

Entry point for any agent (or human) touching `spaces/musik/`. Read **Hard
Rules** first; they are the reason the rest is safe.

The engine lives in a separate repo ([Flissel/Musik][engine]) and carries its
own specs: `docs/FREIGABE.md` (the gate), `docs/SPACE.md` (this space),
`docs/GENERATOR.md`, `docs/STEUERUNG.md` (the protocol).

[engine]: https://github.com/Flissel/Musik

---

## Hard Rules — read before touching ANY file under spaces/musik/

1. **Never enable a shell or filesystem tool in this space.** The approval gate
   sits in the MCP bridge. An agent that can run commands or write files goes
   around it — including writing the approval file itself. This rule is the
   precondition that makes rules 2–7 mean anything.
2. **Never put `erzeugen` in the approval file.** It is refused there by
   construction (`test_9_erzeugen_laeuft_nie_ueber_die_datei`), and the refusal
   must stay. Generation costs money per call and needs a per-call decision.
3. **Never use `musik_set` where `musik_ramp` fits.** A jump on the crossfader
   is audible. The repertoire (`musik_uebergang`) exists for this.
4. **Never start a transition while two decks are playing.** The engine refuses
   it — `err es laufen mehrere Decks` — and that is the answer, not a fault.
   Stop the outgoing deck first or pick the deck explicitly.
5. **Never rewrite `master.arc` mid-set.** The arc is what somebody intended;
   changing it afterwards makes the comparison between plan and evening
   worthless. The room (`master.room`) is the sanctioned way to bend the target.
6. **Never treat a number from `section`, `energie` or `bpm` as verified.** The
   analysis has only ever run against material the project built itself. Under
   70 BPM the detector reports *wrong*, not nothing.
7. **Never wait longer than one phrase for a decision.** At 128 BPM sixteen
   bars are thirty seconds. Let the running track run; a missed transition beats
   one that lands in the wrong place.

## How a call reaches the engine

```text
Agent → MCP tool → approval gate → line protocol → socket → Steuerpult → mixer
                        │
                        └── refused: nothing is sent, and the answer says why
```

The gate is checked **before** the connection is opened. Read tools skip it.

## The seven classes

| Class | Touches | Default |
| --- | --- | --- |
| `lesen` | status, search, list, get, plan, events | free |
| `mischen` | faders, EQ, filter, crossfader, cue, stem levels | set approval |
| `spielen` | play, cues, loops, sync, tempo, jumps | set approval |
| `zeit` | ramp, schedule, when, uebergang, cancel | set approval |
| `datei` | record, load, queue_add, grid edits | per call |
| `dramaturgie` | arc, room, signals, queue order | per call |
| `erzeugen` | generation | never via the file |

All 194 controls are classified; an unclassified one is **refused**, not
guessed.

## Roles (one built)

From the engine's `docs/AGENTEN.md`:

| Role | Job | State |
| --- | --- | --- |
| Mix-Engineer | plan and drive a transition | **built** — `agents/mix_engineer.py`, driven end to end |
| Track-Wähler | pick from the collection, harmonically and tempo-wise | after that |
| Set-Planer | dramaturgy over time | after that |
| Guardrail | veto; also: notice when an agent goes quiet | after that |
| Crowd-Sensor, Energie-Analyst | turn signals into a state | **deliberately absent** — no signal source exists |
| Generator | commission new tracks | blocked on provider, money, licence |

**The Mix-Engineer is first because it is the only one whose work can be
checked the same evening.** `musik-kritik` finds a transition in the recording
and measures duration, phrase position and level curve; over 24 sets its error
is a median +2.0 s, a hard cut found to within one window.

That is the acceptance criterion for this space, and it is **met**: the agent
chose a blend, the engine landed it 0.04 beats into the phrase, and the critic
found it again — 32.0 of 32 beats, tempo holding.
[`docs/ABNAHME.md`](docs/ABNAHME.md) has the numbers and, just as importantly,
the four attempts that measured nothing first.

It is met **on material this project built itself**, headless, with nobody
listening. That is a real result and a narrow one; Hard Rule 6 still stands.

## What to do when something refuses

| Answer | Meaning |
| --- | --- |
| `freigabe verweigert …` | no valid approval, or wrong class. Not a bug. |
| `err es laufen mehrere Decks` | rule 4. Pick the outgoing deck. |
| `err unbekanntes Control` | typo, or a new control nobody classified. |
| `plan N abgebrochen — deck1 ist durchgelaufen` | the timing deck ended. Expected. |
| `Fehler: Musik ist unter … nicht erreichbar` | the engine is not running. |
