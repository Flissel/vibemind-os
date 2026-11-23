"""Ergebnistext des Auftragsbuchs fuer Standzeile und Protokoll des Gehirns.

Nur Standardbibliothek. Keine Funktion wirft. Aus der Antwort des Auftragsbuchs gelangt nie freier
Inhalt in den Text: erlaubt sind nur die ersten acht Zeichen der Kennung, `an/art`, `stand`, die
Laenge jeder Liste auf oberster Ebene von `ergebnis` und der Beleg-Zustand als feste Worte.
"""
from __future__ import annotations

import json
import re

_ZIEL = re.compile(r"^mcp:[^:\s]+:auftragsbuch:[A-Za-z0-9_]+$")
_KENNUNG = re.compile(r"^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$")
_NAME = re.compile(r"^[a-z][a-z0-9_-]{0,39}$")
_LISTENNAME = re.compile(r"^[a-z][a-z0-9_]{0,23}$")
_MAX_TIEFE = 4
UNLESBAR = "Auftragsbuch: Antwort ohne lesbaren Stand"
_TRENNER = " \u00b7 "


def _auspacken(wert: object, tiefe: int = 0) -> dict | None:
    """Findet das Antwort-Woerterbuch (mit `stand`) in Text, MCP-Ergebnis, Umhuellung oder Woerterbuch."""
    if tiefe > _MAX_TIEFE:
        return None
    if isinstance(wert, (str, bytes)):
        try:
            return _auspacken(json.loads(wert), tiefe + 1)
        except (ValueError, TypeError):
            return None
    if not isinstance(wert, dict):
        return None
    if isinstance(wert.get("stand"), str):
        return wert
    inhalt = wert.get("content")
    if isinstance(inhalt, list):
        for teil in inhalt:
            if isinstance(teil, dict) and isinstance(teil.get("text"), str):
                gefunden = _auspacken(teil["text"], tiefe + 1)
                if gefunden is not None:
                    return gefunden
    return _auspacken(wert["result"], tiefe + 1) if "result" in wert else None


_BELEG = {"entfaellt": "Beleg entf\u00e4llt", "bestaetigt": "Beleg best\u00e4tigt",
          "nicht_bestaetigt": "Beleg nicht best\u00e4tigt", "nicht_pruefbar": "Beleg nicht pr\u00fcfbar"}


def _beleg(oben: object, ergebnis: object) -> str | None:
    """Schluessel des Beleg-Zustands: `beleg` oben (vier feste Woerter) vor der alten Form im Ergebnis."""
    if isinstance(oben, str) and oben in _BELEG:
        return oben
    alt = ergebnis.get("beleg") if isinstance(ergebnis, dict) else None
    if alt is True or (isinstance(alt, dict) and alt.get("bestaetigt") is True):
        return "bestaetigt"
    return None  # alles andere: keine Angabe, der Wert wird nie wiedergegeben


def _lesen(wert: object) -> dict | None:
    """Reduziert die Antwort auf die erlaubten Felder; None, wenn kein gueltiger Stand erkennbar ist."""
    try:
        antwort = _auspacken(wert)
        if antwort is None:
            return None
        stand = antwort.get("stand")
        if not isinstance(stand, str) or _NAME.fullmatch(stand) is None:
            return None
        aus: dict = {"stand": stand, "listen": {}, "beleg": None}
        kennung = antwort.get("auftrag_id")
        if isinstance(kennung, str) and _KENNUNG.fullmatch(kennung):
            aus["auftrag_id"] = kennung
        for feld in ("an", "art"):
            roh = antwort.get(feld)
            if isinstance(roh, str) and _NAME.fullmatch(roh):
                aus[feld] = roh
        ergebnis = antwort.get("ergebnis")
        if isinstance(ergebnis, dict):
            for name, inhalt in ergebnis.items():
                if isinstance(inhalt, list) and isinstance(name, str) and _LISTENNAME.fullmatch(name):
                    aus["listen"][name] = len(inhalt)
        aus["beleg"] = _beleg(antwort.get("beleg"), ergebnis)
        return aus
    except Exception:  # noqa: BLE001 - die Standzeile darf nie an der Antwort scheitern
        return None


def _gilt(ziel: object) -> bool:
    return isinstance(ziel, str) and _ZIEL.fullmatch(ziel) is not None


def vorschau(ziel: str, ergebnis: object) -> str | None:
    """Standzeile fuer ein Ziel des Auftragsbuchs; `None` bei jedem anderen Ziel (alter Weg)."""
    if not _gilt(ziel):
        return None
    d = _lesen(ergebnis)
    if d is None:
        return UNLESBAR
    teile: list[str] = []
    if "auftrag_id" in d:
        teile.append(f"Auftrag {d['auftrag_id'][:8]}")
    if "an" in d and "art" in d:
        teile.append(f"{d['an']}/{d['art']}")
    teile.append(d["stand"])
    teile.extend(f"{name}: {n}" for name, n in d["listen"].items())
    if d["beleg"] is not None:
        teile.append(_BELEG[d["beleg"]])
    return _TRENNER.join(teile)


def fuers_protokoll(ziel: str, ergebnis: object) -> object:
    """Dieselbe Antwort ohne Inhalt (nur Kennungen, Anzahlen, Stand); fremde Ziele bleiben unveraendert."""
    if not _gilt(ziel):
        return ergebnis
    d = _lesen(ergebnis)
    if d is None:
        return {"stand": "unlesbar"}
    aus: dict = {k: d[k] for k in ("auftrag_id", "an", "art") if k in d}
    aus["stand"] = d["stand"]
    aus["listen"] = dict(d["listen"])
    if d["beleg"] is not None:
        aus["beleg"] = d["beleg"]
    return aus
