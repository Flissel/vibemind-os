"""Pruefung der gerenderten Videomusik.

Prueft, was Laura sich auf das Stueck verlassen muss: genau die Laenge des
Videos, ein stiller Schluss (kein harter Schnitt), der Zielpegel, der
True-Peak-Deckel und keine Loecher mitten im Stueck.
"""

import math
from pathlib import Path
from typing import Literal

import numpy as np
import soundfile as sf
from pydantic import BaseModel

from djset.dsp.analyze import measure_loudness
from djset.qa import TRUE_PEAK_CEILING_DB, luecken
from djset.render.stretch import SR
from djset.video.laenge import VideoFehler

LAENGE_TOLERANZ_S = 0.05
LUFS_TOLERANZ = 1.0
AUSKLANG_STILL_DB = -60.0
_AUSKLANG_FENSTER_S = 0.01
# Die Pegelung deckelt auf genau -1,0 dBTP; Messrauschen im Mikro-dB-Bereich ist kein Befund.
_SPITZE_TOLERANZ_DB = 0.01


class VideoBefund(BaseModel):
    art: Literal["laenge", "schluss", "pegel", "spitze", "luecke"]
    text: str


def videomusik_pruefen(
    datei: Path, samples: int, lufs_ziel: float, sr: int = SR
) -> list[VideoBefund]:
    """Alle Abweichungen des gerenderten Stuecks; leere Liste = in Ordnung."""
    datei = Path(datei)
    try:
        signal, rate = sf.read(str(datei), dtype="float32", always_2d=True)
    except (sf.LibsndfileError, OSError, RuntimeError) as fehler:
        raise VideoFehler(f"{datei.name}: Datei nicht lesbar ({fehler})") from fehler
    if rate != sr:
        raise VideoFehler(f"{datei.name}: Samplerate {rate} Hz, erwartet {sr} Hz")

    befunde: list[VideoBefund] = []

    ist_s = len(signal) / sr
    soll_s = samples / sr
    if abs(ist_s - soll_s) > LAENGE_TOLERANZ_S:
        befunde.append(VideoBefund(
            art="laenge",
            text=f"{datei.name}: {ist_s:.3f} s lang, erwartet {soll_s:.3f} s "
                 f"(Toleranz {LAENGE_TOLERANZ_S * 1000:.0f} ms).",
        ))

    ende = signal[-round(_AUSKLANG_FENSTER_S * sr):]
    rms = float(np.sqrt(np.mean(np.square(ende.astype(np.float64))))) if ende.size else 0.0
    ende_db = 20.0 * math.log10(rms) if rms > 0 else -200.0
    if ende_db >= AUSKLANG_STILL_DB:
        befunde.append(VideoBefund(
            art="schluss",
            text=f"{datei.name}: die letzten 10 ms liegen bei {ende_db:.1f} dBFS, "
                 f"das Stueck klingt nicht aus (Grenze {AUSKLANG_STILL_DB:.0f} dBFS).",
        ))

    try:
        pegel = measure_loudness(datei, peak_per_channel=True)
    except Exception as fehler:  # noqa: BLE001 - jede Messpanne ist hier ein Dateifehler
        raise VideoFehler(f"{datei.name}: Lautheit nicht messbar ({fehler})") from fehler
    if not math.isfinite(pegel.lufs_integrated) or abs(pegel.lufs_integrated - lufs_ziel) > LUFS_TOLERANZ:
        befunde.append(VideoBefund(
            art="pegel",
            text=f"{datei.name}: {pegel.lufs_integrated:.1f} LUFS, Ziel {lufs_ziel:.1f} LUFS "
                 f"(Toleranz {LUFS_TOLERANZ:.1f}).",
        ))
    if not math.isfinite(pegel.true_peak) or pegel.true_peak > TRUE_PEAK_CEILING_DB + _SPITZE_TOLERANZ_DB:
        befunde.append(VideoBefund(
            art="spitze",
            text=f"{datei.name}: True Peak {pegel.true_peak:.1f} dBTP, "
                 f"ueber dem Deckel {TRUE_PEAK_CEILING_DB:.1f} dBTP.",
        ))

    mono = np.max(np.abs(signal), axis=1) if signal.size else np.zeros(0, dtype=np.float32)
    for start in luecken(mono, sr):
        befunde.append(VideoBefund(
            art="luecke",
            text=f"{datei.name}: bei {start:.2f} s steht laenger als 0,5 s Stille mitten im Stueck.",
        ))
    return befunde
