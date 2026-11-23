"""Vorsicht bei "Zurück" ohne genannte Änderung (Arbeitslauf 2, Punkt 7).

Bisher nahm ``ideen_zurueck`` ohne Gruppe einfach die jüngste offene Änderung eines
Agenten zurück. Zwei Fälle gingen dabei schief:

1. Ein abgelehnter Plan (Snapshot im Plan-Recorder mit ``ok`` False) kann schon
   Zeilen geschrieben haben. Er ist nie das stille Ziel von "Zurück".
2. War der letzte eigene Schritt abgelehnt oder liegt die Änderung länger zurück,
   traf "Zurück" eine ältere Änderung, ohne es zu sagen.

Deshalb wählt das Gehirn das Ziel selbst und reicht es als Gruppe weiter:
- jüngste offene Änderung, nicht abgelehnt, höchstens ``FRISCH_S`` alt -> sofort;
- sonst wird nichts geändert; der Satz nennt, WAS zurückgenommen würde, und wie alt
  es ist. "Ja, zurück" binnen ``BESTAETIGUNG_GILT_S`` nimmt genau diese Gruppe zurück.
Ist das Protokoll nicht lesbar, wird nichts zurückgenommen (lieber fragen als raten).
"""
from __future__ import annotations

import os
import re
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Mapping, Optional
from urllib.parse import urlsplit

FRISCH_S = 120
BESTAETIGUNG_GILT_S = 120
ZEILEN = 200

FRAGE_MARKE = "aenderung_zurueck_frage: "

_JA = re.compile(r"^\s*ja\b", re.IGNORECASE)
_VORGANG = {"INSERT": "angelegt", "UPDATE": "geändert", "DELETE": "gelöscht"}

_sperre = threading.Lock()
_offen: Dict[str, Any] = {}          # {"gruppe": str, "bis": float}


def _jetzt() -> float:
    return time.time()


def protokoll_lesen() -> Optional[List[Dict[str, Any]]]:
    """Offene Änderungen von Agenten, jüngste zuerst; ``None``, wenn nicht lesbar."""
    raw_base = os.environ.get("SUPABASE_URL", "")
    try:
        from core.config import get_secret
        key = get_secret("SUPABASE_SERVICE_ROLE_KEY")
    except Exception:  # noqa: BLE001
        key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    key = (key or "").strip()
    try:
        teile = urlsplit(raw_base)
        if teile.scheme not in {"http", "https"} or not teile.hostname or teile.username or teile.query:
            return None
    except (TypeError, ValueError):
        return None
    if not key:
        return None
    try:
        import requests
        antwort = requests.get(
            f"{raw_base.rstrip('/')}/rest/v1/ideen_aenderungen",
            params={
                "select": ("id,gruppe,erstellt_am,tabelle,vorgang,"
                           "vorher_titel:vorher->>title,nachher_titel:nachher->>title,"
                           "vorher_name:vorher->>name,nachher_name:nachher->>name"),
                "quelle": "eq.agent", "zurueckgenommen_am": "is.null",
                "order": "id.desc", "limit": str(ZEILEN),
            },
            headers={"apikey": key, "Authorization": f"Bearer {key}"},
            timeout=5, allow_redirects=False,
        )
        if antwort.status_code >= 300:
            return None
        zeilen = antwort.json()
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(zeilen, list) or not all(isinstance(z, dict) for z in zeilen):
        return None
    return zeilen


def _zeitpunkt(wert: Any) -> Optional[float]:
    if not isinstance(wert, str) or not wert:
        return None
    try:
        dt = datetime.fromisoformat(wert.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _gruppen(zeilen: List[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    gruppen: Dict[str, Dict[str, Any]] = {}
    for z in zeilen:
        name = z.get("gruppe")
        if not isinstance(name, str) or not name:
            continue
        g = gruppen.setdefault(name, {"gruppe": name, "zeilen": [], "juengste": _zeitpunkt(z.get("erstellt_am"))})
        g["zeilen"].append(z)
    return list(gruppen.values())


def _dauer(sekunden: float) -> str:
    minuten = int(sekunden // 60)
    if minuten < 60:
        return "einer Minute" if minuten <= 1 else f"{minuten} Minuten"
    stunden = minuten // 60
    if stunden < 48:
        return "einer Stunde" if stunden == 1 else f"{stunden} Stunden"
    return f"{stunden // 24} Tagen"


def beschreibung(gruppe: Mapping[str, Any]) -> str:
    """"„Bühnenplan“ geändert" oder "3 Einträge, darunter „Bühnenplan“ angelegt"."""
    zeilen = gruppe.get("zeilen") or []
    erste = zeilen[-1] if zeilen else {}          # älteste Zeile: der eigentliche Schritt
    titel = next((erste.get(k) for k in ("nachher_titel", "vorher_titel", "nachher_name", "vorher_name")
                  if isinstance(erste.get(k), str) and erste.get(k).strip()), None)
    was = f"„{titel.strip()[:60]}“ {_VORGANG.get(str(erste.get('vorgang')), 'geändert')}" if titel else \
        f"ein Eintrag {_VORGANG.get(str(erste.get('vorgang')), 'geändert')}"
    if len(zeilen) > 1:
        return f"{len(zeilen)} Einträge, darunter {was}"
    return was


def entscheiden(gruppe: str, absicht: Any, *, ist_abgelehnt: Callable[[str], bool],
                lesen: Optional[Callable[[], Optional[List[Dict[str, Any]]]]] = None) -> Dict[str, Any]:
    """Ziel für "Zurück" wählen.

    Rückgabe ``{"aktion": "weiter", "gruppe": g}`` (g leer heißt: nichts offen, das
    Werkzeug meldet es selbst) oder ``{"aktion": "fragen", "satz": ...}``.
    Eine vom Menschen genannte Gruppe geht unverändert weiter.
    """
    if gruppe:
        return {"aktion": "weiter", "gruppe": gruppe}
    jetzt = _jetzt()
    bestaetigt = None
    if isinstance(absicht, str) and _JA.match(absicht):
        with _sperre:
            if _offen and _offen.get("bis", 0) >= jetzt:
                bestaetigt = _offen.get("gruppe")
            _offen.clear()
    zeilen = (lesen or protokoll_lesen)()
    if zeilen is None:
        return {"aktion": "fragen", "satz": "Ich kann gerade nicht nachsehen, was zuletzt geändert wurde. "
                                             "Ich nehme deshalb nichts zurück."}
    gruppen = _gruppen(zeilen)
    abgelehnt_davor = False
    ziel = None
    for g in gruppen:
        if ist_abgelehnt(g["gruppe"]):
            abgelehnt_davor = True
            continue
        ziel = g
        break
    if ziel is None:
        if abgelehnt_davor:
            return {"aktion": "fragen", "satz": "Der letzte Schritt wurde abgelehnt; den nehme ich nicht zurück. "
                                                 "Sonst gibt es nichts zurückzunehmen."}
        return {"aktion": "weiter", "gruppe": ""}
    if bestaetigt and bestaetigt == ziel["gruppe"]:
        return {"aktion": "weiter", "gruppe": ziel["gruppe"]}
    alter = None if ziel["juengste"] is None else max(0.0, jetzt - ziel["juengste"])
    if not abgelehnt_davor and alter is not None and alter <= FRISCH_S:
        return {"aktion": "weiter", "gruppe": ziel["gruppe"]}
    vorne = "Der letzte Schritt wurde abgelehnt; den nehme ich nicht zurück. " if abgelehnt_davor else ""
    wann = f" vor {_dauer(alter)}" if alter is not None else ""
    with _sperre:
        _offen.clear()
        _offen.update({"gruppe": ziel["gruppe"], "bis": jetzt + BESTAETIGUNG_GILT_S})
    return {"aktion": "fragen", "satz": (
        f"{vorne}Die letzte Änderung, die ich zurücknehmen kann, ist älter: {beschreibung(ziel)}{wann}. "
        "Sag „Ja, zurück“, wenn ich genau das zurücknehmen soll.")}


def satz_aus_fehler(fehler: Any) -> Optional[str]:
    text = str(fehler or "")
    i = text.find(FRAGE_MARKE)
    return text[i + len(FRAGE_MARKE):].strip() or None if i >= 0 else None
