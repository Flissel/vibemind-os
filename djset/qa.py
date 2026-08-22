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
from djset.render.tracklist import timestamp

# Erfolgskriterium 3: Ziellaenge plus/minus 5 Prozent.
LENGTH_TOLERANCE = 0.05

# Erfolgskriterium 5: hoechstens minus 1 dBTP.
TRUE_PEAK_CEILING_DB = -1.0

# Erfolgskriterium 4: an der Naht darf das Tempo nicht wegbrechen. Ein Fenster
# um jede Naht wird nachanalysiert.
#
# SEAM_DRIFT_TOLERANCE ist die PRIMAERE Pruefung: wie weit die Intervalle IN
# DER NACHBARSCHAFT der Naht (siehe SEAM_NEIGHBOURHOOD_BEATS unten) vom
# naechsten ganzen Schlag abweichen (siehe _seam_drift_beats). Drei Fassungen
# fuehrten hierher, jede aus einem gemessenen Fehler der vorigen:
#
# Fix Round 1: der groesste Ausreisser im GANZEN Fenster. Reagierte auf jeden
# verpassten Onset irgendwo im Fenster, auch Sekunden von der Naht entfernt -
# feuerte deshalb auf sauberem Material bei 128, 140 und 160 BPM.
#
# Fix Round 2: nur noch das EINE Intervall, das die Naht ueberspannt. Das
# behob die Fehlalarme - aber die Hauptursache war gar nicht die Statistik,
# sondern ein Fixture-Fehler (_entry_length_s in tests/fixtures/render.py,
# siehe dort). Und die Einschraenkung auf ein einziges Intervall kostete
# etwas Echtes: ein durch Audioentfernen erzeugter 0,1-Schlag-Bruch auf einem
# starken Impuls wird vom Beat-Tracker selbst im ueberspannenden Intervall
# maskiert (die Beat-Zeiten dort sind byteidentisch mit einem sauberen
# Render) und taucht ERST im naechsten Intervall auf. Der damalige Kommentar
# hier nannte das faelschlich eine Grenze des Beat-Trackers, die keine
# Statistik auffangen koenne - das stimmte nicht: eine Statistik, die auch
# das naechste Intervall sieht, faengt ihn zuverlaessig.
#
# Fix Round 3 (aktuell): die Nachbarschaft aus SEAM_NEIGHBOURHOOD_BEATS statt
# eines einzelnen Intervalls oder des ganzen Fensters - haelt beide
# Eigenschaften gleichzeitig, siehe dort.
#
# SEAM_BPM_TOLERANCE bleibt als GROBE Zweitpruefung ueber das gemittelte
# Tempo bestehen, fuer einen kompletten Tempowechsel ueber das ganze Fenster
# (z. B. ein falsch zugeschnittener Track bei 140 statt 174 BPM) - ein
# anderes Fehlerbild als ein einzelner Bruch an der Naht.
#
# 0.05 in allen drei Runden ueber mehrere Tempi bestaetigt. Mit der
# Nachbarschaft aus Fix Round 3, ueber alle sieben Tempi (100-174 BPM):
# sauberes Maximum 0.0248 Schlaege, kleinster zuverlaessig erkannter Bruch
# (0,1 Schlag per Audioentfernen auf einem starken Impuls) 0.0909 Schlaege
# (siehe task-6-report.md, Fix Round 3, fuer die volle Sweep-Tabelle). 0.05
# liegt klar dazwischen.
#
# Eine Grenze bleibt bestehen, und sie ist echt, nicht nur eine Frage der
# Nachbarschaftsbreite: Versatz um ein GANZES Vielfaches eines Schlags (1, 2,
# 3 Schlaege) bleibt unsichtbar - bei periodischem Material steht dabei JEDES
# Intervall im Fenster weiterhin auf dem Raster, auch die Nachbarn der Naht.
# Musikalisch der mildere Fehler: nur die Phrasierung verschiebt sich, die
# Schlaege bleiben zusammen.
SEAM_DRIFT_TOLERANCE = 0.05
SEAM_BPM_TOLERANCE = 3.0
SEAM_WINDOW_BARS = 8

# Wie weit um die Naht herum Intervalle noch zaehlen. Nicht nur das
# ueberspannende: ein Bruch, den der Beat-Tracker an der Naht selbst
# glattbuegelt, taucht ein Intervall spaeter auf - gemessen bei einem
# Versatz von 0,1 Schlaegen, der durch Entfernen von Audio auf einem
# starken Impuls entsteht. Und nicht das ganze Fenster: dort schlaegt
# ein verpasster Onset mehrere Sekunden entfernt durch, der mit der
# Naht nichts zu tun hat.
#
# 1.0 gemessen, per Sweep von 0.05 bis 3.0 Schlaegen ueber alle sieben
# Tempi (100-174 BPM): unter etwa 0.46-0.48 Schlaegen liegt bei JEDEM
# Tempo ueberhaupt kein Intervall in der Nachbarschaft - der Naht-
# Zeitpunkt sitzt strukturell nahe einer Taktgrenze, und der Mittelpunkt
# jedes Ein-Schlag-Intervalls ist davon mindestens etwa einen halben
# Schlag entfernt. Ab 0.5 wird die Statistik ueberall definiert, und
# beide geforderten Eigenschaften halten sofort: der 0,1-Schlag-Bruch
# wird bei allen sieben Tempi erkannt, kein sauberer Render ueberschreitet
# SEAM_DRIFT_TOLERANCE. Von 0.5 bis knapp unter 1.5 bleiben beide Werte
# stabil (dasselbe Intervallpaar bleibt in der Nachbarschaft); danach
# ruecken weitere, entferntere Intervalle nach. 1.0 liegt in der Mitte
# dieses stabilen Bereichs - deutlicher Sicherheitsabstand zur
# strukturellen Untergrenze bei 0.5, ohne unnoetig frueh ein drittes
# Intervall hereinzuholen. Volle Sweep-Tabelle in task-6-report.md, Fix
# Round 3.
SEAM_NEIGHBOURHOOD_BEATS = 1.0

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


def _seam_drift_beats(
    seam: Path, seam_offset_s: float, target_bpm: float
) -> float | None:
    """Wie weit das Beatraster um die Naht herum aus dem Takt faellt, in Schlaegen.

    Zwei Entscheidungen stecken darin, und beide sind noetig.

    Gemessen wird der Abstand zum naechsten GANZEN Schlag, nicht zu einem.
    Verpasst die Erkennung einen Onset, ist das Intervall ein fast exaktes
    Vielfaches des Schlags und der Abstand nahe null - obwohl das Raster
    stimmt. Ein Versatz um einen halben Schlag ergibt dagegen 1,5 Schlaege und
    damit einen Abstand von 0,5. Ohne diese Reduktion feuert die Pruefung auf
    sauberem Material, sobald librosa einen Beat auslaesst: gemessen bei 128,
    140 und 160 BPM.

    Und gemessen wird das Maximum ueber alle Intervalle in einer NACHBARSCHAFT
    um die Naht (SEAM_NEIGHBOURHOOD_BEATS), nicht nur ueber das eine
    Intervall, das die Naht ueberspannt, und nicht ueber das ganze Fenster.
    Ein Versatz durch Audioentfernen auf einem starken Impuls kann im
    ueberspannenden Intervall selbst maskiert sein: der Schnitt sieht dort wie
    ein normaler Einsatz aus, die Beat-Zeiten sind byteidentisch mit einem
    sauberen Render, und der Versatz zeigt sich erst im NAECHSTEN Intervall.
    Ein einzelnes Intervall allein verpasst das deshalb; die Nachbarschaft
    faengt es, weil sie auch den Nachbarn sieht - gemessen bei einem
    0,1-Schlag-Versatz an allen sieben getesteten Tempi. Das ganze Fenster
    waere die andere Extremposition: das reagiert auf jeden verpassten Onset
    irgendwo im Fenster, auch Sekunden von der Naht entfernt, und das ist
    keine Antwort auf die Frage, die diese Pruefung stellt.

    Versatz um ein exaktes Vielfaches eines Schlags bleibt unsichtbar. Das ist
    keine Frage der Nachbarschaftsbreite, sondern eine Eigenschaft
    periodischen Materials: dort steht JEDES Intervall im Fenster weiterhin
    auf dem Raster, auch die Nachbarn der Naht. Musikalisch ist es auch der
    mildere Fehler: die Schlaege liegen weiter zusammen, nur die Phrasierung
    verschiebt sich.

    Liefert None, wenn kein Intervall in der Nachbarschaft liegt - das ist
    selbst ein Befund und wird vom Aufrufer als solcher behandelt.
    """
    times = beat_times(seam)
    intervals = np.diff(times)
    if intervals.size == 0:
        return None
    expected = 60.0 / target_bpm
    # Der Mittelpunkt jedes Intervalls entscheidet, ob es zur Nachbarschaft
    # gehoert - nicht sein Anfang oder Ende, damit ein Intervall, das die Naht
    # nur knapp beruehrt, nicht anders behandelt wird als eines, das mittig
    # daneben liegt.
    midpoints = (times[:-1] + times[1:]) / 2.0
    nearby = np.abs(midpoints - seam_offset_s) / expected <= SEAM_NEIGHBOURHOOD_BEATS
    if not nearby.any():
        return None
    beats = intervals[nearby] / expected
    return float(np.max(np.abs(beats - np.round(beats))))


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
        # timestamp() aus tracklist.py floort Minuten und Sekunden aus
        # DEMSELBEN Ganzzahlwert. Die vorherige Fassung rundete `start / 60`
        # und rechnete `start % 60` unabhaengig davon - jenseits der
        # Minutenhaelfte zeigten die beiden Anteile dann auf verschiedene
        # Minuten (111.4s wurde "2:51" statt "1:51").
        findings.append(
            QaFinding(
                kind="gap",
                text=f"Bei {timestamp(start)} steht laenger als "
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
            seam_offset_s = transition.start_at_s - begin / sr

            # Primaerpruefung: wie weit die Intervalle um die Naht herum vom
            # naechsten ganzen Schlag abweichen.
            try:
                drift = _seam_drift_beats(seam, seam_offset_s, plan.target_bpm)
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
                        stats.append(
                            f"ein Intervall um die Naht weicht {drift:.2f} Schlaege "
                            f"vom naechsten ganzen Schlag ab"
                        )
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
                        f"BPM ohne Abweichung ueber {SEAM_DRIFT_TOLERANCE:.2f} "
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
