"""Gemeinsames Render-Testmaterial: Impulston, EQ-Kurve und ein Mixplan.

Sowohl der Renderer (Task 4) als auch die Naht-QA (Task 6) brauchen genau
dieselben Bausteine - private Helfer in tests/render/test_engine.py waeren
ueber ein Testmodul hinweg importiert worden, und das ist fragil. Hier liegen
sie deshalb einmal, im schon vorhandenen tests/fixtures/-Verzeichnis.
"""

from pathlib import Path

import numpy as np
import soundfile as sf

from djset.contracts import EqCurve, EqPoint, MixEntry, MixPlan, Transition
from djset.render.engine import bar_length_s

SR = 44_100


def pulsed_tone(
    path: Path, seconds: float, hz: float = 220.0, bpm: float = 174.0, sr: int = SR
) -> Path:
    """Ein Ton mit hoerbarem Puls: ein Impulszug im Takt, jeder Impuls aus `hz`.

    Reine Sinustoene taugen als Render-Fixture nicht: die QA analysiert jede
    Naht nach und muss dort ein Tempo messen koennen. Ein Impulszug bei `bpm`
    liefert der Erkennung Einsaetze, und die Traegerfrequenz haelt die Tracks
    klanglich auseinander.
    """
    total = int(seconds * sr)
    t = np.arange(total) / sr
    beat_s = 60.0 / bpm
    phase = np.mod(t, beat_s) / beat_s
    # Kurze Huellkurve je Schlag: sofortiger Anstieg, schneller Abfall.
    shape = np.exp(-phase * 18.0)
    signal = (0.4 * np.sin(2 * np.pi * hz * t) * shape).astype(np.float32)
    sf.write(path, signal, sr)
    return path


def flat_curve(gain_db: float = 0.0) -> EqCurve:
    """Eine EQ-Kurve, die auf allen drei Baendern konstant bei gain_db bleibt."""
    points = [EqPoint(position=0.0, gain_db=gain_db),
              EqPoint(position=1.0, gain_db=gain_db)]
    return EqCurve(low=points, mid=points, high=points)


def simple_plan(bars: int = 1, target_bpm: float = 174.0) -> MixPlan:
    """Ein dreiteiliger Mixplan mit zwei gleich langen Uebergaengen."""
    overlap = bars * bar_length_s(target_bpm)
    entries = [
        MixEntry(track_id=f"t{i}", play_from_s=0.0, play_to_s=10.0,
                 timestretch_ratio=1.0, gain_db=0.0)
        for i in range(3)
    ]
    starts = [10.0 - overlap, 2 * (10.0 - overlap)]
    transitions = [
        Transition(from_track=f"t{i}", to_track=f"t{i + 1}", start_at_s=starts[i],
                   bars=bars, technique="blend", eq_out=flat_curve(), eq_in=flat_curve())
        for i in range(2)
    ]
    return MixPlan(
        target_length_s=starts[-1] + 10.0,
        target_bpm=target_bpm,
        entries=entries,
        transitions=transitions,
    )


def sources_for(tmp_path: Path, target_bpm: float = 174.0) -> dict[str, Path]:
    """Passendes Quellmaterial zu simple_plan() - je Track eine andere Frequenz.

    `target_bpm` muss zum gleichnamigen Parameter von simple_plan() passen: sonst
    behauptet der Plan ein Tempo, das im Fixture-Material gar nicht steckt. Bis
    Task 4 macht das keinen Unterschied (dort wird nur gerendert), aber Task 6
    misst das Tempo an jeder Naht nach - ein stiller Bruch hier wuerde dort einen
    Phantomfehler jagen lassen.
    """
    return {f"t{i}": pulsed_tone(tmp_path / f"t{i}.wav", 10.0, hz=220.0 * (i + 1),
                                  bpm=target_bpm)
            for i in range(3)}
