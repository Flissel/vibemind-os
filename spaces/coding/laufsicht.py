"""Was ein Lauf gerade tut: Protokoll und Aufgaben, nur gelesen.

Die Seite zeigt neben den Stufen den Fortschritt der Engine, so wie das alte
Engine-Dashboard es tat, aber ohne dessen API, WebSocket und Electron. Alles
kommt aus den Dateien, die der Dienst je Auftrag ohnehin schreibt:

- ``<projekt>/lauf.log``                 Ausgabe des Erzeugers (Protokoll)
- ``<projekt>/tasks/epic-NNN-tasks.json`` Stand je Aufgabe

Gelesen wird nur unter der Arbeitswurzel. Schluessel, die in einer Zeile
auftauchen koennten, werden vor der Ausgabe unkenntlich gemacht.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from spaces.coding import engine_huelle, pipeline

PROTOKOLL = "lauf.log"
GRENZE = 64 * 1024  # hoechstens so viele Bytes je Abfrage

# Werte hinter diesen Mustern erscheinen nie auf der Seite.
_GEHEIM = (
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"(?i)((?:api[_-]?key|token|secret|passwor[dt]|schluessel)[\"']?\s*[:=]\s*[\"']?)[^\s\"',;]{4,}"),
    re.compile(r"(sk-[A-Za-z0-9_-]{4})[A-Za-z0-9_-]{8,}"),
)

ART = (("verify", "Prüfen"), ("test", "Tests schreiben"))


def schwaerzen(text: str) -> str:
    for muster in _GEHEIM:
        text = muster.sub(lambda m: m.group(1) + "•••", text)
    return text


def _projekt(auftrag: Dict[str, Any]) -> Optional[Path]:
    """Der Projektordner eines Engine-Auftrags, nur wenn er unter der Arbeitswurzel liegt."""
    roh = auftrag.get("projektordner")
    if auftrag.get("art") != "engine" or not roh:
        return None
    projekt = Path(str(roh))
    return projekt if pipeline._unter_wurzel(projekt, pipeline.arbeitswurzel()) else None


def protokoll(auftrag_id: Optional[str], ab: Optional[int] = None, grenze: int = GRENZE) -> Dict[str, Any]:
    """Das Protokoll ab Byte ``ab``; ohne ``ab`` das Ende. ``weiter`` ist das ``ab`` der naechsten Abfrage."""
    auftrag = pipeline._waehle(auftrag_id)
    leer = {"auftrag": auftrag["id"], "text": "", "weiter": 0, "groesse": 0, "neu": True, "gekuerzt": False}
    projekt = _projekt(auftrag)
    if projekt is None:
        return {**leer, "satz": "Dieser Lauf hat kein Protokoll der Engine."}
    datei = projekt / PROTOKOLL
    try:
        groesse = datei.stat().st_size
    except OSError:
        return {**leer, "satz": "Das Protokoll ist noch leer."}
    neu = ab is None or ab < 0 or ab > groesse  # die Datei ist neu oder kuerzer geworden
    start = max(0, groesse - grenze) if neu else ab
    ende = min(groesse, start + grenze)
    with open(datei, "rb") as f:
        f.seek(start)
        roh = f.read(ende - start)
    if start > 0 and neu:
        # Mitten in einer Zeile begonnen: bis zum naechsten Zeilenende ueberspringen.
        schnitt = roh.find(b"\n")
        roh = roh[schnitt + 1:] if schnitt >= 0 else roh
    return {
        "auftrag": auftrag["id"],
        "text": schwaerzen(roh.decode("utf-8", errors="replace")),
        "weiter": ende,
        "groesse": groesse,
        "neu": neu,
        "gekuerzt": neu and start > 0,
    }


def _liste(wert: Any) -> List[str]:
    return [str(x) for x in wert] if isinstance(wert, list) else []


def _art(typ: str) -> str:
    for anfang, name in ART:
        if typ.startswith(anfang):
            return name
    return "Code schreiben"


def _zustand(status: str) -> str:
    return {"completed": pipeline.FERTIG, "running": pipeline.LAEUFT, "failed": pipeline.FEHLGESCHLAGEN,
            "skipped": pipeline.ABGEBROCHEN}.get(status, pipeline.WARTET)


def aufgaben(auftrag_id: Optional[str]) -> Dict[str, Any]:
    """Aufgaben je Paket mit Zustand und Anteil fertig; ohne Kennungen (Name des Pakets statt AP-01)."""
    auftrag = pipeline._waehle(auftrag_id)
    projekt = _projekt(auftrag)
    pakete: List[Dict[str, Any]] = []
    zaehler = {"gesamt": 0, "fertig": 0, "laeuft": 0, "fehlgeschlagen": 0}
    for eintrag in (auftrag.get("pakete") or []) if projekt is not None else []:
        inhalt = engine_huelle._json(projekt / "tasks" / f"{str(eintrag.get('epic', '')).lower()}-tasks.json")
        zeilen = []
        for a in (inhalt or {}).get("tasks") or []:
            if not isinstance(a, dict):
                continue
            zustand = _zustand(str(a.get("status") or "pending"))
            zaehler["gesamt"] += 1
            if zustand in zaehler:
                zaehler[zustand] += 1
            zeilen.append({
                "art": _art(str(a.get("type") or "")),
                "zustand": zustand,
                "dateien": _liste(a.get("output_files")),
                "befehl": str(a.get("command") or ""),
                "fehler": schwaerzen(str(a.get("error_message") or "")) or None,
                "versuche": int(a.get("retry_count") or 0),
            })
        pakete.append({"name": engine_huelle.anzeigename(eintrag), "aufgaben": zeilen})
    gesamt = zaehler["gesamt"]
    return {
        "auftrag": auftrag["id"],
        "zustand": auftrag.get("zustand"),
        **zaehler,
        "anteil": round(100 * zaehler["fertig"] / gesamt) if gesamt else 0,
        "pakete": pakete,
    }
