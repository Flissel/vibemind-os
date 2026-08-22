"""Stufe 9: prueft das fertige File.

Bewusst messend, nicht rechnend: sie liest set.wav und analysiert es, statt dem
Mixplan zu glauben. Ein Renderer-Fehler, der den Plan korrekt umsetzt aber
falsch schreibt, faellt nur so auf.
"""

import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf

from djset.contracts import MixPlan, QaFinding, QaReport
from djset.dsp.analyze import beat_times, detect_bpm_and_grid, measure_loudness
from djset.render.engine import bar_length_s

# Erfolgskriterium 3: Ziellaenge plus/minus 5 Prozent.
LENGTH_TOLERANCE = 0.05

# Erfolgskriterium 5: hoechstens minus 1 dBTP.
TRUE_PEAK_CEILING_DB = -1.0

# Erfolgskriterium 4: an der Naht darf das Tempo nicht wegbrechen. Ein Fenster
# um jede Naht wird nachanalysiert.
#
# SEAM_DRIFT_TOLERANCE ist die PRIMAERE Pruefung: der groesste Ausreisser
# unter den Beatabstaenden im Fenster (siehe _seam_drift_beats). Sie erkennt
# einen Phasenbruch MITTEN in der Naht - genau dort, wo zwei Tracks
# zusammenlaufen sollen.
#
# SEAM_BPM_TOLERANCE bleibt als GROBE Zweitpruefung ueber das gemittelte
# Tempo bestehen. detect_bpm_and_grid rechnet dieses Mittel teleskopierend
# als (letzter Beat - erster Beat) / (n - 1) - ein Wert, der einen einzelnen
# Ausreisser mitten im Fenster fast nicht bewegt (gemessen: ein halber Schlag
# Versatz veraendert ihn um weniger als 3 BPM), aber einen kompletten
# Tempowechsel ueber das ganze Fenster zuverlaessig zeigt, z. B. wenn ein
# falsch zugeschnittener Track bei 140 statt 174 BPM liegt. Die beiden
# Pruefungen fangen deshalb verschiedene Fehlerbilder: ein einzelner Bruch
# vs. ein durchgehend falsches Tempo.
#
# 0.05 gemessen, nicht geschaetzt: auf den Fixture-Nahten aus
# tests/fixtures/render.py liegt der groesste Ausreisser eines SAUBEREN
# Renders bei 0.0236 Schlaegen (beide Naehte, reproduzierbar identisch - das
# Fixture-Material ist deterministisch). Ein Versatz um 0.1 Schlag an einer
# Naht misst 0.09-0.11, um 0.25 Schlag 0.12-0.21, um 0.5 Schlag 0.246 (siehe
# task-6-report.md). 0.05 liegt reichlich ueber dem sauberen Rauschen (gut
# das Doppelte) und reichlich unter dem kleinsten geprueften Bruch (fast die
# Haelfte davon) - beide Verteilungen sind damit klar getrennt. Ein Versatz
# um ein GANZES Vielfaches eines Schlags (1, 2, 3 Schlaege) bleibt fuer
# beide Pruefungen unsichtbar: die Beatabstaende UND das gemittelte Tempo
# sind bei periodischem Material unabhaengig von einer reinen Phasen-
# verschiebung um ganze Schlaege - das ist eine Grenze der Methode, keine
# Einstellungsfrage, und betrifft Taktausrichtung, nicht Beatkontinuitaet.
SEAM_DRIFT_TOLERANCE = 0.05
SEAM_BPM_TOLERANCE = 3.0
SEAM_WINDOW_BARS = 8

# Laenger als das darf nirgends Stille stehen.
GAP_SECONDS = 0.5
_GAP_FLOOR = 10 ** (-60.0 / 20.0)


def _gaps(signal: np.ndarray, sr: int) -> list[float]:
    """Anfaenge aller Stillen, die laenger als GAP_SECONDS dauern."""
    quiet = np.abs(signal) < _GAP_FLOOR
    if not quiet.any():
        return []
    edges = np.diff(quiet.astype(np.int8))
    starts = list(np.flatnonzero(edges == 1) + 1)
    stops = list(np.flatnonzero(edges == -1) + 1)
    if quiet[0]:
        starts.insert(0, 0)
    if quiet[-1]:
        stops.append(len(quiet))
    needed = int(GAP_SECONDS * sr)
    return [start / sr for start, stop in zip(starts, stops) if stop - start >= needed]


def _seam_drift_beats(seam: Path, target_bpm: float) -> float | None:
    """Der groesste Ausreisser unter den Beatabstaenden, in Schlaegen.

    Bewusst nicht ueber das mittlere Tempo: detect_bpm_and_grid rechnet den
    Wert teleskopierend als (letzter - erster) / (n-1) und benutzt damit nur
    den ersten und letzten Beat des Fensters. Ein Phasenbruch mitten darin
    bewegt diese Zahl fast nicht - gemessen an einem halben Schlag Versatz
    verschiebt er sie um weniger als drei BPM.

    Der Abstand zwischen zwei benachbarten Beats zeigt den Bruch dagegen
    unmittelbar: ein Versatz um einen halben Schlag erzeugt dort genau einen
    Abstand, der einen halben Schlag zu lang oder zu kurz ist.

    Liefert None, wenn das Fenster zu wenige Beats hergibt, um Abstaende zu
    bilden - das ist selbst ein Befund und wird vom Aufrufer als solcher
    behandelt.
    """
    times = beat_times(seam)
    intervals = np.diff(times)
    if intervals.size == 0:
        return None
    expected = 60.0 / target_bpm
    return float(np.max(np.abs(intervals - expected) / expected))


def check_set(path: Path, plan: MixPlan) -> QaReport:
    """Misst das fertige Set und sammelt, was nicht stimmt."""
    signal, sr = sf.read(path, dtype="float32", always_2d=True)
    mono = signal.mean(axis=1)
    length_s = len(mono) / sr
    loudness = measure_loudness(path)
    findings: list[QaFinding] = []

    low = plan.target_length_s * (1.0 - LENGTH_TOLERANCE)
    high = plan.target_length_s * (1.0 + LENGTH_TOLERANCE)
    if not low <= length_s <= high:
        findings.append(
            QaFinding(
                kind="length",
                text=f"Das Set ist {length_s / 60:.1f} Minuten lang, geplant waren "
                f"{plan.target_length_s / 60:.1f} plus/minus "
                f"{LENGTH_TOLERANCE * 100:.0f} Prozent.",
            )
        )

    if loudness.true_peak > TRUE_PEAK_CEILING_DB:
        findings.append(
            QaFinding(
                kind="true_peak",
                text=f"True Peak liegt bei {loudness.true_peak:.1f} dB, erlaubt sind "
                f"hoechstens {TRUE_PEAK_CEILING_DB:.1f} dB.",
            )
        )

    for start in _gaps(mono, sr):
        findings.append(
            QaFinding(
                kind="gap",
                text=f"Bei {start / 60:.0f}:{start % 60:04.1f} steht laenger als "
                f"{GAP_SECONDS:.1f} Sekunden Stille.",
            )
        )

    window_s = SEAM_WINDOW_BARS * bar_length_s(plan.target_bpm)
    # Die Ausschnitte gehen in ein Temp-Verzeichnis, nicht neben set.wav: der
    # Run-Ordner ist laut Spec das Fenster in die Entscheidungen des Laufs, und
    # ein Abbruch mittendrin soll dort keine Bruchstuecke hinterlassen.
    with tempfile.TemporaryDirectory() as raw:
        work = Path(raw)
        for index, transition in enumerate(plan.transitions):
            begin = max(0, int((transition.start_at_s - window_s / 2) * sr))
            end = min(len(mono), int((transition.start_at_s + window_s / 2) * sr))
            if end - begin < sr:
                continue
            seam = work / f"seam-{index}.wav"
            sf.write(seam, mono[begin:end], sr)

            # Primaerpruefung: groesster Beatabstands-Ausreisser im Fenster.
            try:
                drift = _seam_drift_beats(seam, plan.target_bpm)
            except Exception:  # noqa: BLE001 - eine unmessbare Naht ist selbst ein Befund
                drift = None
            # Grobe Zweitpruefung: das gemittelte Tempo, siehe SEAM_BPM_TOLERANCE.
            try:
                bpm, _grid = detect_bpm_and_grid(seam)
            except Exception:  # noqa: BLE001 - eine unmessbare Naht ist selbst ein Befund
                bpm = None

            drift_broken = drift is None or drift > SEAM_DRIFT_TOLERANCE
            tempo_broken = bpm is None or abs(bpm.value - plan.target_bpm) > SEAM_BPM_TOLERANCE

            if drift_broken or tempo_broken:
                if drift is None and bpm is None:
                    gemessen = "das Beatraster ist an dieser Naht nicht messbar"
                else:
                    stats = []
                    if drift is not None:
                        stats.append(f"groesster Beatabstand-Ausreisser {drift:.2f} Schlaege")
                    if bpm is not None:
                        stats.append(f"gemitteltes Tempo {bpm.value:.1f} BPM")
                    gemessen = ", ".join(stats)
                findings.append(
                    QaFinding(
                        kind="seam",
                        text=f"An der Naht von {transition.from_track} nach "
                        f"{transition.to_track} bei "
                        f"{transition.start_at_s / 60:.0f}:"
                        f"{transition.start_at_s % 60:04.1f} stimmt das Timing "
                        f"nicht: {gemessen}, erwartet waren {plan.target_bpm:.1f} "
                        f"BPM ohne Ausreisser ueber {SEAM_DRIFT_TOLERANCE:.2f} "
                        f"Schlaege.",
                    )
                )

    return QaReport(
        length_s=length_s,
        target_length_s=plan.target_length_s,
        lufs_integrated=loudness.lufs_integrated,
        true_peak=loudness.true_peak,
        findings=findings,
    )
