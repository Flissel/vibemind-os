"""Telegram-Gruppe (Event) und Direktchat (Person) per Einmal-Code verbinden.

Ablauf: eine Leitung erzeugt in der Oberflaeche (oder ueber das Werkzeug
`gruppe_verbinden`) einen Code (`code_erzeugen`), schickt ihn in der Gruppe bzw. im
Direktchat an den Bot, der Empfangs-Agent meldet ihn mit der Chat-ID (`melden`, Werkzeuge
`gruppe_melden`/`chat_melden` des `melden`-Schluessels), und erst die Bestaetigung in der
Oberflaeche (`bestaetigen`) legt genau einen Bereitstellungs-Auftrag an. Ohne Bestaetigung
entsteht kein Auftrag; der Bot bestaetigt nie (``k.quelle == 'bot'``: KeinRecht).

Code: 8 Zeichen aus ALPHABET (32 Zeichen, ohne 0/O/1/I), 15 Minuten gueltig, einmalig,
gespeichert nur als sha256-Hex. Von Hand getippte Codes werden vor der Formatpruefung mit
``strip().upper()`` normalisiert. Ein neuer Code verwirft die alten offenen Codes desselben
Ziels (Event bzw. Person).

Melden (Ruling S3): event-mcp hat nur die App-DSN, also laeuft das Melden ueber die
SD-Funktionen ``event.verbindung_finden`` und ``event.verbindung_melden``. Sie nehmen den
Klartext-Code und hashen selbst (Ruling T7-c): der lesbare Hash allein reicht nie. Antwort
an den Bot ist nur `DANKE` oder einheitlich `UNGUELTIG` (auch waehrend einer Sperre).
Gruppe: Chat-ID negativ, Telegram meldet eine Gruppe und den Bot als Mitglied; Direktchat:
Chat-ID positiv, Telegram meldet einen privaten Chat (Titel = Anzeigename der Person, damit
die Bestaetigung nicht blind ist, Ruling T7-b). Sperre: 5 Fehlmeldungen
je Chat-ID in 15 Minuten sperren diesen Chat 15 Minuten lang (prozesslokal wie die
Login-Sperre in zugang.py). Jede Ablehnung zaehlt, auch ein Telegram-Fehler.

Rueckkanal-Pruefcode (Abschluss-Review F1, Ruling F-a): die chat_id beim Melden ist nur ein
Argument des Empfangs-LLM, getChat beweist nur, dass der geteilte Bot im Chat ist. Nach
erfolgreichem Melden schickt der Dienst deshalb einen zweiten Code (PRUEFCODE_LAENGE
Zeichen aus ALPHABET) per ``telegram.senden`` in genau diesen Chat, und `bestaetigen`
verlangt ihn: wer bestaetigt, beweist Lesezugriff auf den Chat. Melden (mit Pruefcode-Hash)
ist schon festgeschrieben, bevor gesendet wird; scheitert das Senden, bleibt die Meldung
stehen und gilt als "Pruefcode nicht zustellbar" (die Oberflaeche bietet Verwerfen an). Die
Antwort an den Bot bleibt in jedem Fall `DANKE`. Der Pruefcode erscheint nie in Logs,
Werkzeug- oder API-Antworten; sein Hash liegt in ``event.verbindung_pruefung`` (kein Grant
an event_app), Vergleich und Fehlversuch-Zaehler in der SD-Funktion
``event.verbindung_pruefcode_pruefen`` (db/008_pruefcode.sql). Der fuenfte falsche Pruefcode
verwirft die Verbindung; die Meldung bleibt dabei "Pruefcode falsch". Die Pruefung laeuft
vor allen Pruefungen, die etwas ueber andere Verbindungen verraten (Chat schon verbunden,
Agent-Name belegt).

Trennen: eine bestaetigte Verbindung wird verworfen und bekommt in derselben Transaktion
einen `abbauen`-Auftrag (Gruppe: Leitung der Firma bzw. Betreiber; Direktchat: die Person
selbst). Nie aus dem Bot.

Bestaetigen (Ruling S5): Agent ``ev-<8 Hex der event_id>`` (Gruppe), ``ld-<8 Hex der
person_id>`` (Leitung) bzw. ``bo-…`` (Betreiber). Hat schon ein anderes Ziel mit denselben
8 Hex eine bestaetigte Verbindung, ValueError statt Ueberschreiben. Eine bisher bestaetigte
Verbindung desselben Ziels wird verworfen und bekommt einen `abbauen`-Auftrag, der vor dem
neuen `anlegen` liegt (Spalten-Default ``angelegt = clock_timestamp()``, 007). Darf die
Person den alten Agenten nicht mehr abbauen (war Betreiber, ist es nicht mehr: RLS),
entfaellt der Abbau-Auftrag; Agenten verlorener Rollen raeumt der Bereitstellungs-Arbeiter
(Task 8, Admin-DSN) ab.
Eine Meldung ist MELDUNG_STUNDEN lang bestaetigbar (Ruling T7-d).

Das zweite Tor sind die Policies aus ``db/006_verbinden.sql``. Logs nennen nie Codes,
Chat-IDs oder Chat-Titel.
"""
from __future__ import annotations

import hashlib
import logging
import secrets
import threading
import time
from typing import Any, Literal, NamedTuple
from uuid import UUID

import psycopg

from spaces.event_management.system import events, rechte, verlauf
from spaces.event_management.system.kontext import (
    KeinRecht, Kontext, NichtGefunden, transaktion,
)
from spaces.event_management.system.telegram import Telegram, TelegramFehler

log = logging.getLogger(__name__)

ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
CODE_LAENGE = 8
PRUEFCODE_LAENGE = 6
GUELTIG_MINUTEN = 15
ARTEN = ("gruppe", "direkt")
TITEL_MAX = 128
# Telegram-Chat-IDs passen in 52 Bit; groessere Zahlen sind sicher falsch.
CHAT_MAX = 2**53

SPERRE_FEHLER = 5
SPERRE_SEKUNDEN = 15 * 60.0
_SPERRE_AUFRAEUMEN_AB = 10_000

DANKE = "Danke, bitte in der Oberfläche bestätigen"
UNGUELTIG = "Code ungültig oder abgelaufen"
TELEGRAM_AUS = "Telegram ist nicht eingerichtet"
ARCHIVIERT = "Event ist archiviert"
LEGACY = "Legacy-Events sind schreibgeschützt"
NUR_GEMELDETE = "Nur gemeldete Verbindungen können bestätigt werden"
NUR_OFFENE_VERWERFEN = "Nur offene oder gemeldete Verbindungen können verworfen werden"
SCHON_VERBUNDEN = "Dieser Telegram-Chat ist schon anderweitig verbunden"
GRUPPE_SCHON_BESTAETIGT = "Für dieses Event ist schon eine Gruppe bestätigt"
DIREKT_SCHON_BESTAETIGT = "Für dich ist schon ein Telegram-Chat bestätigt"
MELDUNG_ABGELAUFEN = "Meldung abgelaufen – bitte neuen Code erzeugen"
MELDUNG_STUNDEN = 24
PRUEFCODE_FALSCH = "Prüfcode falsch"
PRUEFCODE_NICHT_ZUSTELLBAR = ("Prüfcode nicht zustellbar – bitte verwerfen und einen neuen"
                              " Code erzeugen")
NUR_BESTAETIGTE_TRENNEN = "Nur bestätigte Verbindungen können getrennt werden"
# Fest und neutral: nennt weder Event noch Firma (Fremde im Chat erfahren nichts).
PRUEFCODE_TEXT = ("Bestätigungscode für die Verbindung mit dem Event-System: {pruefcode} "
                  "– nur in der Oberfläche eingeben, wenn du diese Verbindung angefordert "
                  "hast.")
# Eindeutige Indizes aus 006 -> Meldung; unbekannt: SCHON_VERBUNDEN.
_EINDEUTIG = {
    "verbindung_chat_bestaetigt": SCHON_VERBUNDEN,
    "verbindung_gruppe_bestaetigt": GRUPPE_SCHON_BESTAETIGT,
    "verbindung_direkt_bestaetigt": DIREKT_SCHON_BESTAETIGT,
}
AGENT_KOLLISION = "Agent-Name schon vergeben – bitte den Betreiber informieren"
BAUSTEIN = "nachrichten"
AKTION = "gruppe_verbinden"

ANLEITUNG_GRUPPE = (
    "1. Füge den Event-Bot zur Telegram-Gruppe hinzu. "
    "2. Schreib in der Gruppe an den Bot (mit @-Erwähnung): {code} "
    "3. Der Bot schreibt danach einen Prüfcode in die Gruppe. Bestätige die gemeldete Gruppe "
    "mit diesem Prüfcode in der Oberfläche (Event-Übersicht, Abschnitt „Telegram-Gruppe“). "
    "Der Code gilt 15 Minuten."
)

_SPALTEN = ("id, art, status, chat_titel, ablauf, gemeldet, bestaetigt,"
            " (pruefcode_gesendet is not null) as pruefcode_zugestellt")
_MELDUNG_FRISCH = f"gemeldet > now() - interval '{MELDUNG_STUNDEN} hours'"
_ZIEL_GRUPPE = "art = 'gruppe' and event_id = %(ziel)s"
_ZIEL_DIREKT = "art = 'direkt' and person_id = %(ziel)s"

Art = Literal["gruppe", "direkt"]


# --- Hilfen -------------------------------------------------------------------------


def _hash(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def _normalisiert(code: object) -> str | None:
    """Code in Grossbuchstaben ohne Rand, oder None bei falschem Format."""
    if not isinstance(code, str):
        return None
    wert = code.strip().upper()
    if len(wert) != CODE_LAENGE or any(z not in ALPHABET for z in wert):
        return None
    return wert


def _neuer_code(laenge: int = CODE_LAENGE) -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(laenge))


def _titel(wert: object) -> str | None:
    """Chat-Titel ohne Steuer- und Formatzeichen, hoechstens TITEL_MAX Zeichen."""
    if not isinstance(wert, str):
        return None
    sauber = "".join(z for z in wert if z.isprintable()).strip()[:TITEL_MAX].strip()
    return sauber or None


def _art_pruefen(art: object) -> None:
    if art not in ARTEN:
        raise ValueError("Art muss gruppe oder direkt sein")


def _ist_leitung_irgendwo(k: Kontext) -> bool:
    return k.betreiber or "leitung" in k.rollen.values()


def _gruppen_event(cur: psycopg.Cursor[Any], k: Kontext, event_id: UUID) -> dict[str, Any]:
    """Event mit Recht `gruppe_verbinden` (Leitung/Betreiber); Legacy/Archiv abgelehnt."""
    ev = events.event_laden(cur, k, event_id)
    rechte.pruefe(k, AKTION, ev["firma_id"])
    if ev["quelle"] == "legacy":
        raise ValueError(LEGACY)
    if ev["status"] == "archiviert":
        raise ValueError(ARCHIVIERT)
    return ev


# --- Code ---------------------------------------------------------------------------


def code_ausstellen(conn: psycopg.Connection[Any], k: Kontext, *, art: Art,
                    event_id: UUID | None) -> dict[str, Any]:
    """Neuer Einmal-Code: ``{"code", "gueltig_bis"}``; der Klartext nur hier.

    gruppe: Recht `gruppe_verbinden` am Event (Leitung der Firma oder Betreiber).
    direkt: nur fuer die eigene Person, die irgendwo Leitung oder Betreiber ist.
    Alte offene Codes desselben Ziels werden verworfen.
    """
    _art_pruefen(art)
    if art == "gruppe" and event_id is None:
        raise ValueError("Event fehlt")
    if art == "direkt" and event_id is not None:
        raise ValueError("Ein Direktchat gehört zu keinem Event")
    if k.person_id is None or (art == "direkt" and not _ist_leitung_irgendwo(k)):
        raise KeinRecht(f"Keine Berechtigung fuer {AKTION}")
    for versuch in range(3):
        code = _neuer_code()
        try:
            with transaktion(conn, k) as cur:
                if art == "gruppe":
                    assert event_id is not None
                    ev = _gruppen_event(cur, k, event_id)
                    firma_id: UUID | None = ev["firma_id"]
                    bedingung, ziel = _ZIEL_GRUPPE, event_id
                else:
                    firma_id, bedingung, ziel = None, _ZIEL_DIREKT, k.person_id
                cur.execute(f"update event.verbindung set status = 'verworfen'"
                            f" where {bedingung} and status = 'offen'", {"ziel": ziel})
                cur.execute(
                    "insert into event.verbindung (art, code_hash, event_id, firma_id,"
                    " person_id, ablauf) values (%s, %s, %s, %s, %s,"
                    " now() + make_interval(mins => %s)) returning ablauf",
                    (art, _hash(code), event_id, firma_id, k.person_id, GUELTIG_MINUTEN))
                zeile = cur.fetchone()
                assert zeile is not None
        except psycopg.errors.UniqueViolation:
            if versuch == 2:
                raise
            continue  # derselbe Code-Hash existiert schon (praktisch nie): neuer Code
        log.info("Verbindungs-Code erzeugt (%s)", art)
        return {"code": code, "gueltig_bis": zeile["ablauf"]}
    raise AssertionError("unerreichbar")


def code_erzeugen(conn: psycopg.Connection[Any], k: Kontext, *, art: Art,
                  event_id: UUID | None) -> str:
    """Wie `code_ausstellen`, liefert nur den Code."""
    return str(code_ausstellen(conn, k, art=art, event_id=event_id)["code"])


# --- Melden und Sperre ------------------------------------------------------------

_sperre_lock = threading.Lock()
_versuche: dict[int, list[float]] = {}     # laufende Meldungen und Fehlmeldungen je Chat
_gesperrt_bis: dict[int, float] = {}


def _sperre_zuruecksetzen() -> None:
    """Nur fuer Tests: vergisst alle Fehlmeldungen und Sperren."""
    with _sperre_lock:
        _versuche.clear()
        _gesperrt_bis.clear()


def _aufraeumen(jetzt: float) -> None:
    grenze = jetzt - SPERRE_SEKUNDEN
    for chat in list(_versuche):
        frisch = [t for t in _versuche[chat] if t > grenze]
        if frisch:
            _versuche[chat] = frisch
        else:
            del _versuche[chat]
    for chat in [c for c, bis in _gesperrt_bis.items() if bis <= jetzt]:
        del _gesperrt_bis[chat]


def _reservieren(chat_id: int, jetzt: float) -> bool:
    """Zaehlt die Meldung vorab mit (auch parallel hoechstens SPERRE_FEHLER je Fenster);
    False, wenn der Chat gesperrt ist."""
    with _sperre_lock:
        if len(_versuche) + len(_gesperrt_bis) > _SPERRE_AUFRAEUMEN_AB:
            _aufraeumen(jetzt)
        if _gesperrt_bis.get(chat_id, 0.0) > jetzt:
            return False
        versuche = [t for t in _versuche.get(chat_id, []) if t > jetzt - SPERRE_SEKUNDEN]
        if len(versuche) >= SPERRE_FEHLER:
            _versuche[chat_id] = versuche
            return False
        versuche.append(jetzt)
        _versuche[chat_id] = versuche
        return True


def _abschliessen(chat_id: int, jetzt: float, ok: bool) -> None:
    """Erfolg: die Reservierung zaehlt nicht. Fehlmeldung: bleibt; die fuenfte im Fenster
    sperrt den Chat ab jetzt fuer SPERRE_SEKUNDEN."""
    with _sperre_lock:
        versuche = _versuche.get(chat_id, [])
        if ok:
            if jetzt in versuche:
                versuche.remove(jetzt)
            if not versuche:
                _versuche.pop(chat_id, None)
            return
        if len([t for t in versuche if t > jetzt - SPERRE_SEKUNDEN]) >= SPERRE_FEHLER:
            _gesperrt_bis[chat_id] = jetzt + SPERRE_SEKUNDEN


def _chat_passt(art: str, chat_id: int, info: dict[str, Any]) -> bool:
    if art == "gruppe":
        return (chat_id < 0 and info.get("art") in ("group", "supergroup")
                and info.get("bot_ist_mitglied") is True)
    return chat_id > 0 and info.get("art") == "private"


def _melden(conn: psycopg.Connection[Any], code: object, chat_id: int, art: str,
            telegram: Telegram) -> bool:
    wert = _normalisiert(code)
    if wert is None or chat_id == 0 or (chat_id < 0) != (art == "gruppe"):
        return False
    with transaktion(conn, Kontext.system()) as cur:
        cur.execute("select art from event.verbindung_finden(%s)", (wert,))
        offen = cur.fetchone()
    if offen is None or offen["art"] != art:
        return False
    # Ausserhalb jeder Transaktion: Netzaufruf.
    try:
        info = telegram.chat_info(chat_id)
    except TelegramFehler:
        log.warning("Meldung abgelehnt: Telegram-Abfrage fehlgeschlagen")
        return False
    if not _chat_passt(art, chat_id, info):
        return False
    pruefcode = _neuer_code(PRUEFCODE_LAENGE)
    # Eigene Transaktion: die Meldung samt Pruefcode-Hash steht fest, bevor gesendet wird.
    with transaktion(conn, Kontext.system()) as cur:
        cur.execute("select event.verbindung_melden(%s, %s, %s, %s) as ok",
                    (wert, chat_id, _titel(info.get("titel")), pruefcode))
        zeile = cur.fetchone()
    if not (zeile is not None and zeile["ok"]):
        return False
    _pruefcode_senden(conn, telegram, wert, chat_id, pruefcode)
    return True


def _pruefcode_senden(conn: psycopg.Connection[Any], telegram: Telegram, code: str,
                      chat_id: int, pruefcode: str) -> None:
    """Schickt den Pruefcode in genau den gemeldeten Chat und vermerkt die Zustellung.
    Jeder Fehler laesst die Meldung stehen (dann "nicht zustellbar"); geloggt wird nur der
    Fehlertyp, nie Code, Pruefcode oder Chat-ID."""
    try:
        telegram.senden(chat_id, PRUEFCODE_TEXT.format(pruefcode=pruefcode))
    except Exception as e:  # noqa: BLE001 - jeder Sendefehler: Meldung bleibt, Antwort gleich
        log.warning("Pruefcode nicht zustellbar (%s)", type(e).__name__)
        return
    try:
        with transaktion(conn, Kontext.system()) as cur:
            cur.execute("select event.verbindung_pruefcode_zugestellt(%s)", (code,))
    except psycopg.Error as e:
        log.warning("Zustellung des Pruefcodes nicht vermerkt (%s)", type(e).__name__)


def melden(conn: psycopg.Connection[Any], *, code: str, chat_id: int, art: str,
           telegram: Telegram | None, jetzt: float | None = None) -> str:
    """Meldet einen Code aus einem Telegram-Chat; Rueckgabe `DANKE` oder `UNGUELTIG`.

    Laeuft mit der App-DSN ohne Mandanten-Kontext (Ruling S3). ValueError nur bei
    falscher `art`/`chat_id` (Aufruffehler) oder ohne Telegram.
    """
    _art_pruefen(art)
    if telegram is None:
        raise ValueError(TELEGRAM_AUS)
    if isinstance(chat_id, bool) or not isinstance(chat_id, int) or abs(chat_id) > CHAT_MAX:
        raise ValueError("chat_id muss eine Telegram-Chat-ID sein")
    jetzt = time.time() if jetzt is None else jetzt
    if not _reservieren(chat_id, jetzt):
        log.warning("Meldung abgelehnt: Chat gesperrt (zu viele Fehlmeldungen)")
        return UNGUELTIG
    ok = False
    try:
        ok = _melden(conn, code, chat_id, art, telegram)
    finally:
        _abschliessen(chat_id, jetzt, ok)
    if ok:
        log.info("Verbindung gemeldet (%s)", art)
        return DANKE
    log.info("Meldung abgelehnt (%s)", art)
    return UNGUELTIG


# --- Lesen, Bestaetigen, Verwerfen -------------------------------------------------


def offene(conn: psycopg.Connection[Any], k: Kontext, *,
           event_id: UUID | None = None) -> list[dict[str, Any]]:
    """Laufende Verbindungen: nicht abgelaufene offene Codes, gemeldete der letzten
    MELDUNG_STUNDEN (mit Chat-Titel zur Bestaetigung) und die bestaetigte. Mit `event_id`
    die Gruppe des Events (Recht ``lesen``), sonst der Direktchat der eigenen Person. Ohne
    Chat-ID und Code-Hash."""
    bedingung = ("(status = 'bestaetigt' or (status = 'offen' and ablauf > now())"
                 f" or (status = 'gemeldet' and {_MELDUNG_FRISCH}))")
    with transaktion(conn, k) as cur:
        if event_id is not None:
            ev = events.event_laden(cur, k, event_id)
            rechte.pruefe(k, "lesen", ev["firma_id"])
            ziel_sql, ziel = _ZIEL_GRUPPE, event_id
        else:
            if k.person_id is None:
                return []
            ziel_sql, ziel = _ZIEL_DIREKT, k.person_id
        cur.execute(f"select {_SPALTEN} from event.verbindung where {ziel_sql}"
                    f" and {bedingung} order by ablauf desc, id", {"ziel": ziel})
        return cur.fetchall()


def _laden(cur: psycopg.Cursor[Any], k: Kontext, verbindung_id: UUID, *,
           leitung_noetig: bool = True) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Verbindung und (Gruppe) ihr Event, mit Rechtepruefung. Fremde Firma, anderes
    gebundenes Event oder fremder Direktchat: NichtGefunden; Mitglied ohne Leitung:
    KeinRecht. Ohne `leitung_noetig` (Trennen) reicht beim Direktchat die eigene Person."""
    cur.execute("select id, art, event_id, firma_id, person_id, chat_id, status,"
                " pruefcode_gesendet is not null as zugestellt,"
                f" {_MELDUNG_FRISCH} as frisch from event.verbindung where id = %s",
                (verbindung_id,))
    v = cur.fetchone()
    if v is None:
        raise NichtGefunden("Verbindung nicht gefunden")
    if v["art"] == "gruppe":
        ev = events.event_laden(cur, k, v["event_id"])
        rechte.pruefe(k, AKTION, ev["firma_id"])
        return v, ev
    if v["person_id"] != k.person_id:
        raise NichtGefunden("Verbindung nicht gefunden")
    if leitung_noetig and not _ist_leitung_irgendwo(k):
        raise KeinRecht(f"Keine Berechtigung fuer {AKTION}")
    return v, None


def _nicht_aus_dem_bot(k: Kontext) -> None:
    if k.quelle == "bot" or k.person_id is None:
        raise KeinRecht(f"Keine Berechtigung fuer {AKTION}")


class _Ziel(NamedTuple):
    """Wofuer ein Auftrag gilt: Ziel-Art, Agent (Ruling S5), SQL-Bedingung des Ziels und
    die Spalten des Auftrags."""
    ziel: str
    agent: str
    bedingung: str
    ziel_id: UUID
    event_id: UUID | None
    person_id: UUID | None


def _ziel(k: Kontext, v: dict[str, Any]) -> _Ziel:
    if v["art"] == "gruppe":
        ziel_id: UUID = v["event_id"]
        return _Ziel("gruppe", f"ev-{UUID(str(ziel_id)).hex[:8]}", _ZIEL_GRUPPE, ziel_id,
                     ziel_id, k.person_id)
    ziel, praefix = ("betreiber", "bo") if k.betreiber else ("leitung", "ld")
    ziel_id = v["person_id"]
    return _Ziel(ziel, f"{praefix}-{UUID(str(ziel_id)).hex[:8]}", _ZIEL_DIREKT, ziel_id,
                 None, ziel_id)


def _alter_agent(cur: psycopg.Cursor[Any], v: dict[str, Any], ziel: str,
                 agent: str) -> tuple[str, str]:
    """Agent einer bisher bestaetigten Verbindung: Gruppe immer derselbe; Direktchat aus
    dem letzten Anlege-Auftrag fuer diesen Chat (falls die Person inzwischen Betreiber
    geworden ist oder nicht mehr), sonst wie der neue."""
    if v["art"] == "gruppe":
        return ziel, agent
    cur.execute("select ziel, agent_id from event.bereitstellung where art = 'anlegen'"
                " and event_id is null and person_id = %s and chat_id = %s"
                " order by angelegt desc, id limit 1", (v["person_id"], v["chat_id"]))
    frueher = cur.fetchone()
    return (frueher["ziel"], frueher["agent_id"]) if frueher else (ziel, agent)


_AUFTRAG = ("insert into event.bereitstellung (art, ziel, event_id, person_id, chat_id,"
            " agent_id) values (%s, %s, %s, %s, %s, %s) returning id")


def _abbau_anlegen(cur: psycopg.Cursor[Any], alt: dict[str, Any], z: _Ziel) -> UUID | None:
    """`abbauen`-Auftrag fuer eine eben verworfene, vorher bestaetigte Verbindung `alt`.
    Darf die Person den alten Agenten nicht mehr abbauen (RLS), entfaellt er (None)."""
    alt_ziel, alt_agent = _alter_agent(cur, alt, z.ziel, z.agent)
    try:
        with cur.connection.transaction():  # Savepoint: RLS-Ablehnung nur fuer diesen Auftrag
            cur.execute(_AUFTRAG, ("abbauen", alt_ziel, z.event_id, z.person_id,
                                   alt["chat_id"], alt_agent))
            zeile = cur.fetchone()
    except psycopg.errors.InsufficientPrivilege:
        # z. B. frueher Betreiber (bo-), jetzt nur Leitung: der Arbeiter (Task 8) raeumt
        # Agenten verlorener Rollen mit der Admin-DSN ab.
        log.warning("Abbau uebersprungen (%s): keine Berechtigung mehr fuer den alten Agenten",
                    alt_ziel)
        return None
    assert zeile is not None
    return UUID(str(zeile["id"]))


def _bestaetigbar(v: dict[str, Any], ev: dict[str, Any] | None) -> None:
    """Pruefungen, die nur die eigene Verbindung betreffen (vor dem Pruefcode)."""
    if ev is not None:
        if ev["quelle"] == "legacy":
            raise ValueError(LEGACY)
        if ev["status"] == "archiviert":
            raise ValueError(ARCHIVIERT)
    if v["status"] != "gemeldet":
        raise ValueError(NUR_GEMELDETE)
    if not v["frisch"]:
        raise ValueError(MELDUNG_ABGELAUFEN)
    if not v["zugestellt"]:
        raise ValueError(PRUEFCODE_NICHT_ZUSTELLBAR)


def _pruefcode_eingabe(pruefcode: object) -> str:
    """Von Hand getippt: ``strip().upper()`` (wie beim Code); nur kurze Texte ohne NUL gehen
    an die SD-Funktion, alles andere ist schlicht falsch (und zaehlt als Fehlversuch)."""
    if isinstance(pruefcode, str) and len(pruefcode) <= 64 and "\x00" not in pruefcode:
        return pruefcode.strip().upper()
    return ""


def bestaetigen(conn: psycopg.Connection[Any], k: Kontext, verbindung_id: UUID,
                pruefcode: str) -> UUID:
    """Bestaetigt eine gemeldete Verbindung mit dem Pruefcode aus dem Chat und legt genau
    einen `anlegen`-Auftrag an; Rueckgabe: dessen id. Eine bisher bestaetigte Verbindung
    desselben Ziels wird verworfen (mit `abbauen`-Auftrag), andere offene/gemeldete Codes
    desselben Ziels auch.

    KeinRecht aus dem Bot, ohne Person oder fuer ein Mitglied ohne Leitung; ValueError
    wenn nicht gemeldet, Meldung aelter als MELDUNG_STUNDEN, Pruefcode nicht zugestellt,
    Event archiviert/Legacy, Pruefcode falsch (zaehlt; der fuenfte verwirft die
    Verbindung), Chat schon anderweitig verbunden, Ziel im Wettlauf schon bestaetigt oder
    Agent-Name belegt (Ruling S5). Die letzten drei erst nach richtigem Pruefcode.
    """
    _nicht_aus_dem_bot(k)
    auftrag: UUID | None = None
    try:
        with transaktion(conn, k) as cur:
            v, ev = _laden(cur, k, verbindung_id)
            _bestaetigbar(v, ev)
            cur.execute("select event.verbindung_pruefcode_pruefen(%s, %s) as ergebnis",
                        (verbindung_id, _pruefcode_eingabe(pruefcode)))
            pruefung = cur.fetchone()
            if pruefung is not None and pruefung["ergebnis"] == "ok":
                auftrag = _bestaetigen_in(cur, k, v, ev, verbindung_id)
            # Sonst endet die Transaktion regulaer: der Fehlversuch bleibt gezaehlt.
    except psycopg.errors.UniqueViolation as e:
        raise ValueError(_EINDEUTIG.get(e.diag.constraint_name or "", SCHON_VERBUNDEN)) from None
    if auftrag is None:
        log.info("Bestaetigung abgelehnt: Pruefcode falsch (%s)", v["art"])
        raise ValueError(PRUEFCODE_FALSCH)
    log.info("Verbindung bestaetigt (%s), Auftrag angelegt", v["art"])
    return auftrag


def _bestaetigen_in(cur: psycopg.Cursor[Any], k: Kontext, v: dict[str, Any],
                    ev: dict[str, Any] | None, verbindung_id: UUID) -> UUID:
    """Bestaetigen nach richtigem Pruefcode (in dessen Transaktion)."""
    z = _ziel(k, v)
    cur.execute("select event.agent_belegt(%s, %s) as belegt", (z.agent, z.ziel_id))
    belegt = cur.fetchone()
    if belegt is None or belegt["belegt"]:
        raise ValueError(AGENT_KOLLISION)

    parameter = {"ziel": z.ziel_id, "id": verbindung_id}
    cur.execute(f"update event.verbindung set status = 'verworfen' where {z.bedingung}"
                " and id <> %(id)s and status = 'bestaetigt'"
                " returning art, person_id, chat_id", parameter)
    alte = cur.fetchall()
    cur.execute(f"update event.verbindung set status = 'verworfen' where {z.bedingung}"
                " and id <> %(id)s and status in ('offen', 'gemeldet')", parameter)
    cur.execute("update event.verbindung set status = 'bestaetigt', bestaetigt = now()"
                f" where id = %s and status = 'gemeldet' and {_MELDUNG_FRISCH}"
                " returning chat_id", (verbindung_id,))
    neu = cur.fetchone()
    if neu is None:
        raise ValueError(NUR_GEMELDETE)

    for alt in alte:
        _abbau_anlegen(cur, alt, z)
    cur.execute(_AUFTRAG, ("anlegen", z.ziel, z.event_id, z.person_id, neu["chat_id"],
                           z.agent))
    zeile = cur.fetchone()
    assert zeile is not None
    if ev is not None:
        verlauf.schreiben(cur, k, ev["id"], ev["firma_id"], BAUSTEIN, "gruppe_bestaetigt",
                          verbindung_id)
    return UUID(str(zeile["id"]))


def verwerfen(conn: psycopg.Connection[Any], k: Kontext,
              verbindung_id: UUID) -> dict[str, Any]:
    """Verwirft einen offenen oder gemeldeten Code (Rechte wie `bestaetigen`, auch im
    Archiv). Eine bestaetigte Verbindung baut `trennen` ab."""
    _nicht_aus_dem_bot(k)
    with transaktion(conn, k) as cur:
        v, ev = _laden(cur, k, verbindung_id)
        cur.execute("update event.verbindung set status = 'verworfen' where id = %s"
                    f" and status in ('offen', 'gemeldet') returning {_SPALTEN}",
                    (verbindung_id,))
        zeile = cur.fetchone()
        if zeile is None:
            raise ValueError(NUR_OFFENE_VERWERFEN)
        if ev is not None and v["status"] == "gemeldet":
            verlauf.schreiben(cur, k, ev["id"], ev["firma_id"], BAUSTEIN, "gruppe_verworfen",
                              verbindung_id)
    return zeile


def trennen(conn: psycopg.Connection[Any], k: Kontext,
            verbindung_id: UUID) -> dict[str, Any]:
    """Trennt eine bestaetigte Verbindung: `verworfen` und in derselben Transaktion ein
    `abbauen`-Auftrag fuer ihren Agenten. Gruppe: Leitung der Firma des Events oder
    Betreiber; Direktchat: die Person selbst (auch ohne Leitung). Nie aus dem Bot (KeinRecht).
    Der Chat ist danach frei. Rueckgabe ``{"id", "status", "bereitstellung"}``; der Auftrag
    ist None, wenn RLS den Abbau nicht zulaesst (der Arbeiter raeumt den Agenten dann als
    verwaist ab)."""
    _nicht_aus_dem_bot(k)
    with transaktion(conn, k) as cur:
        v, ev = _laden(cur, k, verbindung_id, leitung_noetig=False)
        cur.execute("update event.verbindung set status = 'verworfen' where id = %s"
                    " and status = 'bestaetigt' returning art, person_id, chat_id",
                    (verbindung_id,))
        alt = cur.fetchone()
        if alt is None:
            raise ValueError(NUR_BESTAETIGTE_TRENNEN)
        auftrag = _abbau_anlegen(cur, alt, _ziel(k, v))
        if ev is not None:
            verlauf.schreiben(cur, k, ev["id"], ev["firma_id"], BAUSTEIN, "gruppe_getrennt",
                              verbindung_id)
    log.info("Verbindung getrennt (%s)", v["art"])
    return {"id": verbindung_id, "status": "verworfen", "bereitstellung": auftrag}
