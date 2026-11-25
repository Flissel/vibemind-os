"""Desktop-Ansicht: Klickmarken lesen und Klicks durchreichen.

* ``GET  /api/desktop/klicks?seit=<Unix-Sekunden>``  Klicks aus dem Aufgabenbuch (core/desktop_klicks.py)
* ``POST /api/desktop/hand-klick`` ``{"x": int, "y": int}``  einen Klick durchreichen (core/desktop_hand.py)

Wer darf: nur ueber den eigenen Rechner (Host-Kopf localhost/127.0.0.1/[::1], gegen DNS-Rebinding)
und **nicht aus einer Webseite**. Das Gehirn antwortet CORS fuer jeden Ursprung
(web/brain_server.py), darum zaehlt die Herkunft der Anfrage: Browser schicken bei fetch immer
``Sec-Fetch-Site`` und bei POST immer ``Origin``. Den Klick nimmt das Gehirn nur ohne beide Koepfe
an, also vom Hauptprozess der App (Node-HTTP), nie direkt aus dem Renderer. Die Marken liest
ausserdem eine Seite vom selben Ursprung (``Sec-Fetch-Site: same-origin``).

Der Klick braucht zusaetzlich das Zeichen der App im Kopf ``X-Vibemind-Eingabe``:
``eingabe_zeichen(VIBEMIND_EINGABE_SCHLUESSEL, "hand-klick <x> <y>", <unix-sekunden>)``, 60 s gueltig,
jedes Zeichen einmal (core/desktop_hand.beleg_pruefen). Ohne Schluessel im Gehirn: nie (403 ``nicht_belegt``).
"""
from __future__ import annotations

import math
import os
from typing import Any, Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter()

_LOKALE_HOSTS = ("localhost", "127.0.0.1", "[::1]")


def host_ist_lokal(request: Request) -> bool:
    host = request.headers.get("host", "").strip().lower()
    name = host.split("]", 1)[0] + "]" if host.startswith("[") else (host.rsplit(":", 1)[0] if ":" in host else host)
    weitere = tuple(h.strip().lower() for h in os.environ.get("DECISION_GRAPH_HOSTS", "").split(",") if h.strip())
    return bool(name) and name in _LOKALE_HOSTS + weitere


def aus_einer_webseite(request: Request, *, gleicher_ursprung_ok: bool) -> bool:
    """True, wenn ein Browser die Anfrage schickt (ausser vom selben Ursprung, falls erlaubt)."""
    seite = request.headers.get("sec-fetch-site", "").strip().lower()
    if gleicher_ursprung_ok and seite == "same-origin":
        return False
    return bool(seite) or "origin" in request.headers


def _abgewiesen() -> JSONResponse:
    return JSONResponse({"ok": False, "success": False, "grund": "nur_lokal"}, status_code=403)


@router.get("/api/desktop/klicks")
def desktop_klicks(request: Request, seit: Optional[float] = None) -> JSONResponse:
    if not host_ist_lokal(request) or aus_einer_webseite(request, gleicher_ursprung_ok=True):
        return _abgewiesen()
    if seit is not None and not math.isfinite(seit):
        return JSONResponse({"success": False, "grund": "eingabe_ungueltig"}, status_code=400)
    from core.desktop_klicks import klicks
    return JSONResponse(klicks(seit))


@router.post("/api/desktop/hand-klick")
async def desktop_hand_klick(request: Request) -> JSONResponse:
    if not host_ist_lokal(request) or aus_einer_webseite(request, gleicher_ursprung_ok=False):
        return _abgewiesen()
    try:
        daten: Any = await request.json()
    except Exception:  # noqa: BLE001
        daten = None
    if not isinstance(daten, dict) or set(daten) != {"x", "y"}:
        return JSONResponse({"ok": False, "grund": "eingabe_ungueltig"}, status_code=400)
    from starlette.concurrency import run_in_threadpool
    from core.desktop_hand import beleg_pruefen, klicken
    from core.desktop_tagesrest import KOPF_EINGABE
    # Entscheidend ist das Zeichen der App (gemeinsamer Schluessel), nicht Host-Kopf oder Absender:
    # die lassen sich vortaeuschen, sobald der Port des Gehirns von aussen erreichbar ist.
    if not beleg_pruefen(daten.get("x"), daten.get("y"), request.headers.get(KOPF_EINGABE)):
        if not all(isinstance(daten.get(k), int) and not isinstance(daten.get(k), bool) for k in ("x", "y")):
            return JSONResponse({"ok": False, "grund": "eingabe_ungueltig"}, status_code=400)
        return JSONResponse({"ok": False, "success": False, "grund": "nicht_belegt"}, status_code=403)
    ergebnis = await run_in_threadpool(klicken, daten.get("x"), daten.get("y"))
    status = 400 if ergebnis.get("grund") == "eingabe_ungueltig" else 200
    return JSONResponse(ergebnis, status_code=status)
