"""Findet das Material im watch-Ordner und prueft vorab, ob es reicht.

Der Vorab-Check steht bewusst vor dem ersten Agentenaufruf: zu wenig Material
ist ein Zustand, den kein Agent reparieren kann, und ein Lauf, der das erst
nach der halben Analyse merkt, hat Tokens fuer nichts verbraucht.
"""

from dataclasses import dataclass
from pathlib import Path

from djset.contracts import Profile
from djset.run import slugify

AUDIO_SUFFIXES = (".wav", ".flac", ".mp3", ".m4a", ".aiff", ".aif", ".ogg")


class NotEnoughMaterial(RuntimeError):
    """Zu wenig Dateien im watch-Ordner, um das Profil zu erfuellen."""


@dataclass(frozen=True)
class AudioFile:
    id: str
    path: Path


def scan(watch_dir: Path) -> list[AudioFile]:
    """Alle Audiodateien im Ordner, stabil sortiert, mit eindeutiger ID.

    Die ID kommt aus dem Dateinamen und nicht aus einem Zaehler, damit ein
    zweiter Lauf ueber denselben Ordner dieselben Artefaktnamen erzeugt - das
    ist die Voraussetzung dafuer, dass build --resume vorhandene Analysen
    wiedererkennt.
    """
    if not watch_dir.is_dir():
        return []

    found = sorted(
        path
        for path in watch_dir.iterdir()
        if path.is_file() and path.suffix.lower() in AUDIO_SUFFIXES
    )

    files: list[AudioFile] = []
    used: set[str] = set()
    for path in found:
        base = slugify(path.stem) or "track"
        track_id = base
        counter = 2
        while track_id in used:
            track_id = f"{base}-{counter}"
            counter += 1
        used.add(track_id)
        files.append(AudioFile(id=track_id, path=path))
    return files


def check_enough(files: list[AudioFile], profile: Profile) -> None:
    """Harter Abbruch, wenn das Material die benoetigte Trackzahl unterschreitet."""
    needed = profile.track_count.needed
    if len(files) < needed:
        raise NotEnoughMaterial(
            f"Nur {len(files)} Audiodateien im watch-Ordner, gebraucht werden "
            f"{needed}. Generiere die fehlenden Tracks bei Suno und lege sie dort ab."
        )
