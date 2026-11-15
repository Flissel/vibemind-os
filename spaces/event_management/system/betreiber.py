"""Ersten Betreiber anlegen und Betreiber-Recht entziehen (Admin-CLI, Etappe 4 F-5).

Warum eine eigene CLI: Ohne laufenden Betreiber gibt es keinen Weg zu
``person.betreiber = true``. Die Policy ``person_anlegen`` (db/002) laesst nur
``not betreiber`` zu, aendern darf eine Person nur ein Betreiber, und Einladungen brauchen
eine Firma, die wiederum nur ein Betreiber anlegt. Darum arbeitet diese CLI mit der
Admin-DSN, genau wie ``schluessel`` und ``legacy``.

CLI (Admin-DSN nur aus der Datei in ``EVENT_ADMIN_DSN_FILE``, Passwort per stdin)::

    python -m spaces.event_management.system.betreiber anlegen \\
        --mail <mail> --name "<Name>" < passwort-datei
    python -m spaces.event_management.system.betreiber entziehen <person-id>

Ausgabe: auf stdout nur die Person-ID, auf stderr nur feste Texte. Mail, Name, Passwort
und DSN erscheinen nie in einer Ausgabe. Gibt es die Mail schon, wird die Person
Betreiber; ein vorhandenes Passwort bleibt unveraendert (die CLI ueberschreibt keine
Anmeldedaten). ``entziehen`` ist der Rueckweg und macht offene Sitzungen ungueltig.
"""
from __future__ import annotations

import argparse
import getpass
import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from spaces.event_management.system import pruefung, zugang
from spaces.event_management.system.kontext import NichtGefunden

# Dieselbe Mindestlaenge wie beim Einloesen einer Einladung (zugang._PASSWORT_MIN).
PASSWORT_MIN = 12

_MAIL_UNGUELTIG = "Mailadresse ungültig"
_PASSWORT_KURZ = f"Das Passwort braucht mindestens {PASSWORT_MIN} Zeichen"
_NAME_FEHLT = "Name fehlt"
_INAKTIV = "Person ist inaktiv"
# Nur diese Texte gehen unveraendert auf stderr; alles andere wird zu "Eingabe abgelehnt."
_FESTE_TEXTE = frozenset({_MAIL_UNGUELTIG, _PASSWORT_KURZ, _NAME_FEHLT, _INAKTIV})
_VERBOTEN_IN_MAIL = frozenset(" \t\r\n\v\f,;<>\"'()[]\\")


@dataclass(frozen=True)
class Ergebnis:
    person_id: UUID
    neu: bool
    passwort_gesetzt: bool
    hochgestuft: bool = False  # war vorher nicht Betreiber


def meldung(erg: Ergebnis) -> str:
    """Fester Text fuer stderr: neu, hochgestuft oder schon Betreiber (R-2)."""
    if erg.neu:
        return "Betreiber angelegt."
    pw = "Passwort gesetzt." if erg.passwort_gesetzt else None
    if erg.hochgestuft:
        return "Vorhandene Person ist jetzt Betreiber, " + (pw or "Passwort unverändert.")
    return "Person war schon Betreiber, " + (pw or "nichts geändert.")


def _mail(roh: object) -> str:
    if not isinstance(roh, str) or not pruefung.zeichen_ok(roh):
        raise ValueError(_MAIL_UNGUELTIG)
    mail = roh.strip().lower()
    lokal, at, domain = mail.partition("@")
    if (not at or not lokal or "@" in domain or "." not in domain or len(mail) > 254
            or domain.startswith(".") or domain.endswith(".")
            or any(z in _VERBOTEN_IN_MAIL or ord(z) < 32 or ord(z) == 127 for z in mail)):
        raise ValueError(_MAIL_UNGUELTIG)
    return mail


def anlegen(conn_admin: psycopg.Connection[Any], *, mail: str, name: str,
            passwort: str) -> Ergebnis:
    """Legt die Person als Betreiber an oder macht eine vorhandene (aktive) zum Betreiber.

    Validiert alles, bevor geschrieben wird. Ein vorhandenes Passwort wird nie ueberschrieben,
    ein vorhandener Name auch nicht. Idempotent.
    """
    mail = _mail(mail)
    try:
        name = pruefung.text("name", name).strip()
    except ValueError:
        raise ValueError(_NAME_FEHLT) from None
    if not name:
        raise ValueError(_NAME_FEHLT)
    if not isinstance(passwort, str) or not pruefung.zeichen_ok(passwort) \
            or len(passwort) < PASSWORT_MIN:
        raise ValueError(_PASSWORT_KURZ)
    h = zugang.passwort_hash(passwort)
    with conn_admin.transaction(), conn_admin.cursor(row_factory=dict_row) as cur:
        # Zwei gleichzeitige Laeufe fuer dieselbe Mail: der zweite wartet und findet die Person.
        cur.execute("select pg_advisory_xact_lock(hashtext('betreiber:' || %s))", (mail,))
        cur.execute(
            "select id, aktiv, betreiber, passwort_hash is not null as hat_pw from event.person"
            " where lower(mail) = %s for update",
            (mail,),
        )
        zeile = cur.fetchone()
        if zeile is None:
            cur.execute(
                "insert into event.person (mail, name, passwort_hash, betreiber)"
                " values (%s, %s, %s, true) returning id",
                (mail, name, h),
            )
            neu = cur.fetchone()
            assert neu is not None
            return Ergebnis(person_id=neu["id"], neu=True, passwort_gesetzt=True)
        if not zeile["aktiv"]:
            raise ValueError(_INAKTIV)
        cur.execute(
            "update event.person set betreiber = true,"
            " passwort_hash = coalesce(passwort_hash, %s) where id = %s",
            (h, zeile["id"]),
        )
        return Ergebnis(person_id=zeile["id"], neu=False, passwort_gesetzt=not zeile["hat_pw"],
                        hochgestuft=not zeile["betreiber"])


def entziehen(conn_admin: psycopg.Connection[Any], person_id: UUID) -> None:
    """Rueckweg: nimmt das Betreiber-Recht und macht offene Sitzungen ungueltig.

    NichtGefunden, wenn die Person unbekannt oder kein Betreiber ist.
    """
    with conn_admin.transaction(), conn_admin.cursor() as cur:
        cur.execute(
            "update event.person set betreiber = false,"
            " sitzungs_version = sitzungs_version + 1 where id = %s and betreiber",
            (person_id,),
        )
        if cur.rowcount != 1:
            raise NichtGefunden("Betreiber nicht gefunden")


class _DsnFehlt(Exception):
    pass


def _dsn_lesen() -> str:
    datei = os.environ.get("EVENT_ADMIN_DSN_FILE", "").strip()
    if not datei:
        raise _DsnFehlt("Kein DSN: EVENT_ADMIN_DSN_FILE setzen.")
    try:
        dsn = Path(datei).read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        raise _DsnFehlt("DSN-Datei nicht lesbar (EVENT_ADMIN_DSN_FILE).") from None
    if not dsn:
        raise _DsnFehlt("DSN-Datei ist leer (EVENT_ADMIN_DSN_FILE).")
    return dsn


def _passwort_lesen() -> str:
    """Am Terminal verdeckt abfragen, sonst genau eine Zeile von stdin (ohne Zeilenende)."""
    if sys.stdin.isatty():
        return getpass.getpass("Passwort: ")
    return sys.stdin.readline().rstrip("\r\n")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m spaces.event_management.system.betreiber")
    befehle = parser.add_subparsers(dest="befehl", required=True)
    an = befehle.add_parser("anlegen", help="Ersten Betreiber anlegen (Passwort per stdin)")
    an.add_argument("--mail", required=True)
    an.add_argument("--name", required=True)
    weg = befehle.add_parser("entziehen", help="Betreiber-Recht entziehen (Rueckweg)")
    weg.add_argument("person_id", type=UUID)
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
            print(_PASSWORT_KURZ, file=sys.stderr)
            return 2
    try:
        dsn = _dsn_lesen()
    except _DsnFehlt as fehler:
        print(str(fehler), file=sys.stderr)
        return 2
    try:
        conn = psycopg.connect(dsn, autocommit=True)
    except psycopg.Error as fehler:
        # Ohne Details: die Meldung koennte Verbindungsangaben enthalten.
        print(f"Verbindung fehlgeschlagen ({type(fehler).__name__}).", file=sys.stderr)
        return 1
    with conn:
        try:
            if args.befehl == "anlegen":
                erg = anlegen(conn, mail=args.mail, name=args.name, passwort=passwort)
                print(meldung(erg), file=sys.stderr)
                print(erg.person_id)
            else:
                entziehen(conn, args.person_id)
                print(f"Betreiber-Recht entzogen: {args.person_id}", file=sys.stderr)
        except ValueError as fehler:
            text = str(fehler)
            print(text if text in _FESTE_TEXTE else "Eingabe abgelehnt.", file=sys.stderr)
            return 2
        except NichtGefunden:
            print("Betreiber nicht gefunden.", file=sys.stderr)
            return 1
        except psycopg.Error as fehler:
            print(f"Datenbankfehler ({type(fehler).__name__}).", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
