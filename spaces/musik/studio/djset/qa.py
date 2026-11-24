"""Stufe 9: prueft das fertige File.

Bewusst messend, nicht rechnend: sie liest set.wav und analysiert es, statt dem
Mixplan zu glauben. Ein Renderer-Fehler, der den Plan korrekt umsetzt aber
falsch schreibt, faellt nur so auf.
"""

import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf

from djset.contracts import AudioFormat, MixPlan, QaFinding, QaReport, StimmNaht
from djset.dsp.analyze import beat_times, detect_bpm_and_grid, measure_loudness
from djset.render.engine import StimmSpur, bar_length_s
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


def luecken(signal: np.ndarray, sr: int) -> list[float]:
    """Anfaenge aller Stillen im Set, die laenger als GAP_SECONDS dauern.

    Eine Stille am Rand zaehlt nicht: der letzte Song darf ausklingen und der
    erste still anfangen - beides ist kein Loch zwischen zwei Songs. Nur
    eine stille Strecke mit Signal davor UND dahinter ist eine Luecke.
    """
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
    return [
        start / sr
        for start, stop in zip(starts, stops)
        if stop - start >= needed and start > 0 and stop < len(quiet)
    ]


# Alter Name, damit bestehende Aufrufer unveraendert weiterlaufen.
_gaps = luecken


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


# Plan 1c. Ab diesem Pegel (RMS je 50 ms) zaehlt eine Stimmspur als Gesang.
# Demucs laesst in der Stimme Reste der Begleitung stehen; gesungene Stellen
# liegen bei Felix' Songs um -20 dBFS, das Uebersprechen weit darunter.
STIMME_AKTIV_DB = -35.0
# So lange duerfen zwei Stimmen im Uebergang gleichzeitig klingen, ohne dass
# es ein Befund ist - ein Atemzug, ein Nachhall.
STIMMEN_ERLAUBT_S = 0.5


def _pegel_bei(spur: StimmSpur, zeiten: np.ndarray) -> np.ndarray:
    index = np.floor((zeiten - spur.start_s) / spur.schritt_s + 1e-9).astype(int)
    drin = (index >= 0) & (index < len(spur.pegel_db))
    pegel = np.full(len(zeiten), -np.inf)
    pegel[drin] = spur.pegel_db[index[drin]]
    return pegel


def stimmen_messen(
    spuren: list[StimmSpur], plan: MixPlan, ungetrennt: list[str]
) -> tuple[list[StimmNaht], list[QaFinding]]:
    """Wie lange in jedem Uebergang beide Stimmen gleichzeitig singen."""
    nach_id = {spur.track_id: spur for spur in spuren}
    naehte: list[StimmNaht] = []
    befunde: list[QaFinding] = []
    for transition in plan.transitions:
        wechsel = transition.stimmwechsel
        naht = StimmNaht(
            from_track=transition.from_track,
            to_track=transition.to_track,
            start_at_s=transition.start_at_s,
            wechsel_s=(
                transition.start_at_s + wechsel.bei_s if wechsel is not None else None
            ),
        )
        aus, ein = nach_id.get(transition.from_track), nach_id.get(transition.to_track)
        if aus is not None and ein is not None:
            schritt = min(aus.schritt_s, ein.schritt_s)
            zeiten = transition.start_at_s + np.arange(
                int(round(transition.overlap_s / schritt))
            ) * schritt
            beide = (_pegel_bei(aus, zeiten) > STIMME_AKTIV_DB) & (
                _pegel_bei(ein, zeiten) > STIMME_AKTIV_DB
            )
            gleichzeitig = float(np.count_nonzero(beide) * schritt)
            naht = naht.model_copy(update={"gleichzeitig_s": gleichzeitig})
            if gleichzeitig > STIMMEN_ERLAUBT_S:
                befunde.append(
                    QaFinding(
                        kind="stimmen",
                        text=f"Im Uebergang von {transition.from_track} nach "
                        f"{transition.to_track} bei "
                        f"{timestamp(transition.start_at_s)} singen beide Stimmen "
                        f"{gleichzeitig:.1f} s gleichzeitig, erlaubt sind "
                        f"{STIMMEN_ERLAUBT_S:.1f} s.",
                    )
                )
        naehte.append(naht)
    if ungetrennt:
        befunde.append(
            QaFinding(
                kind="stimmen_ungetrennt",
                text=f"Ohne getrennte Stimme: {', '.join(ungetrennt)}. An ihren "
                "Uebergaengen koennen sich zwei Stimmen ueberlagern.",
            )
        )
    return naehte, befunde


def check_set(
    path: Path,
    plan: MixPlan,
    wunsch_laenge_s: float | None = None,
    stimmspuren: list[StimmSpur] | None = None,
    ungetrennt: list[str] | None = None,
) -> QaReport:
    """Misst das fertige Set und sammelt, was nicht stimmt.

    `wunsch_laenge_s` ist die gewuenschte Laenge (profile.length_minutes *
    60). Mit ihr misst der Laengen-Hinweis gegen das, was bestellt war; ohne
    sie, wie bisher, gegen plan.target_length_s - die Laenge, die der Plan
    aus dem Material errechnet hat und die er deshalb fast immer trifft.
    """
    signal, sr = sf.read(path, dtype="float32", always_2d=True)
    # Luecken und Naehte werden auf der Mono-Summe gemessen (Stille und Tempo
    # sind keine Kanalfrage); Lautheit und True Peak auf dem Stereosignal, der
    # True Peak als Maximum beider Kanaele.
    mono = signal.mean(axis=1)
    length_s = len(mono) / sr
    loudness = measure_loudness(path, peak_per_channel=True)
    # Das Format der ausgelieferten Datei gehoert in den Bericht, und zwar
    # gemessen: die Spec sagt 44,1 kHz / 24 Bit Stereo zu, geliefert wird
    # Stereo im Float-Subtyp. Der Subtyp ist bewusst so, und die Artefakte
    # sind die einzige Rechenschaft eines unbeaufsichtigten Laufs.
    audio_format = AudioFormat(
        channels=signal.shape[1],
        sample_rate=sr,
        subtype=sf.info(path).subtype,
    )
    findings: list[QaFinding] = []

    soll_s = plan.target_length_s if wunsch_laenge_s is None else wunsch_laenge_s
    soll_wort = "geplant" if wunsch_laenge_s is None else "gewuenscht"
    low = soll_s * (1.0 - LENGTH_TOLERANCE)
    high = soll_s * (1.0 + LENGTH_TOLERANCE)
    if not low <= length_s <= high:
        findings.append(
            QaFinding(
                kind="length",
                text=f"Das Set ist {length_s / 60:.1f} Minuten lang, {soll_wort} "
                f"waren {soll_s / 60:.1f} plus/minus "
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

            # timestamp() statt einer eigenen Rechnung, aus demselben Grund wie
            # bei den Luecken oben: `start / 60` mit `:.0f` rundet die Minute,
            # waehrend `start % 60` unabhaengig davon rechnet - jenseits der
            # Minutenhaelfte zeigten die beiden Anteile auf verschiedene
            # Minuten (111.4 s wurde "2:51.4" statt "1:51.4"). In einem
            # einstuendigen Set weist das den Hoerer eine Minute neben die Naht.
            wo = (
                f"An der Naht von {transition.from_track} nach "
                f"{transition.to_track} bei {timestamp(transition.start_at_s)}"
            )

            if drift is None and bpm is None:
                # Eigene Beanstandungsart, nicht `seam`. Eine unmessbare Naht
                # ist keine gemessene Abweichung, sondern eine offene Frage -
                # und im Betrieb ist sie zu erwarten: das Nahtfenster liegt auf
                # Intro- und Outro-Material, dem duennsten Teil beider Tracks,
                # waehrend SEAM_DRIFT_TOLERANCE an einem durchgehenden
                # Impulszug kalibriert wurde. Wer die Befunde nach kind
                # filtert, muss die beiden Faelle auseinanderhalten koennen.
                findings.append(
                    QaFinding(
                        kind="seam_unmeasurable",
                        text=f"{wo} laesst sich kein Beatraster messen - ob der "
                        f"Beat dort durchlaeuft, bleibt offen. Nahtfenster "
                        f"liegen auf Intro- und Outro-Material; ein duenn "
                        f"besetzter Uebergang ist kein Beweis fuer einen "
                        f"Bruch, aber auch keiner fuer Kontinuitaet.",
                    )
                )
            elif drift_broken or tempo_broken:
                stats = []
                if drift is not None:
                    stats.append(
                        f"ein Intervall um die Naht weicht {drift:.2f} Schlaege "
                        f"vom naechsten ganzen Schlag ab"
                    )
                if bpm is not None:
                    stats.append(f"gemitteltes Tempo {bpm.value:.1f} BPM")
                findings.append(
                    QaFinding(
                        kind="seam",
                        text=f"{wo} stimmt das Timing nicht: {', '.join(stats)}, "
                        f"erwartet waren {plan.target_bpm:.1f} BPM ohne "
                        f"Abweichung ueber {SEAM_DRIFT_TOLERANCE:.2f} Schlaege.",
                    )
                )

    # Plan 1c: nur wenn der Renderer Stimmen gelegt hat oder Songs ungetrennt
    # blieben - ein Lauf ohne Trennung bekommt keinen Stimmen-Abschnitt.
    naehte: list[StimmNaht] = []
    if stimmspuren is not None:
        naehte, befunde = stimmen_messen(stimmspuren, plan, ungetrennt or [])
        findings += befunde

    return QaReport(
        length_s=length_s,
        target_length_s=plan.target_length_s,
        audio_format=audio_format,
        lufs_integrated=loudness.lufs_integrated,
        true_peak=loudness.true_peak,
        findings=findings,
        stimmen=naehte,
    )
