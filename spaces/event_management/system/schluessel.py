"""Bot-Schluessel: ausstellen, pruefen, widerrufen und den Aufrufkontext daraus ableiten.

Der Klartext (``evk_<token_urlsafe(32)>``) wird genau einmal zurueckgegeben und nie
gespeichert oder geloggt; in der Datenbank steht nur sein sha256-Hex. Ausstellen und
Widerrufen laufen mit der Admin-DSN (CLI, Bereitstellungs-Arbeiter); `pruefen` und
`kontext_fuer` laufen auch mit der App-DSN (event_app hat keine Grants auf
`event.schluessel`, nur die SD-Funktion `event.schluessel_pruefen`).

CLI (Admin-DSN aus der Datei in ``EVENT_ADMIN_DSN_FILE``)::

    python -m spaces.event_management.system.schluessel ausstellen --art event \\
        --bezeichnung "Gruppe Sommerfest" --event-id <uuid>
    python -m spaces.event_management.system.schluessel widerrufen <uuid>
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import logging
import os
import re
import secrets
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from spaces.event_management.system import firmen, pruefung
from spaces.event_management.system.kontext import Kontext, NichtGefunden, transaktion

log = logging.getLogger(__name__)

ART = ("event", "leitung", "betreiber", "betreiber_lesen", "melden")
PRAEFIX = "evk_"
MAX_TAGE = 730
_TOKEN_BYTES = 32
# token_urlsafe(32): 43 Zeichen aus dem URL-sicheren Base64-Alphabet, ohne Padding.
_FORMAT = re.compile(r"evk_[A-Za-z0-9_-]{43}")
# Eine Meldung fuer alle Ablehnungsgruende (kein Orakel: unbekannt, abgelaufen, ...).
_ABGELEHNT = "Schlüssel nicht gefunden"


@dataclass(frozen=True)
class SchluesselInfo:
    id: UUID
    art: str
    event_id: UUID | None
    firma_id: UUID | None
    person_id: UUID | None


def _hash(klartext: str) -> str:
    return hashlib.sha256(klartext.encode("utf-8")).hexdigest()


def ausstellen(
    conn_admin: psycopg.Connection[Any], *, art: str, bezeichnung: str,
    gueltig_tage: int = 365, event_id: UUID | None = None, person_id: UUID | None = None,
) -> tuple[UUID, str]:
    """Legt einen Schluessel an. Rueckgabe (id, Klartext); der Klartext existiert nur hier.

    ValueError bei falscher Kombination (event ohne Event, leitung ohne Person, Event bei
    anderen Arten), leerer Bezeichnung oder Laufzeit ausserhalb 1..730 Tage;
    NichtGefunden bei unbekanntem Event oder unbekannter Person.
    """
    if art not in ART:
        raise ValueError("Schlüssel-Art ungültig")
    bezeichnung = pruefung.text("bezeichnung", bezeichnung).strip()
    if not bezeichnung:
        raise ValueError("Bezeichnung fehlt")
    if isinstance(gueltig_tage, bool) or not isinstance(gueltig_tage, int):
        raise ValueError("Gültigkeit muss eine ganze Zahl von Tagen sein")
    if not 1 <= gueltig_tage <= MAX_TAGE:
        raise ValueError(f"Gültigkeit muss zwischen 1 und {MAX_TAGE} Tagen liegen")
    if art == "event" and event_id is None:
        raise ValueError("Ein Event-Schlüssel braucht ein Event")
    if art != "event" and event_id is not None:
        raise ValueError("Nur ein Event-Schlüssel ist an ein Event gebunden")
    if art == "leitung" and person_id is None:
        raise ValueError("Ein Leitungs-Schlüssel braucht eine Person")

    klartext = PRAEFIX + secrets.token_urlsafe(_TOKEN_BYTES)
    with conn_admin.transaction(), conn_admin.cursor(row_factory=dict_row) as cur:
        firma_id: UUID | None = None
        if event_id is not None:
            cur.execute("select firma_id from event.event where id = %s", (event_id,))
            ev = cur.fetchone()
            if ev is None:
                raise NichtGefunden("Event nicht gefunden")
            firma_id = ev["firma_id"]
        if person_id is not None:
            cur.execute("select 1 from event.person where id = %s", (person_id,))
            if cur.fetchone() is None:
                raise NichtGefunden("Person nicht gefunden")
        cur.execute(
            "insert into event.schluessel"
            " (hash, art, event_id, firma_id, person_id, bezeichnung, ablauf)"
            " values (%s, %s, %s, %s, %s, %s, now() + make_interval(days => %s))"
            " returning id",
            (_hash(klartext), art, event_id, firma_id, person_id, bezeichnung, gueltig_tage),
        )
        zeile = cur.fetchone()
    assert zeile is not None
    sid: UUID = zeile["id"]
    log.info("Schluessel ausgestellt: id=%s art=%s", sid, art)
    return sid, klartext


def widerrufen(conn_admin: psycopg.Connection[Any], schluessel_id: UUID) -> None:
    """Setzt `widerrufen` (einmal; ein zweiter Aufruf aendert nichts). NichtGefunden bei
    unbekannter id."""
    with conn_admin.transaction(), conn_admin.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "update event.schluessel set widerrufen = coalesce(widerrufen, now())"
            " where id = %s",
            (schluessel_id,),
        )
        if cur.rowcount != 1:
            raise NichtGefunden("Schlüssel nicht gefunden")
    log.info("Schluessel widerrufen: id=%s", schluessel_id)


def pruefen(conn: psycopg.Connection[Any], klartext: object) -> SchluesselInfo:
    """SchluesselInfo eines gueltigen Schluessels, sonst NichtGefunden mit immer derselben
    Meldung (unbekannt, abgelaufen, widerrufen, Person inaktiv, falsches Format)."""
    if not isinstance(klartext, str) or not _FORMAT.fullmatch(klartext):
        raise NichtGefunden(_ABGELEHNT)
    h = _hash(klartext)
    with transaktion(conn, Kontext.system()) as cur:
        cur.execute(
            "select id, hash, art, event_id, firma_id, person_id"
            " from event.schluessel_pruefen(%s)",
            (h,),
        )
        zeile = cur.fetchone()
    if zeile is None or not hmac.compare_digest(str(zeile["hash"]), h):
        raise NichtGefunden(_ABGELEHNT)
    return SchluesselInfo(
        id=zeile["id"], art=zeile["art"], event_id=zeile["event_id"],
        firma_id=zeile["firma_id"], person_id=zeile["person_id"],
    )


def kontext_fuer(conn: psycopg.Connection[Any], info: SchluesselInfo) -> Kontext:
    """Aufrufkontext allein aus dem Schluessel (nie aus Werkzeug-Argumenten).

    event           Firma des Events als Mitglied, an das Event gebunden, ohne Person
    leitung         nur Firmen, in denen die Person JETZT Leitung ist; nie Betreiber
    betreiber(_lesen)  Betreiber (lesend erzwingt der Werkzeugkatalog)
    melden          keine Firmen
    """
    actor = f"bot:{info.art}"
    if info.art == "event":
        if info.event_id is None or info.firma_id is None:
            raise NichtGefunden(_ABGELEHNT)
        return Kontext(person_id=None, betreiber=False, rollen={info.firma_id: "mitglied"},
                       event_id=info.event_id, actor=actor, quelle="bot")
    if info.art == "leitung":
        if info.person_id is None:
            raise NichtGefunden(_ABGELEHNT)
        person = firmen.kontext_fuer_person(conn, info.person_id)
        rollen = {f: r for f, r in person.rollen.items() if r == "leitung"}
        return Kontext(person_id=info.person_id, betreiber=False, rollen=rollen,
                       actor=actor, quelle="bot")
    if info.art in ("betreiber", "betreiber_lesen"):
        return Kontext(person_id=info.person_id, betreiber=True, rollen={},
                       actor=actor, quelle="bot")
    if info.art == "melden":
        return Kontext(person_id=None, betreiber=False, rollen={}, actor=actor, quelle="bot")
    raise NichtGefunden(_ABGELEHNT)


# --- CLI ----------------------------------------------------------------------------


def _dsn_lesen() -> str:
    datei = os.environ.get("EVENT_ADMIN_DSN_FILE", "").strip()
    if not datei:
        return ""
    return Path(datei).read_text(encoding="utf-8").strip()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m spaces.event_management.system.schluessel")
    befehle = parser.add_subparsers(dest="befehl", required=True)
    aus = befehle.add_parser("ausstellen", help="Schlüssel ausstellen (Klartext auf stdout)")
    aus.add_argument("--art", required=True, choices=ART)
    aus.add_argument("--bezeichnung", required=True)
    aus.add_argument("--gueltig-tage", type=int, default=365)
    aus.add_argument("--event-id", type=UUID)
    aus.add_argument("--person-id", type=UUID)
    weg = befehle.add_parser("widerrufen", help="Schlüssel widerrufen")
    weg.add_argument("id", type=UUID)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    dsn = _dsn_lesen()
    if not dsn:
        print("Kein DSN: EVENT_ADMIN_DSN_FILE setzen.", file=sys.stderr)
        return 2
    try:
        conn = psycopg.connect(dsn, autocommit=True)
    except psycopg.Error as fehler:
        # Ohne Details: die Meldung koennte Verbindungsangaben enthalten.
        print(f"Verbindung fehlgeschlagen ({type(fehler).__name__}).", file=sys.stderr)
        return 1
    with conn:
        try:
            if args.befehl == "ausstellen":
                sid, klartext = ausstellen(
                    conn, art=args.art, bezeichnung=args.bezeichnung,
                    gueltig_tage=args.gueltig_tage, event_id=args.event_id,
                    person_id=args.person_id,
                )
                print(f"Schlüssel-ID: {sid}", file=sys.stderr)
                print(klartext)
            else:
                widerrufen(conn, args.id)
                print(f"Widerrufen: {args.id}", file=sys.stderr)
        except (ValueError, NichtGefunden) as fehler:
            print(f"Fehler: {fehler}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
