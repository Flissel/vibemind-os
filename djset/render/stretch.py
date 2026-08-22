"""Zeitdehnung ueber ffmpegs rubberband-Filter.

Die Spec nennt pyrubberband. Das ist ein duenner Wrapper um ein
rubberband-CLI-Binary, das auf dieser Maschine nicht existiert - waehrend das
installierte ffmpeg librubberband einkompiliert hat und den Filter bereitstellt.
Gleiche Bibliothek, gleiche Qualitaet, kein neues Installationsartefakt. Die
Abweichung betrifft den Weg, nicht das Werkzeug.

Dies ist die einzige Stelle im Renderer, die einen Unterprozess startet. Alles
andere ist Arithmetik ueber Samples und bleibt in numpy - siehe eq.py.

Bit-Identitaet zwischen zwei Laeufen mit demselben Input ist keine Annahme,
sondern verifiziertes Verhalten von libavformats WAV-Muxer: dessen einziger
Metadaten-Chunk ist ein statischer Encoder-Versionsstring (kein Zeitstempel),
also ist die Ausgabe bei gleicher ffmpeg-Version deterministisch - das gilt
fuer WAV/PCM-Output und ist bei einem ffmpeg-Upgrade neu zu pruefen.
"""

import shutil
import subprocess
from pathlib import Path

from djset.contracts import MAX_RATIO, MIN_RATIO

SR = 44_100


class RenderToolMissing(RuntimeError):
    """Ein Werkzeug, das der Renderer braucht, liegt nicht auf dem Pfad.

    Bewusst getrennt von RenderFailed: das hier ist eine Frage der Umgebung,
    die der Nutzer beheben kann. Ein gescheiterter Lauf ist etwas anderes.
    """


class RenderFailed(RuntimeError):
    """ffmpeg lief und scheiterte.

    Der Renderer ist deterministisch - laut Spec Abschnitt 7 ist ein Fehler
    hier ein Bug und kein Wiederholungsfall. Er darf nicht als Umgebungsproblem
    durchgehen, sonst sucht der Nutzer an der falschen Stelle.
    """


def ffmpeg_path() -> str:
    """Der Pfad zu ffmpeg, oder ein klarer Fehler statt eines Absturzes."""
    found = shutil.which("ffmpeg")
    if found is None:
        raise RenderToolMissing(
            "ffmpeg wurde nicht gefunden. Der Renderer braucht ffmpeg mit "
            "einkompiliertem librubberband; installiere es und lege es auf den Pfad."
        )
    return found


def stretch(source: Path, target: Path, ratio: float, sr: int = SR) -> None:
    """Zieht eine Audiodatei auf ein anderes Tempo, ohne die Tonhoehe zu aendern.

    `ratio` ist Ziel-Tempo geteilt durch Quell-Tempo, genau wie
    MixEntry.timestretch_ratio - die Ausgabedauer ist also Quelldauer geteilt
    durch ratio. ffmpegs `tempo` bedeutet dasselbe, deshalb geht der Wert
    unveraendert durch.
    """
    if not MIN_RATIO <= ratio <= MAX_RATIO:
        raise ValueError(
            f"Stretch-Verhaeltnis {ratio} liegt ausserhalb von "
            f"{MIN_RATIO} bis {MAX_RATIO}"
        )

    target.parent.mkdir(parents=True, exist_ok=True)
    command = [
        ffmpeg_path(),
        "-hide_banner",
        "-loglevel", "error",
        "-nostdin",
        "-y",
        "-i", str(source),
        "-filter:a", f"rubberband=tempo={ratio:.10f}",
        "-ar", str(sr),
        "-c:a", "pcm_f32le",
        str(target),
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise RenderFailed(
            f"ffmpeg scheiterte beim Dehnen von {source.name}: "
            f"{result.stderr.strip() or 'keine Meldung'}"
        )
