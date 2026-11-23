"""Fehlermeldung fuer Werkzeuge des Auftragsbuchs.

Nur Standardbibliothek, wirft nie. Nur Texte aus einer festen Liste kommen in die Meldung; jeder
andere Text des Werkzeugs bleibt verborgen (er koennte Inhalte von Auftraegen enthalten).
"""
from __future__ import annotations

import re
from collections.abc import Mapping

# Die vier Texte der Bruecke (spaces/auftragsbuch/bruecke.py) ...
_FEST = frozenset({
    "auftragsbuch nicht erreichbar",
    "auftragsbuch: Schl\u00fcssel abgelehnt",
    "auftragsbuch: Zertifikat nicht pruefbar",
    # ... die sechs Ablehnungen aus Migration 100 (spaces/marketing/db/100_*.sql) ...
    "Unbekannte Auftragsart",
    "Auftrag braucht Laden und Mandant",
    "Frist ausserhalb 1 bis 120 Minuten",
    "Plan-Kennung ungueltig",
    "Tagesgrenze erreicht: hoechstens 50 Proben pro Tag",
    "Tagesgrenze erreicht: hoechstens 60 Auftraege des Gehirns pro Tag",
    # ... und die Antwort mit falscher Form.
    "Ergebnis in falscher Form",
})
# `auftragsbuch: unerwartete Antwort (<status>)` der Bruecke, mit oder ohne Vorsatz.
_ANTWORT = re.compile(r"^(?:auftragsbuch: )?unerwartete Antwort \(\d{3}\)$")


def _text(wert: object, tiefe: int = 0) -> str | None:
    """Holt den Text aus MCP-Ergebnis (`content[].text`), JSON-RPC-Fehlerobjekt (`message`) oder Umhuellung."""
    if tiefe > 3:
        return None
    if isinstance(wert, str):
        return wert
    if not isinstance(wert, Mapping):
        return None
    inhalt = wert.get("content")
    if isinstance(inhalt, (list, tuple)):
        for teil in inhalt:
            if isinstance(teil, Mapping) and isinstance(teil.get("text"), str):
                return teil["text"]
    for schluessel in ("message", "text"):
        if isinstance(wert.get(schluessel), str):
            return wert[schluessel]
    if "error" in wert:
        return _text(wert["error"], tiefe + 1)
    return None


def fehlertext(server: str, werkzeug: str, mcp_ergebnis: Mapping) -> str:
    """`<server>: <Text>` bei Texten der festen Liste, sonst `<server>/<werkzeug>: Werkzeug meldet einen Fehler`."""
    try:
        roh = _text(mcp_ergebnis)
        text = roh.strip() if roh is not None else None
        if text is not None and (text in _FEST or _ANTWORT.fullmatch(text)):
            return text if text.startswith(server) else f"{server}: {text}"
    except Exception:  # noqa: BLE001 - die Meldung darf nie selbst scheitern
        pass
    return f"{server}/{werkzeug}: Werkzeug meldet einen Fehler"
