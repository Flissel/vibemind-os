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

Kontakte (Ruling S1): nur `kontakte_suchen` und `kontakt_anlegen` (Firma des Events);
Aendern/Entfernen gibt es fuer den Bot nicht, weil die Kontakt-Fachschicht die
Event-Bindung nicht prueft.

Erweitern (Task 5 ff.): neue Werkzeuge als `_Sonder`-Eintrag in `_SONDER` und ihr Name in
`_SONDER_JE_ART`.
"""
from __future__ import annotations

import logging
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

import psycopg

from spaces.event_management.system import events, kern, marke, pruefung, rechte, verlauf
from spaces.event_management.system.bausteine import REGISTRY
from spaces.event_management.system.kategorien import Kategorie, WirksamesSchema
from spaces.event_management.system.kontext import Kontext, NichtGefunden, transaktion
from spaces.event_management.system.schluessel import SchluesselInfo, kontext_fuer

log = logging.getLogger(__name__)

UNBEKANNT = "unbekanntes Werkzeug"
LEGACY = "Legacy-Event: bitte die alte App nutzen"
TEXT_MAX = 2000
MAX_TREFFER = 20
VERLAUF_VORGABE = 50

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
Ausfuehrung = Callable[
    [psycopg.Connection[Any], SchluesselInfo, dict[str, Any], Kategorien], dict[str, Any]
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
    if "pattern" in s and not re.match(s["pattern"], wert):
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
                   kategorien: Kategorien) -> dict[str, Any]:
        g = _binden(conn, info, args)
        schema = _schema(g.event, kategorien)
        if schema is None or baustein not in schema.bausteine:
            begriffe = schema.begriffe if schema is not None else None
            raise ValueError(f"Dieses Event hat keinen Bereich {begriff(baustein, begriffe)}")
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
                kategorien: Kategorien) -> dict[str, Any]:
    g = _binden(conn, info, args)
    ev = g.event
    schema = _schema(ev, kategorien)
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
                   kategorien: Kategorien) -> dict[str, Any]:
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


def _kontakte_suchen(conn: psycopg.Connection[Any], info: SchluesselInfo,
                     args: dict[str, Any], kategorien: Kategorien) -> dict[str, Any]:
    g = _binden(conn, info, args)
    firma_id = g.event["firma_id"]
    suche = (args.get("suche") or "").strip()
    try:
        gesucht: UUID | None = UUID(suche) if suche else None
    except ValueError:
        gesucht = None
    if gesucht is not None:
        zeilen = [z for z in kern.kontakte(conn, g.k, firma_id) if z["id"] == gesucht]
    else:
        zeilen = kern.kontakte(conn, g.k, firma_id, suche or None)
    return {"event": g.kopf, "kontakte": [_kontakt(z) for z in zeilen[:MAX_TREFFER]]}


def _kontakt_anlegen(conn: psycopg.Connection[Any], info: SchluesselInfo,
                     args: dict[str, Any], kategorien: Kategorien) -> dict[str, Any]:
    g = _binden(conn, info, args)
    neu = kern.kontakt_anlegen(conn, g.k, g.event["firma_id"], args["name"],
                               args.get("mail"), args.get("telegram"))
    return {"event": g.kopf, "zeile": _kontakt(neu)}


def _events_finden(conn: psycopg.Connection[Any], info: SchluesselInfo, suche: str | None,
                   kategorie: str | None, kategorien: Kategorien) -> list[dict[str, Any]]:
    k = kontext_fuer(conn, info)
    return events.katalog(conn, k, suche=suche, kategorie=kategorie, kategorien=kategorien)


def _events_suchen(conn: psycopg.Connection[Any], info: SchluesselInfo, args: dict[str, Any],
                   kategorien: Kategorien) -> dict[str, Any]:
    zeilen = _events_finden(conn, info, (args.get("suche") or "").strip() or None,
                            args.get("kategorie"), kategorien)
    return {"events": [
        {"name": z["name"], "firma": z["firma_name"], "kategorie": z["kategorie"],
         "beginn": z["beginn"], "ende": z["ende"], "phase": z["phase"], "status": z["status"]}
        for z in zeilen[:MAX_TREFFER]
    ]}


def _event_waehlen(conn: psycopg.Connection[Any], info: SchluesselInfo, args: dict[str, Any],
                   kategorien: Kategorien) -> dict[str, Any]:
    suche = args["suche"].strip()
    if not suche:
        raise ValueError("Suche fehlt")
    zeilen = _events_finden(conn, info, suche, None, kategorien)
    if not zeilen:
        raise NichtGefunden("Event nicht gefunden")
    kurz = [{"name": z["name"], "firma": z["firma_name"], "beginn": z["beginn"]}
            for z in zeilen]
    if len(zeilen) > 1:
        return {"treffer": kurz[:MAX_TREFFER], "hinweis": "Bitte genauer"}
    return {"marke": marke.marke_ausstellen(conn, info, zeilen[0]["id"]), "event": kurz[0]}


@dataclass(frozen=True)
class _Sonder:
    beschreibung: str
    eigenschaften: Mapping[str, dict[str, Any]]
    pflicht: tuple[str, ...]
    schreibend: bool
    braucht_event: bool  # True: Pflichtargument `marke` fuer Schluessel ohne festes Event
    ausfuehren: Ausfuehrung


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
        "Ein Event per Suche wählen; liefert die Marke für alle Event-Werkzeuge",
        {"suche": {"type": "string", "maxLength": 200}}, ("suche",), False, False,
        _event_waehlen),
}

_EVENT_LESEN = ("event_info", "verlauf_lesen", "kontakte_suchen")
_WAHL = ("events_suchen", "event_waehlen")
_SONDER_JE_ART: dict[str, tuple[str, ...]] = {
    "event": (*_EVENT_LESEN, "kontakt_anlegen"),
    "leitung": (*_WAHL, *_EVENT_LESEN, "kontakt_anlegen"),
    "betreiber": (*_WAHL, *_EVENT_LESEN, "kontakt_anlegen"),
    "betreiber_lesen": (*_WAHL, *_EVENT_LESEN),
    "melden": (),
}
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
               ) -> dict[str, Any]:
    """Fuehrt ein Werkzeug aus dem Katalog des Schluessels aus.

    NichtGefunden("unbekanntes Werkzeug") ausserhalb des Katalogs; ValueError bei
    Argumenten ausserhalb des Schemas, Legacy-Events und fehlendem Bereich; sonst die
    Fehler der Fachschicht (NichtGefunden, KeinRecht, ValueError).
    """
    eintrag = _eintraege(conn, info, kategorien).get(name) if isinstance(name, str) else None
    if eintrag is None:
        raise NichtGefunden(UNBEKANNT)
    args = argumente_pruefen(eintrag.werkzeug.eingabe, argumente)
    return eintrag.ausfuehren(conn, info, args, kategorien)
