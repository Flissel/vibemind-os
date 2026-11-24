# -*- coding: utf-8 -*-
"""Feste Absichten des Project Space: welcher Aufruf an den Dienst, welcher deutsche Satz zurueck.

Lesende Absichten brauchen kein Modell. Das Gehirn ruft den Dienst (``spaces/coding/dienst.py``)
mit einem festen ``http:GET:``-Ziel und macht aus der JSON-Antwort mit ``antwort`` einen Satz.
Nur Standardbibliothek; die Datei darf ohne die Engine geladen werden.

Beschrieben in ``spaces/coding/ABSICHTEN.md``.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

DIENST = "http://127.0.0.1:8163"
SPACE = "coding"
AGENT = "brain-coder"

# Absicht -> feste Aufrufe (der Reihe nach) und Beispielsaetze. "wenn_fertig": nur wenn der
# vorige Aufruf einen fertigen Lauf zeigt.
LESEN: Dict[str, Dict[str, Any]] = {
    "code.status": {
        "aufrufe": [f"http:GET:{DIENST}/api/stand"],
        "saetze": ["Wie ist der Code-Status?", "Was macht der Project Space gerade?", "Läuft noch ein Bau?"],
    },
    "code.list": {
        "aufrufe": [f"http:GET:{DIENST}/api/liste"],
        "parameter": {"grenze": 5},
        "saetze": ["Welche Läufe gibt es?", "Zeig mir die letzten Läufe.", "Was wurde zuletzt gebaut?"],
    },
    "code.show": {
        "aufrufe": [f"http:GET:{DIENST}/api/stand", f"http:GET:{DIENST}/api/ergebnis"],
        "wenn_fertig": True,
        "saetze": ["Zeig den letzten Lauf.", "Wie ist der letzte Lauf ausgegangen?", "Wie starte ich die App?"],
    },
    "code.dienst": {
        "aufrufe": [f"http:GET:{DIENST}/health"],
        "saetze": ["Läuft der Coding-Dienst?", "Ist der Project Space erreichbar?"],
    },
}

STUFE = {"annahme": "Annahme", "plan": "Plan", "bau": "Bau", "pruefung": "Prüfung", "abschluss": "Abschluss"}
ZUSTAND = {"wartet": "wartet", "laeuft": "läuft", "fertig": "fertig", "fehlgeschlagen": "gescheitert",
           "abgebrochen": "abgebrochen"}

NICHT_ERREICHBAR = "Der Dienst des Project Space antwortet nicht."
KEIN_LAUF = "Im Project Space gibt es noch keinen Lauf."


def dauer(sekunden: Any) -> str:
    """Dauer in Worten: 42 Sekunden, 3 Minuten, 1 Stunde 5 Minuten."""
    try:
        s = max(0, round(float(sekunden)))
    except (TypeError, ValueError):
        return ""
    if s < 60:
        return f"{s} Sekunden"
    m = s // 60
    if m < 60:
        return "1 Minute" if m == 1 else f"{m} Minuten"
    h, rest = divmod(m, 60)
    stunden = "1 Stunde" if h == 1 else f"{h} Stunden"
    return f"{stunden} {rest} Minuten" if rest else stunden


def _titel(stand: Dict[str, Any]) -> str:
    return f"„{(stand.get('titel') or 'Ohne Titel').strip()}“"


def _wo(stand: Dict[str, Any]) -> str:
    kurz = stand.get("kurz") or {}
    stufe = STUFE.get(kurz.get("stufe") or stand.get("stufe"), stand.get("stufe") or "")
    if kurz.get("pakete_gesamt"):
        return f"{stufe}, Paket {kurz.get('paket_nr')} von {kurz['pakete_gesamt']}"
    return stufe


def stand_satz(stand: Dict[str, Any]) -> str:
    """Ein Satz ueber einen Lauf, aus der Antwort von ``/api/stand``."""
    zustand = stand.get("zustand")
    titel = _titel(stand)
    if zustand == "laeuft":
        return f"{titel} läuft gerade: {_wo(stand)}."
    if zustand == "wartet":
        return f"{titel} ist angelegt und wartet auf den Start."
    if zustand == "fertig":
        zeit = dauer(stand.get("dauer_s"))
        return f"{titel} ist fertig{f' nach {zeit}' if zeit else ''}."
    if zustand == "fehlgeschlagen":
        grund = (stand.get("fehler") or "").strip()
        return f"{titel} ist gescheitert. {grund}".strip()
    if zustand == "abgebrochen":
        return f"{titel} wurde abgebrochen."
    return f"{titel}: {ZUSTAND.get(zustand, zustand or 'unbekannt')}."


def liste_satz(liste: Dict[str, Any]) -> str:
    """Ein Satz ueber die neuesten Laeufe, aus der Antwort von ``/api/liste``."""
    auftraege: List[Dict[str, Any]] = liste.get("auftraege") or []
    anzahl = int(liste.get("anzahl") or len(auftraege))
    if not auftraege:
        return KEIN_LAUF
    teile = [f"{_titel(a)} ({ZUSTAND.get(a.get('zustand'), a.get('zustand'))})" for a in auftraege]
    kopf = "Es gibt einen Lauf" if anzahl == 1 else f"Es gibt {anzahl} Läufe"
    if anzahl > len(auftraege):
        return f"{kopf}, die neuesten: {', '.join(teile)}."
    return f"{kopf}: {', '.join(teile)}."


def ergebnis_satz(stand: Dict[str, Any], ergebnis: Optional[Dict[str, Any]]) -> str:
    """Satz zum letzten Lauf; bei einem fertigen Lauf mit Paketen, Pruefung und Startbefehl."""
    satz = stand_satz(stand)
    if stand.get("zustand") != "fertig" or not ergebnis:
        return satz
    pakete = len(ergebnis.get("pakete") or [])
    dateien = len(ergebnis.get("dateien") or [])
    teile = [satz, f"Gebaut wurden {pakete} {'Paket' if pakete == 1 else 'Pakete'} mit "
                   f"{dateien} {'Datei' if dateien == 1 else 'Dateien'}."]
    pruefung = ergebnis.get("pruefung") or {}
    teile.append("Alle Prüfungen sind bestanden." if pruefung.get("bestanden")
                 else "Die Prüfung ist nicht bestanden.")
    if ergebnis.get("start"):
        teile.append(f"Gestartet wird die App mit „{ergebnis['start']}“ in ihrem Ordner.")
    return " ".join(teile)


def angenommen_satz(sicht: Dict[str, Any], status: int = 200) -> str:
    """Satz zu ``code.build``, aus der Antwort von ``POST /api/auftrag``."""
    if status == 0:
        return NICHT_ERREICHBAR
    if status in (401, 403):
        return "Der Coding-Dienst nimmt gerade keine Aufträge an."
    if status >= 400:
        return f"Der Entwurf wurde nicht angenommen: {sicht.get('error', status)}."
    n = len(sicht.get("pakete") or [])
    satz = f"{_titel(sicht)} ist angenommen"
    if n:
        satz += f": {n} {'Paket' if n == 1 else 'Pakete'}"
        if sicht.get("modellaufrufe_erwartet"):
            satz += (f", {sicht['modellaufrufe_erwartet']} bis {sicht.get('modellaufrufe_hoechstens')}"
                     " Modellaufrufe")
    if not sicht.get("angestossen"):
        return satz + ". Der Bau startet, sobald der Dienst frei ist."
    return satz + ". Der Bau läuft."


def antwort(absicht: str, antworten: List[Optional[Dict[str, Any]]], status: int = 200) -> str:
    """Der deutsche Satz zu einer lesenden Absicht.

    ``antworten`` sind die JSON-Antworten der festen Aufrufe in ihrer Reihenfolge (``None`` fuer
    einen Aufruf, der nicht lief). ``status`` ist der HTTP-Status des ersten Aufrufs; 0 heisst:
    keine Verbindung.
    """
    if absicht not in LESEN:
        raise ValueError(f"keine lesende Absicht: {absicht}")
    erste = antworten[0] if antworten else None
    if status == 0 or erste is None:
        return NICHT_ERREICHBAR
    if status == 404:
        return KEIN_LAUF
    if status >= 400:
        return f"Der Dienst des Project Space meldet einen Fehler: {erste.get('error', status)}."
    if absicht == "code.dienst":
        schreiben = "nimmt Aufträge an" if erste.get("schreiben") else "zeigt nur an und nimmt keine Aufträge an"
        return f"Der Coding-Dienst läuft und {schreiben}."
    if absicht == "code.list":
        return liste_satz(erste)
    if absicht == "code.show":
        return ergebnis_satz(erste, antworten[1] if len(antworten) > 1 else None)
    return stand_satz(erste)
