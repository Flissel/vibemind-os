"""Klickmarken fuer die Desktop-Ansicht: alle Klicks aus dem Aufgabenbuch, ohne Inhalt.

Das Aufgabenbuch schreibt der Werkzeug-Dienst ``spaces-desktop`` (spaces/desktop/aufgaben.py):
je Aufgabe eine JSON-Datei mit ihren Schritten. Hier wird es nur gelesen. Hinaus geht je Klick:
Punkt (x, y in Bildschirmpixeln des ganzen Desktops), Zeit, ``ok``, ``echt``, Quelle und bei
abgelehnten Klicks ein kurzer Grund-Code. Nie der Auftragstext, nie ein Fenstertitel.

Quelle:
* ``hand``    durchgereicht aus der Ansicht (Aufgabe "Hand: Klick", core/desktop_hand.py)
* ``trocken`` gebucht, aber nicht ausgefuehrt (Trockenlauf)
* ``agent``   vom Desktop-Agenten ausgefuehrt
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any, Mapping, Optional

ID_MUSTER = re.compile(r"dt-[0-9]{8}-[0-9]{6}-[0-9a-f]{6}")
HAND_PRAEFIX = "Hand:"
STANDARD_FENSTER_SEKUNDEN = 120.0
HOECHSTENS_SEKUNDEN = 24 * 3600.0
HOECHSTENS_KLICKS = 200
# Im Container ist der Benutzerordner des Rechners unter /host_users eingehaengt.
_CONTAINER_ORDNER = Path("/host_users/AppData/Local/VibeMind/desktop-aufgaben")
_GRUND = re.compile(r"^([a-z_]{3,40})(?::|$)")


def ordner(env: Optional[Mapping[str, str]] = None) -> Optional[Path]:
    """Ordner des Aufgabenbuchs wie im Werkzeug-Dienst; im Container der eingehaengte Ordner."""
    env = os.environ if env is None else env
    eigener = str(env.get("VIBEMIND_DESKTOP_AUFGABEN_DIR") or "").strip()
    if eigener:
        return Path(eigener)
    basis = str(env.get("LOCALAPPDATA") or "").strip()
    if basis:
        return Path(basis) / "VibeMind" / "desktop-aufgaben"
    if _CONTAINER_ORDNER.is_dir():
        return _CONTAINER_ORDNER
    return None


def _zahl(wert: Any) -> Optional[int]:
    if isinstance(wert, bool) or not isinstance(wert, (int, float)):
        return None
    return int(wert)


def _grund(meldung: Any) -> Optional[str]:
    """Nur der Code vor dem Doppelpunkt ("gesperrt", "not_aus"), nie freier Text."""
    m = _GRUND.match(str(meldung or "").strip())
    return m.group(1) if m else None


def _quelle(aufgabe: Mapping[str, Any], schritt: Mapping[str, Any]) -> str:
    if aufgabe.get("trocken") or (schritt.get("ok") is True and schritt.get("echt") is False):
        return "trocken"
    return "hand" if str(aufgabe.get("aufgabe") or "").startswith(HAND_PRAEFIX) else "agent"


def klicks(seit: Optional[float] = None, *, jetzt: Optional[float] = None,
           env: Optional[Mapping[str, str]] = None) -> dict[str, Any]:
    """Alle Klicks seit ``seit`` (Unix-Sekunden), neueste zuletzt, hoechstens ``HOECHSTENS_KLICKS``.

    Ohne ``seit``: die letzten zwei Minuten. Weiter zurueck als ein Tag geht es nicht.
    """
    jetzt = time.time() if jetzt is None else float(jetzt)
    if seit is None:
        seit = jetzt - STANDARD_FENSTER_SEKUNDEN
    seit = max(float(seit), jetzt - HOECHSTENS_SEKUNDEN)
    pfad = ordner(env)
    if pfad is None or not pfad.is_dir():
        return {"success": False, "error": "aufgabenbuch_fehlt", "klicks": [], "stopped": False}
    liste: list[dict[str, Any]] = []
    for datei in pfad.glob("dt-*.json"):
        if not ID_MUSTER.fullmatch(datei.stem):
            continue
        try:
            if datei.stat().st_mtime < seit:
                continue  # seit der Zeit nicht mehr geschrieben, also kein neuer Schritt
            aufgabe = json.loads(datei.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(aufgabe, Mapping):
            continue
        for schritt in aufgabe.get("schritte") or []:
            if not isinstance(schritt, Mapping) or schritt.get("art") != "click":
                continue
            zeit = schritt.get("zeit")
            angaben = schritt.get("angaben") if isinstance(schritt.get("angaben"), Mapping) else {}
            x, y = _zahl(angaben.get("x")), _zahl(angaben.get("y"))
            if not isinstance(zeit, (int, float)) or isinstance(zeit, bool) or zeit < seit or x is None or y is None:
                continue
            eintrag = {
                "x": x, "y": y, "zeit": float(zeit),
                "ok": schritt.get("ok") if isinstance(schritt.get("ok"), bool) else None,
                "echt": bool(schritt.get("echt")),
                "quelle": _quelle(aufgabe, schritt),
                # Auch im Trockenlauf erkennbar, ob der Klick aus der Ansicht kam.
                "hand": str(aufgabe.get("aufgabe") or "").startswith(HAND_PRAEFIX),
                "task_id": datei.stem,
            }
            if eintrag["ok"] is False:
                eintrag["grund"] = _grund(schritt.get("meldung")) or "fehlgeschlagen"
            liste.append(eintrag)
    liste.sort(key=lambda k: k["zeit"])
    return {"success": True, "klicks": liste[-HOECHSTENS_KLICKS:], "gekuerzt": len(liste) > HOECHSTENS_KLICKS,
            "stopped": (pfad / "STOP").exists(), "jetzt": jetzt}
