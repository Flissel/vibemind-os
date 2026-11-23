"""Aufnahme vom Leitungs-/Aux-Eingang und Umwandlung vorhandener Dateien.

Beide Wege fuettern denselben watch-Ordner wie die Suno-Downloads, und ueber
den laeuft danach derselbe Vorab-Check wie ueber jedes andere Material:
djset.watch.check_sample_rate verwirft alles, das nicht in SR (44,1 kHz)
vorliegt, bevor der erste Agentenaufruf stattfindet - eine falsche Rate kam
sonst erst in Stufe 8 zum Vorschein, nach jedem bezahlten Token (siehe
djset/watch.py). Diese Pruefung bleibt unangetastet; hier wird nur dafuer
gesorgt, dass sie nie anschlaegt. record() und convert() schreiben beide
ueber "-ar 44100" exakt in dieser Rate.

Die Kanalzahl der Quelle bleibt in beiden Faellen erhalten - keine `-ac`
Flagge zwingt sie auf Stereo. Eine Mono-Quelle (der Regelfall an einem
Aux-/Leitungseingang) kommt sonst nicht einfach kanalverdoppelt heraus:
ffmpeg mischt sie leistungserhaltend auf beide Kanaele, mit Faktor 1/sqrt(2)
(~-3dB) je Kanal, und das Material waere leiser als es hineinging, ohne jede
Meldung. Der Renderer liest Quellmaterial als Stereo
(djset.render.engine._load_audio): eine Mono-Quelle wird dort ohne
Pegelaenderung auf beide Kanaele gelegt, eine Stereo-Quelle behaelt ihr
Stereobild, weil ffmpeg ohne `-ac` die native Kanalzahl uebernimmt.
"""

import subprocess
from pathlib import Path

from djset.render.stretch import SR, ffmpeg_path
from djset.run import slugify
from djset.watch import AUDIO_SUFFIXES, scan


class CaptureFailed(RuntimeError):
    """ffmpeg lief und scheiterte - bei Aufnahme oder Umwandlung.

    Getrennt von RenderToolMissing (djset.render.stretch), aus demselben
    Grund wie dort RenderFailed von RenderToolMissing getrennt ist: "das
    Werkzeug fehlt" ist eine Umgebungsfrage, die der Nutzer beheben kann,
    "das Werkzeug ist gescheitert" etwas anderes - und beides verlangt vom
    Nutzer eine andere Reaktion.
    """


def _parse_device_list(output: str) -> list[str]:
    """Parst ffmpegs dshow-Geraeteliste aus stderr-Text.

    ffmpeg schreibt die Liste nach stderr, nicht stdout - list_input_devices()
    liest deshalb result.stderr, nicht result.stdout. Der Aufruf endet dabei
    grundsaetzlich mit "Error opening input file dummy." und einem
    Rueckgabecode ungleich 0, auch wenn die Liste vollstaendig und korrekt
    dasteht; der Rueckgabecode wird hier deshalb gar nicht erst angesehen.

    Gesucht werden Zeilen, die auf ' (audio)' enden. Videogeraete (' (video)')
    und die "Alternative name"-Zeilen darunter - die selbst kein '(audio)'-
    Suffix tragen - fallen damit von selbst heraus.
    """
    devices: list[str] = []
    for line in output.splitlines():
        stripped = line.strip()
        if not stripped.endswith("(audio)"):
            continue
        start = stripped.find('"')
        end = stripped.rfind('"')
        if start == -1 or end == -1 or start == end:
            continue
        devices.append(stripped[start + 1 : end])
    return devices


def list_input_devices() -> list[str]:
    """Die Namen der Audio-Eingabegeraete dieser Maschine, ueber ffmpegs
    dshow-Backend (Windows)."""
    command = [
        ffmpeg_path(),
        "-hide_banner",
        "-f", "dshow",
        "-list_devices", "true",
        "-i", "dummy",
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    return _parse_device_list(result.stderr)


def record(device: str, target: Path, seconds: float, sr: int = SR) -> None:
    """Nimmt `seconds` Sekunden vom benannten Geraet auf und schreibt eine
    Float-WAV in `sr` Hz, mit der nativen Kanalzahl des Geraets (kein
    erzwungenes `-ac` - siehe Modul-Docstring)."""
    target.parent.mkdir(parents=True, exist_ok=True)
    command = [
        ffmpeg_path(),
        "-hide_banner",
        "-loglevel", "error",
        "-y",
        "-f", "dshow",
        "-i", f"audio={device}",
        "-t", str(seconds),
        "-ar", str(sr),
        "-c:a", "pcm_f32le",
        str(target),
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise CaptureFailed(
            f"ffmpeg scheiterte bei der Aufnahme von '{device}': "
            f"{result.stderr.strip() or 'keine Meldung'}"
        )


def convert(source: Path, target: Path, sr: int = SR) -> None:
    """Wandelt eine vorhandene Audiodatei in eine Float-WAV in `sr` Hz um,
    mit der Kanalzahl der Quelle (kein erzwungenes `-ac` - siehe
    Modul-Docstring)."""
    target.parent.mkdir(parents=True, exist_ok=True)
    command = [
        ffmpeg_path(),
        "-hide_banner",
        "-loglevel", "error",
        "-y",
        "-i", str(source),
        "-ar", str(sr),
        "-c:a", "pcm_f32le",
        str(target),
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise CaptureFailed(
            f"ffmpeg scheiterte bei der Umwandlung von {source.name}: "
            f"{result.stderr.strip() or 'keine Meldung'}"
        )


def import_folder(source_dir: Path, watch_dir: Path, sr: int = SR) -> list[Path]:
    """Wandelt jede Audiodatei aus `source_dir` in `watch_dir` um und liefert
    die geschriebenen Pfade zurueck.

    Zielnamen kommen aus djset.run.slugify - genau wie djset.watch.scan seine
    IDs aus den Dateinamen ableitet, und mit demselben Kollisionsschema:
    stabile Sortierung ueber casefold und rohen Namen, ein Zaehler haengt an,
    wo zwei Quellnamen auf denselben Slug abbilden. Nur so leiten sich aus
    diesen Dateinamen spaeter dieselben stabilen, kollisionsfreien IDs ab,
    die scan() auch fuer unveraendert abgelegtes Material liefert.

    Der Zaehler startet dabei nicht bei jedem Aufruf leer, sondern wird aus
    scan(watch_dir) gespeist: sonst ueberschreibt ein zweiter Import, dessen
    Datei auf denselben Slug abbildet wie eine aus einem frueheren Import,
    deren Audio kommentarlos - Material, das der Nutzer bereits hereingeholt
    hat, waere unwiderruflich weg.

    Ein fehlender Quellordner ist hier - wie bei scan() - keine Fehlermeldung,
    sondern eine leere Liste.
    """
    if not source_dir.is_dir():
        return []

    watch_dir.mkdir(parents=True, exist_ok=True)
    found = sorted(
        (
            path
            for path in source_dir.iterdir()
            if path.is_file() and path.suffix.lower() in AUDIO_SUFFIXES
        ),
        key=lambda path: (path.name.casefold(), path.name),
    )

    written: list[Path] = []
    used: set[str] = {file.id for file in scan(watch_dir)}
    for path in found:
        base = slugify(path.stem) or "track"
        name = base
        counter = 2
        while name in used:
            name = f"{base}-{counter}"
            counter += 1
        used.add(name)

        target = watch_dir / f"{name}.wav"
        convert(path, target, sr)
        written.append(target)
    return written
