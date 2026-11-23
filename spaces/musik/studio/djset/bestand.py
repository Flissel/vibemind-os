"""Bestand: die Song-Bibliothek ausserhalb des Repos.

Ablage unter einer Wurzel:
  eingang/      Ablage fuer neue Dateien; bleibt nach der Aufnahme unveraendert
  songs/        <id>.wav (umgewandelt) und <id>.json (ein Eintrag je Song)

Drei Produktregeln erzwingt dieses Modul:
- Ein Song ohne gueltige Herkunft kommt nie hinein (pruefe_herkunft).
- Aufgenommen wird nur ueber den blossen Dateinamen aus eingang/ - nie ueber
  einen Pfad, damit nichts ausserhalb des Eingangs gelesen wird.
- Dasselbe Audio wird nie zweimal aufgenommen (Pruefsumme der Eingangsdatei).

aufnehmen() ist alles-oder-nichts: Die Umwandlung laeuft in einen
Arbeitsordner, erst danach wandern WAV und JSON nach songs/. Scheitert etwas,
bleibt songs/ unveraendert.
"""

import hashlib
import os
import re
import shutil
import tempfile
import unicodedata
from datetime import date
from pathlib import Path

from pydantic import BaseModel, ValidationError, field_validator

from djset.capture import CaptureFailed, convert
from djset.contracts import TrackMeasurements
from djset.dsp.analyze import measure_track, tempo_abschnitte
from djset.render.stretch import RenderToolMissing
from djset.run import slugify
from djset.watch import AUDIO_SUFFIXES

Herkunft = str  # geprueft durch pruefe_herkunft

_FESTE_HERKUNFT = ("suno-eigen", "eigen-aufgenommen")
_LIZENZ_PRAEFIX = "lizenz:"
_ID_MUSTER = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")


# Deutsche Umschrift vor dem Abstreifen der Akzente: NFKD allein machte aus
# "Ü" ein "U" (und run.slugify ohne Vorarbeit liess es ganz fallen - "Über den
# Wolken" wurde "ber-den-wolken").
_UMSCHRIFT = str.maketrans(
    {"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss",
     "Ä": "Ae", "Ö": "Oe", "Ü": "Ue", "ẞ": "SS"}
)


def song_id_aus_titel(titel: str) -> str:
    """Die Song-ID zu einem Titel: ae/oe/ue/ss umschreiben, uebrige Akzente
    abstreifen (NFKD, ohne die kombinierenden Zeichen), dann slugify.
    "Über den Wolken" wird "ueber-den-wolken", "Café" wird "cafe"."""
    umschrieben = titel.translate(_UMSCHRIFT)
    zerlegt = unicodedata.normalize("NFKD", umschrieben)
    ohne_akzente = "".join(
        zeichen for zeichen in zerlegt if not unicodedata.combining(zeichen)
    )
    return slugify(ohne_akzente)


class BestandFehler(RuntimeError):
    """Eine Aufnahme oder Abfrage im Bestand ist nicht zulaessig."""


def pruefe_herkunft(text: str) -> str:
    """Gibt text zurueck, wenn 'suno-eigen', 'eigen-aufgenommen' oder
    'lizenz:<nichtleer>'; sonst BestandFehler."""
    if text in _FESTE_HERKUNFT:
        return text
    if text.startswith(_LIZENZ_PRAEFIX) and text[len(_LIZENZ_PRAEFIX):].strip():
        return text
    raise BestandFehler(
        f"ungueltige Herkunft {text!r}: erlaubt sind 'suno-eigen', "
        "'eigen-aufgenommen' oder 'lizenz:<Angabe>'"
    )


class SongEintrag(BaseModel):
    id: str  # song_id_aus_titel(titel), eindeutig im Bestand
    titel: str
    datei: str  # Dateiname unter songs/, immer <id>.wav
    herkunft: str
    suno_id: str | None = None
    aufgenommen_am: date
    pruefsumme: str  # sha256 der Eingangsdatei
    messung: TrackMeasurements | None = None
    # Tempo je Abschnitt (dsp.analyze.tempo_abschnitte). None: noch nicht
    # gemessen - Eintraege von vor dieser Pruefung; set holt es nach.
    tempo_abschnitte: list[float] | None = None

    @field_validator("herkunft")
    @classmethod
    def _herkunft_gueltig(cls, herkunft: str) -> str:
        """Die Produktregel gilt auch beim Zuruecklesen: ein von Hand
        geaenderter Eintrag mit ungueltiger Herkunft kommt nicht ins Set.
        Als ValueError, damit pydantic daraus eine ValidationError macht und
        Bestand._lies den Song in der Meldung nennt."""
        try:
            return pruefe_herkunft(herkunft)
        except BestandFehler as fehler:
            raise ValueError(str(fehler)) from fehler


def _sha256(pfad: Path) -> str:
    digest = hashlib.sha256()
    with pfad.open("rb") as strom:
        for block in iter(lambda: strom.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _schreibe_atomar(ziel: Path, text: str) -> None:
    """Erst in eine Nachbardatei, dann os.replace - wie Run.write_text."""
    handle, temporaer = tempfile.mkstemp(
        dir=ziel.parent, prefix=f"{ziel.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as strom:
            strom.write(text)
        os.replace(temporaer, ziel)
    except BaseException:
        Path(temporaer).unlink(missing_ok=True)
        raise


class Bestand:
    def __init__(self, wurzel: Path) -> None:
        self.wurzel = wurzel
        self.eingang = wurzel / "eingang"
        self.songs = wurzel / "songs"
        self.eingang.mkdir(parents=True, exist_ok=True)
        self.songs.mkdir(parents=True, exist_ok=True)

    def _eingangsdatei(self, dateiname: str) -> Path:
        """Prueft den Namen, bevor das Dateisystem damit in Beruehrung kommt."""
        if (
            not dateiname
            or "/" in dateiname
            or "\\" in dateiname
            or ":" in dateiname
            or dateiname in (".", "..")
        ):
            raise BestandFehler(
                f"ungueltiger Dateiname {dateiname!r}: nur ein blosser Name "
                "aus dem Eingang ist erlaubt"
            )
        pfad = self.eingang / dateiname
        if pfad.suffix.lower() not in AUDIO_SUFFIXES:
            raise BestandFehler(
                f"{dateiname}: unbekannte Endung, erlaubt sind "
                f"{', '.join(AUDIO_SUFFIXES)}"
            )
        if not pfad.is_file() or pfad.resolve().parent != self.eingang.resolve():
            raise BestandFehler(f"{dateiname} liegt nicht als Datei im Eingang")
        return pfad

    def _json(self, song_id: str) -> Path:
        if not _ID_MUSTER.fullmatch(song_id):
            raise BestandFehler(f"ungueltige Song-ID {song_id!r}")
        return self.songs / f"{song_id}.json"

    def aufnehmen(
        self,
        dateiname: str,
        titel: str,
        herkunft: str,
        heute: date,
        suno_id: str | None = None,
    ) -> SongEintrag:
        quelle = self._eingangsdatei(dateiname)
        pruefe_herkunft(herkunft)
        song_id = song_id_aus_titel(titel)
        if not song_id:
            raise BestandFehler(f"aus dem Titel {titel!r} ergibt sich keine ID")

        summe = _sha256(quelle)
        for vorhanden in self.alle():
            if vorhanden.pruefsumme == summe:
                raise BestandFehler(
                    f"dasselbe Audio ist schon aufgenommen als {vorhanden.id!r}"
                )
        if self._json(song_id).exists():
            raise BestandFehler(f"die ID {song_id!r} ist im Bestand schon vergeben")

        eintrag = SongEintrag(
            id=song_id,
            titel=titel,
            datei=f"{song_id}.wav",
            herkunft=herkunft,
            suno_id=suno_id,
            aufgenommen_am=heute,
            pruefsumme=summe,
        )

        arbeit = Path(tempfile.mkdtemp(dir=self.wurzel, prefix=".aufnahme-"))
        try:
            fertig = arbeit / eintrag.datei
            try:
                convert(quelle, fertig)
            except (CaptureFailed, RenderToolMissing) as fehler:
                raise BestandFehler(f"Umwandlung gescheitert: {fehler}") from fehler
            wav = self.songs / eintrag.datei
            os.replace(fertig, wav)
            try:
                _schreibe_atomar(
                    self._json(song_id), eintrag.model_dump_json(indent=2)
                )
            except BaseException:
                wav.unlink(missing_ok=True)
                raise
        finally:
            shutil.rmtree(arbeit, ignore_errors=True)
        return eintrag

    def messen(self, song_id: str) -> SongEintrag:
        eintrag = self.hole(song_id)
        pfad = self.pfad(song_id)
        messung = measure_track(pfad, song_id)
        aktualisiert = eintrag.model_copy(
            update={"messung": messung, "tempo_abschnitte": tempo_abschnitte(pfad)}
        )
        _schreibe_atomar(self._json(song_id), aktualisiert.model_dump_json(indent=2))
        return aktualisiert

    def abschnitte_messen(self, song_id: str) -> SongEintrag:
        """Nur die Tempo-Abschnitte, fuer Eintraege, die schon eine Messung
        haben - die bleibt unberuehrt."""
        eintrag = self.hole(song_id)
        aktualisiert = eintrag.model_copy(
            update={"tempo_abschnitte": tempo_abschnitte(self.pfad(song_id))}
        )
        _schreibe_atomar(self._json(song_id), aktualisiert.model_dump_json(indent=2))
        return aktualisiert

    def alle(self) -> list[SongEintrag]:
        return sorted(
            (self._lies(pfad) for pfad in self.songs.glob("*.json")),
            key=lambda eintrag: eintrag.id,
        )

    def hole(self, song_id: str) -> SongEintrag:
        pfad = self._json(song_id)
        if not pfad.is_file():
            raise BestandFehler(f"kein Song {song_id!r} im Bestand")
        return self._lies(pfad)

    def pfad(self, song_id: str) -> Path:
        eintrag = self.hole(song_id)
        wav = self.songs / eintrag.datei
        if not wav.is_file():
            raise BestandFehler(f"die WAV zu {song_id!r} fehlt unter songs/")
        return wav

    def _lies(self, pfad: Path) -> SongEintrag:
        try:
            return SongEintrag.model_validate_json(pfad.read_text(encoding="utf-8"))
        except (ValidationError, ValueError) as fehler:
            raise BestandFehler(
                f"{pfad.name} ist kein gueltiger Eintrag: {fehler}"
            ) from fehler
