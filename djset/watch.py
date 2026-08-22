"""Findet das Material im watch-Ordner und prueft vorab, ob es reicht.

Der Vorab-Check steht bewusst vor dem ersten Agentenaufruf: zu wenig Material
ist ein Zustand, den kein Agent reparieren kann, und ein Lauf, der das erst
nach der halben Analyse merkt, hat Tokens fuer nichts verbraucht.
"""

from dataclasses import dataclass
from pathlib import Path

import soundfile as sf

from djset.contracts import Profile
from djset.render.stretch import SR, UnsupportedSampleRate
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
    ist die Voraussetzung dafuer, dass ein wiederaufgenommener Lauf vorhandene
    Analysen wiedererkennt.

    Die Sortierung laeuft ueber casefold und erst danach ueber den rohen Namen,
    nicht ueber den Pfad. `sorted()` auf Path vergleicht zeichenweise und ist
    damit auf POSIX gross-/kleinschreibungsempfindlich, unter Windows nicht -
    und genau diese Reihenfolge speist den Kollisionszaehler unten. Bei
    "Morning Glass.wav" neben "morning glass.mp3" vergaeben die beiden
    Plattformen morning-glass und morning-glass-2 an jeweils die andere Datei,
    ein zwischen Maschinen getragener Run-Ordner haenge also jede
    zwischengespeicherte Analyse an den falschen Track. Der rohe Name als
    zweites Kriterium haelt die Ordnung stabil, wo casefold zwei Namen
    gleichsetzt.
    """
    if not watch_dir.is_dir():
        return []

    found = sorted(
        (
            path
            for path in watch_dir.iterdir()
            if path.is_file() and path.suffix.lower() in AUDIO_SUFFIXES
        ),
        key=lambda path: (path.name.casefold(), path.name),
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


def check_sample_rate(files: list[AudioFile], expected_hz: int = SR) -> None:
    """Harter Abbruch, wenn Material nicht in der Rate der Kette vorliegt.

    Gehoert in den Vorab-Check und nicht in den Renderer. Eine 48-kHz-Datei
    kam bisher durch Scan, Analyse (librosa resampelt anstandslos), Kuratierung,
    Anordnung, Entwurf und Kritik und riss den Lauf erst in Stufe 8 ab - mit
    einem Traceback, ohne qa-report.md und nachdem jedes Token bezahlt war.
    Spec Abschnitt 7 stellt genau diese Klasse von Pruefung davor: "`build`
    zaehlt zuerst die Dateien in `watch/` ... bevor der erste Agent Tokens
    verbraucht."

    Gelesen werden nur die Kopfdaten (sf.info), nicht das Audio.

    Eine Datei, deren Kopf sich gar nicht lesen laesst, ist hier KEIN Befund:
    das ist der Fall "einzelne Datei kaputt oder unlesbar", und dafuer sieht
    die Spec vor, dass der Analyst sie als unbrauchbar markiert und der Lauf
    weiterlaeuft. Sie am Vorab-Check zu haengen wuerde aus einem verkraftbaren
    Ausfall einen harten Abbruch machen. Dasselbe gilt fuer Formate, die
    libsndfile nicht oeffnet - deren Rate ist hier schlicht nicht bekannt.
    """
    wrong: list[str] = []
    for file in files:
        try:
            rate = sf.info(file.path).samplerate
        except Exception:  # noqa: BLE001 - unlesbar ist Sache des Analysten
            continue
        if rate != expected_hz:
            wrong.append(f"{file.path.name} ({rate} Hz)")
    if wrong:
        raise UnsupportedSampleRate(
            f"Diese Dateien liegen nicht in {expected_hz} Hz vor: "
            f"{', '.join(wrong)}. Wandle sie um und lege sie erneut ab."
        )
