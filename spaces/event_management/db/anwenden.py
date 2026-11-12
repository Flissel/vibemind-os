"""Migrationslauf fuer das Schema `event`.

Migrationen sind nummerierte SQL-Dateien `NNN_<name>.sql` in diesem Ordner. Sie
werden nur als Admin angewendet, jede in einer eigenen Transaktion, und in
`event.migration` vermerkt. Bereits vermerkte Dateien werden uebersprungen.

CLI: ``python -m spaces.event_management.db.anwenden``. Der DSN kommt aus der
Datei in ``EVENT_ADMIN_DSN_FILE`` oder aus ``EVENT_ADMIN_DSN``. Ausgegeben werden
nur die Namen der neu angewendeten Dateien, nie der DSN.
"""
from __future__ import annotations

import logging
import os
import re
import sys
from pathlib import Path

import psycopg
from psycopg.rows import tuple_row

logger = logging.getLogger(__name__)

ORDNER = Path(__file__).resolve().parent
_MUSTER = re.compile(r"^(\d+)_[A-Za-z0-9_]+\.sql$")

_VORBEREITUNG = """
create schema if not exists event;
create table if not exists event.migration (
    nr int primary key,
    name text not null,
    angewendet timestamptz not null default now()
);
"""


def migrationen(ordner: Path | None = None) -> list[tuple[int, str, Path]]:
    """Migrationsdateien als (Nummer, Dateiname, Pfad), sortiert nach Nummer."""
    ordner = ORDNER if ordner is None else ordner
    gefunden: dict[int, Path] = {}
    for pfad in ordner.glob("*.sql"):
        treffer = _MUSTER.match(pfad.name)
        if treffer is None:
            continue
        nr = int(treffer.group(1))
        if nr in gefunden:
            raise ValueError(
                f"Migrationsnummer {nr} doppelt: {gefunden[nr].name}, {pfad.name}"
            )
        gefunden[nr] = pfad
    return [(nr, gefunden[nr].name, gefunden[nr]) for nr in sorted(gefunden)]


def anwenden(conn: psycopg.Connection) -> list[str]:
    """Wendet fehlende Migrationen an. Rueckgabe: Namen der neu angewendeten Dateien."""
    with conn.transaction():
        conn.execute(_VORBEREITUNG)
    with conn.transaction(), conn.cursor(row_factory=tuple_row) as cur:
        cur.execute("select nr from event.migration")
        erledigt = {zeile[0] for zeile in cur.fetchall()}

    neu: list[str] = []
    for nr, name, pfad in migrationen():
        if nr in erledigt:
            continue
        sql_text = pfad.read_text(encoding="utf-8")
        with conn.transaction():
            conn.execute(sql_text)  # type: ignore[call-overload]  # Dateiinhalt, keine Parameter
            conn.execute(
                "insert into event.migration (nr, name) values (%s, %s)", (nr, name)
            )
        logger.info("Migration angewendet: %s", name)
        neu.append(name)
    return neu


def _dsn_lesen() -> str:
    datei = os.environ.get("EVENT_ADMIN_DSN_FILE", "").strip()
    if datei:
        return Path(datei).read_text(encoding="utf-8").strip()
    return os.environ.get("EVENT_ADMIN_DSN", "").strip()


def main() -> int:
    dsn = _dsn_lesen()
    if not dsn:
        print("Kein DSN: EVENT_ADMIN_DSN_FILE oder EVENT_ADMIN_DSN setzen.", file=sys.stderr)
        return 2
    try:
        conn = psycopg.connect(dsn, autocommit=True)
    except psycopg.Error as fehler:
        # Meldung bewusst ohne Details: sie koennte Verbindungsangaben enthalten.
        print(f"Verbindung fehlgeschlagen ({type(fehler).__name__}).", file=sys.stderr)
        return 1
    with conn:
        for name in anwenden(conn):
            print(name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
