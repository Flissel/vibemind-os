"""Legacy-Festival als Katalogeintrag (Etappe 4).

Ein Legacy-Event ist ein schreibgeschuetzter Verweis auf die alte App: Kategorie
`festival`, `quelle='legacy'`, `legacy_url`. Die Fachschicht lehnt jeden Schreibzugriff
darauf ab (kern, marke, werkzeuge, verbinden). Dieses Modul legt den Eintrag als Admin an.

CLI: ``python -m spaces.event_management.system.legacy eintragen <datei.json>``. Der
Admin-DSN kommt nur aus der Datei in ``EVENT_ADMIN_DSN_FILE``. Ausgegeben wird nur die
Event-ID, nie Werte aus der Datei.
"""
from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from spaces.event_management.system import events

log = logging.getLogger(__name__)

KATEGORIE = "festival"
_ERLAUBT = frozenset({"Firma nicht gefunden", "legacy_url muss mit https:// beginnen",
                      "Name fehlt", "Ende liegt vor dem Beginn", "Ort ungueltig",
                      "Legacy-Event gehoert einer anderen Firma"})


def eintragen(
    conn_admin: psycopg.Connection[Any], *, firma_kurz: str, name: str, beginn: datetime,
    ende: datetime, ort: str | None, legacy_url: str,
) -> UUID:
    """Legt das Legacy-Event an oder aktualisiert die Eckdaten (idempotent ueber `legacy_url`).

    ValueError("Firma nicht gefunden") bei unbekanntem Kuerzel; ValueError bei einem
    Event dieser URL in einer anderen Firma (nichts wird geaendert); ValueError bei einer URL
    ohne https://, leerem Namen oder Ende vor Beginn.
    """
    if (not isinstance(legacy_url, str) or not legacy_url.startswith("https://")
            or len(legacy_url) <= len("https://")):
        raise ValueError("legacy_url muss mit https:// beginnen")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("Name fehlt")
    beginn, ende = events.zeit(beginn, "beginn"), events.zeit(ende, "ende")
    if ende < beginn:
        raise ValueError("Ende liegt vor dem Beginn")
    if ort is not None and not isinstance(ort, str):
        raise ValueError("Ort ungueltig")
    with conn_admin.transaction(), conn_admin.cursor(row_factory=dict_row) as cur:
        # Es gibt keinen Unique-Index auf legacy_url: der Advisory-Lock serialisiert
        # gleichzeitige Erstaufrufe (Ruling T3-a); `for update` sperrt nur vorhandene Zeilen.
        cur.execute("select pg_advisory_xact_lock(hashtext('legacy:' || %s))", (legacy_url,))
        cur.execute("select id from event.firma where kurz = %s", (firma_kurz,))
        firma = cur.fetchone()
        if firma is None:
            raise ValueError("Firma nicht gefunden")
        cur.execute(
            "select id, firma_id from event.event where quelle = 'legacy' and legacy_url = %s"
            " for update", (legacy_url,))
        vorhanden = cur.fetchone()
        if vorhanden is not None:
            if vorhanden["firma_id"] != firma["id"]:
                raise ValueError("Legacy-Event gehoert einer anderen Firma")
            cur.execute(
                "update event.event set name=%s, beginn=%s, ende=%s,"
                " ort_adresse=%s, geaendert=now() where id=%s returning id",
                (name.strip(), beginn, ende, ort, vorhanden["id"]))
        else:
            cur.execute(
                "insert into event.event (firma_id, kategorie, name, beginn, ende,"
                " ort_adresse, quelle, legacy_url) values (%s,%s,%s,%s,%s,%s,'legacy',%s)"
                " returning id",
                (firma["id"], KATEGORIE, name.strip(), beginn, ende, ort, legacy_url))
        zeile = cur.fetchone()
    assert zeile is not None
    event_id: UUID = zeile["id"]
    return event_id


def _dsn_lesen() -> str:
    datei = os.environ.get("EVENT_ADMIN_DSN_FILE", "").strip()
    if not datei:
        return ""
    return Path(datei).read_text(encoding="utf-8").strip()


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2 or args[0] != "eintragen":
        print("Aufruf: legacy eintragen <datei.json>", file=sys.stderr)
        return 2
    try:
        dsn = _dsn_lesen()
    except OSError:
        dsn = ""
    if not dsn:
        print("Kein DSN: EVENT_ADMIN_DSN_FILE setzen.", file=sys.stderr)
        return 2
    try:
        roh = json.loads(Path(args[1]).read_text(encoding="utf-8"))
        werte = {
            "firma_kurz": roh["firma_kurz"], "name": roh["name"],
            "beginn": datetime.fromisoformat(roh["beginn"]),
            "ende": datetime.fromisoformat(roh["ende"]),
            "ort": roh.get("ort"), "legacy_url": roh["legacy_url"],
        }
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        print("Eingabedatei ungueltig.", file=sys.stderr)
        return 2
    try:
        conn = psycopg.connect(dsn, autocommit=True)
    except psycopg.Error as fehler:
        print(f"Verbindung fehlgeschlagen ({type(fehler).__name__}).", file=sys.stderr)
        return 1
    try:
        with conn:
            event_id = eintragen(conn, **werte)
    except ValueError as fehler:
        print(str(fehler) if str(fehler) in _ERLAUBT else "Eingabe abgelehnt.", file=sys.stderr)
        return 2
    except psycopg.Error as fehler:
        print(f"Datenbankfehler ({type(fehler).__name__}).", file=sys.stderr)
        return 1
    print(event_id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
