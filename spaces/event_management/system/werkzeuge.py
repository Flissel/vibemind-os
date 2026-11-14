"""Werkzeugkatalog je Bot-Schluessel und Ausfuehrung, MCP-frei (der Adapter ist mcp_server).

Der Katalog entsteht bei jedem Aufruf neu aus der Schluessel-Art und (Art `event`) dem
wirksamen Schema des gebundenen Events; Baustein-Werkzeuge kommen aus `REGISTRY`:
``<eintrag>_liste``, ``_anlegen``, ``_aendern``, ``_loeschen``. Kein Werkzeug nimmt
`event_id`, `firma_id` oder `person_id`; das Event steht im Schluessel (Art `event`) oder
kommt ueber eine Marke (Pflichtargument `marke` aller anderen Arten).

`ausfuehren` laesst nur Werkzeuge aus dem Katalog des Schluessels zu, prueft die Argumente
gegen das JSON-Schema (Meldungen nennen nur Argumentnamen, nie Werte), baut den Kontext
ueber `marke.kontext_mit_event` und ruft die Fachschicht; Rechte, Event-Bindung und RLS
prueft diese selbst. `betreiber_lesen` ist fachlich Betreiber; lesend haelt ihn allein
dieser Katalog.

Kontakte (Ruling S1): fuer den Event-Schluessel nur `kontakte_suchen` und
`kontakt_anlegen` (Firma des Events); Aendern gibt es fuer den Bot nicht, Entfernen nur
zweistufig fuer Leitung/Betreiber (die Firma kommt dort aus dem gebundenen Event).

Verwaltung (Task 5, nur `leitung`/`betreiber`): Event-Wahl, `event_anlegen`, `einladen`
(Mailer und Basis-URL der App), `firma_anlegen` (nur `betreiber`) und die zweistufigen
Aktionen `event_absagen`, `event_archivieren`, `event_loeschen`, `kontakt_entfernen`,
`rolle_aendern` sowie `einladen` mit Rolle `leitung` (Abschluss-Review F4): der erste
Aufruf ohne `bestaetigung` aendert nichts und liefert eine
Zusammenfassung mit Code (`marke.bestaetigung_ausstellen`, gebunden an Aktion und die
selbst aufgeloesten kanonischen IDs); erst der zweite Aufruf mit dem Code fuehrt aus. Der
eingeloeste Code ist nur Zustimmung: Rechte und RLS prueft die Fachschicht trotzdem.
Firmen heissen in Argumenten nur per Kuerzel (`kurz`), Events nur per Marke oder Suche.

Archiv (Ruling T5-a): schreibende Baustein-Werkzeuge lehnen ein archiviertes gebundenes
Event ab (alle Schluessel-Arten); Lesen bleibt erlaubt, die Marke bleibt gueltig.

Nachrichten (Task 6, `nachrichten.py`): `nachricht_an_gruppe` sendet ueber das Telegram der
App an die bestaetigte Gruppe des Events (ohne Telegram: "Telegram ist nicht
eingerichtet"), `mail_entwerfen` speichert nur einen Entwurf (freigeben kann allein eine
Leitung in der Oberflaeche, versendet wird getrennt), `nachrichten_liste` liest.
`betreiber_lesen` hat nur die Liste. Archiv lehnt die Fachschicht ab.

Verbinden (Task 7, `verbinden.py`): `gruppe_verbinden` (nur `leitung`/`betreiber`, mit Marke)
liefert einen Einmal-Code samt Anleitung. Der `melden`-Schluessel (Empfangs-Agent) hat nur
`gruppe_melden` und `chat_melden`; ihre Antwort ist bewusst nur `verbinden.DANKE` oder
`verbinden.UNGUELTIG` (ohne Event: der Melder ist ein Fremder, bis die Leitung bestaetigt).

Erweitern: neue Werkzeuge als `_Sonder`-Eintrag in `_SONDER` und ihr Name in
`_SONDER_JE_ART`.
"""
from __future__ import annotations

import logging
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from uuid import UUID

import psycopg

from spaces.event_management.system import (
    events, firmen, kern, marke, nachrichten, pruefung, rechte, verbinden, verlauf, zugang,
)
from spaces.event_management.system.bausteine import REGISTRY
from spaces.event_management.system.kategorien import Kategorie, WirksamesSchema
from spaces.event_management.system.kontext import Kontext, NichtGefunden, transaktion
from spaces.event_management.system.mailer import Mailer
from spaces.event_management.system.schluessel import SchluesselInfo, kontext_fuer
from spaces.event_management.system.telegram import Telegram

log = logging.getLogger(__name__)

UNBEKANNT = "unbekanntes Werkzeug"
LEGACY = "Legacy-Event: bitte die alte App nutzen"
TEXT_MAX = 2000
MAX_TREFFER = 20
VERLAUF_VORGABE = 50
GENAUER = "Bitte genauer"
GENAUER_DATUM = "Bitte genauer – oder mit datum (JJJJ-MM-TT, Tag des Beginns) wählen"
ARCHIVIERT = "Event ist archiviert"
EINLADEN_AUS = "Einladen ist nicht eingerichtet"
_DATUM = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")

# Deutsche Standardnamen (Gegenstueck zu web/lib/begriffe.ts); das Event-Schema
# (`begriffe` der Kategorie) hat Vorrang.
STANDARD_BEGRIFFE: Mapping[str, str] = {
    "aufgaben": "Aufgaben", "orga_team": "Orga-Team", "teilnehmende": "Teilnehmende",
    "ablauf": "Ablauf", "budget": "Budget", "nachrichten": "Nachrichten", "verlauf": "Verlauf",
    "wettbewerb_teams": "Teams", "abgaben": "Abgaben", "jury": "Jury",
    "aussteller": "Aussteller", "staende": "Stände", "sprecher": "Sprecher",
    "online_raum": "Online-Raum", "aufzeichnung": "Aufzeichnung", "protokoll": "Protokoll",
    "beschluesse": "Beschlüsse", "verpflegung": "Verpflegung", "programm": "Programm",
    "helferschichten": "Helferschichten", "buehnen": "Bühnen",
}
# Registry-Eintraege ohne eigenen Baustein.
_HILFS_BEGRIFFE: Mapping[str, str] = {"aufgaben_kategorien": "Aufgaben-Kategorien"}

_OPS = ("liste", "anlegen", "aendern", "loeschen")
_NUR_LESEN = ("liste",)
_OHNE_IDS = frozenset({"event_id", "firma_id"})


@dataclass(frozen=True)
class Werkzeug:
    name: str
    beschreibung: str
    eingabe: dict[str, Any]  # JSON-Schema
    schreibend: bool


Kategorien = Mapping[str, Kategorie] | None


@dataclass(frozen=True)
class _Umgebung:
    """Was die App den Werkzeugen mitgibt (Kategorien; Mailer und Basis-URL fuer Einladen;
    Telegram fuer Nachrichten an die Gruppe)."""
    kategorien: Kategorien = None
    mailer: Mailer | None = None
    basis_url: str | None = None
    telegram: Telegram | None = None


Ausfuehrung = Callable[
    [psycopg.Connection[Any], SchluesselInfo, dict[str, Any], _Umgebung], dict[str, Any]
]


@dataclass(frozen=True)
class _Eintrag:
    werkzeug: Werkzeug
    ausfuehren: Ausfuehrung


# --- Begriffe und JSON-Schema -------------------------------------------------------


def begriff(eintrag: str, schema_begriffe: Mapping[str, str] | None = None) -> str:
    """Anzeigename eines Registry-Eintrags oder Bausteins (Event-Begriff vor Standard)."""
    if eintrag in _HILFS_BEGRIFFE:
        return _HILFS_BEGRIFFE[eintrag]
    return (schema_begriffe or {}).get(eintrag) or STANDARD_BEGRIFFE.get(eintrag, eintrag)


def _feld_schema(typ: str) -> dict[str, Any]:
    if typ == "text":
        return {"type": "string", "maxLength": TEXT_MAX}
    if typ == "ganzzahl":
        return {"type": "integer", "minimum": 0}
    if typ == "datumzeit":
        return {"type": "string", "format": "date-time",
                "description": "ISO-8601; ohne Zeitzone gilt Europe/Berlin"}
    if typ.startswith("auswahl:"):
        return {"type": "string", "enum": typ[len("auswahl:"):].split(",")}
    if typ == "url":
        return {"type": "string", "pattern": "^https?://"}
    if typ == "kontakt":
        return {"type": "string", "format": "uuid",
                "description": "Kontakt-ID (kontakte_suchen)"}
    if typ.startswith("ref:"):
        ziel = typ[len("ref:"):]
        return {"type": "string", "format": "uuid", "description": f"ID aus {ziel}_liste"}
    if typ == "uuid":
        return {"type": "string", "format": "uuid"}
    raise ValueError("Unbekannter Feldtyp")


_MARKE_SCHEMA: dict[str, Any] = {
    "type": "string", "maxLength": 64,
    "description": "Marke des Events aus event_waehlen (gilt 30 Minuten)",
}


def _objekt(eigenschaften: Mapping[str, dict[str, Any]], pflicht: list[str],
            *, mit_marke: bool) -> dict[str, Any]:
    props: dict[str, Any] = {}
    erforderlich: list[str] = []
    if mit_marke:
        props["marke"] = dict(_MARKE_SCHEMA)
        erforderlich.append("marke")
    for name, s in eigenschaften.items():
        props[name] = dict(s)
    erforderlich += pflicht
    schema: dict[str, Any] = {"type": "object", "properties": props,
                              "additionalProperties": False}
    if erforderlich:
        schema["required"] = erforderlich
    return schema


# --- Argumentpruefung ---------------------------------------------------------------


def _wert_pruefen(name: str, s: Mapping[str, Any], wert: object) -> None:
    if wert is None:
        raise ValueError(f"Argument '{name}' darf nicht leer sein")
    if s["type"] == "array":
        if not isinstance(wert, list):
            raise ValueError(f"Argument '{name}' muss eine Liste sein")
        if len(wert) < s.get("minItems", 0):
            raise ValueError(f"Argument '{name}' braucht mindestens {s['minItems']} Eintrag")
        if "maxItems" in s and len(wert) > s["maxItems"]:
            raise ValueError(f"Argument '{name}' hat zu viele Einträge (höchstens {s['maxItems']})")
        for eintrag in wert:
            _wert_pruefen(name, s["items"], eintrag)
        return
    if s["type"] == "integer":
        if isinstance(wert, bool) or not isinstance(wert, int):
            raise ValueError(f"Argument '{name}' muss eine ganze Zahl sein")
        if "minimum" in s and wert < s["minimum"]:
            raise ValueError(f"Argument '{name}' muss mindestens {s['minimum']} sein")
        if "maximum" in s and wert > s["maximum"]:
            raise ValueError(f"Argument '{name}' darf höchstens {s['maximum']} sein")
        return
    if not isinstance(wert, str):
        raise ValueError(f"Argument '{name}' muss Text sein")
    if not pruefung.zeichen_ok(wert):
        raise ValueError(f"Argument '{name}' enthält ungültige Zeichen")
    if "maxLength" in s and len(wert) > s["maxLength"]:
        raise ValueError(f"Argument '{name}' ist zu lang (höchstens {s['maxLength']} Zeichen)")
    if "enum" in s and wert not in s["enum"]:
        raise ValueError(f"Argument '{name}' erlaubt nur: {', '.join(s['enum'])}")
    if s.get("format") == "date":
        # fullmatch mit ASCII-Ziffern (das \d im Schema-Muster liesse Unicode-Ziffern und
        # ein "\n" am Ende durch), dann echtes Kalenderdatum.
        try:
            if not _DATUM.fullmatch(wert):
                raise ValueError
            date.fromisoformat(wert)
        except ValueError:
            raise ValueError(f"Argument '{name}' muss ein Datum JJJJ-MM-TT sein") from None
    elif "pattern" in s and not re.match(s["pattern"], wert):
        raise ValueError(f"Argument '{name}' muss mit http:// oder https:// beginnen")
    if s.get("format") == "uuid":
        try:
            UUID(wert)
        except ValueError:
            raise ValueError(f"Argument '{name}' muss eine UUID sein") from None
    if s.get("format") == "date-time":
        try:
            datetime.fromisoformat(wert)
        except ValueError:
            raise ValueError(f"Argument '{name}' ist keine gültige Zeit (ISO-8601)") from None


def argumente_pruefen(schema: Mapping[str, Any], argumente: object) -> dict[str, Any]:
    """Prueft `argumente` gegen ein Werkzeug-Schema; Rueckgabe ist eine Kopie."""
    if not isinstance(argumente, Mapping):
        raise ValueError("Argumente müssen ein Objekt sein")
    props: Mapping[str, Any] = schema["properties"]
    for name in argumente:
        if name not in props:
            erlaubt = ", ".join(props) or "keine"
            raise ValueError(f"Unbekanntes Argument (erlaubt: {erlaubt})")
    for name in schema.get("required", ()):
        if argumente.get(name) is None:
            raise ValueError(f"Pflichtargument '{name}' fehlt")
    for name, wert in argumente.items():
        _wert_pruefen(name, props[name], wert)
    return dict(argumente)


# --- Event-Bindung ------------------------------------------------------------------


@dataclass(frozen=True)
class _Gebunden:
    k: Kontext
    event: dict[str, Any]

    @property
    def kopf(self) -> dict[str, str]:
        return {"name": self.event["name"], "firma": self.event["firma_name"]}


def _event_laden(conn: psycopg.Connection[Any], k: Kontext, event_id: UUID) -> dict[str, Any]:
    with transaktion(conn, k) as cur:
        ev = events.event_laden(cur, k, event_id)
        rechte.pruefe(k, "lesen", ev["firma_id"])
        cur.execute("select name from event.firma where id = %s", (ev["firma_id"],))
        firma = cur.fetchone()
    if firma is None:
        raise NichtGefunden("Event nicht gefunden")
    ev["firma_name"] = firma["name"]
    return ev


def _binden(conn: psycopg.Connection[Any], info: SchluesselInfo,
            args: dict[str, Any]) -> _Gebunden:
    """Kontext an das Event des Schluessels bzw. der Marke gebunden; Legacy abgelehnt."""
    k = marke.kontext_mit_event(conn, info, args.pop("marke", None))
    if k.event_id is None:
        raise NichtGefunden("Event nicht gefunden")
    ev = _event_laden(conn, k, k.event_id)
    if ev["quelle"] == "legacy":
        raise ValueError(LEGACY)
    return _Gebunden(k, ev)


def _schema(ev: Mapping[str, Any], kategorien: Kategorien) -> WirksamesSchema | None:
    try:
        return events.schema_fuer(ev["kategorie"], kategorien)
    except ValueError:
        return None  # unbekannte Kategorie: keine Bausteine


def _ohne_ids(zeile: Mapping[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in zeile.items() if k not in _OHNE_IDS}


# --- Baustein-Werkzeuge -------------------------------------------------------------


def _baustein_werkzeug(eintrag: str, op: str, begriffe: Mapping[str, str] | None,
                       *, mit_marke: bool) -> Werkzeug:
    t = REGISTRY[eintrag]
    b = begriff(eintrag, begriffe)
    zeile = {"zeile": {"type": "string", "format": "uuid",
                       "description": f"ID aus {eintrag}_liste"}}
    if op == "liste":
        return Werkzeug(f"{eintrag}_liste", f"{b} des Events anzeigen",
                        _objekt({}, [], mit_marke=mit_marke), False)
    felder = {name: _feld_schema(typ) for name, typ in t.felder.items()}
    if op == "anlegen":
        pflicht = [name for name in t.felder if name in t.pflicht]
        return Werkzeug(f"{eintrag}_anlegen", f"Neuen Eintrag in {b} anlegen",
                        _objekt(felder, pflicht, mit_marke=mit_marke), True)
    if op == "aendern":
        return Werkzeug(f"{eintrag}_aendern",
                        f"Eintrag in {b} ändern (nur die genannten Felder)",
                        _objekt({**zeile, **felder}, ["zeile"], mit_marke=mit_marke), True)
    return Werkzeug(f"{eintrag}_loeschen", f"Eintrag aus {b} löschen",
                    _objekt(zeile, ["zeile"], mit_marke=mit_marke), True)


def _baustein_ausfuehrung(eintrag: str, op: str) -> Ausfuehrung:
    t = REGISTRY[eintrag]
    baustein = t.baustein or t.name

    def ausfuehren(conn: psycopg.Connection[Any], info: SchluesselInfo, args: dict[str, Any],
                   u: _Umgebung) -> dict[str, Any]:
        kategorien = u.kategorien
        g = _binden(conn, info, args)
        schema = _schema(g.event, kategorien)
        if schema is None or baustein not in schema.bausteine:
            begriffe = schema.begriffe if schema is not None else None
            raise ValueError(f"Dieses Event hat keinen Bereich {begriff(baustein, begriffe)}")
        if op != "liste" and g.event["status"] == "archiviert":
            raise ValueError(ARCHIVIERT)  # Ruling T5-a
        event_id = g.event["id"]
        if op == "liste":
            zeilen = kern.liste(conn, g.k, event_id, eintrag, kategorien=kategorien)
            return {"event": g.kopf, "zeilen": [_ohne_ids(z) for z in zeilen]}
        if op == "anlegen":
            neu = kern.anlegen(conn, g.k, event_id, eintrag, args, kategorien=kategorien)
            return {"event": g.kopf, "zeile": _ohne_ids(neu)}
        zeile_id = UUID(args.pop("zeile"))
        if op == "aendern":
            neu = kern.aendern(conn, g.k, event_id, eintrag, zeile_id, args,
                               kategorien=kategorien)
            return {"event": g.kopf, "zeile": _ohne_ids(neu)}
        kern.loeschen(conn, g.k, event_id, eintrag, zeile_id, kategorien=kategorien)
        return {"event": g.kopf, "zeile": {"id": zeile_id, "geloescht": True}}

    return ausfuehren


# --- Weitere Werkzeuge --------------------------------------------------------------


def _event_info(conn: psycopg.Connection[Any], info: SchluesselInfo, args: dict[str, Any],
                u: _Umgebung) -> dict[str, Any]:
    g = _binden(conn, info, args)
    ev = g.event
    schema = _schema(ev, u.kategorien)
    begriffe = schema.begriffe if schema is not None else None
    return {
        "event": g.kopf,
        "kategorie": ev["kategorie"],
        "kategorie_name": schema.name if schema is not None else ev["kategorie"],
        "status": ev["status"],
        "beginn": ev["beginn"], "ende": ev["ende"],
        "ort_adresse": ev["ort_adresse"], "ort_link": ev["ort_link"],
        "beschreibung": ev["beschreibung"], "metadaten": ev["metadaten"],
        "begriffe": {b: begriff(b, begriffe)
                     for b in (schema.bausteine if schema is not None else ())},
    }


def _verlauf_lesen(conn: psycopg.Connection[Any], info: SchluesselInfo, args: dict[str, Any],
                   u: _Umgebung) -> dict[str, Any]:
    g = _binden(conn, info, args)
    seit = events.zeit(datetime.fromisoformat(args["seit"]), "seit") if "seit" in args else None
    eintraege = verlauf.lesen(conn, g.k, g.event["id"], seit=seit,
                              limit=args.get("limit", VERLAUF_VORGABE))
    return {"event": g.kopf, "eintraege": [
        {name: e[name] for name in ("zeitpunkt", "actor", "quelle", "baustein", "aktion",
                                    "ziel_id")}
        for e in eintraege
    ]}


def _kontakt(zeile: Mapping[str, Any]) -> dict[str, Any]:
    return {name: zeile[name] for name in ("id", "name", "mail", "telegram")}


def _kontakte_finden(conn: psycopg.Connection[Any], k: Kontext, firma_id: UUID,
                     suche: str) -> list[dict[str, Any]]:
    """Kontakte der Firma per ID (exakt) oder Name/Mail/Telegram (Teiltext)."""
    try:
        gesucht: UUID | None = UUID(suche) if suche else None
    except ValueError:
        gesucht = None
    if gesucht is not None:
        return [z for z in kern.kontakte(conn, k, firma_id) if z["id"] == gesucht]
    return kern.kontakte(conn, k, firma_id, suche or None)


def _exakt_eindeutig(zeilen: list[dict[str, Any]], suche: str,
                     felder: tuple[str, ...]) -> dict[str, Any] | None:
    """Die eine Zeile, die exakt so heisst (Gross-/Kleinschreibung egal), sonst None.
    Ohne diese Regel waere ein Name, der Teil eines anderen ist, nie eindeutig waehlbar."""
    s = suche.strip().casefold()
    exakt = [z for z in zeilen
             if any(str(z.get(f) or "").strip().casefold() == s for f in felder)]
    return exakt[0] if len(exakt) == 1 else None


def _kontakte_suchen(conn: psycopg.Connection[Any], info: SchluesselInfo,
                     args: dict[str, Any], u: _Umgebung) -> dict[str, Any]:
    g = _binden(conn, info, args)
    zeilen = _kontakte_finden(conn, g.k, g.event["firma_id"], (args.get("suche") or "").strip())
    return {"event": g.kopf, "kontakte": [_kontakt(z) for z in zeilen[:MAX_TREFFER]]}


def _kontakt_anlegen(conn: psycopg.Connection[Any], info: SchluesselInfo,
                     args: dict[str, Any], u: _Umgebung) -> dict[str, Any]:
    g = _binden(conn, info, args)
    neu = kern.kontakt_anlegen(conn, g.k, g.event["firma_id"], args["name"],
                               args.get("mail"), args.get("telegram"))
    return {"event": g.kopf, "zeile": _kontakt(neu)}


def _events_finden(conn: psycopg.Connection[Any], info: SchluesselInfo, suche: str | None,
                   kategorie: str | None, kategorien: Kategorien) -> list[dict[str, Any]]:
    k = kontext_fuer(conn, info)
    return events.katalog(conn, k, suche=suche, kategorie=kategorie, kategorien=kategorien)


def _events_suchen(conn: psycopg.Connection[Any], info: SchluesselInfo, args: dict[str, Any],
                   u: _Umgebung) -> dict[str, Any]:
    zeilen = _events_finden(conn, info, (args.get("suche") or "").strip() or None,
                            args.get("kategorie"), u.kategorien)
    return {"events": [
        {"name": z["name"], "firma": z["firma_name"], "kategorie": z["kategorie"],
         "beginn": z["beginn"], "ende": z["ende"], "phase": z["phase"], "status": z["status"]}
        for z in zeilen[:MAX_TREFFER]
    ]}


def _event_waehlen(conn: psycopg.Connection[Any], info: SchluesselInfo, args: dict[str, Any],
                   u: _Umgebung) -> dict[str, Any]:
    """Reihenfolge (Ruling T5-b): Suche, dann `datum` (Berliner Ortsdatum des Beginns),
    dann die Exakt-Regel nur unter nicht archivierten Events. Bleiben mehrere, geht die
    Trefferliste (mit `beginn` zur Unterscheidung) ohne Marke zurueck."""
    suche = args["suche"].strip()
    if not suche:
        raise ValueError("Suche fehlt")
    zeilen = _events_finden(conn, info, suche, None, u.kategorien)
    if "datum" in args:
        tag = date.fromisoformat(args["datum"])
        zeilen = [z for z in zeilen if z["beginn"].astimezone(events.ZEITZONE).date() == tag]
    if not zeilen:
        raise NichtGefunden("Event nicht gefunden")
    if len(zeilen) > 1:
        aktiv = [z for z in zeilen if z["status"] != "archiviert"]
        exakt = _exakt_eindeutig(aktiv, suche, ("name",))
        if exakt is not None:
            zeilen = [exakt]
    kurz = [{"name": z["name"], "firma": z["firma_name"], "beginn": z["beginn"]}
            for z in zeilen]
    if len(zeilen) > 1:
        return {"treffer": kurz[:MAX_TREFFER], "hinweis": GENAUER_DATUM}
    return {"marke": marke.marke_ausstellen(conn, info, zeilen[0]["id"]), "event": kurz[0]}


# --- Verwaltung (Task 5) -------------------------------------------------------------


def _firma_finden(conn: psycopg.Connection[Any], k: Kontext, kurz: str) -> dict[str, Any]:
    """Firma per Kuerzel, nur wenn der Kontext sie lesen darf; sonst NichtGefunden (auch
    fuer fremde Firmen, damit es kein Orakel ueber vergebene Kuerzel gibt)."""
    with transaktion(conn, k) as cur:
        cur.execute("select id, name, kurz from event.firma where kurz = %s",
                    (kurz.strip().lower(),))
        firma = cur.fetchone()
    if firma is None:
        raise NichtGefunden("Firma nicht gefunden")
    rechte.pruefe(k, "lesen", firma["id"])
    return firma


def _firma_kopf(firma: Mapping[str, Any]) -> dict[str, str]:
    return {"name": firma["name"], "kurz": firma["kurz"]}


def _zweistufig(
    conn: psycopg.Connection[Any], info: SchluesselInfo, args: Mapping[str, Any],
    aktion: str, ziel: dict[str, str], kopf: dict[str, Any], zusammenfassung: str,
    ausfuehren: Callable[[], dict[str, Any]],
) -> dict[str, Any]:
    """Ohne `bestaetigung`: nur Zusammenfassung und Code (aendert fachlich nichts). Mit:
    Code fuer genau (Schluessel, aktion, ziel) einloesen, dann ausfuehren. `ziel` enthaelt
    nur selbst aufgeloeste kanonische Werte, nie Nutzertext."""
    code = args.get("bestaetigung")
    if code is None:
        return {**kopf, "zusammenfassung": zusammenfassung,
                "bestaetigung": marke.bestaetigung_ausstellen(conn, info, aktion, ziel),
                "gueltig_minuten": marke.BESTAETIGUNG_MINUTEN}
    marke.bestaetigung_einloesen(conn, info, code, aktion, ziel)
    return ausfuehren()


def _event_anlegen(conn: psycopg.Connection[Any], info: SchluesselInfo, args: dict[str, Any],
                   u: _Umgebung) -> dict[str, Any]:
    k = kontext_fuer(conn, info)
    firma = _firma_finden(conn, k, args["firma"])
    neu = events.event_anlegen(
        conn, k, firma["id"], args["kategorie"], args["name"],
        datetime.fromisoformat(args["beginn"]), datetime.fromisoformat(args["ende"]),
        args.get("ort_adresse"), args.get("ort_link"), args.get("beschreibung"),
        kategorien=u.kategorien,
    )
    return {"event": {"name": neu["name"], "firma": firma["name"]},
            "marke": marke.marke_ausstellen(conn, info, neu["id"]),
            "kategorie": neu["kategorie"], "beginn": neu["beginn"], "ende": neu["ende"],
            "status": neu["status"]}


def _einladen(conn: psycopg.Connection[Any], info: SchluesselInfo, args: dict[str, Any],
              u: _Umgebung) -> dict[str, Any]:
    if u.mailer is None or u.basis_url is None:
        raise ValueError(EINLADEN_AUS)
    k = kontext_fuer(conn, info)
    firma = _firma_finden(conn, k, args["firma"])
    if args["rolle"] != "leitung":
        zugang.einladen(conn, k, u.mailer, args["mail"], args["name"], firma["id"],
                        args["rolle"], u.basis_url)
        return {"firma": _firma_kopf(firma), "eingeladen": True}
    # Eine Leitung einladen gibt Verwaltungsrechte weiter (Abschluss-Review F4): zweistufig
    # wie rolle_aendern. Ziel kanonisch: Firma-UUID, Mail ohne Rand in Kleinbuchstaben.
    rechte.pruefe(k, "einladen", firma["id"])
    mail = args["mail"].strip().lower()
    mailer, basis_url = u.mailer, u.basis_url

    def tun() -> dict[str, Any]:
        zugang.einladen(conn, k, mailer, mail, args["name"], firma["id"], "leitung", basis_url)
        return {"firma": _firma_kopf(firma), "eingeladen": True}

    return _zweistufig(
        conn, info, args, "einladen",
        {"firma": str(firma["id"]), "mail": mail, "rolle": "leitung"},
        {"firma": _firma_kopf(firma)},
        f"{args['name']} ({mail}) als Leitung in die Firma {firma['name']} einladen", tun)


def _firma_anlegen(conn: psycopg.Connection[Any], info: SchluesselInfo, args: dict[str, Any],
                   u: _Umgebung) -> dict[str, Any]:
    neu = firmen.firma_anlegen(conn, kontext_fuer(conn, info), args["name"], args["kurz"])
    return {"firma": _firma_kopf(neu)}


def _event_status_werkzeug(aktion: str, status: str, verb: str) -> Ausfuehrung:
    def ausfuehren(conn: psycopg.Connection[Any], info: SchluesselInfo, args: dict[str, Any],
                   u: _Umgebung) -> dict[str, Any]:
        g = _binden(conn, info, args)
        event_id: UUID = g.event["id"]
        # Vorab: wer nicht darf, bekommt keinen Code (event_status prueft trotzdem selbst).
        rechte.pruefe(g.k, "event_absagen", g.event["firma_id"])

        def tun() -> dict[str, Any]:
            neu = events.event_status(conn, g.k, event_id, status)
            return {"event": g.kopf, "status": neu["status"]}

        return _zweistufig(conn, info, args, aktion, {"event": str(event_id)},
                           {"event": g.kopf},
                           f"Event {g.kopf['name']} ({g.kopf['firma']}) {verb}", tun)

    return ausfuehren


def _event_loeschen(conn: psycopg.Connection[Any], info: SchluesselInfo, args: dict[str, Any],
                    u: _Umgebung) -> dict[str, Any]:
    g = _binden(conn, info, args)
    event_id: UUID = g.event["id"]
    rechte.pruefe(g.k, "event_loeschen", g.event["firma_id"])

    def tun() -> dict[str, Any]:
        events.event_loeschen(conn, g.k, event_id)
        return {"event": g.kopf, "geloescht": True}

    return _zweistufig(
        conn, info, args, "event_loeschen", {"event": str(event_id)}, {"event": g.kopf},
        f"Event {g.kopf['name']} ({g.kopf['firma']}) endgültig löschen, mit allen Einträgen",
        tun)


def _kontakt_entfernen(conn: psycopg.Connection[Any], info: SchluesselInfo,
                       args: dict[str, Any], u: _Umgebung) -> dict[str, Any]:
    g = _binden(conn, info, args)
    firma_id: UUID = g.event["firma_id"]
    rechte.pruefe(g.k, "mitglied_entfernen", firma_id)
    suche = args["kontakt"].strip()
    if not suche:
        raise ValueError("Kontakt fehlt")
    zeilen = _kontakte_finden(conn, g.k, firma_id, suche)
    if len(zeilen) > 1:
        exakt = _exakt_eindeutig(zeilen, suche, ("name", "mail"))
        if exakt is not None:
            zeilen = [exakt]
    if not zeilen:
        raise NichtGefunden("Kontakt nicht gefunden")
    if len(zeilen) > 1:
        return {"event": g.kopf, "treffer": [_kontakt(z) for z in zeilen[:MAX_TREFFER]],
                "hinweis": GENAUER}
    kontakt = zeilen[0]

    def tun() -> dict[str, Any]:
        kern.kontakt_entfernen(conn, g.k, firma_id, kontakt["id"])
        return {"event": g.kopf, "kontakt": {"name": kontakt["name"], "entfernt": True}}

    return _zweistufig(
        conn, info, args, "kontakt_entfernen",
        {"event": str(g.event["id"]), "kontakt": str(kontakt["id"])}, {"event": g.kopf},
        f"Kontakt {kontakt['name']} aus der Firma entfernen "
        f"(gewählt über Event {g.kopf['name']} ({g.kopf['firma']}))", tun)


def _rolle_aendern(conn: psycopg.Connection[Any], info: SchluesselInfo, args: dict[str, Any],
                   u: _Umgebung) -> dict[str, Any]:
    k = kontext_fuer(conn, info)
    firma = _firma_finden(conn, k, args["firma"])
    rechte.pruefe(k, "rolle_aendern", firma["id"])
    # Kanonischer Wert aus der eigenen Liste (das Schema prueft schon per enum).
    rolle = next((r for r in firmen.ROLLEN if r == args["rolle"]), None)
    if rolle is None:
        raise ValueError("Rolle ungültig")
    mail = args["mail"].strip().lower()
    person = next((m for m in firmen.mitglieder(conn, k, firma["id"])
                   if str(m["mail"]).lower() == mail), None)
    if person is None:
        raise NichtGefunden("Mitglied nicht gefunden")
    person_id: UUID = person["person_id"]

    def tun() -> dict[str, Any]:
        firmen.rolle_aendern(conn, k, person_id, firma["id"], rolle)
        return {"firma": _firma_kopf(firma),
                "mitglied": {"name": person["name"], "mail": person["mail"], "rolle": rolle}}

    return _zweistufig(
        conn, info, args, "rolle_aendern",
        {"firma": str(firma["id"]), "person": str(person_id), "rolle": rolle},
        {"firma": _firma_kopf(firma)},
        f"Rolle von {person['name']} ({person['mail']}) in der Firma {firma['name']} "
        f"von {person['rolle']} auf {rolle} ändern", tun)


# --- Nachrichten (Task 6) -------------------------------------------------------------


_ENTWURF_HINWEIS = ("Entwurf gespeichert – versendet wird erst nach der Freigabe durch eine "
                    "Leitung in der Oberfläche")


def _nachricht(zeile: Mapping[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in zeile.items() if k != "event_id"}


def _nachricht_an_gruppe(conn: psycopg.Connection[Any], info: SchluesselInfo,
                         args: dict[str, Any], u: _Umgebung) -> dict[str, Any]:
    g = _binden(conn, info, args)
    neu = nachrichten.an_gruppe(conn, g.k, g.event["id"], args["text"], telegram=u.telegram)
    return {"event": g.kopf, "nachricht": _nachricht(neu)}


def _mail_entwerfen(conn: psycopg.Connection[Any], info: SchluesselInfo, args: dict[str, Any],
                    u: _Umgebung) -> dict[str, Any]:
    g = _binden(conn, info, args)
    neu = nachrichten.mail_entwerfen(conn, g.k, g.event["id"], args["betreff"], args["text"],
                                     args["zusage"])
    return {"event": g.kopf, "nachricht": _nachricht(neu), "empfaenger": neu["empfaenger"],
            "hinweis": _ENTWURF_HINWEIS}


def _nachrichten_liste(conn: psycopg.Connection[Any], info: SchluesselInfo,
                       args: dict[str, Any], u: _Umgebung) -> dict[str, Any]:
    g = _binden(conn, info, args)
    zeilen = nachrichten.liste(conn, g.k, g.event["id"])
    return {"event": g.kopf, "nachrichten": [_nachricht(z) for z in zeilen]}


# --- Verbinden (Task 7) ----------------------------------------------------------------


def _gruppe_verbinden(conn: psycopg.Connection[Any], info: SchluesselInfo,
                      args: dict[str, Any], u: _Umgebung) -> dict[str, Any]:
    g = _binden(conn, info, args)
    code = verbinden.code_erzeugen(conn, g.k, art="gruppe", event_id=g.event["id"])
    return {"event": g.kopf, "code": code, "gueltig_minuten": verbinden.GUELTIG_MINUTEN,
            "anleitung": verbinden.ANLEITUNG_GRUPPE.format(code=code)}


def _melden_werkzeug(art: str) -> Ausfuehrung:
    def ausfuehren(conn: psycopg.Connection[Any], info: SchluesselInfo, args: dict[str, Any],
                   u: _Umgebung) -> dict[str, Any]:
        return {"antwort": verbinden.melden(conn, code=args["code"], chat_id=args["chat_id"],
                                            art=art, telegram=u.telegram)}
    return ausfuehren


_MELDEN_SCHEMA: dict[str, dict[str, Any]] = {
    "code": {"type": "string", "maxLength": 32,
             "description": "Der Code aus der Nachricht (8 Zeichen)"},
    "chat_id": {"type": "integer", "minimum": -verbinden.CHAT_MAX,
                "maximum": verbinden.CHAT_MAX,
                "description": "Telegram-Chat-ID des Chats, aus dem die Nachricht kam"},
}


@dataclass(frozen=True)
class _Sonder:
    beschreibung: str
    eigenschaften: Mapping[str, dict[str, Any]]
    pflicht: tuple[str, ...]
    schreibend: bool
    braucht_event: bool  # True: Pflichtargument `marke` fuer Schluessel ohne festes Event
    ausfuehren: Ausfuehrung


_FIRMA_SCHEMA: dict[str, Any] = {"type": "string", "maxLength": 32,
                                 "description": "Kürzel der Firma"}
_ROLLE_SCHEMA: dict[str, Any] = {"type": "string", "enum": list(firmen.ROLLEN)}
_ZEIT_SCHEMA: dict[str, Any] = {"type": "string", "format": "date-time",
                                "description": "ISO-8601; ohne Zeitzone gilt Europe/Berlin"}
_BESTAETIGUNG_SCHEMA: dict[str, Any] = {
    "type": "string", "maxLength": 64,
    "description": "Code aus dem ersten Aufruf (gilt 5 Minuten); ohne ihn wird nur gefragt",
}

_SONDER: dict[str, _Sonder] = {
    "event_info": _Sonder(
        "Eckdaten des Events und die Namen seiner Bereiche anzeigen", {}, (), False, True,
        _event_info),
    "verlauf_lesen": _Sonder(
        "Änderungsverlauf des Events anzeigen (neueste zuerst)",
        {"limit": {"type": "integer", "minimum": 1, "maximum": 500},
         "seit": {"type": "string", "format": "date-time",
                  "description": "ISO-8601; ohne Zeitzone gilt Europe/Berlin"}},
        (), False, True, _verlauf_lesen),
    "kontakte_suchen": _Sonder(
        "Kontakte der Firma des Events suchen (Name, Mail oder ID; höchstens 20)",
        {"suche": {"type": "string", "maxLength": 200}}, (), False, True, _kontakte_suchen),
    "kontakt_anlegen": _Sonder(
        "Neuen Kontakt in der Firma des Events anlegen",
        {"name": {"type": "string", "maxLength": 200},
         "mail": {"type": "string", "maxLength": 320},
         "telegram": {"type": "string", "maxLength": 100}},
        ("name",), True, True, _kontakt_anlegen),
    "events_suchen": _Sonder(
        "Sichtbare Events suchen (höchstens 20)",
        {"suche": {"type": "string", "maxLength": 200},
         "kategorie": {"type": "string", "maxLength": 64}},
        (), False, False, _events_suchen),
    "event_waehlen": _Sonder(
        "Ein Event per Suche wählen; liefert die Marke für alle Event-Werkzeuge. Bei "
        "gleichen Namen mit datum (Tag des Beginns) eingrenzen",
        {"suche": {"type": "string", "maxLength": 200},
         "datum": {"type": "string", "pattern": r"^\d{4}-\d{2}-\d{2}$", "format": "date",
                   "description": "Tag des Beginns (JJJJ-MM-TT, Ortszeit Berlin)"}},
        ("suche",), False, False, _event_waehlen),
    "event_anlegen": _Sonder(
        "Neues Event in einer Firma anlegen; liefert die Marke des neuen Events",
        {"firma": _FIRMA_SCHEMA,
         "kategorie": {"type": "string", "maxLength": 64,
                       "description": "Kategorie-ID, z. B. team_meeting, festival, webinar"},
         "name": {"type": "string", "maxLength": 200},
         "beginn": _ZEIT_SCHEMA, "ende": _ZEIT_SCHEMA,
         "ort_adresse": {"type": "string", "maxLength": 500},
         "ort_link": {"type": "string", "pattern": "^https?://", "maxLength": TEXT_MAX},
         "beschreibung": {"type": "string", "maxLength": TEXT_MAX}},
        ("firma", "kategorie", "name", "beginn", "ende"), True, False, _event_anlegen),
    "einladen": _Sonder(
        "Person per Mail in eine Firma einladen (Einladungslink per Mail; als Leitung "
        "zweistufig: erst Zusammenfassung und Code, dann mit Code ausführen)",
        {"firma": _FIRMA_SCHEMA, "mail": {"type": "string", "maxLength": 320},
         "name": {"type": "string", "maxLength": 200}, "rolle": _ROLLE_SCHEMA,
         "bestaetigung": _BESTAETIGUNG_SCHEMA},
        ("firma", "mail", "name", "rolle"), True, False, _einladen),
    "firma_anlegen": _Sonder(
        "Neue Firma anlegen (nur Betreiber)",
        {"name": {"type": "string", "maxLength": 200},
         "kurz": {"type": "string", "maxLength": 32,
                  "description": "Kürzel: 2 bis 32 Zeichen aus a-z, 0-9 und Bindestrich"}},
        ("name", "kurz"), True, False, _firma_anlegen),
    "event_absagen": _Sonder(
        "Event absagen (zweistufig: erst Zusammenfassung und Code, dann mit Code ausführen)",
        {"bestaetigung": _BESTAETIGUNG_SCHEMA}, (), True, True,
        _event_status_werkzeug("event_absagen", "abgesagt", "absagen")),
    "event_archivieren": _Sonder(
        "Event archivieren (zweistufig: erst Zusammenfassung und Code, dann mit Code "
        "ausführen)",
        {"bestaetigung": _BESTAETIGUNG_SCHEMA}, (), True, True,
        _event_status_werkzeug("event_archivieren", "archiviert", "archivieren")),
    "event_loeschen": _Sonder(
        "Event endgültig löschen (zweistufig: erst Zusammenfassung und Code, dann mit Code "
        "ausführen)",
        {"bestaetigung": _BESTAETIGUNG_SCHEMA}, (), True, True, _event_loeschen),
    "kontakt_entfernen": _Sonder(
        "Kontakt aus der Firma des Events entfernen (Name, Mail oder ID; zweistufig)",
        {"kontakt": {"type": "string", "maxLength": 200},
         "bestaetigung": _BESTAETIGUNG_SCHEMA}, ("kontakt",), True, True, _kontakt_entfernen),
    "rolle_aendern": _Sonder(
        "Rolle eines Mitglieds der Firma ändern (per Mail; zweistufig)",
        {"firma": _FIRMA_SCHEMA, "mail": {"type": "string", "maxLength": 320},
         "rolle": _ROLLE_SCHEMA, "bestaetigung": _BESTAETIGUNG_SCHEMA},
        ("firma", "mail", "rolle"), True, False, _rolle_aendern),
    "nachricht_an_gruppe": _Sonder(
        "Nachricht sofort an die verbundene Telegram-Gruppe des Events senden",
        {"text": {"type": "string", "maxLength": nachrichten.TEXT_MAX}}, ("text",), True, True,
        _nachricht_an_gruppe),
    "mail_entwerfen": _Sonder(
        "Mail an Teilnehmende (nach Zusage) als Entwurf speichern; versendet wird erst nach "
        "der Freigabe durch eine Leitung in der Oberfläche",
        {"betreff": {"type": "string", "maxLength": nachrichten.BETREFF_MAX},
         "text": {"type": "string", "maxLength": nachrichten.TEXT_MAX},
         "zusage": {"type": "array", "minItems": 1, "maxItems": len(nachrichten.ZUSAGEN),
                    "items": {"type": "string", "enum": list(nachrichten.ZUSAGEN)}}},
        ("betreff", "text", "zusage"), True, True, _mail_entwerfen),
    "nachrichten_liste": _Sonder(
        "Nachrichten des Events anzeigen (Gruppe und Mail, mit Status und Empfängerzahl)",
        {}, (), False, True, _nachrichten_liste),
    "gruppe_verbinden": _Sonder(
        "Einmal-Code erzeugen, um eine Telegram-Gruppe mit dem Event zu verbinden (gilt 15 "
        "Minuten; die Leitung bestätigt danach in der Oberfläche)", {}, (), True, True,
        _gruppe_verbinden),
    "gruppe_melden": _Sonder(
        "Code melden, der in einer Telegram-Gruppe an den Bot geschickt wurde",
        _MELDEN_SCHEMA, ("code", "chat_id"), True, False, _melden_werkzeug("gruppe")),
    "chat_melden": _Sonder(
        "Code melden, der im Direktchat an den Bot geschickt wurde",
        _MELDEN_SCHEMA, ("code", "chat_id"), True, False, _melden_werkzeug("direkt")),
}

_EVENT_LESEN = ("event_info", "verlauf_lesen", "kontakte_suchen", "nachrichten_liste")
_NACHRICHTEN = ("nachricht_an_gruppe", "mail_entwerfen")
_WAHL = ("events_suchen", "event_waehlen")
_VERWALTUNG = ("event_anlegen", "einladen", "event_absagen", "event_archivieren",
               "event_loeschen", "kontakt_entfernen", "rolle_aendern", "gruppe_verbinden")
_SONDER_JE_ART: dict[str, tuple[str, ...]] = {
    "event": (*_EVENT_LESEN, "kontakt_anlegen", *_NACHRICHTEN),
    "leitung": (*_WAHL, *_EVENT_LESEN, "kontakt_anlegen", *_NACHRICHTEN, *_VERWALTUNG),
    "betreiber": (*_WAHL, *_EVENT_LESEN, "kontakt_anlegen", *_NACHRICHTEN, *_VERWALTUNG,
                  "firma_anlegen"),
    "betreiber_lesen": (*_WAHL, *_EVENT_LESEN),
    "melden": ("gruppe_melden", "chat_melden"),
}
# Nur mit Person im Schluessel sichtbar (der Code wird als diese Person angelegt).
_BRAUCHT_PERSON = frozenset({"gruppe_verbinden"})
_OPS_JE_ART: dict[str, tuple[str, ...]] = {
    "event": _OPS, "leitung": _OPS, "betreiber": _OPS, "betreiber_lesen": _NUR_LESEN,
    "melden": (),
}


# --- Katalog und Ausfuehrung --------------------------------------------------------


def _eintraege(conn: psycopg.Connection[Any], info: SchluesselInfo,
               kategorien: Kategorien) -> dict[str, _Eintrag]:
    art = info.art
    if art not in _SONDER_JE_ART:
        return {}
    mit_marke = art != "event"
    if art == "event":
        if info.event_id is None:
            raise NichtGefunden("Event nicht gefunden")
        ev = _event_laden(conn, kontext_fuer(conn, info), info.event_id)
        schema = _schema(ev, kategorien)
        bausteine = schema.bausteine if schema is not None else ()
        begriffe: Mapping[str, str] | None = schema.begriffe if schema is not None else None
        registry = [n for n, t in REGISTRY.items() if (t.baustein or n) in bausteine]
    else:
        begriffe = None
        registry = list(REGISTRY)

    ergebnis: dict[str, _Eintrag] = {}
    for name in _SONDER_JE_ART[art]:
        if name in _BRAUCHT_PERSON and info.person_id is None:
            continue
        s = _SONDER[name]
        w = Werkzeug(name, s.beschreibung,
                     _objekt(s.eigenschaften, list(s.pflicht),
                             mit_marke=mit_marke and s.braucht_event),
                     s.schreibend)
        ergebnis[name] = _Eintrag(w, s.ausfuehren)
    for eintrag in registry:
        for op in _OPS_JE_ART[art]:
            w = _baustein_werkzeug(eintrag, op, begriffe, mit_marke=mit_marke)
            ergebnis[w.name] = _Eintrag(w, _baustein_ausfuehrung(eintrag, op))
    return ergebnis


def katalog(conn: psycopg.Connection[Any], info: SchluesselInfo,
            *, kategorien: Kategorien = None) -> list[Werkzeug]:
    """Werkzeuge, die dieser Schluessel jetzt sehen darf."""
    return [e.werkzeug for e in _eintraege(conn, info, kategorien).values()]


def ausfuehren(conn: psycopg.Connection[Any], info: SchluesselInfo, name: str,
               argumente: Mapping[str, object], *, kategorien: Kategorien = None,
               mailer: Mailer | None = None, basis_url: str | None = None,
               telegram: Telegram | None = None,
               ) -> dict[str, Any]:
    """Fuehrt ein Werkzeug aus dem Katalog des Schluessels aus.

    NichtGefunden("unbekanntes Werkzeug") ausserhalb des Katalogs; ValueError bei
    Argumenten ausserhalb des Schemas, Legacy-Events, archivierten Events (schreibende
    Baustein-Werkzeuge), fehlendem Bereich, `einladen` ohne `mailer`/`basis_url` und
    `nachricht_an_gruppe`/`gruppe_melden`/`chat_melden` ohne `telegram`; sonst die Fehler
    der Fachschicht (NichtGefunden, KeinRecht, ValueError).
    """
    eintrag = _eintraege(conn, info, kategorien).get(name) if isinstance(name, str) else None
    if eintrag is None:
        raise NichtGefunden(UNBEKANNT)
    args = argumente_pruefen(eintrag.werkzeug.eingabe, argumente)
    return eintrag.ausfuehren(conn, info, args,
                              _Umgebung(kategorien, mailer, basis_url, telegram))
