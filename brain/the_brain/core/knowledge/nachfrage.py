"""Einen Beleg erneut an seiner Quelle abfragen (Nachfrage-Probe, Spec §5).

Dieselbe Abfrage dient beim Erstellen eines Fakts (quellen.py) und beim
Nachpruefen. So misst die Probe denselben Pfad wie der Betrieb.
Sonderfelder: `#count` = Gesamtzahl aus Content-Range, `#status` = HTTP-Status.
"""
from __future__ import annotations

import os
import re
from typing import Any, Optional, Tuple

TIMEOUT = float(os.environ.get("KNOWLEDGE_HTTP_TIMEOUT", "5"))
_AUSWAHL = re.compile(r"^\[(\w+)=([^\]]+)\]$")


def feld_lesen(daten: Any, feld: str) -> Optional[str]:
    teile = re.findall(r"\[[^\]]+\]|[^.\[\]]+", feld)
    wert = daten
    if isinstance(wert, list) and teile and not _AUSWAHL.match(teile[0]):
        wert = wert[0] if wert else None
    for t in teile:
        if wert is None:
            return None
        m = _AUSWAHL.match(t)
        if m:
            k, v = m.groups()
            wert = next((e for e in wert if isinstance(e, dict) and str(e.get(k)) == v), None) \
                if isinstance(wert, list) else None
        elif isinstance(wert, dict):
            wert = wert.get(t)
        else:
            return None
    return None if wert is None else str(wert)


def _anfrage(quelle: str, ziel: str, mit_zaehlung: bool):
    import requests
    if quelle == "supabase":
        from core.world_observer import _supabase_zugang
        base, hdr, grund = _supabase_zugang()
        if base is None:
            raise RuntimeError(grund)
        if mit_zaehlung:
            hdr = {**hdr, "Prefer": "count=exact"}
        return requests.get(f"{base}/rest/v1/{ziel}", headers=hdr, timeout=TIMEOUT)
    if quelle == "openfang":
        base = os.environ.get("OPENFANG_URL", "http://127.0.0.1:4200").rstrip("/")
        hdr = {"Authorization": f"Bearer {os.environ.get('OPENFANG_API_KEY', '')}"}
        return requests.get(f"{base}{ziel}", headers=hdr, timeout=TIMEOUT)
    if quelle == "http":
        return requests.get(ziel, timeout=TIMEOUT)
    raise RuntimeError(f"unbekannte Quelle {quelle!r}")


def abfragen(quelle: str, ziel: str, mit_zaehlung: bool = False) -> Tuple[Any, Optional[int], int]:
    """(json, gesamtzahl, status). Wirft RuntimeError bei Transportfehler/HTTP>=400."""
    try:
        r = _anfrage(quelle, ziel, mit_zaehlung)
    except RuntimeError:
        raise
    except Exception as e:
        raise RuntimeError(f"Transport: {e}") from e
    status = int(r.status_code)
    if quelle != "http" and status >= 400:
        raise RuntimeError(f"HTTP {status}")
    cr = (getattr(r, "headers", None) or {}).get("Content-Range", "")
    gesamt = int(cr.rsplit("/", 1)[1]) if "/" in cr and cr.rsplit("/", 1)[1].isdigit() else None
    try:
        daten = r.json()
    except Exception:
        daten = None
    return daten, gesamt, status


def wert_von(quelle: str, ziel: str, feld: str) -> Optional[str]:
    daten, gesamt, status = abfragen(quelle, ziel, mit_zaehlung=(feld == "#count"))
    if feld == "#count":
        return None if gesamt is None else str(gesamt)
    if feld == "#status":
        return str(status)
    return feld_lesen(daten, feld)


def nachfragen(beleg) -> Optional[bool]:
    try:
        aktuell = wert_von(beleg.quelle, beleg.ziel, beleg.feld)
    except RuntimeError:
        return None
    if aktuell is None:
        return False
    return aktuell == beleg.wert
