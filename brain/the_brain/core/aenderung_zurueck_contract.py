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


_KENNUNG_IM_SATZ = re.compile(r"[A-Za-z0-9][A-Za-z0-9:_.-]*")


def _sieht_wie_kennung_aus(wort: str) -> bool:
    return (ist_gruppe(wort) and bool(re.search(r"[0-9]", wort))
            and bool(re.search(r"[:_-]", wort)))


def gruppe_aus_plan_argument(argument: Any, absicht: Any) -> Optional[str]:
    """Die Gruppe nur uebernehmen, wenn der Mensch sie selbst genannt hat.

    "zurueck" ohne Angabe meint immer die juengste Aenderung. Planer und
    Muster-Abkuerzung fuellen das Argument aber gern mit einem Wort aus dem Satz
    ("letzte", "zurueck") oder erfinden eine Kennung. Eine echte Gruppe steht woertlich
    in der Anfrage und sieht wie eine Kennung aus (Ziffer plus Binde-, Unterstrich
    oder Doppelpunkt, etwa ``hand-4711`` oder ``plan_7f3a``).

    Rueckgabe: die Gruppe; "" fuer "die juengste"; ``None``, wenn die Anfrage mehrere
    Kennungen nennt und das Argument keine davon ist. Dann darf nichts zurueckgenommen
    werden, sonst traefe es die falsche Aenderung.
    """
    if not isinstance(absicht, str):
        return ""
    if isinstance(argument, str):
        gruppe = argument.strip()
        if _sieht_wie_kennung_aus(gruppe) and gruppe in absicht:
            return gruppe
    genannt = []
    for wort in _KENNUNG_IM_SATZ.findall(absicht):
        wort = wort.rstrip(".:-_")
        if _sieht_wie_kennung_aus(wort) and wort not in genannt:
            genannt.append(wort)
    if len(genannt) == 1:
        return genannt[0]
    return None if genannt else ""


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


def quittung_oder_fehler(value: Any) -> str:
    """"" bei gueltiger Quittung, sonst eine Fehlermeldung fuer den Plan-Schritt.

    OpenFang reicht das Fehlerkennzeichen des Werkzeugs nicht verlaesslich durch: Am
    21.11.2026 kam ein Fehlertext mit ``isError: false`` an, und der Schritt galt als
    gelungen, obwohl nichts zurueckgenommen war. Deshalb entscheidet der Inhalt.
    Durchgereicht werden nur die drei benannten Meldungen und sonst hoechstens der
    Fehlername vor dem Doppelpunkt, nie freier Werkzeugtext.
    """
    payload = _mcp_text(value, fehler=False)
    if payload is None:
        payload = _mcp_text(value, fehler=True)
    if isinstance(payload, dict) and "error" in payload:
        meldung = payload["error"]
        if isinstance(meldung, str) and _FEHLER.fullmatch(meldung):
            return f"aenderung_zurueck: {meldung}"
        name = meldung.split(":", 1)[0] if isinstance(meldung, str) else ""
        if re.fullmatch(r"[a-z][a-z_]{0,40}", name):
            return f"aenderung_zurueck: Werkzeug meldet einen Fehler ({name})"
        return "aenderung_zurueck: Werkzeug meldet einen Fehler"
    if extract_aenderung_zurueck_receipt(value)[0] is None:
        return "aenderung_zurueck: keine gueltige Quittung"
    return ""


def aenderung_fehler_aus_mcp_ergebnis(value: Any) -> str:
    """Eine der drei benannten Meldungen von ideen_zurueck, sonst ""."""
    payload = _mcp_text(value, fehler=True)
    if not isinstance(payload, dict):
        return ""
    meldung = payload.get("error")
    return meldung if isinstance(meldung, str) and _FEHLER.fullmatch(meldung) else ""
