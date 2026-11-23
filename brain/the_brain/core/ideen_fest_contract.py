"""Vertrag der festen Aufrufe des Ideen-Space (ohne Modell).

Je Ereignis steht hier, welches Werkzeug des Ideen-Servers laeuft, welche Argumente
es annimmt und woran der echte Zustand danach nachgeprueft wird. Plan-Ausfuehrer,
Ausfuehrer und Pruefer benutzen dieselbe Tabelle; ``idea.connect`` und
``idea.aenderung_zurueck`` haben ihre eigenen, aelteren Vertraege und stehen nicht hier.

Die Werkzeuge nehmen nur Kennungen. Namen aus einem Satz loest ``ideen_abkuerzung``
vorher auf.
"""
from __future__ import annotations

import json
import re
from typing import Any, Callable, Optional

_KENNUNG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:_-]{0,127}$")
FORMATE = (
    "note", "table", "action_list", "pros_cons", "hierarchy", "technical_specs",
    "kanban", "mindmap", "swot", "user_story", "flowchart",
)
VALIDATOR_ART = "truth:supabase_ideen_fest"
PRUEFUNG = "supabase_ideen_fest"

# Feldarten: ("kennung",) ("text", hoechstens, leer_erlaubt) ("zahl", von, bis)
# ("wahl", werte) ("kennungen", hoechstens)
_K = ("kennung",)
_WERT = ("zahl", 0, 100)
_LAGE = ("zahl", -1_000_000, 1_000_000)

FESTE_EREIGNISSE: dict[str, dict[str, Any]] = {
    "idea.create": {
        "werkzeug": "idea_create",
        "pflicht": {"bubble_id": _K, "title": ("text", 500, False)},
        "wahl": {"content": ("text", 20000, True), "id": _K, "x": _LAGE, "y": _LAGE},
    },
    "idea.update": {
        "werkzeug": "idea_update",
        "pflicht": {"id": _K},
        "wahl": {"title": ("text", 500, False), "content": ("text", 20000, True), "x": _LAGE, "y": _LAGE},
        "mindestens_eins": ("title", "content", "x", "y"),
    },
    "idea.list": {
        "werkzeug": "idea_list",
        "pflicht": {},
        "wahl": {"bubble_id": _K, "limit": ("zahl", 1, 100)},
        "liest_nur": True,
    },
    "idea.format": {
        "werkzeug": "idea_format",
        "pflicht": {"id": _K, "format": ("wahl", (*FORMATE, "revert"))},
        "wahl": {},
    },
    "idea.arrange": {
        "werkzeug": "idea_arrange",
        "pflicht": {"bubble_id": _K},
        "wahl": {"order": ("wahl", ("position", "title", "score")), "columns": ("zahl", 1, 20)},
    },
    "idea.auto_link": {
        "werkzeug": "idea_auto_link",
        "pflicht": {"bubble_id": _K},
        "wahl": {"limit": ("zahl", 1, 50)},
    },
    "idea.rate": {
        "werkzeug": "idea_rate",
        "pflicht": {"id": _K, "score": _WERT},
        "wahl": {},
    },
    "idea.rate_reason": {
        "werkzeug": "bewertung_begruenden",
        "pflicht": {"bubble_id": _K, "text": ("text", 2000, False)},
        "wahl": {"idea_ids": ("kennungen", 20), "score": _WERT},
    },
    "bubble.rate": {
        "werkzeug": "bubble_rate",
        "pflicht": {"id": _K, "score": _WERT},
        "wahl": {},
    },
    "bubble.promote": {
        "werkzeug": "bubble_promote",
        "pflicht": {"bubble_id": _K},
        "wahl": {},
    },
}

# Schreibende Werkzeuge dieser Tabelle; der Ausfuehrer haengt ihnen die Plan-Kennung an.
SCHREIBENDE_WERKZEUGE = frozenset(
    spec["werkzeug"] for spec in FESTE_EREIGNISSE.values() if not spec.get("liest_nur")
)
ALLE_WERKZEUGE = frozenset(spec["werkzeug"] for spec in FESTE_EREIGNISSE.values())
# Ein zweiter Versuch waere eine zweite Aenderung (neuer Eintrag, neues Projekt).
EINMAL_EREIGNISSE = frozenset({"idea.rate_reason", "bubble.promote"})


def ist_festes_ereignis(event_id: Any) -> bool:
    return isinstance(event_id, str) and event_id in FESTE_EREIGNISSE


def ist_fester_aufruf(event_id: Any, target: Any) -> bool:
    """Gilt dieser Vertrag? Nur wenn das Verzeichnis das Ereignis genau auf das
    Werkzeug dieser Tabelle am Ideen-Server bindet (``mcp:<agent>:spaces-ideas:<werkzeug>``).

    Das Verzeichnis bleibt die Instanz: bindet es das Ereignis anders oder gar nicht
    als festen Aufruf, bleibt der bisherige Weg unveraendert.
    """
    if not ist_festes_ereignis(event_id) or not isinstance(target, str) or not target.startswith("mcp:"):
        return False
    teile = [teil.strip() for teil in target.split(":", 1)[1].split(":", 2)]
    if len(teile) != 3:
        return False
    normal = lambda wert: wert.lower().replace("-", "_")
    return (normal(teile[1]) == "spaces_ideas"
            and normal(teile[2]) == FESTE_EREIGNISSE[event_id]["werkzeug"])


def ist_kennung(wert: Any) -> bool:
    return isinstance(wert, str) and _KENNUNG.fullmatch(wert) is not None


def _ist_zahl(wert: Any) -> bool:
    return isinstance(wert, int) and not isinstance(wert, bool)


def _feld_gueltig(art: tuple, wert: Any) -> bool:
    if art[0] == "kennung":
        return ist_kennung(wert)
    if art[0] == "text":
        return (isinstance(wert, str) and len(wert) <= art[1]
                and (art[2] or bool(wert.strip())))
    if art[0] == "zahl":
        return _ist_zahl(wert) and art[1] <= wert <= art[2]
    if art[0] == "wahl":
        return isinstance(wert, str) and wert in art[1]
    if art[0] == "kennungen":
        return (isinstance(wert, list) and len(wert) <= art[1]
                and all(ist_kennung(item) for item in wert)
                and len(set(wert)) == len(wert))
    return False


def feste_argumente(event_id: str, wert: Any) -> tuple[Optional[dict[str, Any]], str]:
    """Nur die Form des Werkzeugs annehmen: bekannte Felder, Kennungen, Pflichtangaben.

    Rueckgabe: (Argumente, "") oder (None, Rueckfrage). Nie wird ein Feld erraten,
    umbenannt oder mit einem leeren Wert weitergegeben.
    """
    spec = FESTE_EREIGNISSE.get(event_id)
    if spec is None:
        return None, f"clarification required: {event_id} is not a fixed Ideas event"
    kandidat = wert
    if isinstance(kandidat, str):
        if not kandidat.strip() and not spec["pflicht"]:
            kandidat = {}
        else:
            try:
                kandidat = json.loads(kandidat)
            except json.JSONDecodeError:
                return None, _rueckfrage(spec)
    if not isinstance(kandidat, dict):
        return None, _rueckfrage(spec)
    felder = {**spec["pflicht"], **spec["wahl"]}
    fremd = sorted(set(kandidat) - set(felder))
    if fremd:
        return None, f"clarification required: unknown field {fremd[0]}"
    fehlend = [name for name in spec["pflicht"] if name not in kandidat]
    if fehlend:
        return None, _rueckfrage(spec)
    for name, inhalt in kandidat.items():
        if not _feld_gueltig(felder[name], inhalt):
            return None, f"clarification required: {name} is invalid"
    eins = spec.get("mindestens_eins")
    if eins and not any(name in kandidat for name in eins):
        return None, "clarification required: one of " + ", ".join(eins)
    return dict(kandidat), ""


def _rueckfrage(spec: dict[str, Any]) -> str:
    pflicht = ", ".join(spec["pflicht"]) or "a JSON object"
    return f"clarification required: {pflicht}"


def validator_config(event_id: str) -> Optional[dict[str, Any]]:
    """Frische Pruefer-Vorgabe je Ausfuehrung; ``None`` fuer reines Lesen."""
    spec = FESTE_EREIGNISSE.get(event_id)
    if spec is None or spec.get("liest_nur"):
        return None
    return {
        "kind": VALIDATOR_ART,
        "on_fail": "block",
        "require_verified": True,
        "postcondition": {"check": PRUEFUNG, "ereignis": event_id},
    }


def _mcp_inhalt(value: Any) -> tuple[Any, bool]:
    """(Inhalt, gefunden) aus der Antwort eines Werkzeugs; das Fehlerkennzeichen zaehlt nicht."""
    if not isinstance(value, dict):
        return None, False
    content = value.get("content")
    if not isinstance(content, list) or len(content) != 1:
        return None, False
    item = content[0]
    if not isinstance(item, dict) or item.get("type") != "text" or not isinstance(item.get("text"), str):
        return None, False
    try:
        return json.loads(item["text"]), True
    except json.JSONDecodeError:
        return None, False


def fehler_aus_ergebnis(value: Any) -> str:
    """"" oder eine Meldung, wenn das Werkzeug einen Fehler meldet.

    OpenFang reicht ``isError`` nicht verlaesslich durch (siehe aenderung_zurueck_contract),
    darum entscheidet der Inhalt. Durchgereicht wird nur der Fehlername vor dem
    Doppelpunkt, nie freier Werkzeugtext.
    """
    inhalt, gefunden = _mcp_inhalt(value)
    if not gefunden:
        return "Ideen-Werkzeug: keine lesbare Antwort"
    if isinstance(inhalt, dict) and "error" in inhalt:
        meldung = inhalt["error"]
        name = meldung.split(":", 1)[0] if isinstance(meldung, str) else ""
        if re.fullmatch(r"[a-z][a-z_]{0,40}", name):
            # Der Ideen-Server nennt das betroffene Feld in einfachen Anfuehrungszeichen.
            # Nur dieser Feldname kommt mit, nie ein Wert.
            feld = re.search(r"'([a-z][a-z_]{0,40})'", meldung)
            if feld:
                return f"Ideen-Werkzeug meldet einen Fehler ({name}: {feld.group(1)})"
            return f"Ideen-Werkzeug meldet einen Fehler ({name})"
        return "Ideen-Werkzeug meldet einen Fehler"
    return ""


def _erste_zeile(inhalt: Any) -> Optional[dict[str, Any]]:
    if isinstance(inhalt, list) and len(inhalt) == 1 and isinstance(inhalt[0], dict):
        return inhalt[0]
    if isinstance(inhalt, dict):
        return inhalt
    return None


def nachpruefung(event_id: str, argumente: Any, raw_result: Any) -> tuple[Optional[dict[str, Any]], str]:
    """Aus Argumenten und Quittung die Erwartung an den echten Zustand bauen.

    Die Kennungen kommen aus den geprueften Argumenten und aus der Quittung des
    Werkzeugs, nie aus dem Satz. Rueckgabe: (Erwartung, "") oder (None, Grund).
    """
    ungueltig = "MCP result receipt is invalid"
    spec = FESTE_EREIGNISSE.get(event_id)
    if spec is None or spec.get("liest_nur"):
        return None, "no ground-truth check for this event"
    argumente, grund = feste_argumente(event_id, _ohne_kennung_der_aenderung(argumente))
    if argumente is None:
        return None, grund
    if fehler_aus_ergebnis(raw_result):
        return None, "MCP tool reported an error"
    inhalt, _ = _mcp_inhalt(raw_result)

    if event_id in ("idea.create", "idea.update", "idea.format", "idea.rate", "bubble.rate"):
        zeile = _erste_zeile(inhalt)
        if zeile is None or not ist_kennung(zeile.get("id")):
            return None, ungueltig
        if event_id != "idea.create" and zeile["id"] != argumente["id"]:
            return None, ungueltig
        tabelle = "ideas" if event_id == "bubble.rate" else "canvas_nodes"
        if event_id == "idea.create":
            felder = {"linked_idea_id": argumente["bubble_id"]}
        elif event_id == "idea.update":
            felder = {name: argumente[name] for name in ("title", "content", "x", "y") if name in argumente}
        elif event_id == "idea.format":
            fmt = argumente["format"]
            if fmt == "revert":
                schema = zeile.get("format_schema")
                fmt = schema.get("type") if isinstance(schema, dict) else None
                if fmt not in FORMATE:
                    return None, ungueltig
            felder = {"format_schema.type": fmt}
        elif event_id == "idea.rate":
            felder = {"metadata.bewertung.wert": argumente["score"]}
        else:
            felder = {"score": argumente["score"]}
        return {"art": "zeile", "tabelle": tabelle, "id": zeile["id"], "felder": felder}, ""

    if not isinstance(inhalt, dict):
        return None, ungueltig
    if event_id == "idea.rate_reason":
        eintrag = inhalt.get("bewertung")
        if (inhalt.get("bubble_id") != argumente["bubble_id"] or not isinstance(eintrag, dict)
                or not ist_kennung(eintrag.get("id"))):
            return None, ungueltig
        return {"art": "eintrag", "bubble_id": argumente["bubble_id"], "eintrag_id": eintrag["id"],
                "text": argumente["text"].strip(), "karten": list(argumente.get("idea_ids", []))}, ""
    if event_id == "idea.arrange":
        lagen = inhalt.get("positionen")
        if inhalt.get("bubble_id") != argumente["bubble_id"] or not isinstance(lagen, list):
            return None, ungueltig
        sauber = []
        for lage in lagen:
            if (not isinstance(lage, dict) or not ist_kennung(lage.get("id"))
                    or not _ist_zahl(lage.get("x")) or not _ist_zahl(lage.get("y"))):
                return None, ungueltig
            sauber.append({"id": lage["id"], "x": lage["x"], "y": lage["y"]})
        return {"art": "lagen", "bubble_id": argumente["bubble_id"], "lagen": sauber}, ""
    if event_id == "idea.auto_link":
        angelegt = inhalt.get("angelegt")
        if inhalt.get("bubble_id") != argumente["bubble_id"] or not isinstance(angelegt, list):
            return None, ungueltig
        kanten = []
        for kante in angelegt:
            if (not isinstance(kante, dict) or not ist_kennung(kante.get("from_id"))
                    or not ist_kennung(kante.get("to_id"))):
                return None, ungueltig
            kanten.append([kante["from_id"], kante["to_id"]])
        return {"art": "kanten", "kanten": kanten}, ""
    if event_id == "bubble.promote":
        if not ist_kennung(inhalt.get("id")) or inhalt.get("from_idea_id") != argumente["bubble_id"]:
            return None, ungueltig
        return {"art": "befoerdert", "bubble_id": argumente["bubble_id"], "project_id": inhalt["id"]}, ""
    return None, ungueltig


def _ohne_kennung_der_aenderung(argumente: Any) -> Any:
    if isinstance(argumente, dict) and "aenderung_id" in argumente:
        return {name: wert for name, wert in argumente.items() if name != "aenderung_id"}
    return argumente


def _pfad(zeile: dict[str, Any], pfad: str) -> Any:
    wert: Any = zeile
    for teil in pfad.split("."):
        if not isinstance(wert, dict):
            return None
        wert = wert.get(teil)
    return wert


def _gleich(ist: Any, soll: Any) -> bool:
    if _ist_zahl(soll):
        return isinstance(ist, (int, float)) and not isinstance(ist, bool) and float(ist) == float(soll)
    return ist == soll


Lesen = Callable[[str, dict[str, str]], Optional[list]]


def pruefe_zustand(erwartet: Any, lies: Lesen) -> tuple[Optional[bool], dict[str, Any], str]:
    """Die Erwartung am echten Zustand pruefen.

    ``lies(tabelle, filter)`` liefert die Zeilen oder ``None``, wenn nicht gelesen
    werden konnte. Rueckgabe wie im world_observer: (True | False | None, Signal, Grund);
    ``None`` heisst "nicht nachpruefbar" und ist nie ein Erfolg.
    """
    offen = {"status": "unverified"}
    if not isinstance(erwartet, dict):
        return None, offen, "invalid expectation"
    art = erwartet.get("art")

    def eine_zeile(tabelle: str, kennung: Any, select: str) -> tuple[Optional[dict], Optional[bool], str]:
        if not ist_kennung(kennung):
            return None, None, "invalid expectation"
        zeilen = lies(tabelle, {"select": select, "id": f"eq.{kennung}", "limit": "2"})
        if zeilen is None:
            return None, None, "read-back unavailable"
        if not zeilen:
            return None, False, f"{tabelle} row not found"
        if len(zeilen) != 1 or not isinstance(zeilen[0], dict):
            return None, None, "read-back malformed"
        return zeilen[0], True, ""

    if art == "zeile":
        tabelle, felder = erwartet.get("tabelle"), erwartet.get("felder")
        if tabelle not in ("canvas_nodes", "ideas") or not isinstance(felder, dict) or not felder:
            return None, offen, "invalid expectation"
        zeile, gefunden, grund = eine_zeile(tabelle, erwartet.get("id"), "*")
        if zeile is None:
            return gefunden, offen if gefunden is None else {"rows_found": 0}, grund
        abweichend = sorted(name for name, soll in felder.items() if not _gleich(_pfad(zeile, name), soll))
        if abweichend:
            return False, {"id": zeile.get("id"), "abweichend": abweichend}, "stored row differs: " + ", ".join(abweichend)
        return True, {"id": zeile.get("id"), "rows_found": 1}, "stored row verified"

    if art == "eintrag":
        zeile, gefunden, grund = eine_zeile("ideas", erwartet.get("bubble_id"), "id,metadata")
        if zeile is None:
            return gefunden, offen if gefunden is None else {"rows_found": 0}, grund
        eintraege = _pfad(zeile, "metadata.bewertungen")
        for eintrag in eintraege if isinstance(eintraege, list) else []:
            if isinstance(eintrag, dict) and eintrag.get("id") == erwartet.get("eintrag_id"):
                if eintrag.get("text") == erwartet.get("text") and eintrag.get("karten") == erwartet.get("karten"):
                    return True, {"eintrag_id": eintrag["id"]}, "reason entry verified"
                return False, {"eintrag_id": eintrag["id"]}, "reason entry differs"
        return False, {"eintrag_id": erwartet.get("eintrag_id")}, "reason entry not found"

    if art == "lagen":
        lagen = erwartet.get("lagen")
        if not ist_kennung(erwartet.get("bubble_id")) or not isinstance(lagen, list):
            return None, offen, "invalid expectation"
        if not lagen:
            return True, {"rows_found": 0}, "nothing to arrange"
        zeilen = lies("canvas_nodes", {"select": "id,x,y", "linked_idea_id": f"eq.{erwartet['bubble_id']}",
                                       "limit": "200"})
        if zeilen is None:
            return None, offen, "read-back unavailable"
        ist = {zeile.get("id"): zeile for zeile in zeilen if isinstance(zeile, dict)}
        falsch = [lage["id"] for lage in lagen
                  if lage["id"] not in ist
                  or not _gleich(ist[lage["id"]].get("x"), lage["x"])
                  or not _gleich(ist[lage["id"]].get("y"), lage["y"])]
        if falsch:
            return False, {"abweichend": len(falsch)}, f"{len(falsch)} card position(s) differ"
        return True, {"rows_found": len(lagen)}, "card positions verified"

    if art == "kanten":
        kanten = erwartet.get("kanten")
        if not isinstance(kanten, list):
            return None, offen, "invalid expectation"
        if not kanten:
            return True, {"rows_found": 0}, "no connection was created"
        von = sorted({kante[0] for kante in kanten})
        if not all(ist_kennung(kennung) for kante in kanten for kennung in kante):
            return None, offen, "invalid expectation"
        zeilen = lies("canvas_edges", {"select": "from_node_id,to_node_id",
                                       "from_node_id": "in.(" + ",".join(von) + ")", "limit": "1000"})
        if zeilen is None:
            return None, offen, "read-back unavailable"
        ist_kanten = {(zeile.get("from_node_id"), zeile.get("to_node_id")) for zeile in zeilen if isinstance(zeile, dict)}
        fehlend = [kante for kante in kanten if tuple(kante) not in ist_kanten]
        if fehlend:
            return False, {"fehlend": len(fehlend)}, f"{len(fehlend)} connection(s) not found"
        return True, {"rows_found": len(kanten)}, "connections verified"

    if art == "befoerdert":
        project_id = erwartet.get("project_id")
        zeile, gefunden, grund = eine_zeile("ideas", erwartet.get("bubble_id"), "id,status,promoted_to_project_id")
        if zeile is None:
            return gefunden, offen if gefunden is None else {"rows_found": 0}, grund
        if zeile.get("status") != "promoted" or zeile.get("promoted_to_project_id") != project_id:
            return False, {"id": zeile.get("id")}, "bubble is not linked to the project"
        projekt, gefunden, grund = eine_zeile("projects", project_id, "id,from_idea_id")
        if projekt is None:
            return gefunden, offen if gefunden is None else {"rows_found": 0}, grund
        if projekt.get("from_idea_id") != erwartet.get("bubble_id"):
            return False, {"id": project_id}, "project does not belong to the bubble"
        return True, {"id": project_id, "rows_found": 1}, "promotion verified"

    return None, offen, "invalid expectation"
