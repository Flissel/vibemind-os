"""Werkzeuge des Auftragsbuchs: zwei feste und je eines je Auftragsart.

Reine Fachschicht ohne MCP: `katalog` beschreibt die Werkzeuge, `ausfuehren` fuehrt eines aus.
Fehler tragen nie Werte aus der Anfrage (nur Regel und Feldpfad).
"""
from __future__ import annotations

import logging
import re
import uuid
from collections.abc import Callable

from spaces.auftragsbuch import belege
from spaces.auftragsbuch.db import Buch
from spaces.auftragsbuch.gleichstand import FESTE_WERKZEUGE
from spaces.auftragsbuch.vertrag import FormFehler, pruefe_eingabe, pruefe_ergebnis, vertrag_hash, werkzeugname

log = logging.getLogger("auftragsbuch")

_PLAN_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")
_PLAN_ID_SCHEMA = {"type": "string", "pattern": "^[A-Za-z0-9_-]{1,64}$"}
PROBE_FRIST_MIN = 5

_NICHT_GEFUNDEN = "nicht gefunden"


class NichtGefunden(LookupError):
    """Unbekanntes Werkzeug, unbekannte Kennung oder fremder Auftrag (immer derselbe Text)."""

    def __init__(self) -> None:
        super().__init__(_NICHT_GEFUNDEN)


def katalog(arten: dict) -> list[dict]:
    """MCP-Werkzeuge: erst die festen, dann die Auftragsarten nach Name sortiert."""
    festes = [
        {
            "name": "auftrag_probe",
            "description": (
                "Legt eine Probe an das Gehirn an, schliesst sie sofort ab und nennt den Vertragsstand. "
                "Zeigt, dass der Weg zum Auftragsbuch funktioniert."
            ),
            "inputSchema": {
                "type": "object", "additionalProperties": False,
                "properties": {"plan_id": _PLAN_ID_SCHEMA},
            },
        },
        {
            "name": "auftrag_lesen",
            "description": "Liest den Stand eines eigenen Auftrags anhand seiner Kennung.",
            "inputSchema": {
                "type": "object", "additionalProperties": False, "required": ["id"],
                "properties": {"id": {"type": "string"}},
            },
        },
    ]
    if tuple(w["name"] for w in festes) != FESTE_WERKZEUGE:
        raise RuntimeError("feste Werkzeuge weichen von gleichstand.FESTE_WERKZEUGE ab")
    je_art = [
        {
            "name": werkzeugname(a),
            "description": a.beschreibung,
            "inputSchema": {
                "type": "object", "additionalProperties": False, "required": ["eingabe"],
                "properties": {"eingabe": a.eingabe, "plan_id": _PLAN_ID_SCHEMA},
            },
        }
        for a in arten.values()
    ]
    je_art.sort(key=lambda w: w["name"])
    return festes + je_art


def _argumente_pruefen(argumente: object, erlaubt: set[str], pflicht: set[str]) -> dict:
    """Form der Argumente; Meldungen nennen nur Regel und Feldpfad."""
    if not isinstance(argumente, dict):
        raise FormFehler("argumente verletzt das Schema: Regel 'type' bei '(Wurzel)'")
    if any(k not in erlaubt for k in argumente):
        raise FormFehler("argumente verletzt das Schema: Regel 'additionalProperties' bei '(Wurzel)'")
    for feld in sorted(pflicht):
        if feld not in argumente:
            raise FormFehler(f"argumente verletzt das Schema: Regel 'required' bei '{feld}'")
    return argumente


def _plan_id(argumente: dict) -> str | None:
    wert = argumente.get("plan_id")
    if wert is None:
        return None
    if not isinstance(wert, str) or _PLAN_ID.fullmatch(wert) is None:
        raise FormFehler("argumente verletzt das Schema: Regel 'pattern' bei 'plan_id'")
    return wert


def _probe(argumente: object, buch: Buch, arten: dict) -> dict:
    daten = _argumente_pruefen(argumente, {"plan_id"}, set())
    plan_id = _plan_id(daten)
    auftrag_id = buch.anlegen("brain", "probe", 1, {}, plan_id, PROBE_FRIST_MIN)
    if not buch.probe_erledigen(auftrag_id):
        raise RuntimeError("Probe nicht erledigt")
    return {"auftrag_id": auftrag_id, "stand": "fertig", "vertrag_hash": vertrag_hash(arten)}


def _lesen(argumente: object, buch: Buch, arten: dict,
           leser: Callable[[str, object], object] | None) -> dict:
    daten = _argumente_pruefen(argumente, {"id"}, {"id"})
    roh = daten["id"]
    if not isinstance(roh, str):
        raise FormFehler("argumente verletzt das Schema: Regel 'type' bei 'id'")
    try:
        kennung = str(uuid.UUID(roh))
    except ValueError:
        raise NichtGefunden() from None
    zeile = buch.lesen(kennung)
    if zeile is None:
        raise NichtGefunden()
    antwort = {
        "auftrag_id": zeile.get("id"), "an": zeile.get("an"), "art": zeile.get("art"),
        "version": zeile.get("art_version"), "stand": zeile.get("stand"),
        "ergebnis": zeile.get("ergebnis"), "fehler": zeile.get("fehler"),
        "plan_id": zeile.get("plan_id"), "frist_bis": zeile.get("frist_bis"),
    }
    art = arten.get((zeile.get("an"), zeile.get("art")))
    if art is None:
        antwort["beleg"] = belege.ENTFAELLT
        return antwort
    if antwort["stand"] != "fertig":
        antwort["beleg"] = belege.ENTFAELLT if art.nachpruefung == "form" else belege.NICHT_PRUEFBAR
        return antwort
    if zeile.get("art_version") != art.version:  # aelteres Ergebnis: wie gespeichert, ohne Form und Beleg
        antwort["beleg"] = belege.NICHT_PRUEFBAR
        return antwort
    try:
        pruefe_ergebnis(art, antwort["ergebnis"])
    except FormFehler:
        log.info("auftrag=%s art=%s stand=falsche_form", kennung[:8], art.art)
        antwort.update(stand="fehler", fehler="Ergebnis in falscher Form", ergebnis=None,
                       beleg=belege.NICHT_PRUEFBAR)
        return antwort
    antwort["beleg"] = belege.beleg(art, zeile.get("eingabe") or {}, antwort["ergebnis"], leser, kennung)
    log.info("auftrag=%s art=%s beleg=%s", kennung[:8], art.art, antwort["beleg"])
    return antwort


def ausfuehren(name: str, argumente: dict, buch: Buch, arten: dict,
               leser: Callable[[str, object], object] | None = None) -> dict:
    """Fuehrt ein Werkzeug aus. NichtGefunden, FormFehler und db.Abgelehnt gehen an den Aufrufer."""
    if name == "auftrag_probe":
        return _probe(argumente, buch, arten)
    if name == "auftrag_lesen":
        return _lesen(argumente, buch, arten, leser)
    art = next((a for a in arten.values() if werkzeugname(a) == name), None)
    if art is None:
        raise NichtGefunden()
    daten = _argumente_pruefen(argumente, {"eingabe", "plan_id"}, {"eingabe"})
    plan_id = _plan_id(daten)
    pruefe_eingabe(art, daten["eingabe"])
    auftrag_id = buch.anlegen(art.space, art.art, art.version, daten["eingabe"], plan_id, art.frist_min)
    return {"auftrag_id": auftrag_id, "an": art.space, "art": art.art, "version": art.version,
            "stand": "offen"}
