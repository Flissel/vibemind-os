"""Vertrag der Faehigkeit "zurueck" (letzte Aenderung an Ideen und Bubbles zuruecknehmen).

Der Aufruf laeuft ueber den Ideen-Server, weil nur der Dienstschluessel die
Datenbankfunktion ``ideen_zurueck`` aufrufen darf. Dieses Modul haelt die drei
Dinge, die Plan-Ausfuehrer, Ausfuehrer und Pruefer gemeinsam brauchen:
die Quittung des Werkzeugs, seine drei benannten Fehlermeldungen und die Regel,
wann ein Plan-Argument als Gruppe gilt.
"""
from __future__ import annotations

import json
import re
from typing import Any, Optional

AENDERUNG_ZURUECK_EVENT = "idea.aenderung_zurueck"
AENDERUNG_ZURUECK_TARGET = "mcp:brain-ideas:spaces-ideas:aenderung_zurueck"

_GRUPPE_MAX = 200
# Dieselben drei Meldungen, die der Ideen-Server von ideen_zurueck durchlaesst.
_FEHLER = re.compile(
    r"^(?:aenderung_konflikt|aenderung_unbekannt|aenderung_nicht_zuruecknehmbar)"
    r"(?:: [A-Za-z0-9._:-]{1,200}(?: [A-Za-z0-9._:-]{1,200})?)?$"
)


def ist_gruppe(wert: Any) -> bool:
    return (isinstance(wert, str) and 1 <= len(wert) <= _GRUPPE_MAX
            and wert == wert.strip() and wert.isprintable())


def gruppe_aus_plan_argument(argument: Any, absicht: Any) -> str:
    """Die Gruppe nur uebernehmen, wenn der Mensch sie selbst genannt hat.

    "zurueck" ohne Angabe meint immer die juengste Aenderung. Der Planer fuellt das
    Argument aber gern mit einem Wort aus dem Satz ("letzte") oder erfindet eine
    Kennung. Eine echte Gruppe steht woertlich in der Anfrage und sieht wie eine
    Kennung aus (Ziffer, Binde- oder Unterstrich, Doppelpunkt). Alles andere wird zu "".
    """
    if not isinstance(argument, str) or not isinstance(absicht, str):
        return ""
    gruppe = argument.strip()
    if not ist_gruppe(gruppe) or not re.search(r"[0-9:_-]", gruppe):
        return ""
    return gruppe if gruppe in absicht else ""


def _mcp_text(value: Any, *, fehler: bool) -> Optional[Any]:
    if not isinstance(value, dict) or value.get("isError") is not fehler:
        return None
    content = value.get("content")
    if not isinstance(content, list) or len(content) != 1:
        return None
    item = content[0]
    if not isinstance(item, dict) or item.get("type") != "text" or not isinstance(item.get("text"), str):
        return None
    try:
        return json.loads(item["text"])
    except json.JSONDecodeError:
        return None


def extract_aenderung_zurueck_receipt(value: Any) -> tuple[Optional[dict[str, Any]], str]:
    """Gruppe und Anzahl aus der Antwort des Werkzeugs, sonst (None, Grund)."""
    receipt = _mcp_text(value, fehler=False)
    if not isinstance(receipt, dict) or "gruppe" not in receipt or "anzahl" not in receipt:
        return None, "MCP result receipt is invalid"
    gruppe, anzahl = receipt["gruppe"], receipt["anzahl"]
    if isinstance(anzahl, bool) or not isinstance(anzahl, int) or anzahl < 0:
        return None, "MCP result receipt is invalid"
    if gruppe is None:
        if anzahl != 0:
            return None, "MCP result receipt is contradictory"
    elif not ist_gruppe(gruppe) or anzahl == 0:
        return None, "MCP result receipt is contradictory"
    return {"gruppe": gruppe, "anzahl": anzahl}, ""


def aenderung_fehler_aus_mcp_ergebnis(value: Any) -> str:
    """Eine der drei benannten Meldungen von ideen_zurueck, sonst ""."""
    payload = _mcp_text(value, fehler=True)
    if not isinstance(payload, dict):
        return ""
    meldung = payload.get("error")
    return meldung if isinstance(meldung, str) and _FEHLER.fullmatch(meldung) else ""
