# Acceptance — what is checked, with what, and what is not

> **Evidence contract.** Every row says how it was produced: *measured* (a
> number from a run), *static* (counted in the source), or *open*. Nothing here
> is asserted.

_Runs: 2026-09-03, headless, on built material. Engine at
`Flissel/Musik@77df25e`._

## The criterion

The engine's `docs/SPACE.md` sets one for this space:

> **5 — Mix-Engineer.** Fertig, wenn er einen Übergang fährt, den `musik-kritik`
> hinterher wiederfindet.

**Met.** Two runs of `python -m spaces.musik.acceptance.run --engine <Musik>`,
plus an earlier one from a different position on the outgoing track.

## What ran

```text
Mix-Engineer → api → MCP stdio client → bridge → approval gate → line protocol
  → socket → Steuerpult → mixer → recording → musik-kritik
```

Every link is the real one. The MCP client is a real stdio client and sees
`tools/list` with 16 tools; the approval gate is the one from `mcp/musik/`; the
mixer renders from the idle clock because there is no audio device, and writes
the same recording it would write with one.

## What the agent decided

The situation it was handed, from `musik_status`:

| | deck1 (outgoing) | deck2 (incoming) |
| --- | --- | --- |
| position | 89.7 s of 120.0 | 0.5 s of 82.5 |
| `beats_left` | 64.6 | — |
| `beats_to_phrase` | 1.6 | 0.0 |
| `beats_to_outro` | 33.6 | none detected |
| `intro_beats` | 32.0 | 32.0 |

Its answer, verbatim from the run:

```text
blende over 32 beats: 33.4133 beats to the outro and a 32-beat intro —
an ordinary blend fits [unverified: beats_to_outro, intro_beats]
```

One tool call — `musik_uebergang griff=blende beats=32` — which the engine
expanded into three protocol lines. The bracket is Hard Rule 6 doing its work:
both numbers the decision rested on are numbers nobody has checked against real
music.

## What the critic found

| | run 1 | run 2 |
| --- | --- | --- |
| transitions found | 1 | 1 |
| matched to a recorded grip | 1 of 1 | 1 of 1 |
| grip at | 18.52 s | 18.50 s |
| outgoing deck, into the phrase | 0.04 beats — on the one | 0.04 beats — on the one |
| incoming deck, into the phrase | 0.00 beats — on the one | 0.00 beats — on the one |
| completed | 32.0 of 32 beats in 15.0 s | 32.0 of 32 beats in 15.0 s |
| tempo across | 127.99 → 127.96 BPM | 127.99 → 127.96 BPM |
| sound estimate vs. transcript | 6.5 s late | 6.5 s late |
| dropped frames | 0 | 0 |

An earlier run, with the outgoing track eight seconds further along, put the
grip at 26.01 s and the outgoing deck 0.01 beats into the phrase, 32.0 of 32
beats in 15.0 s, 6.0 s late. Same behaviour from a different starting point.

The lateness is expected and was measured before this space existed: the start
of a long blend is inaudible by construction, and N3 put a 32-beat blend at a
median +2.0 s over 24 sets (engine `docs/FAHRPLAN.md`). These two are further
out than that median and inside the spread.

The engine agreed with itself afterwards: `master.transitions` read `blende`
and `master.repeats` read 1.

## A warning that pointed at the wrong second

The critic reported, on both runs:

```text
Pegel in der Blende: 0.142 gegen 0.394 außen
⚠ 64 % Einbruch in der Mitte. Das ist die Crossfader-Kurve;
   `master.crossfader_curve` härter stellen.
```

**Neither number came from the blend.** Measured back with a one-second RMS,
0.142 is the level between seconds 12 and 20 of the recording — the *outgoing*
track's own break, six seconds before the grip, where no fader moved at all.

The cause is an off-by-two: `pegel()` produces one value per second, and
`uebergang_bericht()` looked one up as if each value covered two. Every level
report about a transition therefore described a point at half the elapsed time,
and the advice above was advice about a place nobody had mixed. Nothing looked
wrong — the report was complete and the numbers were plausible.

Fixed in the engine: `PEGEL_SEK` names the width once, and the window selection
moved into `pegel_um()` so that a test can see it. With the window right, the
same recording reads:

```text
Pegel in der Blende: 0.081 gegen 0.381 außen
⚠ 79 % Einbruch — aber die Ränder sind verschieden laut: davor 0.381,
   danach 0.200. Dann liegt es am Material und nicht an der Kurve —
   Gain, oder eine andere Stelle zum Einsteigen.
```

That is the real picture. The blend takes a drop with kick and bass (0.381) into
a chord-only intro (0.081) — twelve decibels apart, by construction in the
material. The engine's crossfader already sits on its constant-power curve;
hardening it would cover the step, not remove it. So the critic now separates
the two cases it used to conflate and prints both edges, and a reader can check
the call instead of taking it.

| | level |
| --- | --- |
| outgoing track, drop section | 0.38 |
| outgoing track, its own break — no fader moved | 0.14 |
| incoming track, intro alone | 0.081 |
| incoming track, aufbau | 0.18 |

Not changed in response: whether a blend from an outro into a quiet intro is a
mistake or is what a set breathing sounds like is a question for someone
listening, on real music. Both are missing.

## What the tests are worth

| Claim | Kind | Evidence |
| --- | --- | --- |
| 25 tests over planner and api | static | `python -m pytest spaces/musik` |
| every guard bites | measured | 12 defects reintroduced one at a time, each turned the intended test red, each restored |
| the planner needs no engine | static | `plan()` is pure; every rule is a function call |
| the room arithmetic matches the engine | **open** | mirrored by hand from `crates/control/src/uebergang.rs`; nothing checks it across repos |
| the critic's level report reads the right second | measured | 7 defects reintroduced in `musik-kritik`, each turned the intended test red |

**A false negative in that guard run, worth writing down.** The first pass
reported two defects as "still green". They were not: both edits were the same
length as the text they replaced (`>= 2` → `>= 3`, `1.0` → `0.0`), Python's
bytecode cache keys on size and whole-second mtime, and the run reused the old
`.pyc`. A verification harness that silently skips the code under test is worse
than none. Re-run with `PYTHONDONTWRITEBYTECODE=1`, all twelve bite.

## What it took to get a run that measures anything

Four attempts failed before one produced evidence, and each failure was the
system answering correctly:

1. **Approval written as JSON** → `keine gültige Freigabe (klassen fehlt)`. The
   file is a line format, not JSON.
2. **Channel faders default to 0** → the recording was 25 s of silence, and the
   critic said `der Mitschnitt ist still`. Setting up the desk is not the
   agent's job, but it is the harness's.
3. **Both tracks in the same key** → `Kein Übergang gefunden`. The critic
   compares chroma; two tracks on the same root are one track to it. The
   engine's own `musik-material` builds every case on the same root, which is
   right for what it tests and useless here. Hence `acceptance/material.py`.
4. **A 26 s recording** → nothing found. The estimator compares each window
   against one sixteen seconds earlier; with four seconds of lead there is
   nothing on the far side. Hence twenty seconds before and after.

None of these is a defect in the engine. Three of the four are the engine
refusing to claim something it could not see.

## What is still not shown

| Gap | Consequence |
| --- | --- |
| The material is built, not heard | every number here describes synthetic tracks this project wrote |
| One grip, one situation | `bassswap`, `filter`, `schnitt` and `schleife` have run only in unit tests |
| No audio device | the mixer rendered from the idle clock; nobody heard it |
| No second agent | the Track-Wähler, the Set-Planer and the Guardrail do not exist |
| The room arithmetic is mirrored | a change in the engine's grips silently invalidates the planner's fit test |
| Phase 1 still not accepted | needs a four-output interface on real hardware |

## Reproducing it

```sh
# in the engine repo
cargo build --release --bins

# here, with the MCP SDK available
python -m spaces.musik.acceptance.run --engine /path/to/Musik --out /tmp/abnahme
/path/to/Musik/target/release/musik-kritik /tmp/abnahme/mitschnitt.wav
```

The run needs `xvfb-run` and takes about a minute of wall clock, most of it the
twenty seconds of lead at each end.
