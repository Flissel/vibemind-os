"""Nachrichten eines Events: an die Telegram-Gruppe und Mail-Entwuerfe mit Freigabe.

Gruppe: `an_gruppe` sendet sofort an die bestaetigte Telegram-Gruppe des Events (aus
`event.verbindung`, ``art='gruppe'``, ``status='bestaetigt'``; die `person_id` der
Verbindung setzt der Melder und zaehlt fuer nichts) und speichert das Ergebnis
(`versendet` oder `fehler`).

Mail: `mail_entwerfen` speichert nur einen Entwurf; versendet wird nie aus dem Bot oder der
Oberflaeche. Eine Leitung (oder ein Betreiber) gibt frei (`freigeben`, Aktion
``nachricht_freigeben``), erst danach verschickt `versenden` (eigener Schritt mit der
Admin-DSN, CLI unten) ueber einen `Versandweg`. Empfaenger sind die Teilnehmenden des
Events mit Mail und passender Zusage, je Adresse (ohne Gross-/Kleinschreibung) einmal;
gezaehlt wird bis zum Versand live, danach steht die Zahl in `empfaenger_anzahl`.

Versandweg (Ruling Etappe 3): der Betreiber-Mailer (`SmtpMailer.aus_umgebung`, wie die
Einladungen); der sales-claw-Weg kommt spaeter als austauschbarer Adapter.

Doppelversand: `versenden` setzt jede Nachricht in einer eigenen Transaktion unter
Zeilensperre (``for update skip locked``) von `freigegeben` auf `versendet`, bevor die
erste Mail rausgeht; ein zweiter Lauf (auch parallel) findet sie nicht mehr. Bricht der
Prozess mitten im Versand ab, bleibt sie `versendet` (lieber eine Mail zu wenig als
doppelt). Das zweite Tor sind die restriktiven RLS-Policies aus ``db/005_nachrichten.sql``.

Logs nennen nie Texte, Betreffe, Adressen oder Chat-IDs.

CLI (Admin-DSN aus der Datei in ``EVENT_ADMIN_DSN_FILE``, Mailer aus ``EVENT_SMTP_*``)::

    python -m spaces.event_management.system.nachrichten versenden
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from spaces.event_management.system import events, pruefung, rechte, verlauf
from spaces.event_management.system.kontext import (
    KeinRecht, Kontext, NichtGefunden, event_bindung_pruefen, transaktion,
)
from spaces.event_management.system.mailer import Mailer, SmtpMailer
from spaces.event_management.system.telegram import Telegram, TelegramFehler

log = logging.getLogger(__name__)

Versandweg = Mailer

ZUSAGEN = ("offen", "zugesagt", "vielleicht", "abgesagt")
TEXT_MAX = 4000  # Telegram erlaubt 4096 Zeichen je Nachricht
BETREFF_MAX = 200
FEHLER_MAX = 300
KEINE_GRUPPE = "Keine Telegram-Gruppe verbunden"
TELEGRAM_AUS = "Telegram ist nicht eingerichtet"
ARCHIVIERT = "Event ist archiviert"
NUR_ENTWUERFE_FREIGEBEN = "Nur Entwürfe können freigegeben werden"
NUR_ENTWUERFE_VERWERFEN = "Nur Entwürfe können verworfen werden"
KEINE_EMPFAENGER = "Keine Empfänger mit Mail für diese Zusage"
BAUSTEIN = "nachrichten"

_SPALTEN = ("id, event_id, kanal, betreff, text, empfaenger_filter, status, angelegt,"
            " erstellt_von, freigegeben, versendet, fehler, empfaenger_anzahl")

# Adressen der Teilnehmenden eines Events (%(event)s) mit einer der Zusagen (%(zusage)s,
# text[]), je Adresse ohne Gross-/Kleinschreibung einmal (der zuerst angelegte Kontakt).
_EMPFAENGER = """
    select distinct on (lower(btrim(k.mail))) btrim(k.mail) as mail
    from event.teilnehmende t
    join event.kontakt k on k.id = t.kontakt_id and k.firma_id = t.firma_id
    where t.event_id = %(event)s and t.zusage = any(%(zusage)s)
      and k.mail is not null and btrim(k.mail) <> ''
    order by lower(btrim(k.mail)), k.angelegt, k.id
"""


# --- Eingaben -----------------------------------------------------------------------


def _text(feld: str, wert: Any, maximum: int) -> str:
    text = pruefung.text(feld, wert).strip()
    if not text:
        raise ValueError(f"Feld '{feld}' darf nicht leer sein")
    if len(text) > maximum:
        raise ValueError(f"Feld '{feld}' ist zu lang (höchstens {maximum} Zeichen)")
    return text


def _betreff(wert: Any) -> str:
    betreff = _text("betreff", wert, BETREFF_MAX)
    if "\n" in betreff or "\r" in betreff:
        raise ValueError("Feld 'betreff' darf keinen Zeilenumbruch enthalten")
    return betreff


def _zusagen(werte: Any) -> list[str]:
    """Nicht leere Liste aus ZUSAGEN; Rueckgabe ohne Doppelte in der Reihenfolge von ZUSAGEN."""
    if not isinstance(werte, (list, tuple)) or not werte:
        raise ValueError("Feld 'zusage' braucht mindestens eine Zusage")
    for wert in werte:
        if not isinstance(wert, str) or wert not in ZUSAGEN:
            raise ValueError(f"Feld 'zusage' erlaubt nur: {', '.join(ZUSAGEN)}")
    return [z for z in ZUSAGEN if z in werte]


# --- Hilfen -------------------------------------------------------------------------


def _vorspann(cur: psycopg.Cursor[Any], k: Kontext, event_id: UUID, aktion: str,
              *, archiv_sperrt: bool = True) -> dict[str, Any]:
    ev = events.event_laden(cur, k, event_id)
    rechte.pruefe(k, aktion, ev["firma_id"])
    if aktion != "lesen":
        if ev["quelle"] == "legacy":
            raise ValueError("Legacy-Events sind schreibgeschützt")
        if archiv_sperrt and ev["status"] == "archiviert":
            raise ValueError(ARCHIVIERT)
    return ev


def _ersteller(cur: psycopg.Cursor[Any], k: Kontext) -> str:
    """Anzeigename wie im Verlauf: k.actor, sonst der Personenname."""
    if k.actor:
        return k.actor
    if k.person_id is not None:
        cur.execute("select name from event.person where id = %s", (k.person_id,))
        row = cur.fetchone()
        if row is not None:
            return str(row["name"])
    return "System"


def _empfaenger(cur: psycopg.Cursor[Any], event_id: UUID, zusage: list[str]) -> list[str]:
    cur.execute(_EMPFAENGER, {"event": event_id, "zusage": zusage})
    return [str(z["mail"]) for z in cur.fetchall()]


def _ausgabe(zeile: dict[str, Any], empfaenger: int | None) -> dict[str, Any]:
    filter_ = zeile.get("empfaenger_filter") or {}
    aus = {name: zeile[name] for name in (
        "id", "event_id", "kanal", "betreff", "text", "status", "angelegt", "erstellt_von",
        "freigegeben", "versendet", "fehler")}
    aus["zusage"] = list(filter_.get("zusage", [])) if zeile["kanal"] == "mail" else None
    aus["empfaenger"] = empfaenger
    return aus


def _empfaenger_zahl(cur: psycopg.Cursor[Any], zeile: dict[str, Any]) -> int | None:
    """Gruppe: None; Mail vor dem Versand: live gezaehlt; danach die gespeicherte Zahl."""
    if zeile["kanal"] != "mail":
        return None
    if zeile["status"] in ("versendet", "fehler") and zeile["empfaenger_anzahl"] is not None:
        return int(zeile["empfaenger_anzahl"])
    zusage = list((zeile.get("empfaenger_filter") or {}).get("zusage", []))
    return len(_empfaenger(cur, zeile["event_id"], zusage))


def _nachricht_laden(cur: psycopg.Cursor[Any], k: Kontext, nachricht_id: UUID) -> dict[str, Any]:
    cur.execute(f"select {_SPALTEN} from event.nachricht where id = %s",
                (nachricht_id,))
    zeile = cur.fetchone()
    if zeile is None:
        raise NichtGefunden("Nachricht nicht gefunden")
    event_bindung_pruefen(k, zeile["event_id"])
    return zeile


# --- Fachfunktionen -----------------------------------------------------------------


def an_gruppe(conn: psycopg.Connection[Any], k: Kontext, event_id: UUID, text: str,
              *, telegram: Telegram | None) -> dict[str, Any]:
    """Sendet `text` an die bestaetigte Telegram-Gruppe des Events (Recht
    ``baustein_schreiben``) und speichert die Nachricht als `versendet` oder `fehler`.

    ValueError: keine bestaetigte Gruppe, Telegram nicht eingerichtet, Text leer/zu lang,
    Event archiviert oder Legacy. Ein Telegram-Fehler wirft nicht, er steht im Ergebnis.
    """
    text = _text("text", text, TEXT_MAX)
    with transaktion(conn, k) as cur:
        _vorspann(cur, k, event_id, "baustein_schreiben")
        cur.execute(
            "select chat_id from event.verbindung"
            " where event_id = %s and art = 'gruppe' and status = 'bestaetigt'"
            " and chat_id is not null order by bestaetigt desc nulls last, id limit 1",
            (event_id,))
        gruppe = cur.fetchone()
    if gruppe is None:
        raise ValueError(KEINE_GRUPPE)
    if telegram is None:
        raise ValueError(TELEGRAM_AUS)

    fehler: str | None = None
    try:
        telegram.senden(int(gruppe["chat_id"]), text)
    except TelegramFehler as e:
        fehler = str(e)[:FEHLER_MAX]  # Text ist maskiert (telegram.py)
        log.warning("Nachricht an die Gruppe nicht gesendet")

    with transaktion(conn, k) as cur:
        ev = events.event_laden(cur, k, event_id)
        cur.execute(
            "insert into event.nachricht (event_id, firma_id, kanal, text, status,"
            " erstellt_von, versendet, fehler) values (%s, %s, 'gruppe', %s, %s, %s,"
            f" case when %s then now() end, %s) returning {_SPALTEN}",
            (event_id, ev["firma_id"], text, "fehler" if fehler else "versendet",
             _ersteller(cur, k), fehler is None, fehler))
        zeile = cur.fetchone()
        assert zeile is not None
        verlauf.schreiben(cur, k, event_id, ev["firma_id"], BAUSTEIN,
                          "fehler" if fehler else "gesendet", zeile["id"])
    return _ausgabe(zeile, None)


def mail_entwerfen(conn: psycopg.Connection[Any], k: Kontext, event_id: UUID, betreff: str,
                   text: str, zusage: list[str]) -> dict[str, Any]:
    """Speichert einen Mail-Entwurf (Recht ``baustein_schreiben``); verschickt nichts.
    `empfaenger` im Ergebnis: Teilnehmende mit Mail und einer der Zusagen."""
    betreff = _betreff(betreff)
    text = _text("text", text, TEXT_MAX)
    zusagen = _zusagen(zusage)
    with transaktion(conn, k) as cur:
        ev = _vorspann(cur, k, event_id, "baustein_schreiben")
        cur.execute(
            "insert into event.nachricht (event_id, firma_id, kanal, betreff, text,"
            " empfaenger_filter, erstellt_von) values (%s, %s, 'mail', %s, %s, %s, %s)"
            f" returning {_SPALTEN}",
            (event_id, ev["firma_id"], betreff, text, Jsonb({"zusage": zusagen}),
             _ersteller(cur, k)))
        zeile = cur.fetchone()
        assert zeile is not None
        anzahl = len(_empfaenger(cur, event_id, zusagen))
        verlauf.schreiben(cur, k, event_id, ev["firma_id"], BAUSTEIN, "entworfen", zeile["id"])
    return _ausgabe(zeile, anzahl)


def freigeben(conn: psycopg.Connection[Any], k: Kontext, nachricht_id: UUID) -> dict[str, Any]:
    """Gibt einen Mail-Entwurf zum Versand frei: nur Leitung oder Betreiber (Aktion
    ``nachricht_freigeben``, eine Person muss dahinterstehen), nur aus `entwurf`, nur mit
    mindestens einem Empfaenger."""
    with transaktion(conn, k) as cur:
        n = _nachricht_laden(cur, k, nachricht_id)
        ev = _vorspann(cur, k, n["event_id"], "nachricht_freigeben")
        if k.person_id is None:
            raise KeinRecht("Keine Berechtigung fuer nachricht_freigeben")
        if n["kanal"] != "mail" or n["status"] != "entwurf":
            raise ValueError(NUR_ENTWUERFE_FREIGEBEN)
        anzahl = _empfaenger_zahl(cur, n)
        if not anzahl:
            raise ValueError(KEINE_EMPFAENGER)
        # Bedingt: zwischen Lesen und Schreiben kann ein anderer freigegeben/verworfen haben.
        cur.execute(
            "update event.nachricht set status = 'freigegeben', freigegeben = now(),"
            " freigegeben_von = %s where id = %s and status = 'entwurf'"
            f" returning {_SPALTEN}", (k.person_id, nachricht_id))
        zeile = cur.fetchone()
        if zeile is None:
            raise ValueError(NUR_ENTWUERFE_FREIGEBEN)
        verlauf.schreiben(cur, k, n["event_id"], ev["firma_id"], BAUSTEIN, "freigegeben",
                          nachricht_id)
    return _ausgabe(zeile, anzahl)


def verwerfen(conn: psycopg.Connection[Any], k: Kontext, nachricht_id: UUID) -> dict[str, Any]:
    """Verwirft einen Mail-Entwurf (Recht ``baustein_schreiben``); auch im Archiv."""
    with transaktion(conn, k) as cur:
        n = _nachricht_laden(cur, k, nachricht_id)
        ev = _vorspann(cur, k, n["event_id"], "baustein_schreiben", archiv_sperrt=False)
        cur.execute(
            "update event.nachricht set status = 'verworfen' where id = %s"
            f" and kanal = 'mail' and status = 'entwurf' returning {_SPALTEN}",
            (nachricht_id,))
        zeile = cur.fetchone()
        if zeile is None:
            raise ValueError(NUR_ENTWUERFE_VERWERFEN)
        verlauf.schreiben(cur, k, n["event_id"], ev["firma_id"], BAUSTEIN, "verworfen",
                          nachricht_id)
        return _ausgabe(zeile, _empfaenger_zahl(cur, zeile))


def liste(conn: psycopg.Connection[Any], k: Kontext, event_id: UUID) -> list[dict[str, Any]]:
    """Nachrichten des Events, neueste zuerst (Recht ``lesen``), mit Empfaengerzahl."""
    with transaktion(conn, k) as cur:
        _vorspann(cur, k, event_id, "lesen")
        cur.execute(f"select {_SPALTEN} from event.nachricht where event_id = %s"
                    " order by angelegt desc, id desc", (event_id,))
        zeilen = cur.fetchall()
        return [_ausgabe(z, _empfaenger_zahl(cur, z)) for z in zeilen]


# --- Versand (Admin-DSN) --------------------------------------------------------------


def _beanspruchen(conn_admin: psycopg.Connection[Any]) -> tuple[dict[str, Any], list[str]] | None:
    """Setzt die aelteste freigegebene Mail auf `versendet` (eigene Transaktion, Sperre)
    und liefert sie mit ihren Empfaengern; None, wenn keine wartet."""
    with conn_admin.transaction(), conn_admin.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "update event.nachricht set status = 'versendet', versendet = now()"
            " where id = (select id from event.nachricht"
            "             where kanal = 'mail' and status = 'freigegeben'"
            "             order by freigegeben, id limit 1 for update skip locked)"
            " and status = 'freigegeben'"
            " returning id, event_id, firma_id, betreff, text, empfaenger_filter")
        n = cur.fetchone()
        if n is None:
            return None
        zusage = list((n["empfaenger_filter"] or {}).get("zusage", []))
        return n, _empfaenger(cur, n["event_id"], zusage)


def versenden(conn_admin: psycopg.Connection[Any], versand: Versandweg) -> int:
    """Verschickt alle freigegebenen Mails je Empfaenger ueber `versand`; jede Nachricht
    genau einmal (siehe Moduldoku). Rueckgabe: Zahl der verarbeiteten Nachrichten."""
    verarbeitet = 0
    while (beansprucht := _beanspruchen(conn_admin)) is not None:
        n, adressen = beansprucht
        fehlgeschlagen = 0
        for an in adressen:
            try:
                versand.senden(an, n["betreff"] or "", n["text"])
            except Exception as e:  # noqa: BLE001 - je Empfaenger weiter, nur Typname
                fehlgeschlagen += 1
                log.warning("Mail nicht versendet (%s)", type(e).__name__)
        fehler = (f"{fehlgeschlagen} von {len(adressen)} Mails nicht versendet"
                  if fehlgeschlagen else None)
        with conn_admin.transaction():
            conn_admin.execute(
                "update event.nachricht set status = %s, fehler = %s, empfaenger_anzahl = %s"
                " where id = %s",
                ("fehler" if fehler else "versendet", fehler, len(adressen), n["id"]))
            conn_admin.execute(
                "insert into event.verlauf (event_id, firma_id, actor, quelle, baustein,"
                " aktion, ziel_id) values (%s, %s, 'System', 'system', %s, %s, %s)",
                (n["event_id"], n["firma_id"], BAUSTEIN,
                 "fehler" if fehler else "versendet", n["id"]))
        log.info("Mail-Nachricht verarbeitet: %d Empfaenger, %d fehlgeschlagen",
                 len(adressen), fehlgeschlagen)
        verarbeitet += 1
    return verarbeitet


# --- CLI ----------------------------------------------------------------------------


class _DsnFehlt(Exception):
    """DSN-Datei nicht gesetzt oder nicht lesbar (Meldung fest, ohne Pfad oder Inhalt)."""


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


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m spaces.event_management.system.nachrichten")
    befehle = parser.add_subparsers(dest="befehl", required=True)
    befehle.add_parser("versenden", help="Freigegebene Mails versenden (Admin-DSN, EVENT_SMTP_*)")
    parser.parse_args(argv)
    try:
        dsn = _dsn_lesen()
        mailer = SmtpMailer.aus_umgebung()
    except (_DsnFehlt, ValueError) as fehler:
        print(str(fehler), file=sys.stderr)
        return 2
    try:
        conn = psycopg.connect(dsn, autocommit=True, row_factory=dict_row)
    except psycopg.Error as fehler:
        # Ohne Details: die Meldung koennte Verbindungsangaben enthalten.
        print(f"Verbindung fehlgeschlagen ({type(fehler).__name__}).", file=sys.stderr)
        return 1
    with conn:
        anzahl = versenden(conn, mailer)
    print(anzahl)
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    sys.exit(main())
