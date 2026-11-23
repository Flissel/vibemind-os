"""Ziellaenge der Videomusik: aus Sekunden, aus Frames oder aus einem Video.

Bei einem Video zaehlt die Bildspur (Frames mal Bildrate), nicht
`format.duration`: die enthaelt oft den laengeren Ton, die Musik muss aber
mit dem letzten Bild enden.
"""

import json
import math
import subprocess
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

from djset.render.stretch import ffprobe_path


class VideoFehler(RuntimeError):
    """Alle erwartbaren Fehler des Videopakets (fehlende oder unlesbare Datei, falsche Angabe)."""


@dataclass(frozen=True)
class Ziellaenge:
    sekunden: float
    frames: int | None = None
    fps_num: int | None = None
    fps_den: int | None = None

    def samples(self, sr: int) -> int:
        return round(self.sekunden * sr)


def _pruefe_sekunden(s: float) -> float:
    if not isinstance(s, (int, float)) or not math.isfinite(s) or s <= 0:
        raise VideoFehler(f"Die Laenge muss eine positive Zahl in Sekunden sein, nicht {s!r}.")
    return float(s)


def _fps(fps: str) -> tuple[int, int]:
    """'30' oder '30000/1001' als (Zaehler, Nenner); alles andere ist ein VideoFehler."""
    try:
        bruch = Fraction(str(fps).strip())
    except (ValueError, ZeroDivisionError):
        raise VideoFehler(f"Ungueltige Bildrate: {fps!r} (erwartet z. B. '30' oder '30000/1001').") from None
    if bruch <= 0:
        raise VideoFehler(f"Die Bildrate muss positiv sein: {fps!r}.")
    return bruch.numerator, bruch.denominator


def aus_sekunden(s: float) -> Ziellaenge:
    return Ziellaenge(sekunden=_pruefe_sekunden(s))


def aus_frames(frames: int, fps: str) -> Ziellaenge:
    if not isinstance(frames, int) or isinstance(frames, bool) or frames <= 0:
        raise VideoFehler(f"Die Bildzahl muss eine positive ganze Zahl sein, nicht {frames!r}.")
    num, den = _fps(fps)
    return Ziellaenge(sekunden=frames * den / num, frames=frames, fps_num=num, fps_den=den)


def aus_video(pfad: Path) -> Ziellaenge:
    """Laenge der ersten Videospur: gezaehlte Pakete mal Bildrate."""
    pfad = Path(pfad)
    if not pfad.is_file():
        raise VideoFehler(f"Video nicht gefunden: {pfad.name}")
    cmd = [
        ffprobe_path(), "-v", "error", "-select_streams", "v:0", "-count_packets",
        "-show_entries", "stream=nb_read_packets,r_frame_rate", "-of", "json", str(pfad),
    ]
    lauf = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if lauf.returncode != 0:
        detail = lauf.stderr.strip().splitlines()[-1:] or ["unbekannter Fehler"]
        raise VideoFehler(f"{pfad.name} ist nicht lesbar: {detail[0]}")
    try:
        streams = json.loads(lauf.stdout).get("streams", [])
    except json.JSONDecodeError:
        raise VideoFehler(f"{pfad.name} ist nicht lesbar (ffprobe lieferte keine Daten).") from None
    if not streams:
        raise VideoFehler(f"{pfad.name} hat keine Videospur.")
    st = streams[0]
    try:
        frames = int(st["nb_read_packets"])
        return aus_frames(frames, str(st["r_frame_rate"]))
    except (KeyError, ValueError):
        raise VideoFehler(f"{pfad.name}: Bildzahl oder Bildrate der Videospur nicht lesbar.") from None
