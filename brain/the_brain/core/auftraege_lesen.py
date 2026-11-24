"""Fester Lesesatz der Aufträge: "Welche Aufträge laufen gerade?" / "Was machen die Agenten gerade?".

Liest die Auftraege des Gehirns an seine Agenten (Tabelle ``brain_agent_auftraege``, dieselbe, die
``scripts/brain_lage.py`` zeigt), ohne Modell und ohne Agent. Gibt nur Faehigkeit, Agent, Stand und
Zeiten weiter, nie den Auftragstext, das Ergebnis oder eine Kennung.

Bis hierher antwortete "Zeig meine Aufträge" nur mit den geplanten Aufgaben (``schedule_list``) und
dem Zusatz, dass Auftraege an Agenten fehlen (core/verstehen.py, ``NUR_GEPLANT``). Dieser Satz
schliesst die Luecke. Die Faehigkeit heisst ``schedule_auftraege``: Die Vorsilbe ``schedule_`` ordnet
sie dem Reiter Aufträge zu (core/verstehen.py, ``SPACES["orders"]``), die App springt dorthin.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, List, Optional

ZEILEN = 20
ZULETZT = 3
ZULETZT_HORIZONT_S = 24 * 3600
IM_SATZ = 5
OFFEN = ("offen", "laeuft")

_STAND = {"offen": "wartet", "laeuft": "läuft", "fertig": "fertig", "fehler": "mit Fehler beendet",
          "abgelehnt": "abgelehnt", "abgelaufen": "abgelaufen"}

# Faehigkeiten, die heute als Auftrag an einen Agenten gehen, in Worten. Unbekannte bleiben beim Namen.
_TAETIGKEIT = {"idea_expand": "Idee ausbauen", "idea_group": "Ideen ordnen", "idea_whitepaper": "White Paper"}


def _zeit(wert: Any) -> Optional[datetime]:
    if not isinstance(wert, str) or not wert:
        return None
    try:
        zeit = datetime.fromisoformat(wert.replace("Z", "+00:00"))
    except ValueError:
        return None
    return zeit if zeit.tzinfo else zeit.replace(tzinfo=timezone.utc)


def _sekunden(von: Optional[datetime], jetzt: datetime) -> Optional[int]:
    return None if von is None else max(0, int((jetzt - von).total_seconds()))


def _eintrag(zeile: dict, jetzt: datetime) -> dict:
    stand = str(zeile.get("status") or "")
    if stand == "laeuft":
        seit = _sekunden(_zeit(zeile.get("begonnen")) or _zeit(zeile.get("angelegt")), jetzt)
    elif stand == "offen":
        seit = _sekunden(_zeit(zeile.get("angelegt")), jetzt)
    else:
        seit = _sekunden(_zeit(zeile.get("beendet")) or _zeit(zeile.get("angelegt")), jetzt)
    return {"faehigkeit": str(zeile.get("capability") or ""), "agent": str(zeile.get("agent") or ""),
            "stand": stand, "sekunden": seit}


def auftraege_lesen(value: Any = None, *, tabelle: Any = None, jetzt: Optional[datetime] = None, **_: Any) -> dict:
    """Ziel ``direct:core.auftraege_lesen:auftraege_lesen``: laufende und zuletzt beendete Auftraege."""
    if tabelle is None:
        try:
            from .agent_auftraege import tabelle_aus_umgebung
            tabelle = tabelle_aus_umgebung()
        except Exception:  # noqa: BLE001 - kein Schluessel, keine Adresse
            return {"success": False, "error": "kein_zugang"}
    try:
        zeilen = tabelle.zuletzt(ZEILEN)
    except Exception:  # noqa: BLE001 - Tabelle antwortet nicht
        return {"success": False, "error": "nicht_lesbar"}
    if not isinstance(zeilen, list):
        return {"success": False, "error": "nicht_lesbar"}
    jetzt = jetzt or datetime.now(timezone.utc)
    eintraege = [_eintrag(z, jetzt) for z in zeilen if isinstance(z, dict)]
    laufend = [e for e in eintraege if e["stand"] in OFFEN]
    zuletzt = [e for e in eintraege if e["stand"] not in OFFEN
               and e["sekunden"] is not None and e["sekunden"] <= ZULETZT_HORIZONT_S][:ZULETZT]
    return {"success": True, "laufend": laufend, "zuletzt": zuletzt, "anzahl_laufend": len(laufend)}


def _dauer(sekunden: Optional[int]) -> str:
    if sekunden is None:
        return ""
    if sekunden < 90:
        return f"{sekunden} s"
    if sekunden < 90 * 60:
        return f"{round(sekunden / 60)} min"
    return f"{round(sekunden / 3600)} h"


def _was(eintrag: dict) -> str:
    taetigkeit = _TAETIGKEIT.get(eintrag["faehigkeit"], eintrag["faehigkeit"] or "ein Auftrag")
    return f"{taetigkeit} bei {eintrag['agent']}" if eintrag["agent"] else taetigkeit


def _laufend(eintrag: dict) -> str:
    dauer = _dauer(eintrag["sekunden"])
    stand = _STAND.get(eintrag["stand"], eintrag["stand"])
    return f"{_was(eintrag)} ({stand}" + (f" seit {dauer})" if dauer else ")")


def _beendet(eintrag: dict) -> str:
    dauer = _dauer(eintrag["sekunden"])
    stand = _STAND.get(eintrag["stand"], eintrag["stand"] or "beendet")
    return f"{_was(eintrag)}, {stand}" + (f" vor {dauer}" if dauer else "")


def antwort(ergebnis: Any) -> Optional[str]:
    """Deutscher Satz; ``None``, wenn das Ergebnis nicht lesbar ist (dann gilt der Fehlersatz)."""
    try:
        if isinstance(ergebnis, dict) and isinstance(ergebnis.get("result"), dict):
            ergebnis = ergebnis["result"]
        if not isinstance(ergebnis, dict) or ergebnis.get("success") is not True:
            return None
        laufend: List[dict] = ergebnis.get("laufend") or []
        zuletzt: List[dict] = ergebnis.get("zuletzt") or []
        if not laufend:
            satz = "Gerade läuft kein Auftrag an einen Agenten."
            if zuletzt:
                satz += " Zuletzt: " + "; ".join(_beendet(e) for e in zuletzt) + "."
            return satz
        kopf = "Ein Auftrag an einen Agenten läuft" if len(laufend) == 1 else \
            f"{len(laufend)} Aufträge an Agenten laufen"
        rest = f" und {len(laufend) - IM_SATZ} weitere" if len(laufend) > IM_SATZ else ""
        return f"{kopf}: " + "; ".join(_laufend(e) for e in laufend[:IM_SATZ]) + rest + "."
    except Exception:  # noqa: BLE001
        return None


def fehlersatz(ergebnis: Any = None, fehler: Any = None) -> Optional[str]:
    """Genauer Satz, wenn die Tabelle nicht lesbar ist."""
    if isinstance(ergebnis, dict) and isinstance(ergebnis.get("result"), dict):
        ergebnis = ergebnis["result"]
    grund = ergebnis.get("error") if isinstance(ergebnis, dict) else None
    if grund == "kein_zugang":
        return "Die Aufträge an Agenten kann ich gerade nicht lesen: Dem Gehirn fehlt der Zugang zur Auftragsliste."
    return "Die Aufträge an Agenten kann ich gerade nicht lesen: Die Auftragsliste antwortet nicht."
