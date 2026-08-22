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


def _entry_length_s(target_bpm: float) -> float:
    """Rund zehn Sekunden, auf einen ganzzahligen Schlag bei target_bpm gerundet.

    Reine 10.0 Sekunden treffen einen ganzen Schlag nur bei Tempi, die 60
    glatt teilen (120, 150, 174, ...). Bei allen anderen (z. B. 128 oder 140
    BPM) liegt das Trackende mitten in einem Schlag, und der Impulszug aus
    pulsed_tone() ist an SEINEM EIGENEN Nullpunkt verankert - t0s Ende und t1s
    Anfang tragen dann in der Ueberblendung zwei gegeneinander phasenversetzte
    Raster, unabhaengig von jeder Pruefung, die das misst. Bei 174 BPM (dem bis
    hierhin einzigen getesteten Wert) faellt das nicht auf, weil 10.0 Sekunden
    dort zufaellig exakt 29 Schlaege sind. Diese Funktion rundet stattdessen
    IMMER auf den naechsten ganzen Schlag - bei 174 BPM bitgleich zu 10.0
    (gepruefte Gleitkommagleichheit, kein Rundungsfehler), bei jedem anderen
    Tempo tatsaechlich phasenrichtig.
    """
    beat_s = 60.0 / target_bpm
    return round(10.0 / beat_s) * beat_s


def simple_plan(bars: int = 1, target_bpm: float = 174.0) -> MixPlan:
    """Ein dreiteiliger Mixplan mit zwei gleich langen Uebergaengen."""
    length = _entry_length_s(target_bpm)
    overlap = bars * bar_length_s(target_bpm)
    entries = [
        MixEntry(track_id=f"t{i}", play_from_s=0.0, play_to_s=length,
                 timestretch_ratio=1.0, gain_db=0.0)
        for i in range(3)
    ]
    starts = [length - overlap, 2 * (length - overlap)]
    transitions = [
        Transition(from_track=f"t{i}", to_track=f"t{i + 1}", start_at_s=starts[i],
                   bars=bars, technique="blend", eq_out=flat_curve(), eq_in=flat_curve())
        for i in range(2)
    ]
    return MixPlan(
        target_length_s=starts[-1] + length,
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
    Phantomfehler jagen lassen. Die Laenge kommt aus _entry_length_s(), aus
    demselben Grund wie in simple_plan().
    """
    length = _entry_length_s(target_bpm)
    return {f"t{i}": pulsed_tone(tmp_path / f"t{i}.wav", length, hz=220.0 * (i + 1),
                                  bpm=target_bpm)
            for i in range(3)}
