"""Stufe 8: fuehrt den Mixplan aus.

Der Renderer trifft keine eigenen Entscheidungen - das ist die
Determinismus-Grenze der Spec: gleicher Mixplan, gleiches WAV. Jede Zahl, nach
der er handelt, steht im Plan. Findet sich beim Bauen eine Groesse, die er
selbst bestimmen muesste, ist das ein Befund ueber den Mixplan.

Die Zeitleiste ist dieselbe wie in Stufe 6: Eintrag 0 beginnt bei null, jeder
weitere bei start_at_s seines eingehenden Uebergangs, und ein Eintrag klingt
(play_to_s - play_from_s) / timestretch_ratio Sekunden lang.
"""

import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf

from djset.contracts import MixEntry, MixPlan
from djset.dsp.analyze import BEATS_PER_BAR
from djset.render.eq import apply_eq
from djset.render.stretch import SR, stretch


class MissingSource(RuntimeError):
    """Zu einem Eintrag des Mixplans fehlt die Audiodatei."""


def bar_length_s(target_bpm: float) -> float:
    """Wie lang ein Takt im fertigen Set dauert."""
    return BEATS_PER_BAR * 60.0 / target_bpm


def _load_mono(path: Path, sr: int) -> np.ndarray:
    signal, file_sr = sf.read(path, dtype="float32", always_2d=True)
    mono = signal.mean(axis=1)
    if file_sr != sr:
        raise MissingSource(
            f"{path.name} liegt mit {file_sr} Hz vor, gebraucht werden {sr} Hz"
        )
    return mono.astype(np.float32)


def _segment(source: Path, entry: MixEntry, sr: int, work: Path) -> np.ndarray:
    """Der gedehnte, verstaerkte Abschnitt eines Eintrags."""
    signal = _load_mono(source, sr)
    start = int(round(entry.play_from_s * sr))
    stop = min(len(signal), int(round(entry.play_to_s * sr)))
    piece = signal[start:stop]

    if entry.timestretch_ratio != 1.0:
        # Die Schnittdatei entsteht nur, wenn wirklich gedehnt wird. Sie immer
        # zu schreiben waere bei dreizehn Tracks eines Sechzig-Minuten-Sets ein
        # voller ueberfluessiger Schreibdurchlauf ueber das gesamte Material.
        cut = work / f"{entry.track_id}-cut.wav"
        out = work / f"{entry.track_id}-stretched.wav"
        sf.write(cut, piece, sr)
        stretch(cut, out, entry.timestretch_ratio, sr=sr)
        piece = _load_mono(out, sr)

    return piece * (10 ** (entry.gain_db / 20.0))


def render(
    plan: MixPlan,
    sources: dict[str, Path],
    target: Path,
    master_gain_db: float = 0.0,
    sr: int = SR,
) -> None:
    """Rendert den Mixplan in eine einzelne Audiodatei."""
    missing = [e.track_id for e in plan.entries if e.track_id not in sources]
    if missing:
        raise MissingSource(
            f"Zu diesen Eintraegen fehlt die Audiodatei: {', '.join(missing)}"
        )

    bar = bar_length_s(plan.target_bpm)
    starts = [0.0] + [t.start_at_s for t in plan.transitions]
    total = int(round(plan.target_length_s * sr))
    mix = np.zeros(total, dtype=np.float32)

    with tempfile.TemporaryDirectory() as raw:
        work = Path(raw)
        for index, entry in enumerate(plan.entries):
            block = _segment(sources[entry.track_id], entry, sr, work)

            # Der eingehende Uebergang faerbt den Kopf, der ausgehende den
            # Schwanz. Dazwischen laeuft der Track flach.
            if index > 0:
                incoming = plan.transitions[index - 1]
                width = min(block.size, int(round(incoming.bars * bar * sr)))
                if width > 0:
                    block[:width] = apply_eq(block[:width], sr, incoming.eq_in)
            if index < len(plan.transitions):
                outgoing = plan.transitions[index]
                width = min(block.size, int(round(outgoing.bars * bar * sr)))
                if width > 0:
                    # Ohne diese Bedingung waere block[-0:] das GANZE Array,
                    # nicht das leere - eine klassische Python-Falle.
                    block[-width:] = apply_eq(block[-width:], sr, outgoing.eq_out)

            offset = int(round(starts[index] * sr))
            end = min(total, offset + block.size)
            if end > offset:
                mix[offset:end] += block[: end - offset]

    mix *= 10 ** (master_gain_db / 20.0)
    target.parent.mkdir(parents=True, exist_ok=True)
    sf.write(target, mix, sr, subtype="FLOAT")
