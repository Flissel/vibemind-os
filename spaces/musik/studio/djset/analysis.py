"""Stufe 3 ueber alle Dateien: parallel analysieren, Vorhandenes wiederverwenden.

Die Analyse ist der teuerste Schritt des Laufs - sie oeffnet Audio, rechnet DSP
und fragt pro Track ein Modell. Deshalb zwei Dinge: sie faechert ueber Threads
auf, und jedes Ergebnis landet sofort als Artefakt auf der Platte. Ein
abgebrochener Lauf verliert damit hoechstens die Tracks, die gerade in Arbeit
waren, statt aller bereits bezahlten.
"""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from pydantic import ValidationError

from djset.agents.analyst import analyse_track
from djset.agents.base import AgentClient, Budget
from djset.contracts import Profile, TrackAnalysis, UnreadableTrack
from djset.run import Run
from djset.watch import AudioFile

# Vier gleichzeitige Analysen. Jede haelt einen SDK-Aufruf und rechnet DSP;
# mehr Threads bringen wenig, weil librosa ohnehin BLAS-parallel rechnet.
DEFAULT_WORKERS = 4


def artifact_name(track_id: str) -> str:
    """Der Artefaktpfad einer Trackanalyse, relativ zum Run-Ordner."""
    return f"tracks/{track_id}.json"


def _read_existing(run: Run, track_id: str) -> TrackAnalysis | UnreadableTrack | None:
    """Liest ein vorhandenes Artefakt, falls es eines gibt.

    Beide Formen liegen unter demselben Namen: erst die vollstaendige Analyse
    versuchen, sonst den Fehlvermerk. Was sich als keines von beidem lesen
    laesst, gilt als nicht vorhanden und wird neu analysiert.

    Die drei gefangenen Fehlerarten sind genau die, die ein halb geschriebenes
    Artefakt erzeugt - und halb geschriebene Artefakte sind der Normalfall, fuer
    den diese Funktion existiert: ValidationError bei abgeschnittenem oder
    schemafremdem JSON, UnicodeDecodeError wenn der Abbruch mitten in ein
    Mehrbyte-Zeichen fiel (die Notizen des Analysten sind deutscher Fliesstext,
    Umlaute darin sind die Regel), OSError wenn die Datei zwischen has() und
    read_model verschwindet. Alle drei bedeuten dasselbe: unbrauchbar, neu
    analysieren. Sie duerfen den Lauf nicht reissen, sonst macht ein einziges
    kaputtes Artefakt die ganze Wiederaufnahme zunichte.
    """
    name = artifact_name(track_id)
    if not run.has(name):
        return None
    for model_type in (TrackAnalysis, UnreadableTrack):
        try:
            return run.read_model(name, model_type)
        except (ValidationError, UnicodeDecodeError, OSError):
            continue
    return None


def analyse_all(
    client: AgentClient,
    files: list[AudioFile],
    profile: Profile,
    run: Run,
    budget: Budget,
    workers: int = DEFAULT_WORKERS,
) -> tuple[list[TrackAnalysis], list[UnreadableTrack]]:
    """Analysiert alle Dateien und schreibt je ein Artefakt.

    ACHTUNG zur Wiederaufnahme: ein vorhandenes Artefakt wird allein anhand
    der track_id wiederverwendet, und die kommt aus dem Dateinamen. Der
    Dateiinhalt wird nicht geprueft - das Wiederaufnehmen setzt also voraus,
    dass watch/ zwischen den Laeufen unveraendert bleibt. Wer einen Track bei
    Suno neu generiert und unter demselben Titel wieder ablegt (Suno behaelt
    den Titel bei), bekommt stillschweigend die alte Analyse. Dann das
    betroffene tracks/<id>.json loeschen.

    Zum Schalter: die Spec nennt `build --resume`, die CLI hat ihn nicht und
    braucht ihn auch nicht. `dj-set build` nimmt ohne Argument den juengsten
    Lauf ohne set.wav und arbeitet dessen vorhandene Artefakte weiter,
    `--run <id>` waehlt einen bestimmten. Das Wiederaufnehmen ist damit das
    Normalverhalten und kein eigener Schalter - wer frisch analysieren will,
    loescht die Artefakte oder legt mit `dj-set plan` einen neuen Lauf an.
    """

    def one(file: AudioFile) -> TrackAnalysis | UnreadableTrack:
        existing = _read_existing(run, file.id)
        if existing is not None:
            return existing
        result = analyse_track(client, Path(file.path), file.id, profile, budget)
        run.write_model(artifact_name(file.id), result)
        return result

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        results = list(pool.map(one, files))

    analyses = [r for r in results if isinstance(r, TrackAnalysis)]
    unreadable = [r for r in results if isinstance(r, UnreadableTrack)]
    return analyses, unreadable
