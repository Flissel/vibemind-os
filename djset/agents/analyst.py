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

import warnings
from pathlib import Path

from pydantic import BaseModel, Field

from djset.agents.base import AgentClient, Budget, run_agent
from djset.contracts import (
    Profile,
    TrackAnalysis,
    TrackMeasurements,
    UnreadableTrack,
    Verdict,
)
from djset.dsp.analyze import measure_track, snap_to_range

# Wurde die Oktave korrigiert, stammt der Wert nicht mehr aus der Messung
# allein - die Konfidenz muss das abbilden.
SNAP_CONFIDENCE_PENALTY = 0.5

# Hoechstens so viele Modellfragen pro Track.
MAX_ROUNDS = 2


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

Gemessen wurde:
- Dauer: {duration:.1f} Sekunden
- BPM: {bpm_value:.1f}, Rastersicherheit {bpm_conf:.2f}
- Taktraster: erster Downbeat bei {downbeat:.2f} s, Taktlaenge {bar:.3f} s
- Tonart: {camelot}, Sicherheit {key_conf:.2f}
- Energiekurve in 4-Takt-Schritten: {energy}
- Intro endet bei {intro:.1f} s, Outro beginnt bei {outro:.1f} s
- Lautheit: {lufs:.1f} LUFS, True Peak {peak:.1f} dB

Beurteile:

1. usable - passt der Track ins Set? Achte darauf, ob das Tempo im Korridor
   liegt, ob das Raster stabil genug fuer Cue-Punkte ist, ob es ueberhaupt ein
   Intro und ein Outro zum Mixen gibt und ob die Tonart aufs Camelot-Rad passt.
2. notes - eine knappe Begruendung in einem Satz. Bei usable false muss dort
   stehen, woran es liegt.
3. energy_level - wo dieser Track auf der Energieskala des Bogens steht,
   zwischen 0 und 1. Die gemessene Kurve ist trackrelativ und taugt dafuer
   nicht; ordne den Track so ein, wie du ihn im Vergleich zu den Zielenergien
   des Bogens hoerst.
4. remeasure_with_bpm - normalerweise null. Nur setzen, wenn die Zahlen auf ein
   falsch erkanntes Raster deuten und mit einem Tempo-Hinweis etwas Besseres
   herauskaeme. Nenne dann das vermutete Tempo.

Antworte ausschliesslich mit dem geforderten JSON."""


def _snap_octave(measurements: TrackMeasurements, profile: Profile) -> TrackMeasurements:
    """Haelt Halftime- und Doubletime-Lesungen an den Profilkorridor an."""
    snapped, corrected = snap_to_range(
        measurements.bpm.value, profile.bpm.min, profile.bpm.max
    )
    if not corrected:
        return measurements
    # model_validate statt model_copy(update=...): letzteres validiert nicht.
    return TrackMeasurements.model_validate(
        measurements.model_dump()
        | {
            "bpm": {
                "value": snapped,
                "confidence": measurements.bpm.confidence * SNAP_CONFIDENCE_PENALTY,
            }
        }
    )


def _prompt(measurements: TrackMeasurements, profile: Profile) -> str:
    arc = ", ".join(f"({p.position:.2f}, {p.energy:.2f})" for p in profile.energy_arc)
    energy = ", ".join(f"{value:.2f}" for value in measurements.energy)
    return PROMPT.format(
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


def _measure(path: Path, track_id: str, hint_bpm: float | None) -> TrackMeasurements:
    """Misst und macht librosa-Warnungen bei entartetem Material zu Fehlern.

    Unter filterwarnings error waere eine Warnung ohnehin ein Absturz; hier wird
    sie zu einem sauberen Messfehler, den der Aufrufer als UnreadableTrack
    verbuchen kann, statt den ganzen Lauf zu reissen.
    """
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        return measure_track(path, track_id, hint_bpm=hint_bpm)


def analyse_track(
    client: AgentClient,
    path: Path,
    track_id: str,
    profile: Profile,
    budget: Budget,
    max_rounds: int = MAX_ROUNDS,
) -> TrackAnalysis | UnreadableTrack:
    """Misst einen Track und holt das Urteil des Analysten dazu."""
    try:
        measurements = _measure(path, track_id, None)
    except Exception as error:  # noqa: BLE001 - jede Messstoerung endet gleich
        return UnreadableTrack(id=track_id, path=str(path), error=str(error))

    rounds = max(1, max_rounds)
    measurements = _snap_octave(measurements, profile)
    answer = run_agent(client, _prompt(measurements, profile), AnalystAnswer, budget)

    for _extra in range(rounds - 1):
        if answer.remeasure_with_bpm is None:
            break
        try:
            measurements = _measure(path, track_id, answer.remeasure_with_bpm)
        except Exception as error:  # noqa: BLE001
            return UnreadableTrack(id=track_id, path=str(path), error=str(error))
        measurements = _snap_octave(measurements, profile)
        answer = run_agent(client, _prompt(measurements, profile), AnalystAnswer, budget)

    return TrackAnalysis(
        **measurements.model_dump(),
        verdict=Verdict(
            usable=answer.usable,
            notes=answer.notes,
            energy_level=answer.energy_level,
        ),
    )
