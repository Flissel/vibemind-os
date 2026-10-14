"""Two structured tracks a fourth apart, so both ends of the chain can see them.

Mirrors `bauen()` in the engine's `crates/musik-cli/src/bin/musik-material.rs`
— same triad, same kick, same phrase arithmetic — with one addition: a
transposition.

**Why the addition.** The engine's own generator builds every case on the same
root, which is right for what it tests (structure) and wrong for this run. The
critic locates a transition by the change in the sound picture, and it compares
chroma; two tracks in the same key are, to it, one track. The first acceptance
attempt used `musik-material`'s files unchanged, the transition ran correctly,
and the critic reported "Kein Übergang gefunden" — which was the right answer
to the question it was asked.

Built, not heard. Everything measured against this material is measured against
something this project wrote itself, and that is a weaker claim than it looks:
see Hard Rule 6.
"""
from __future__ import annotations

import math
import struct
from pathlib import Path

RATE = 44_100
PHRASE_BEATS = 16.0

#: (kind, phrases, kick, bass, chord level) — the columns of the engine's `Teil`.
#: Two breaks and an outro, so the outgoing track has somewhere to end.
OUTGOING = [
    ("intro", 2, False, False, 0.20),
    ("aufbau", 2, True, False, 0.30),
    ("drop", 2, True, True, 0.33),
    ("break", 2, True, False, 0.12),
    ("drop", 2, True, True, 0.31),
    ("break", 2, True, False, 0.11),
    ("drop", 2, True, True, 0.32),
    ("outro", 2, False, False, 0.18),
]
#: Two phrases of intro — 32 beats, which is what the planner's first rule
#: asks for, and enough room for a blend.
INCOMING = [
    ("intro", 2, False, False, 0.20),
    ("aufbau", 2, True, False, 0.30),
    ("drop", 2, True, True, 0.32),
    ("break", 2, True, False, 0.12),
    ("drop", 3, True, True, 0.31),
]

#: How far apart the two tracks are. A fourth: far enough that the chroma
#: changes, close enough that it is a mix and not a collision.
SEMITONES = 5


def build(sections, bpm: float, semitones: int) -> tuple[list[float], list[tuple[str, float]]]:
    """One track, plus where each section starts.

    The chord's phase runs on across section boundaries. Breaking it would put
    a click at every boundary — an onset the structure analysis would then
    quite rightly find, and the run would be measuring the click.
    """
    factor = 2.0 ** (semitones / 12.0)
    chord = [220.0 * factor, 277.18 * factor, 329.63 * factor]
    bass_hz = 55.0 * factor
    mono: list[float] = []
    bounds: list[tuple[str, float]] = []
    phase = 0.0
    per_beat = 60.0 / bpm * RATE

    for kind, phrases, kick, bass, level in sections:
        length = int(per_beat * PHRASE_BEATS) * phrases
        bounds.append((kind, len(mono) / RATE))
        start_at = len(mono)
        mono.extend([0.0] * length)
        for i in range(start_at, len(mono)):
            value = 0.0
            for hz in chord:
                value += math.sin(math.tau * hz * phase) * level / 3.0
            if bass:
                value += math.sin(math.tau * bass_hz * phase) * 0.5
            mono[i] = value
            phase += 1.0 / RATE
        if kick:
            step = int(per_beat)
            at = start_at
            while at + RATE // 4 < len(mono):
                for j in range(RATE // 4):
                    t = j / RATE
                    f = 110.0 * math.exp(-t * 30.0) + 45.0
                    mono[at + j] += math.sin(math.tau * f * t) * math.exp(-t * 22.0) * 0.9
                at += step
    return mono, bounds


def write_wav(path: Path, mono: list[float]) -> None:
    data = bytearray()
    for v in mono:
        s = int(max(-1.0, min(1.0, v)) * 32_767)
        data += struct.pack("<hh", s, s)
    with open(path, "wb") as f:
        f.write(b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVEfmt ")
        f.write(struct.pack("<IHHIIHH", 16, 1, 2, RATE, RATE * 4, 4, 16))
        f.write(b"data" + struct.pack("<I", len(data)))
        f.write(data)


def make(target: Path, bpm: float = 128.0) -> tuple[Path, Path]:
    """Write both tracks and say where they went."""
    target.mkdir(parents=True, exist_ok=True)
    out = target / "ausgehend.wav"
    into = target / "eingehend.wav"
    for path, sections, semitones in ((out, OUTGOING, 0), (into, INCOMING, SEMITONES)):
        mono, bounds = build(sections, bpm, semitones)
        write_wav(path, mono)
        print(f"  {path.name}: {len(mono)/RATE:.1f} s, {len(bounds)} sections, +{semitones} st")
    return out, into


if __name__ == "__main__":
    import sys

    make(Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/abnahme"))
