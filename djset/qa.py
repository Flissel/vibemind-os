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
from djset.dsp.analyze import detect_bpm_and_grid, measure_loudness
from djset.render.engine import bar_length_s

# Erfolgskriterium 3: Ziellaenge plus/minus 5 Prozent.
LENGTH_TOLERANCE = 0.05

# Erfolgskriterium 5: hoechstens minus 1 dBTP.
TRUE_PEAK_CEILING_DB = -1.0

# Erfolgskriterium 4: an der Naht darf das Tempo nicht wegbrechen. Ein Fenster
# um jede Naht wird nachanalysiert; weicht das gemessene Tempo weiter als das
# ab, laufen die beiden Tracks dort nicht mehr zusammen.
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
            try:
                bpm, _grid = detect_bpm_and_grid(seam)
            except Exception:  # noqa: BLE001 - eine unmessbare Naht ist selbst ein Befund
                bpm = None
            if bpm is None or abs(bpm.value - plan.target_bpm) > SEAM_BPM_TOLERANCE:
                gemessen = "nicht messbar" if bpm is None else f"{bpm.value:.1f} BPM"
                findings.append(
                    QaFinding(
                        kind="seam",
                        text=f"An der Naht von {transition.from_track} nach "
                        f"{transition.to_track} bei "
                        f"{transition.start_at_s / 60:.0f}:"
                        f"{transition.start_at_s % 60:04.1f} misst sich "
                        f"{gemessen}, erwartet waren {plan.target_bpm:.1f}.",
                    )
                )

    return QaReport(
        length_s=length_s,
        target_length_s=plan.target_length_s,
        lufs_integrated=loudness.lufs_integrated,
        true_peak=loudness.true_peak,
        findings=findings,
    )
