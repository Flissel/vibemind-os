"""Trennt einen Song in Stimme und Begleitung (Plan 1c, Gesangsspur).

Demucs (`htdemucs`, MIT) laeuft auf der CPU: gemessen 89 s fuer einen Song
von 233 s, die Grafikkarte bleibt fuer andere Dienste frei. Das Modell liefert
vier Spuren - Drums, Bass, Other, Vocals. Die Begleitung ist die Summe der drei
ohne Stimme, damit Stimme + Begleitung wieder den Song ergeben.

Demucs und torch sind ein optionales Extra (`pip install -e .[gesang]`). Fehlt
es, laeuft die Kette ohne getrennte Stimmen weiter; `trenner_fehlt` sagt, warum.
"""

import warnings
from importlib.util import find_spec
from pathlib import Path
from typing import Any, Protocol

import numpy as np
import soundfile as sf

MODELL = "htdemucs"

INSTALLATION = (
    "pip install torch==2.5.1 torchaudio==2.5.1 --index-url "
    "https://download.pytorch.org/whl/cpu && pip install demucs==4.0.1"
)


class Trenner(Protocol):
    """Schreibt Stimme und Begleitung einer Quelle in zwei WAV-Dateien."""

    modell: str

    def trennen(self, quelle: Path, stimme: Path, begleitung: Path) -> None: ...


def trenner_fehlt() -> str | None:
    """None, wenn Demucs bereitsteht, sonst der Grund mit dem Installationsweg."""
    fehlend = [name for name in ("torch", "demucs") if find_spec(name) is None]
    if not fehlend:
        return None
    return (
        f"Demucs ist nicht installiert (es fehlt {', '.join(fehlend)}). "
        f"In der Studio-Umgebung: {INSTALLATION}"
    )


class DemucsTrenner:
    """Der echte Trenner. Laedt das Modell beim ersten Aufruf und behaelt es."""

    modell = MODELL

    def __init__(self) -> None:
        self._netz: Any = None

    def _laden(self) -> Any:
        if self._netz is None:
            # Demucs 4.0.1 ruft torch.load ohne weights_only auf, torch 2.5
            # warnt davor mit einer FutureWarning. Die Gewichte kommen aus
            # Demucs' eigenem Verzeichnis, nicht aus einer fremden Datei.
            # Dazu liest es seine htdemucs.yaml, ohne sie zu schliessen
            # (ResourceWarning).
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", FutureWarning)
                warnings.simplefilter("ignore", ResourceWarning)
                from demucs.pretrained import get_model

                netz = get_model(MODELL)
            netz.eval()
            self._netz = netz
        return self._netz

    def trennen(self, quelle: Path, stimme: Path, begleitung: Path) -> None:
        import torch
        from demucs.apply import apply_model

        netz = self._laden()
        audio, sr = sf.read(quelle, dtype="float32", always_2d=True)
        if sr != netz.samplerate:
            raise ValueError(
                f"{quelle.name} liegt mit {sr} Hz vor, Demucs braucht {netz.samplerate} Hz"
            )
        if audio.shape[1] == 1:
            audio = np.repeat(audio, 2, axis=1)
        wav = torch.from_numpy(np.ascontiguousarray(audio[:, :2].T))
        # Wie demucs.separate: auf Mittelwert und Streuung der Mono-Summe
        # normieren, danach zurueckrechnen.
        mono = wav.mean(0)
        mitte, streu = mono.mean(), mono.std()
        if float(streu) == 0.0:
            streu = torch.tensor(1.0)
        with torch.no_grad(), warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            spuren = apply_model(
                netz, ((wav - mitte) / streu)[None], device="cpu",
                shifts=1, split=True, overlap=0.25, progress=False,
            )[0]
        spuren = spuren * streu + mitte
        nummer = netz.sources.index("vocals")
        gesang = spuren[nummer]
        rest = spuren.sum(0) - gesang
        sf.write(stimme, gesang.numpy().T, sr, subtype="FLOAT")
        sf.write(begleitung, rest.numpy().T, sr, subtype="FLOAT")
