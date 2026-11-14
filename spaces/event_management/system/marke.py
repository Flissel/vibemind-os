"""Event-Wahl per Marke und zweistufige Bestaetigung riskanter Aktionen (Bot-Werkzeuge).

Marke (``mk_<12 Zeichen>``, 30 min): Schluessel ohne festes Event (leitung, Betreiber-Arten)
waehlen damit ein Event; `kontext_mit_event` bindet den Kontext daran, sodass die
Fachschicht (`event_bindung_pruefen`) jeden Zugriff auf ein anderes Event ablehnt.

Bestaetigungscode (``bs_<8 Zeichen>``, 5 min): einmalig, gebunden an Schluessel, Aktion und
Ziel (die kanonischen IDs der Aktion).

Klartexte stammen aus `secrets` (Alphabet ohne i/l/o/0/1), werden nur als Rueckgabewert
herausgegeben und nie gespeichert oder geloggt; die Datenbank kennt nur ihr sha256-Hex.
event_app hat keine Grants auf `event.marke`/`event.bestaetigung`; alles laeuft ueber die
SD-Funktionen aus `db/004_bot.sql`, gebunden an den Schluessel-HASH (Besitznachweis).
Legacy und Archiv prueft die SQL-Funktion nicht, deshalb hier vorab per Fachschicht.
"""
from __future__ import annotations

import dataclasses
import hashlib
import re
import secrets
from collections.abc import Mapping
from typing import Any
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb

from spaces.event_management.system import events, pruefung, rechte
from spaces.event_management.system.kontext import Kontext, NichtGefunden, transaktion
from spaces.event_management.system.schluessel import SchluesselInfo, kontext_fuer

MARKE_MINUTEN = 30
BESTAETIGUNG_MINUTEN = 5

_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"
_MARKE_PRAEFIX, _MARKE_LAENGE = "mk_", 12
_CODE_PRAEFIX, _CODE_LAENGE = "bs_", 8
_MARKE_FORMAT = re.compile(rf"mk_[{_ALPHABET}]{{{_MARKE_LAENGE}}}")
_CODE_FORMAT = re.compile(rf"bs_[{_ALPHABET}]{{{_CODE_LAENGE}}}")
_AKTION = re.compile(r"[a-z_]{1,64}")
_ZIEL_WERT_MAX = 256

# Je eine Meldung fuer alle Ablehnungsgruende (kein Orakel ueber den Grund).
MARKE_UNGUELTIG = "Marke ungültig oder abgelaufen – bitte Event neu wählen"
CODE_UNGUELTIG = "Bestätigungscode ungültig, abgelaufen oder für eine andere Aktion"
_SCHLUESSEL_ABGELEHNT = "Schlüssel nicht gefunden"


def _zufall(praefix: str, laenge: int) -> str:
    return praefix + "".join(secrets.choice(_ALPHABET) for _ in range(laenge))


def _hash(klartext: str) -> str:
    return hashlib.sha256(klartext.encode("utf-8")).hexdigest()


def _event_uuid(wert: object) -> UUID:
    if isinstance(wert, UUID):
        return wert
    if isinstance(wert, str):
        try:
            return UUID(wert)
        except ValueError:
            pass
    raise ValueError("Feld 'event_id' muss eine UUID sein")


def _aktion(wert: object) -> str:
    if not isinstance(wert, str) or not _AKTION.fullmatch(wert):
        raise ValueError("Aktion ungültig")
    return wert


def _ziel(wert: object) -> dict[str, str]:
    """Ziel als flaches Objekt Text -> Text (Schluessel wie Aktionen: a-z und _)."""
    if not isinstance(wert, Mapping):
        raise ValueError("Ziel ungültig")
    ziel: dict[str, str] = {}
    for name, inhalt in wert.items():
        if not isinstance(name, str) or not _AKTION.fullmatch(name):
            raise ValueError("Ziel ungültig")
        if (not isinstance(inhalt, str) or len(inhalt) > _ZIEL_WERT_MAX
                or not pruefung.zeichen_ok(inhalt)):
            raise ValueError("Ziel ungültig")
        ziel[name] = inhalt
    return ziel


# --- Marke --------------------------------------------------------------------------


def marke_ausstellen(conn: psycopg.Connection[Any], info: SchluesselInfo, event_id: UUID) -> str:
    """Marke fuer ein Event, das der Schluessel sehen darf (Klartext nur hier).

    NichtGefunden, wenn das Event fuer den Schluessel unsichtbar ist (fremde Firma,
    anderes Event beim Event-Schluessel, Melde-Schluessel, unbekannt); ValueError bei
    Legacy- oder archivierten Events.
    """
    event_id = _event_uuid(event_id)
    k = kontext_fuer(conn, info)
    wert = _zufall(_MARKE_PRAEFIX, _MARKE_LAENGE)
    with transaktion(conn, k) as cur:
        ev = events.event_laden(cur, k, event_id)
        rechte.pruefe(k, "lesen", ev["firma_id"])
        if ev["quelle"] == "legacy":
            raise ValueError("Legacy-Event: bitte die alte App nutzen")
        if ev["status"] == "archiviert":
            raise ValueError("Event ist archiviert")
        cur.execute(
            "select event.marke_anlegen(%s, %s, %s, %s) as ok",
            (_hash(wert), info.schluessel_hash, event_id, MARKE_MINUTEN),
        )
        zeile = cur.fetchone()
    if zeile is None or not zeile["ok"]:
        # Schluessel inzwischen ungueltig oder Event fuer ihn nicht (mehr) waehlbar.
        raise NichtGefunden("Event nicht gefunden")
    return wert


def marke_aufloesen(conn: psycopg.Connection[Any], info: SchluesselInfo, marke: str) -> UUID:
    """event_id der Marke; NichtGefunden mit immer derselben Meldung bei fremdem
    Schluessel, Ablauf, unbekannter oder kaputter Marke."""
    # Telegram schreibt den Anfang gross, ein LLM haengt Leerzeichen an: normalisieren.
    if not isinstance(marke, str):
        raise NichtGefunden(MARKE_UNGUELTIG)
    marke = marke.strip().lower()
    if not _MARKE_FORMAT.fullmatch(marke):
        raise NichtGefunden(MARKE_UNGUELTIG)
    with transaktion(conn, Kontext.system()) as cur:
        cur.execute("select event.marke_pruefen(%s, %s) as event_id",
                    (_hash(marke), info.schluessel_hash))
        zeile = cur.fetchone()
    if zeile is None or zeile["event_id"] is None:
        raise NichtGefunden(MARKE_UNGUELTIG)
    event_id: UUID = zeile["event_id"]
    return event_id


def kontext_mit_event(
    conn: psycopg.Connection[Any], info: SchluesselInfo, marke: str | None,
) -> Kontext:
    """Kontext des Schluessels, an ein Event gebunden: Art `event` an sein festes Event
    (die Marke wird ignoriert), alle anderen Arten an das Event ihrer Marke (Pflicht)."""
    k = kontext_fuer(conn, info)
    if info.art == "event":
        return k
    if marke is None:
        raise ValueError("Marke fehlt – bitte zuerst ein Event wählen")
    return dataclasses.replace(k, event_id=marke_aufloesen(conn, info, marke))


# --- Bestaetigung -------------------------------------------------------------------


def bestaetigung_ausstellen(
    conn: psycopg.Connection[Any], info: SchluesselInfo, aktion: str, ziel: Mapping[str, str],
) -> str:
    """Einmal-Code fuer (Schluessel, Aktion, Ziel), Klartext nur hier. ValueError bei
    kaputter Aktion oder kaputtem Ziel; NichtGefunden, wenn der Schluessel nicht mehr
    gilt."""
    aktion = _aktion(aktion)
    sauber = _ziel(ziel)
    code = _zufall(_CODE_PRAEFIX, _CODE_LAENGE)
    with transaktion(conn, Kontext.system()) as cur:
        cur.execute(
            "select event.bestaetigung_anlegen(%s, %s, %s, %s, %s) as ok",
            (_hash(code), info.schluessel_hash, aktion, Jsonb(sauber), BESTAETIGUNG_MINUTEN),
        )
        zeile = cur.fetchone()
    if zeile is None or not zeile["ok"]:
        raise NichtGefunden(_SCHLUESSEL_ABGELEHNT)
    return code


def bestaetigung_einloesen(
    conn: psycopg.Connection[Any], info: SchluesselInfo, code: str, aktion: str,
    ziel: Mapping[str, str],
) -> None:
    """Loest den Code atomar genau einmal ein. ValueError mit immer derselben Meldung,
    wenn er unbekannt, abgelaufen, benutzt, fremd oder fuer eine andere Aktion/ein anderes
    Ziel ist; ein Fehlversuch verbraucht ihn nicht."""
    try:
        aktion = _aktion(aktion)
        sauber = _ziel(ziel)
    except ValueError:
        raise ValueError(CODE_UNGUELTIG) from None
    if not isinstance(code, str):
        raise ValueError(CODE_UNGUELTIG)
    code = code.strip().lower()  # wie bei der Marke: Gross-/Leerzeichen tolerieren
    if not _CODE_FORMAT.fullmatch(code):
        raise ValueError(CODE_UNGUELTIG)
    with transaktion(conn, Kontext.system()) as cur:
        cur.execute(
            "select event.bestaetigung_einloesen(%s, %s, %s, %s) as ok",
            (_hash(code), info.schluessel_hash, aktion, Jsonb(sauber)),
        )
        zeile = cur.fetchone()
    if zeile is None or not zeile["ok"]:
        raise ValueError(CODE_UNGUELTIG)
