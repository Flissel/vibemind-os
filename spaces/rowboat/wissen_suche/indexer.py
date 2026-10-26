"""Indexer: gleicht ``knowledge/`` mit dem Index ab und bettet offene Notizen ein.

Laeuft als Faden im Dienst ``rowboat-wissen``. Ist der PC aus, bleiben Dateien ``offen`` (kein
Fehlversuch) und kommen in der naechsten Runde dran. Fehlt mehr als die Haelfte der indexierten
Dateien (oder ist der Ordner leer, etwa weil der Mount fehlt), wird nichts geloescht.
"""
from __future__ import annotations

import hashlib
import logging
import os
import threading
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

from .einbetten import Einbetter, EingabeAbgelehnt, PcNichtErreichbar
from .index import WissensIndex
from .zerlegen import zerlegen

log = logging.getLogger(__name__)

AUSGESCHLOSSEN = ("_konflikte/", ".spiegel-eingang/", ".git/")
# Der Schutz gegen Massenverschwinden greift erst ab so vielen bekannten Dateien; darunter ist
# "mehr als die Haelfte fehlt" normales Aufraeumen (z. B. 1 von 2 Notizen geloescht).
MIN_BEKANNT_FUER_SCHUTZ = 10


class Abgleich(NamedTuple):
    neu_oder_geaendert: int
    entfernt: int
    loeschen_ausgesetzt: bool


class Runde(NamedTuple):
    eingebettet: int
    offen: int
    pc_erreichbar: bool


def ausgeschlossen(rel: str) -> bool:
    """Gilt fuer POSIX-Pfade relativ zu ``knowledge/``: Ausschluesse, versteckte Teile, kein ``.md``."""
    if not rel.lower().endswith(".md"):
        return True
    if any(rel.startswith(a) or f"/{a}" in rel for a in AUSGESCHLOSSEN):
        return True
    return any(teil.startswith(".") for teil in rel.split("/"))


def bestand_scannen(wurzel: Path) -> dict[str, tuple[int, float]]:
    """Alle ``*.md`` unter ``wurzel`` als ``{posix_rel_nfc: (groesse, mtime)}``.

    Versteckte und ausgeschlossene Ordner werden nicht betreten; symbolische Links in Ordner werden
    nicht verfolgt, Datei-Links nur, wenn ihr Ziel innerhalb der Wurzel liegt.
    """
    wurzel = Path(wurzel)
    if not wurzel.is_dir():
        return {}
    echt = wurzel.resolve()
    bestand: dict[str, tuple[int, float]] = {}
    for ordner, unterordner, dateien in os.walk(wurzel, followlinks=False):
        unterordner[:] = [u for u in unterordner if not u.startswith(".") and u != "_konflikte"]
        for name in dateien:
            pfad = Path(ordner) / name
            rel = unicodedata.normalize("NFC", pfad.relative_to(wurzel).as_posix())
            if ausgeschlossen(rel):
                continue
            try:
                if pfad.is_symlink() and not pfad.resolve().is_relative_to(echt):
                    continue
                st = pfad.stat()
            except OSError:
                continue
            bestand[rel] = (st.st_size, st.st_mtime)
    return bestand


def datei_pfad(wurzel: Path, rel: str) -> Path:
    """Pfad auf der Platte zum Schluessel ``rel`` (NFC).

    Der Schluessel ist NFC, der Name auf der Platte kann NFD sein (z. B. von macOS kopiert). Gibt es
    einen Teil nicht unter seinem NFC-Namen, wird im Ordner nach dem Namen mit gleicher NFC-Form gesucht.
    """
    pfad = Path(wurzel)
    for teil in rel.split("/"):
        kandidat = pfad / teil
        if not kandidat.exists():
            try:
                namen = os.listdir(pfad)
            except OSError:
                namen = []
            gleich = [n for n in namen if unicodedata.normalize("NFC", n) == teil]
            if gleich:
                kandidat = pfad / gleich[0]
        pfad = kandidat
    return pfad


def _lesen(pfad: Path) -> tuple[bytes, str]:
    daten = pfad.read_bytes()
    return daten, hashlib.sha256(daten).hexdigest()


def abgleichen(index: WissensIndex, wurzel: Path) -> Abgleich:
    bestand = bestand_scannen(wurzel)
    bekannt = index.dateien()
    neu = 0
    for rel, (groesse, mtime) in bestand.items():
        alt = bekannt.get(rel)
        if alt is not None and alt.groesse == groesse and alt.mtime == mtime:
            continue
        try:
            _, sha = _lesen(datei_pfad(wurzel, rel))
        except OSError as exc:
            log.warning("%s nicht lesbar (%s), naechste Runde", rel, type(exc).__name__)
            continue
        if alt is not None and alt.sha256 == sha:
            index.stat_merken(rel, groesse, mtime)
            continue
        index.als_offen(rel, sha, groesse, mtime)
        neu += 1

    fehlend = [rel for rel in bekannt if rel not in bestand]
    if fehlend and len(bekannt) >= MIN_BEKANNT_FUER_SCHUTZ and (not bestand or len(fehlend) > len(bekannt) / 2):
        log.warning("Loeschen ausgesetzt: %d von %d indexierten Notizen fehlen unter %s "
                    "(Mount leer oder fehlt?)", len(fehlend), len(bekannt), wurzel)
        return Abgleich(neu, 0, True)
    for rel in fehlend:
        index.entfernen(rel)
    return Abgleich(neu, len(fehlend), False)


def einbetten_runde(index: WissensIndex, wurzel: Path, einbetter: Einbetter,
                    max_abschnitte: int = 2000, stapel: int = 32) -> Runde:
    bekannt = index.dateien()
    eingebettet = 0
    for rel in index.offene(limit=len(bekannt) or 1):
        datei = datei_pfad(wurzel, rel)
        try:
            daten, sha = _lesen(datei)
        except OSError as exc:
            log.warning("%s nicht lesbar (%s), naechste Runde", rel, type(exc).__name__)
            continue
        if sha != bekannt[rel].sha256:
            continue        # seit dem Abgleich geaendert; der naechste Abgleich setzt den neuen Stand
        abschnitte = zerlegen(rel, daten.decode("utf-8", errors="replace"))
        if eingebettet and eingebettet + len(abschnitte) > max_abschnitte:
            break
        vektoren: list[list[float]] = []
        try:
            for i in range(0, len(abschnitte), stapel):
                vektoren += einbetter.dokumente([a.text for a in abschnitte[i:i + stapel]])
        except PcNichtErreichbar as exc:
            log.info("PC nicht erreichbar (%s); %s und der Rest bleiben offen", exc, rel)
            return Runde(eingebettet, index.stand().offen, False)
        except EingabeAbgelehnt as exc:
            log.warning("%s: %s", rel, exc)
            index.fehlschlag(rel, str(exc))
            continue
        # Die Datei kann sich waehrend des Einbettens geaendert haben: dann nicht den alten Stand
        # als fertig speichern. Sie bleibt offen; der naechste Abgleich erkennt die Aenderung.
        try:
            _, sha_nachher = _lesen(datei)
        except OSError:
            sha_nachher = ""
        if sha_nachher != sha:
            log.warning("%s hat sich waehrend des Einbettens geaendert; naechste Runde", rel)
            continue
        index.ersetzen(rel, sha, abschnitte, vektoren)
        eingebettet += len(abschnitte)
        if eingebettet >= max_abschnitte:
            break
    return Runde(eingebettet, index.stand().offen, True)


class Indexer(threading.Thread):
    """Faden: sofort eine Runde, danach alle ``intervall_s`` Sekunden (Abgleich + Einbetten)."""

    def __init__(self, index: WissensIndex, wurzel: Path, einbetter: Einbetter, intervall_s: int = 600) -> None:
        super().__init__(name="wissen-indexer", daemon=True)
        self.index = index
        self.wurzel = Path(wurzel)
        self.einbetter = einbetter
        self.intervall_s = intervall_s
        self.runde_fertig = threading.Event()
        self._stopp = threading.Event()

    def stoppen(self) -> None:
        self._stopp.set()

    def runde(self) -> None:
        try:
            ab = abgleichen(self.index, self.wurzel)
            r = einbetten_runde(self.index, self.wurzel, self.einbetter)
            self.index.merken("letzte_runde", datetime.now(timezone.utc).isoformat(timespec="seconds"))
            log.info("Runde: %d neu/geaendert, %d entfernt%s; %d Abschnitte eingebettet, %d offen, PC %s",
                     ab.neu_oder_geaendert, ab.entfernt, " (Loeschen ausgesetzt)" if ab.loeschen_ausgesetzt else "",
                     r.eingebettet, r.offen, "erreichbar" if r.pc_erreichbar else "nicht erreichbar")
        except Exception:  # noqa: BLE001 - der Faden darf nicht sterben
            log.exception("Indexer-Runde fehlgeschlagen")
        finally:
            self.runde_fertig.set()

    def run(self) -> None:
        while not self._stopp.is_set():
            self.runde()
            self._stopp.wait(self.intervall_s)
