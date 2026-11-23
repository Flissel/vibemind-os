"""Ein Trenner fuer Tests: teilt das Signal hart in zwei Haelften.

Stimme und Begleitung bekommen je die Haelfte des Signals - beide sind also
das ganze Material, leiser, und ergeben zusammen wieder den Song. Das reicht,
um Ablage, Pfade und Summe zu pruefen, ohne Demucs zu laden.
"""

from pathlib import Path

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


class KaputtTrenner:
    modell = "kaputt-test"

    def trennen(self, quelle: Path, stimme: Path, begleitung: Path) -> None:
        stimme.write_bytes(b"halb")
        raise RuntimeError("Trennung abgebrochen")
