"""Test-Mitglied ohne Mailversand anlegen und wieder entfernen (Betreiber-CLI).

Wozu: den Events-Reiter mit einem gewoehnlichen Mitglied durchprobieren, ohne eine echte Einladung
zu verschicken und ohne dass das Konto echte Events sieht.

Wie: genau der Weg einer Einladung, nur dass die Mail nicht hinausgeht. Die CLI arbeitet mit der
App-DSN (Rolle event_app, also unter denselben Regeln wie die API) und einem Betreiber-Kontext:
``zugang.einladen`` mit einem Speicher-Mailer, dann ``zugang.einladung_einloesen`` mit dem Token
aus dieser Mail. Kein SQL an den Regeln vorbei. Die Firma ist immer die eigene Testfirma
(Kuerzel ``testfirma``); fehlt sie, wird sie ueber ``firmen.firma_anlegen`` angelegt.

Sicherungen:
- Gibt es die Mailadresse schon, legt die CLI nichts an (sonst bekaeme etwa ein Betreiber-Konto
  eine zweite Rolle, oder ein echtes Mitglied fremde Daten).
- ``entfernen`` (Rueckweg) gilt nur fuer eine Person, die ausser der Testfirma keine Mitgliedschaft
  hat und kein Betreiber ist: Mitgliedschaft weg, Person inaktiv, offene Sitzungen ungueltig.

CLI (App-DSN nur aus der Datei in ``EVENT_APP_DSN_FILE``, Passwort per stdin)::

    python -m spaces.event_management.system.testmitglied anlegen \\
        --mail <mail> --name "<Name>" < passwort-datei
    python -m spaces.event_management.system.testmitglied entfernen --mail <mail>

Ausgabe: auf stdout nur die Person-ID, auf stderr nur feste Texte. Mail, Name, Passwort, Token
und DSN erscheinen nie in einer Ausgabe.
"""
from __future__ import annotations

import argparse
import getpass
import os
import re
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from spaces.event_management.system import betreiber, firmen, pruefung, zugang
from spaces.event_management.system.kontext import Kontext, NichtGefunden, transaktion
from spaces.event_management.system.mailer import SpeicherMailer

TESTFIRMA_NAME = "Testfirma"
TESTFIRMA_KURZ = "testfirma"
ROLLE = "mitglied"
PASSWORT_MIN = betreiber.PASSWORT_MIN
# Nur fuer den Link im (nie versendeten) Mailtext; aus ihm wird das Token gelesen.
_BASIS = "http://testmitglied.invalid"
_TOKEN = re.compile(r"/einladung/([A-Za-z0-9_-]+)")

_MAIL_VERGEBEN = "Diese Mailadresse gibt es schon; für ein Testkonto bitte eine eigene Adresse nehmen"
_NICHT_TEST = "Das ist kein reines Testkonto der Testfirma; nichts geändert"
_KEIN_TOKEN = "Einladung ließ sich nicht einlösen"
_FESTE_TEXTE = frozenset({betreiber._MAIL_UNGUELTIG, betreiber._PASSWORT_KURZ, betreiber._NAME_FEHLT,
                          _MAIL_VERGEBEN, _NICHT_TEST, _KEIN_TOKEN})


def _kontext() -> Kontext:
    """Betreiber-Kontext der CLI (wie ein Betreiber in der Oberflaeche, ohne eigene Person)."""
    return Kontext(person_id=None, betreiber=True, rollen={}, actor="testmitglied-cli", quelle="cli")


def testfirma(conn: psycopg.Connection[Any], k: Kontext) -> UUID:
    """id der Testfirma; legt sie an, wenn sie fehlt."""
    for f in firmen.firmen(conn, k):
        if f["kurz"] == TESTFIRMA_KURZ:
            return f["id"]
    try:
        return firmen.firma_anlegen(conn, k, TESTFIRMA_NAME, TESTFIRMA_KURZ)["id"]
    except ValueError:
        # Parallel angelegt: noch einmal lesen
        for f in firmen.firmen(conn, k):
            if f["kurz"] == TESTFIRMA_KURZ:
                return f["id"]
        raise


def _person(conn: psycopg.Connection[Any], k: Kontext, mail: str) -> dict[str, Any] | None:
    with transaktion(conn, k) as cur:
        cur.execute("select id, betreiber, aktiv from event.person where lower(mail) = %s", (mail,))
        return cur.fetchone()


def anlegen(conn: psycopg.Connection[Any], *, mail: str, name: str, passwort: str) -> UUID:
    """Legt das Test-Mitglied ueber Einladung und Einloesen an; gibt die Person-ID zurueck."""
    mail = betreiber._mail(mail)
    try:
        name = pruefung.text("name", name).strip()
    except ValueError:
        raise ValueError(betreiber._NAME_FEHLT) from None
    if not name:
        raise ValueError(betreiber._NAME_FEHLT)
    if not isinstance(passwort, str) or not pruefung.zeichen_ok(passwort) or len(passwort) < PASSWORT_MIN:
        raise ValueError(betreiber._PASSWORT_KURZ)
    k = _kontext()
    if _person(conn, k, mail) is not None:
        raise ValueError(_MAIL_VERGEBEN)
    firma_id = testfirma(conn, k)
    post = SpeicherMailer()
    zugang.einladen(conn, k, post, mail, name, firma_id, ROLLE, _BASIS)
    treffer = _TOKEN.search(post.gesendet[-1].text) if post.gesendet else None
    if treffer is None:
        raise ValueError(_KEIN_TOKEN)
    return zugang.einladung_einloesen(conn, treffer.group(1), passwort, name)


def entfernen(conn: psycopg.Connection[Any], *, mail: str) -> UUID:
    """Rueckweg: Mitgliedschaft in der Testfirma weg, Person inaktiv, Sitzungen ungueltig.

    NichtGefunden, wenn es die Person nicht gibt; ValueError(_NICHT_TEST), wenn sie Betreiber ist
    oder auch in einer anderen Firma Mitglied ist."""
    mail = betreiber._mail(mail)
    k = _kontext()
    p = _person(conn, k, mail)
    if p is None:
        raise NichtGefunden("Person nicht gefunden")
    firma_id = testfirma(conn, k)
    with transaktion(conn, k) as cur:
        cur.execute("select firma_id from event.mitgliedschaft where person_id = %s", (p["id"],))
        andere = {z["firma_id"] for z in cur.fetchall()} - {firma_id}
    if p["betreiber"] or andere:
        raise ValueError(_NICHT_TEST)
    try:
        firmen.mitglied_entfernen(conn, k, p["id"], firma_id)
    except NichtGefunden:
        pass                                   # schon entfernt: der Rueckweg bleibt wiederholbar
    with transaktion(conn, k) as cur:
        cur.execute(
            "update event.person set aktiv = false, sitzungs_version = sitzungs_version + 1"
            " where id = %s and not betreiber",
            (p["id"],),
        )
    return p["id"]


def _dsn_lesen() -> str:
    datei = os.environ.get("EVENT_APP_DSN_FILE", "").strip()
    if not datei:
        raise betreiber._DsnFehlt("Kein DSN: EVENT_APP_DSN_FILE setzen.")
    try:
        dsn = Path(datei).read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        raise betreiber._DsnFehlt("DSN-Datei nicht lesbar (EVENT_APP_DSN_FILE).") from None
    if not dsn:
        raise betreiber._DsnFehlt("DSN-Datei ist leer (EVENT_APP_DSN_FILE).")
    return dsn


def _passwort_lesen() -> str:
    if sys.stdin.isatty():
        return getpass.getpass("Passwort: ")
    return sys.stdin.readline().rstrip("\r\n")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m spaces.event_management.system.testmitglied")
    befehle = parser.add_subparsers(dest="befehl", required=True)
    an = befehle.add_parser("anlegen", help="Test-Mitglied der Testfirma anlegen (Passwort per stdin)")
    an.add_argument("--mail", required=True)
    an.add_argument("--name", required=True)
    weg = befehle.add_parser("entfernen", help="Test-Mitglied entfernen (Rueckweg)")
    weg.add_argument("--mail", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """0 = OK, 1 = Datenbank/nicht gefunden, 2 = Eingabe oder DSN fehlt."""
    args = _parser().parse_args(argv)
    passwort = ""
    if args.befehl == "anlegen":
        passwort = _passwort_lesen()
        if not passwort:
            print("Kein Passwort auf stdin.", file=sys.stderr)
            return 2
        if len(passwort) < PASSWORT_MIN:
            print(betreiber._PASSWORT_KURZ, file=sys.stderr)
            return 2
    try:
        dsn = _dsn_lesen()
    except betreiber._DsnFehlt as fehler:
        print(str(fehler), file=sys.stderr)
        return 2
    try:
        conn = psycopg.connect(dsn, autocommit=True, row_factory=dict_row)
    except psycopg.Error as fehler:
        print(f"Verbindung fehlgeschlagen ({type(fehler).__name__}).", file=sys.stderr)
        return 1
    with conn:
        try:
            if args.befehl == "anlegen":
                pid = anlegen(conn, mail=args.mail, name=args.name, passwort=passwort)
                print("Test-Mitglied angelegt (Testfirma, Rolle mitglied, keine Mail versendet).",
                      file=sys.stderr)
            else:
                pid = entfernen(conn, mail=args.mail)
                print("Test-Mitglied entfernt (inaktiv, Sitzungen ungültig).", file=sys.stderr)
            print(pid)
        except ValueError as fehler:
            text = str(fehler)
            print(text if text in _FESTE_TEXTE else "Eingabe abgelehnt.", file=sys.stderr)
            return 2
        except NichtGefunden:
            print("Person nicht gefunden.", file=sys.stderr)
            return 1
        except psycopg.Error as fehler:
            print(f"Datenbankfehler ({type(fehler).__name__}).", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
