"""Auslieferung: das fertige Set als MP3, die man herumschicken kann.

set.wav (Float-WAV) ist das Arbeitsergebnis und bleibt die Fertig-Marke des
Laufs; die MP3 kommt zusaetzlich daneben. Wie in render/stretch.py ist ffmpeg
der einzige Weg - der Aufruf und die Fehlerklassen sind dieselben.
"""

import subprocess
from pathlib import Path

from djset.render.stretch import RenderFailed, ffmpeg_path


def to_mp3(source: Path, target: Path, bitrate: str = "320k") -> None:
    """Schreibt `source` als MP3 nach `target`, Kanaele und Abtastrate bleiben.

    Geschrieben wird unter einem Zwischennamen neben dem Ziel und erst nach
    Erfolg umbenannt - ein gescheiterter Lauf hinterlaesst keine halbe Datei.
    Eine fehlende Quelle oder ein ffmpeg-Fehler ist ein RenderFailed mit der
    Meldung von ffmpeg.
    """
    if not source.is_file():
        raise RenderFailed(f"MP3-Export: Quelle {source} existiert nicht")

    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + ".partial")
    command = [
        ffmpeg_path(),
        "-hide_banner",
        "-loglevel", "error",
        "-nostdin",
        "-y",
        "-i", str(source),
        "-vn",
        "-c:a", "libmp3lame",
        "-b:a", bitrate,
        "-f", "mp3",
        str(partial),
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        partial.unlink(missing_ok=True)
        raise RenderFailed(
            f"ffmpeg scheiterte beim MP3-Export von {source.name}: "
            f"{result.stderr.strip() or 'keine Meldung'}"
        )
    partial.replace(target)
