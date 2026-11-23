"""Pegel der moeglichen Ausschnitte: taugt ein Start, den Zielpegel zu erreichen?

`videomusik_rendern` regelt nur die Verstaerkung. Ein Ausschnitt mit leisem
Intro und lautem Schlag am Ende kommt deshalb am True-Peak-Deckel nie auf den
Zielpegel. Darum wird jedes Fenster VOR der Wahl gemessen, und zwar in der
Quelle, die wirklich gerendert wird (Begleitung oder Song). Die Quelle wird
einmal gelesen; gemessen wird auf dem Array.
"""

import math
from dataclasses import dataclass
from pathlib import Path

import librosa
import numpy as np
import pyloudnorm

from djset.dsp.analyze import _LEVEL_FLOOR_DB
from djset.qa import TRUE_PEAK_CEILING_DB
from djset.render.stretch import SR
from djset.video.klang import _laden, huelle
from djset.video.laenge import VideoFehler
from djset.video.pruefung import LUFS_TOLERANZ, luecken_vor_schluss

# True Peak je Block von 50 ms: Die Fenster beginnen auf Taktanfaengen, ein Block
# gehoert ganz zum Fenster oder zu dessen Rand und wird dann mitgezaehlt
# (leicht vorsichtig, nie zu optimistisch).
_BLOCK_S = 0.05
_UEBERABTASTUNG = 4
# Lautheitsfenster nach BS.1770.
_GATE_BLOCK_S = 0.4
_GATE_UEBERLAPPUNG = 0.75

# Alles darunter ist Stille (Bodenwert der Messung), nie pegelbar.
_STILLE_LUFS = -100.0


@dataclass(frozen=True)
class FensterPegel:
    start_s: float
    lufs: float
    true_peak: float
    # Lautheit, die das Fenster unter dem Spitzendeckel hoechstens erreicht.
    erreichbar_lufs: float
    # True: vor dem Schluss steht laenger als 0,5 s Stille.
    luecke: bool


def _spitzen_je_block(signal: np.ndarray, sr: int) -> np.ndarray:
    """Groesster True Peak (linear) je Block, ueber beide Kanaele.

    Einmal fuer die ganze Quelle statt je Fenster: das vierfache Ueberabtasten
    ist der teuerste Schritt der Messung, und die Fenster ueberlappen stark.
    Polyphasen-Ueberabtastung wie in BS.1770; gegen den Soxr-Wert der Endmessung
    liegen Hundertstel dB, weit unter der Pegeltoleranz.
    """
    block = round(_BLOCK_S * sr) * _UEBERABTASTUNG
    grenzen = np.arange(0, signal.shape[0] * _UEBERABTASTUNG, block)
    spitzen = np.zeros(len(grenzen))
    for kanal in range(signal.shape[1]):
        hoch = librosa.resample(
            np.ascontiguousarray(signal[:, kanal]),
            orig_sr=sr, target_sr=sr * _UEBERABTASTUNG, res_type="polyphase",
        )
        spitzen = np.maximum(spitzen, np.maximum.reduceat(np.abs(hoch), grenzen))
    return spitzen


def _leistung(signal: np.ndarray, sr: int) -> np.ndarray:
    """Die K-bewertete Leistung (beide Kanaele), Probe fuer Probe.

    Das K-Filter (BS.1770) laeuft einmal ueber die ganze Quelle; ein Fenster liest
    seine Bloecke dann aus der Leistung. Der Einschwingfehler des Filters am
    Fensteranfang liegt weit unter 0,01 LU.
    """
    gefiltert = signal.astype(np.float64)
    for stufe in pyloudnorm.Meter(sr)._filters.values():
        for kanal in range(gefiltert.shape[1]):  # die Stufen filtern nur eindimensional
            gefiltert[:, kanal] = stufe.apply_filter(gefiltert[:, kanal])
    leistung = np.sum(np.square(gefiltert), axis=1)
    return leistung


def _lautheit(leistung: np.ndarray, sr: int) -> float:
    """Integrierte Lautheit nach BS.1770 (400-ms-Bloecke, 75 % Ueberlappung, beide Gates)."""
    block = round(_GATE_BLOCK_S * sr)
    schritt = round(_GATE_BLOCK_S * (1 - _GATE_UEBERLAPPUNG) * sr)
    laenge = len(leistung)
    if laenge < block:
        return _LEVEL_FLOOR_DB
    summe = np.concatenate(([0.0], np.cumsum(leistung)))
    von = np.arange(0, laenge - block + 1, schritt)
    z = (summe[von + block] - summe[von]) / block
    z = z[z > 0]
    l = -0.691 + 10.0 * np.log10(z)
    z = z[l > -70.0]
    if z.size == 0:
        return _LEVEL_FLOOR_DB
    relativ = -0.691 + 10.0 * math.log10(float(np.mean(z))) - 10.0
    z = z[-0.691 + 10.0 * np.log10(z) > relativ]
    if z.size == 0:
        return _LEVEL_FLOOR_DB
    return max(_LEVEL_FLOOR_DB, -0.691 + 10.0 * math.log10(float(np.mean(z))))


def fenster_messen(
    quelle: Path,
    starts: list[float],
    ziel_s: float,
    schluss_im_stueck_s: float,
    sr: int = SR,
    lufs_ziel: float = -14.0,
) -> list[FensterPegel]:
    """Misst zu jedem Start das Fenster [start, start + ziel_s) der Quelle.

    Die Luecken-Pruefung sieht das Fenster so, wie das fertige Stueck klingt:
    mit der Einblendung und mit der Verstaerkung auf lufs_ziel (hoechstens bis
    zum Spitzendeckel). Sonst waere Stille, die erst nach dem Anheben hoerbar
    wird, kein Befund, und ein Start in leiser Strecke kaeme als tauglich durch.
    """
    name = Path(quelle).name
    signal = _laden(Path(quelle), sr)
    laenge = round(ziel_s * sr)
    schluss_n = max(round(schluss_im_stueck_s * sr), 0)
    block = round(_BLOCK_S * sr)
    spitzen = _spitzen_je_block(signal, sr)
    leistung = _leistung(signal, sr)
    betrag = np.max(np.abs(signal), axis=1)  # wie die Pruefung: lauterer Kanal je Probe
    ergebnis: list[FensterPegel] = []
    for start in starts:
        anfang = round(start * sr)
        stueck = betrag[anfang:anfang + laenge]
        if len(stueck) < sr // 2:  # unter 0,5 s misst pyloudnorm nichts mehr
            raise VideoFehler(f"{name}: Fenster ab {start:.2f} s ist zu kurz zum Messen")
        # Das Stueck wird mit Einblendung und Ausklang gerendert; der Ausklang senkt die
        # integrierte Lautheit. Gemessen wird deshalb mit derselben Huelle wie im Render.
        kurve = huelle(laenge, sr, schluss_im_stueck_s)[: len(stueck)]
        lufs = _lautheit(leistung[anfang:anfang + len(stueck)] * kurve**2, sr)
        spitze = float(np.max(spitzen[anfang // block: -(-(anfang + len(stueck)) // block)]))
        spitze_db = 20.0 * math.log10(spitze) if spitze > 0 else _LEVEL_FLOOR_DB
        if not math.isfinite(lufs) or lufs <= _STILLE_LUFS:
            erreichbar = -math.inf
        else:
            erreichbar = lufs + (TRUE_PEAK_CEILING_DB - spitze_db)
        gain_db = min(lufs_ziel - lufs, TRUE_PEAK_CEILING_DB - spitze_db) if math.isfinite(erreichbar) else 0.0
        gehoert = (stueck * kurve) * 10.0 ** (gain_db / 20.0)
        ergebnis.append(FensterPegel(
            start_s=start,
            lufs=lufs,
            true_peak=spitze_db,
            erreichbar_lufs=erreichbar,
            luecke=bool(luecken_vor_schluss(gehoert, sr, schluss_n)),
        ))
    return ergebnis


def tauglich(f: FensterPegel, lufs_ziel: float) -> bool:
    """Erreicht das Fenster den Zielpegel unter dem Deckel, ohne Luecke vor dem Schluss?"""
    return not f.luecke and f.erreichbar_lufs >= lufs_ziel - LUFS_TOLERANZ
