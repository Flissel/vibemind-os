"""Klang der Videomusik: das zugeschnittene Stueck rendern.

Das Stueck wird genau `samples` Proben lang (das Video hat eine exakte Laenge),
bekommt eine winzige Einblendung gegen Knacken am Anfang und klingt ab dem
letzten Taktanfang mit einer Kosinus-Halbwelle bis auf null aus. Zuletzt wird
auf den Zielpegel gebracht, ohne den True-Peak-Deckel zu reissen.
"""

import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import soundfile as sf

from djset.dsp.analyze import measure_loudness
from djset.qa import TRUE_PEAK_CEILING_DB
from djset.render.stretch import SR
from djset.video.laenge import VideoFehler
from djset.video.zuschnitt import Zuschnitt

EINBLENDE_S = 0.01
# Alles darunter gilt als Stille: pyloudnorm meldet dort den Bodenwert, und eine
# Verstaerkung auf -14 LUFS wuerde nur Rauschen aufblasen.
_STILLE_LUFS = -100.0


@dataclass(frozen=True)
class Pegel:
    """Gemessener Pegel des fertigen Stuecks (nach der Verstaerkung)."""

    lufs: float
    true_peak: float
    gain_db: float
    gedeckelt: bool


def _laden(quelle: Path, sr: int) -> np.ndarray:
    signal, rate = sf.read(str(quelle), dtype="float32", always_2d=True)
    if rate != sr:
        raise VideoFehler(
            f"{quelle.name}: Samplerate {rate} Hz, erwartet {sr} Hz (kein Resampling)"
        )
    if signal.shape[1] == 1:
        signal = np.repeat(signal, 2, axis=1)
    return signal[:, :2]


def _messen(pfad: Path, quelle: Path):  # noqa: ANN202 - Loudness bleibt intern
    try:
        werte = measure_loudness(pfad, peak_per_channel=True)
    except Exception as fehler:  # noqa: BLE001 - jede Messpanne ist hier ein Quellfehler
        raise VideoFehler(f"{quelle.name}: Lautheit nicht messbar ({fehler})") from fehler
    if not math.isfinite(werte.lufs_integrated) or werte.lufs_integrated <= _STILLE_LUFS:
        raise VideoFehler(f"{quelle.name}: Stueck ist still, Lautheit nicht messbar")
    if not math.isfinite(werte.true_peak):
        raise VideoFehler(f"{quelle.name}: True Peak nicht messbar")
    return werte


def videomusik_rendern(
    quelle: Path,
    zuschnitt: Zuschnitt,
    samples: int,
    ziel: Path,
    lufs_ziel: float = -14.0,
    sr: int = SR,
) -> Pegel:
    """Schneidet, blendet ein und aus und pegelt auf `lufs_ziel`.

    `samples` ist massgeblich fuer die Laenge; fehlt der Quelle am Ende etwas,
    wird mit Stille aufgefuellt, damit die Ausgabe trotzdem exakt passt.
    """
    signal = _laden(quelle, sr)
    anfang = round(zuschnitt.start_s * sr)
    stueck = np.zeros((samples, 2), dtype=np.float64)
    schnitt = signal[anfang:anfang + samples]
    stueck[: len(schnitt)] = schnitt

    einblende = min(round(EINBLENDE_S * sr), samples)
    if einblende > 0:
        stueck[:einblende] *= np.linspace(0.0, 1.0, einblende, endpoint=False)[:, None]

    # Ausblenden: von schluss_s bis zur letzten Probe, 1 -> exakt 0.
    aus_start = min(max(round((zuschnitt.schluss_s - zuschnitt.start_s) * sr), 0), samples - 1)
    laenge = samples - aus_start
    if laenge > 1:
        kurve = 0.5 * (1.0 + np.cos(np.linspace(0.0, np.pi, laenge)))
        stueck[aus_start:] *= kurve[:, None]
    else:
        stueck[-1] = 0.0

    ziel.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(ziel), stueck.astype(np.float32), sr, subtype="FLOAT")
    vorher = _messen(ziel, quelle)

    gain = lufs_ziel - vorher.lufs_integrated
    gedeckelt = False
    if vorher.true_peak + gain > TRUE_PEAK_CEILING_DB:
        gain = TRUE_PEAK_CEILING_DB - vorher.true_peak
        gedeckelt = True
    stueck *= 10.0 ** (gain / 20.0)
    sf.write(str(ziel), stueck.astype(np.float32), sr, subtype="FLOAT")

    nachher = _messen(ziel, quelle)
    return Pegel(
        lufs=nachher.lufs_integrated,
        true_peak=nachher.true_peak,
        gain_db=gain,
        gedeckelt=gedeckelt,
    )
