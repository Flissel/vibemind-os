"""Ein Trenner fuer Tests: teilt das Signal hart in zwei Haelften.

Stimme und Begleitung bekommen je die Haelfte des Signals - beide sind also
das ganze Material, leiser, und ergeben zusammen wieder den Song. Das reicht,
um Ablage, Pfade und Summe zu pruefen, ohne Demucs zu laden.
"""

from pathlib import Path

import numpy as np
import soundfile as sf


class HalbTrenner:
    modell = "halb-test"

    def __init__(self) -> None:
        self.aufrufe: list[Path] = []

    def trennen(self, quelle: Path, stimme: Path, begleitung: Path) -> None:
        self.aufrufe.append(quelle)
        signal, sr = sf.read(quelle, dtype="float32", always_2d=True)
        sf.write(stimme, signal * 0.5, sr, subtype="FLOAT")
        sf.write(begleitung, signal * 0.5, sr, subtype="FLOAT")


class TonTrenner:
    """Die Begleitung ist ein 440-Hz-Sinus in Laenge und Rate des Songs, die
    Stimme der Song selbst. Anders als beim HalbTrenner laesst sich damit
    hoeren (per FFT), ob ein Ergebnis aus der Begleitung kommt: Song/2 und Song
    sind nach dem Angleichen der Lautheit nicht zu unterscheiden."""

    modell = "ton-test"

    def trennen(self, quelle: Path, stimme: Path, begleitung: Path) -> None:
        signal, sr = sf.read(quelle, dtype="float32", always_2d=True)
        ton = 0.5 * np.sin(2 * np.pi * 440.0 * np.arange(len(signal)) / sr)
        sf.write(stimme, signal, sr, subtype="FLOAT")
        sf.write(
            begleitung,
            np.repeat(ton[:, None], signal.shape[1], axis=1).astype(np.float32),
            sr,
            subtype="FLOAT",
        )


class KaputtTrenner:
    modell = "kaputt-test"

    def trennen(self, quelle: Path, stimme: Path, begleitung: Path) -> None:
        stimme.write_bytes(b"halb")
        raise RuntimeError("Trennung abgebrochen")
