"""Wissens-Index: SQLite-Datei mit Abschnitten und float32-Vektoren, Suche per Kosinus (numpy).

Es gibt genau einen Schreiber (den Indexer-Faden) und Leser im selben Prozess (MCP-Werkzeuge);
eine Verbindung mit Sperre reicht. Die Vektor-Matrix liegt im Speicher und wird nach jeder
Aenderung (Generationszaehler) beim naechsten ``suchen`` neu geladen.
"""
from __future__ import annotations

import logging
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

import numpy as np

from .zerlegen import Abschnitt

log = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (schluessel TEXT PRIMARY KEY, wert TEXT);
CREATE TABLE IF NOT EXISTS dateien (
    pfad TEXT PRIMARY KEY,
    sha256 TEXT NOT NULL,
    groesse INTEGER NOT NULL,
    mtime REAL NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('offen', 'fertig', 'fehler')),
    versuche INTEGER NOT NULL DEFAULT 0,
    fehler TEXT,
    aktualisiert TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS abschnitte (
    pfad TEXT NOT NULL,
    nr INTEGER NOT NULL,
    ueberschrift TEXT NOT NULL,
    text TEXT NOT NULL,
    vektor BLOB NOT NULL,
    PRIMARY KEY (pfad, nr)
);
"""


class DateiStand(NamedTuple):
    sha256: str
    groesse: int
    mtime: float
    status: str
    versuche: int


class Treffer(NamedTuple):
    pfad: str
    ueberschrift: str
    text: str
    punkte: float


class Stand(NamedTuple):
    fertig: int
    offen: int
    fehler: int
    abschnitte: int
    modell: str


def _jetzt() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


class WissensIndex:
    def __init__(self, pfad: Path, modell: str, dim: int) -> None:
        self.modell = modell
        self.dim = dim
        Path(pfad).parent.mkdir(parents=True, exist_ok=True)
        self._sperre = threading.RLock()
        self._db = sqlite3.connect(str(pfad), check_same_thread=False, isolation_level=None)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=NORMAL")
        self._db.executescript(_SCHEMA)
        self._generation = 0
        self._geladen = -1
        self._matrix = np.zeros((0, dim), dtype=np.float32)
        self._zeilen: list[tuple[str, str, str]] = []
        self._modell_pruefen()

    def close(self) -> None:
        with self._sperre:
            self._db.close()

    # -- meta ---------------------------------------------------------------------------------
    def _modell_pruefen(self) -> None:
        with self._sperre:
            alt_modell, alt_dim = self.lesen("modell"), self.lesen("dim")
            if alt_modell == self.modell and alt_dim == str(self.dim):
                return
            with self._db:
                self._db.execute("BEGIN IMMEDIATE")
                if alt_modell is not None or alt_dim is not None:
                    log.warning("Modell/Dimension geaendert (%s/%s -> %s/%s): Index wird neu aufgebaut",
                                alt_modell, alt_dim, self.modell, self.dim)
                    self._db.execute("DELETE FROM abschnitte")
                    self._db.execute("UPDATE dateien SET status='offen', versuche=0, fehler=NULL")
                self._db.executemany("INSERT OR REPLACE INTO meta (schluessel, wert) VALUES (?, ?)",
                                     [("modell", self.modell), ("dim", str(self.dim))])
            self._generation += 1

    def merken(self, schluessel: str, wert: str) -> None:
        with self._sperre:
            self._db.execute("INSERT OR REPLACE INTO meta (schluessel, wert) VALUES (?, ?)", (schluessel, wert))

    def lesen(self, schluessel: str) -> str | None:
        with self._sperre:
            zeile = self._db.execute("SELECT wert FROM meta WHERE schluessel=?", (schluessel,)).fetchone()
        return None if zeile is None else zeile[0]

    # -- Dateien ------------------------------------------------------------------------------
    def dateien(self) -> dict[str, DateiStand]:
        with self._sperre:
            zeilen = self._db.execute(
                "SELECT pfad, sha256, groesse, mtime, status, versuche FROM dateien").fetchall()
        return {p: DateiStand(s, g, m, st, v) for p, s, g, m, st, v in zeilen}

    def als_offen(self, pfad: str, sha256: str, groesse: int, mtime: float) -> None:
        with self._sperre:
            self._db.execute(
                "INSERT INTO dateien (pfad, sha256, groesse, mtime, status, versuche, fehler, aktualisiert)"
                " VALUES (?, ?, ?, ?, 'offen', 0, NULL, ?)"
                " ON CONFLICT(pfad) DO UPDATE SET sha256=excluded.sha256, groesse=excluded.groesse,"
                " mtime=excluded.mtime, status='offen', versuche=0, fehler=NULL,"
                " aktualisiert=excluded.aktualisiert",
                (pfad, sha256, groesse, mtime, _jetzt()))

    def entfernen(self, pfad: str) -> None:
        with self._sperre:
            with self._db:
                self._db.execute("BEGIN IMMEDIATE")
                self._db.execute("DELETE FROM abschnitte WHERE pfad=?", (pfad,))
                self._db.execute("DELETE FROM dateien WHERE pfad=?", (pfad,))
            self._generation += 1

    def offene(self, limit: int) -> list[str]:
        with self._sperre:
            zeilen = self._db.execute(
                "SELECT pfad FROM dateien WHERE status='offen' ORDER BY aktualisiert, rowid LIMIT ?",
                (limit,)).fetchall()
        return [z[0] for z in zeilen]

    def ersetzen(self, pfad: str, sha256: str, abschnitte: list[Abschnitt],
                 vektoren: list[list[float]]) -> None:
        """Ersetzt alle Abschnitte der Datei in einer Transaktion und setzt sie auf ``fertig``.

        Passt ``sha256`` nicht mehr zum gespeicherten Stand, hat sich die Datei waehrend des
        Einbettens geaendert: dann bleibt alles, wie es ist (die neue Fassung ist schon ``offen``).
        """
        if len(abschnitte) != len(vektoren):
            raise ValueError(f"{pfad}: {len(abschnitte)} Abschnitte, aber {len(vektoren)} Vektoren")
        blobs = []
        for v in vektoren:
            arr = np.asarray(v, dtype=np.float32)
            if arr.shape != (self.dim,):
                raise ValueError(f"{pfad}: Vektor mit Form {arr.shape} statt ({self.dim},)")
            blobs.append(arr.tobytes())
        with self._sperre:
            with self._db:
                self._db.execute("BEGIN IMMEDIATE")
                zeile = self._db.execute("SELECT sha256 FROM dateien WHERE pfad=?", (pfad,)).fetchone()
                if zeile is None or zeile[0] != sha256:
                    log.warning("%s hat sich waehrend des Einbettens geaendert oder ist weg; "
                                "Abschnitte verworfen, naechste Runde bettet neu ein", pfad)
                    return
                self._db.execute("DELETE FROM abschnitte WHERE pfad=?", (pfad,))
                self._db.executemany(
                    "INSERT INTO abschnitte (pfad, nr, ueberschrift, text, vektor) VALUES (?, ?, ?, ?, ?)",
                    [(pfad, a.nr, a.ueberschrift, a.text, b) for a, b in zip(abschnitte, blobs)])
                self._db.execute(
                    "UPDATE dateien SET status='fertig', versuche=0, fehler=NULL, aktualisiert=? WHERE pfad=?",
                    (_jetzt(), pfad))
            self._generation += 1

    def fehlschlag(self, pfad: str, fehler: str, max_versuche: int = 3) -> None:
        with self._sperre:
            self._db.execute(
                "UPDATE dateien SET versuche=versuche+1, fehler=?, aktualisiert=?,"
                " status=CASE WHEN versuche+1 >= ? THEN 'fehler' ELSE 'offen' END WHERE pfad=?",
                (fehler[:500], _jetzt(), max_versuche, pfad))

    # -- Suche --------------------------------------------------------------------------------
    def _matrix_laden(self) -> None:
        if self._geladen == self._generation:
            return
        zeilen = self._db.execute(
            "SELECT pfad, ueberschrift, text, vektor FROM abschnitte ORDER BY pfad, nr").fetchall()
        self._zeilen = [(p, u, t) for p, u, t, _ in zeilen]
        if zeilen:
            self._matrix = np.frombuffer(b"".join(z[3] for z in zeilen), dtype=np.float32).reshape(
                len(zeilen), self.dim)
        else:
            self._matrix = np.zeros((0, self.dim), dtype=np.float32)
        self._geladen = self._generation

    def suchen(self, vektor: list[float], anzahl: int) -> list[Treffer]:
        """Kosinus ueber alle Abschnitte; je Datei nur der beste Abschnitt, absteigend sortiert."""
        frage = np.asarray(vektor, dtype=np.float32)
        if frage.shape != (self.dim,):
            raise ValueError(f"Frage-Vektor mit Form {frage.shape} statt ({self.dim},)")
        laenge = float(np.linalg.norm(frage))
        if laenge > 0:
            frage = frage / laenge
        with self._sperre:
            self._matrix_laden()
            matrix, zeilen = self._matrix, self._zeilen
        if not zeilen or anzahl <= 0:
            return []
        punkte = matrix @ frage
        treffer: list[Treffer] = []
        gesehen: set[str] = set()
        for i in np.argsort(-punkte, kind="stable"):
            pfad, ueberschrift, text = zeilen[int(i)]
            if pfad in gesehen:
                continue
            gesehen.add(pfad)
            treffer.append(Treffer(pfad, ueberschrift, text, float(punkte[int(i)])))
            if len(treffer) >= anzahl:
                break
        return treffer

    def stand(self) -> Stand:
        with self._sperre:
            zaehler = dict(self._db.execute("SELECT status, COUNT(*) FROM dateien GROUP BY status").fetchall())
            abschnitte = self._db.execute("SELECT COUNT(*) FROM abschnitte").fetchone()[0]
        return Stand(zaehler.get("fertig", 0), zaehler.get("offen", 0), zaehler.get("fehler", 0),
                     abschnitte, self.modell)
