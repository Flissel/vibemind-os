"""Musik unter ein Video legen, ohne das Bild anzufassen.

Die Videospur wird kopiert (kein Neukodieren), der Ton neu gemischt. Die
Ausgabelaenge ist die der Bildspur: kein `-shortest`, stattdessen wird der Ton
aufgefuellt und auf die Videolaenge begrenzt. Geschrieben wird unter einem
Zwischennamen und erst danach umbenannt, damit am Ziel nie etwas Halbfertiges liegt.
"""

import json
import os
import subprocess
from pathlib import Path

from djset.render.stretch import ffmpeg_path, ffprobe_path
from djset.video.laenge import VideoFehler, aus_video

# Vorgabe: die Musik liegt 6 dB unter dem Originalton, der bleibt hoerbar.
ORIGINALTON_DB = -6.0
_FFMPEG_TIMEOUT_S = 600
_FFPROBE_TIMEOUT_S = 60


def _hat_ton(video: Path) -> bool:
    cmd = [
        ffprobe_path(), "-v", "error", "-select_streams", "a", "-show_entries",
        "stream=index", "-of", "json", str(video),
    ]
    try:
        lauf = subprocess.run(
            cmd, capture_output=True, encoding="utf-8", errors="replace",
            check=False, timeout=_FFPROBE_TIMEOUT_S,
        )
        return bool(json.loads(lauf.stdout or "{}").get("streams"))
    except subprocess.TimeoutExpired:
        raise VideoFehler(f"{video.name}: ffprobe hat die Tonspur nicht in {_FFPROBE_TIMEOUT_S} s gefunden") from None
    except json.JSONDecodeError:
        raise VideoFehler(f"{video.name}: Tonspuren nicht lesbar (ffprobe lieferte keine Daten)") from None


def unterlegen(
    video: Path,
    musik: Path,
    ziel: Path,
    musik_db: float = ORIGINALTON_DB,
    original_ersetzen: bool = False,
) -> None:
    """Legt `musik` unter `video` und schreibt das Ergebnis nach `ziel`."""
    video, musik, ziel = Path(video), Path(musik), Path(ziel)
    if not video.is_file():
        raise VideoFehler(f"Video nicht gefunden: {video.name}")
    if not musik.is_file():
        raise VideoFehler(f"Musik nicht gefunden: {musik.name}")
    dauer_s = aus_video(video).sekunden
    mit_original = _hat_ton(video) and not original_ersetzen

    if mit_original:
        filter_ = (
            f"[1:a]volume={musik_db}dB[m];"
            "[0:a][m]amix=inputs=2:duration=longest:normalize=0,apad[a]"
        )
    else:
        # Allein bleibt die Musik auf ihrem Pegel: `musik_db` regelt nur das Verhaeltnis zum Original.
        filter_ = "[1:a]apad[a]"

    try:
        ziel.parent.mkdir(parents=True, exist_ok=True)
    except OSError as fehler:
        raise VideoFehler(f"{ziel}: Zielordner nicht anlegbar ({fehler})") from fehler
    zwischen = ziel.with_name(f"{ziel.stem}.teil{os.getpid()}{ziel.suffix}")
    cmd = [
        ffmpeg_path(), "-v", "error", "-y", "-i", str(video), "-i", str(musik),
        "-filter_complex", filter_, "-map", "0:v:0", "-map", "[a]",
        "-c:v", "copy", "-c:a", "aac", "-t", f"{dauer_s:.6f}", str(zwischen),
    ]
    try:
        lauf = subprocess.run(
            cmd, capture_output=True, encoding="utf-8", errors="replace",
            check=False, timeout=_FFMPEG_TIMEOUT_S,
        )
        if lauf.returncode != 0:
            ende = "\n".join(lauf.stderr.strip().splitlines()[-5:]) or "keine Meldung"
            raise VideoFehler(f"ffmpeg konnte {video.name} nicht mit {musik.name} mischen: {ende}")
        os.replace(zwischen, ziel)
    except OSError as fehler:
        raise VideoFehler(f"{ziel}: Ergebnis nicht schreibbar ({fehler})") from fehler
    except subprocess.TimeoutExpired:
        raise VideoFehler(
            f"ffmpeg hat {video.name} nicht in {_FFMPEG_TIMEOUT_S} s fertig gemischt"
        ) from None
    finally:
        zwischen.unlink(missing_ok=True)
