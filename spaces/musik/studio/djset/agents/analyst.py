"""Stufe 3: misst jeden Track und faellt ein Urteil ueber die Brauchbarkeit.

Orchestriert, nicht werkzeugnutzend. Python ruft die DSP-Messung, reicht die
Zahlen an das Modell und dreht die Schleife selbst; das Modell darf eine
Neumessung mit Tempo-Hinweis anfordern, ruft die Messung aber nicht auf. Damit
bleibt die Stufe ohne Netz testbar, und die Auffaecherung ueber viele Dateien
ist ein Thread-Pool statt N SDK-Subprozesse.

Was Arithmetik ist, bleibt Arithmetik: die Oktavkorrektur laeuft ueber
snap_to_range gegen den Profilkorridor, bevor das Modell die Zahlen sieht. Das
Modell urteilt ueber Brauchbarkeit und Energiepegel, nicht ueber Faktor zwei.
"""

from pathlib import Path

import numpy as np
import soundfile as sf
from pydantic import BaseModel, Field

from djset.agents.base import AgentClient, Budget, run_agent
from djset.contracts import (
    Profile,
    TrackAnalysis,
    TrackMeasurements,
    UnreadableTrack,
    Verdict,
)
from djset.dsp.analyze import measure_track, mischbereich, snap_to_range

# Wurde die Oktave korrigiert, stammt der Wert nicht mehr aus der Messung
# allein - die Konfidenz muss das abbilden.
SNAP_CONFIDENCE_PENALTY = 0.5

# Hoechstens so viele Modellfragen pro Track.
MAX_ROUNDS = 2

# Unterhalb dieses Spitzenwerts enthaelt die Datei kein verwertbares Signal.
_SILENCE_FLOOR = 1e-6


class AnalystAnswer(BaseModel):
    """Was der Analyst zurueckgibt.

    remeasure_with_bpm ist der orchestrierte Ersatz fuer einen Werkzeugaufruf:
    das Modell sagt, mit welchem Tempo-Hinweis neu gemessen werden soll, und
    Python fuehrt das aus.
    """

    usable: bool
    notes: str = Field(min_length=1)
    energy_level: float = Field(ge=0.0, le=1.0)
    remeasure_with_bpm: float | None = None


PROMPT = """Du bist ein DJ und beurteilst, ob ein Track in ein Set passt.

Das Set: {genre}, Ziel-BPM {bpm_target}, erlaubt {bpm_min} bis {bpm_max},
harmonische Strenge {strictness}. Der Energiebogen des Sets laeuft ueber diese
Stuetzpunkte aus Position und Zielenergie: {arc}.

Gemessen wurde (Track {track_id}):
- Dauer: {duration:.1f} Sekunden
- BPM: {bpm_value:.1f}, Rastersicherheit {bpm_conf:.2f}
- Taktraster: erster Downbeat bei {downbeat:.2f} s, Taktlaenge {bar:.3f} s
- Tonart: {camelot}, Sicherheit {key_conf:.2f}
- Energiekurve in 4-Takt-Schritten: {energy}
- Leiser als der Kern: Intro endet bei {intro:.1f} s, Outro beginnt bei {outro:.1f} s
- Gemischt wird von {mix_from:.1f} bis {mix_intro:.1f} s und ab {mix_outro:.1f} s
- Lautheit: {lufs:.1f} LUFS, True Peak {peak:.1f} dB

Zu Intro und Outro: Viele fertige Songs bleiben bis zum Schluss laut, dann
liegt das gemessene Outro auf dem Trackende. Die Kette blendet solche Songs wie
ein DJ ueber die letzten und ersten 16 Takte. Ein fehlendes leises Intro oder
Outro ist deshalb kein Grund, den Track auszusortieren.

Beurteile:

1. usable - passt der Track ins Set? Achte darauf, ob das Tempo im Korridor
   liegt, ob das Raster stabil genug fuer Cue-Punkte ist und ob die Tonart
   aufs Camelot-Rad passt.
2. notes - eine knappe Begruendung in einem Satz. Bei usable false muss dort
   stehen, woran es liegt.
3. energy_level - wo dieser Track auf der Energieskala des Bogens steht,
   zwischen 0 und 1. Die gemessene Energiekurve ist zwar vergleichbar normiert,
   deckt bei fertig gemastertem Material aber nur einen schmalen Bereich um
   0,75 bis 0,87 ab - zu eng fuer den Bogen von 0,30 bis 0,90. Ordne den Track
   so ein, wie du ihn im Vergleich zu den Zielenergien des Bogens hoerst.
4. remeasure_with_bpm - normalerweise null. Nur setzen, wenn die Zahlen auf ein
   falsch erkanntes Raster deuten und mit einem Tempo-Hinweis etwas Besseres
   herauskaeme. Nenne dann das vermutete Tempo.

Antworte ausschliesslich mit dem geforderten JSON."""


def _has_signal(path: Path) -> bool:
    """Ob die Datei ueberhaupt Signal enthaelt.

    Digitale Stille ist der Fall, bei dem librosa warnt und danach eine
    willkuerliche Tonart liefert. Der wird hier vorher abgefangen.

    Bewusst nicht ueber warnings.catch_warnings: das veraendert eine globale
    Liste und ist ausdruecklich nicht threadsicher, und ab Task 3 laeuft diese
    Funktion aus einem Thread-Pool. Ein Blockdurchlauf ueber die Datei kostet
    einen Bruchteil dessen, was die eigentliche Analyse ohnehin an Lesevorgaengen
    braucht, und ist dafuer deterministisch.
    """
    peak = 0.0
    with sf.SoundFile(path) as handle:
        for block in handle.blocks(blocksize=1 << 16, dtype="float32"):
            if block.size:
                peak = max(peak, float(np.max(np.abs(block))))
            if peak >= _SILENCE_FLOOR:
                return True
    return peak >= _SILENCE_FLOOR


def _measure(path: Path, track_id: str, hint_bpm: float | None) -> TrackMeasurements:
    """Misst einen Track und lehnt entartetes Material sauber ab."""
    if not _has_signal(path):
        raise ValueError("Die Datei enthaelt kein Signal")
    return measure_track(path, track_id, hint_bpm=hint_bpm)


def _measure_in_corridor(
    path: Path, track_id: str, profile: Profile, hint_bpm: float | None = None
) -> TrackMeasurements:
    """Misst und holt Halftime- oder Doubletime-Lesungen in den Korridor.

    Wird neu gemessen statt die Zahl zu patchen, und zwar aus einem harten
    Grund: bar_length_s, energy und mix_regions sind alle aus dem erkannten
    Raster abgeleitet. Wer nur bpm.value ueberschreibt, erzeugt einen Datensatz,
    der sich selbst widerspricht - 174 BPM neben einer Taktlaenge von 2,76
    Sekunden - und Stufe 6 setzt ihre Cue-Punkte auf genau dieses Raster.

    Bleibt die Neumessung trotz Hinweis ausserhalb des Korridors, wird nichts
    zurechtgebogen: die Zahlen gehen so, wie sie gemessen wurden, an das Modell,
    und das urteilt darueber. Ein in sich stimmiger Datensatz ausserhalb des
    Korridors ist brauchbarer als ein frisierter innerhalb.

    `hint_bpm` traegt den vom Modell angeforderten Tempo-Hinweis herein. Auch
    dessen Ergebnis laeuft durch dieselbe Korridorpruefung: es ist die einzige
    Stelle, an der eine Modellaeusserung eine Zahl steuert, die spaeter die
    Cue-Punkte formt. Faellt es daneben und laesst sich die Oktave korrigieren,
    passiert dasselbe wie bei der ersten Messung - neu messen und die Konfidenz
    senken.
    """
    measurements = _measure(path, track_id, hint_bpm)
    snapped, corrected = snap_to_range(
        measurements.bpm.value, profile.bpm.min, profile.bpm.max
    )
    if not corrected:
        return measurements

    retried = _measure(path, track_id, snapped)
    # Konfidenz senken: das Tempo stammt jetzt aus einem Hinweis, nicht mehr
    # aus der Messung allein. model_validate statt model_copy(update=...),
    # letzteres validiert in Pydantic v2 nicht.
    return TrackMeasurements.model_validate(
        retried.model_dump()
        | {
            "bpm": {
                "value": retried.bpm.value,
                "confidence": retried.bpm.confidence * SNAP_CONFIDENCE_PENALTY,
            }
        }
    )


def _prompt(measurements: TrackMeasurements, profile: Profile) -> str:
    arc = ", ".join(f"({p.position:.2f}, {p.energy:.2f})" for p in profile.energy_arc)
    energy = ", ".join(f"{value:.2f}" for value in measurements.energy)
    gemischt = mischbereich(measurements)
    return PROMPT.format(
        mix_from=measurements.beatgrid.first_downbeat_s,
        mix_intro=gemischt.intro_end_s,
        mix_outro=gemischt.outro_start_s,
        track_id=measurements.id,
        genre=profile.genre,
        bpm_target=profile.bpm.target,
        bpm_min=profile.bpm.min,
        bpm_max=profile.bpm.max,
        strictness=profile.harmonic_strictness,
        arc=arc,
        duration=measurements.duration_s,
        bpm_value=measurements.bpm.value,
        bpm_conf=measurements.bpm.confidence,
        downbeat=measurements.beatgrid.first_downbeat_s,
        bar=measurements.beatgrid.bar_length_s,
        camelot=measurements.key.camelot,
        key_conf=measurements.key.confidence,
        energy=energy,
        intro=measurements.mix_regions.intro_end_s,
        outro=measurements.mix_regions.outro_start_s,
        lufs=measurements.loudness.lufs_integrated,
        peak=measurements.loudness.true_peak,
    )


def analyse_track(
    client: AgentClient,
    path: Path,
    track_id: str,
    profile: Profile,
    budget: Budget,
    max_rounds: int = MAX_ROUNDS,
    messung: TrackMeasurements | None = None,
) -> TrackAnalysis | UnreadableTrack:
    """Misst einen Track und holt das Urteil des Analysten dazu.

    Mit `messung` (Laeufe aus dem Bestand) wird nicht gemessen: die
    gespeicherte Messung geht so, wie sie ist, an das Modell - ohne
    Korridor-Snap und ohne die vom Modell verlangte Neumessung. Sonst koennte
    das Tempo im Lauf von tempo.json abweichen, nach dem der Bereich gewaehlt
    wurde. Das Urteil des Analysten ueber die Brauchbarkeit bleibt.
    """
    if messung is not None:
        answer = run_agent(client, _prompt(messung, profile), AnalystAnswer, budget)
        return _with_verdict(messung, answer)

    try:
        measurements = _measure_in_corridor(path, track_id, profile)
    except Exception as error:  # noqa: BLE001 - jede Messstoerung endet gleich
        return UnreadableTrack(id=track_id, path=str(path), error=str(error))

    rounds = max(1, max_rounds)
    answer = run_agent(client, _prompt(measurements, profile), AnalystAnswer, budget)

    for _extra in range(rounds - 1):
        if answer.remeasure_with_bpm is None:
            break
        try:
            # Der Hinweis kommt vom Modell, die Korridorpruefung bleibt
            # trotzdem dieselbe. Ein Modell, das 87 als Halftime-Fehler erkennt
            # und daraufhin 116 vorschlaegt, bekaeme sonst einen Datensatz
            # ausserhalb des Profils zu voller Konfidenz - und Stufe 6 setzt
            # ihre Cue-Punkte auf genau dieses Raster.
            measurements = _measure_in_corridor(
                path, track_id, profile, answer.remeasure_with_bpm
            )
        except Exception as error:  # noqa: BLE001
            return UnreadableTrack(id=track_id, path=str(path), error=str(error))
        answer = run_agent(client, _prompt(measurements, profile), AnalystAnswer, budget)

    return _with_verdict(measurements, answer)


def _with_verdict(
    measurements: TrackMeasurements, answer: AnalystAnswer
) -> TrackAnalysis:
    return TrackAnalysis(
        **measurements.model_dump(),
        verdict=Verdict(
            usable=answer.usable,
            notes=answer.notes,
            energy_level=answer.energy_level,
        ),
    )
