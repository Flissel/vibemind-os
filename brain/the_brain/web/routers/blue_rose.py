"""Blue Rose als Personal Space, Stufe 1 (core/blue_rose.py).

* ``GET    /api/blue_rose/uebersicht?tage=14&stunde=<0-23>``  alles fuer die Seite
* ``POST   /api/blue_rose/befinden``       ``{"stimmung", "energie", "stunde"}``
* ``POST   /api/blue_rose/einstellungen``  ``{"analyse": true|false}``
* ``POST   /api/blue_rose/loeschen``       ``{"art": "befinden"|"alt_..."|"alles", "id"?}``

Wer darf: nur der Hauptprozess der App am eigenen Rechner. Host-Kopf localhost/127.0.0.1/[::1]
und keine Anfrage aus einer Webseite (web/routers/desktop_klicks.py: Browser schicken immer
``Sec-Fetch-Site`` oder ``Origin``; das Gehirn antwortet CORS fuer jeden Ursprung, darum zaehlt
die Herkunft). Auch eine Seite vom selben Ursprung bekommt nichts: Befinden ist persoenlich.

Die Antworten tragen Anzahlen, die fuenf Stimmungswoerter und Zahlen. Nie Satztext.
"""
from __future__ import annotations

import time
from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from core import blue_rose as _br
from web.routers.desktop_klicks import aus_einer_webseite, host_ist_lokal

router = APIRouter()


def _darf(request: Request) -> bool:
    return host_ist_lokal(request) and not aus_einer_webseite(request, gleicher_ursprung_ok=False)


def _abgewiesen() -> JSONResponse:
    return JSONResponse({"ok": False, "grund": "nur_lokal"}, status_code=403)


def _alter_s(eintrag: Optional[Dict[str, Any]]) -> Optional[float]:
    try:
        angelegt = datetime.fromisoformat(str(eintrag["angelegt"]).replace("Z", "+00:00"))
        return time.time() - angelegt.timestamp()
    except Exception:  # noqa: BLE001
        return None


async def _koerper(request: Request) -> Dict[str, Any]:
    try:
        daten = await request.json()
    except Exception:  # noqa: BLE001
        return {}
    return daten if isinstance(daten, dict) else {}


@router.get("/api/blue_rose/uebersicht")
async def uebersicht(request: Request, tage: int = 14, stunde: Optional[int] = None):
    if not _darf(request):
        return _abgewiesen()
    client = _br.tabelle_aus_umgebung()
    if client is None:
        return JSONResponse({"ok": False, "grund": "kein_schluessel"})
    einstellungen = await _br.einstellungen_lesen(client)
    befinden = await _br.befinden_liste(client)
    out: Dict[str, Any] = {
        "ok": True,
        "einstellungen": einstellungen,
        "befinden": befinden,
        "bestand": await _br.bestand(client),
        "stimmungen": list(_br.STIMMUNGEN),
    }
    if einstellungen.get("analyse") is True:
        import asyncio

        graph = getattr(request.app.state, "decision_graph", None)
        out["zahlen"] = await asyncio.get_running_loop().run_in_executor(
            None, lambda: _br.zahlen_lesen(graph, tage=tage))
    else:
        out["zahlen"] = {"ok": False, "grund": "analyse_aus"}
    letzter = (befinden.get("eintraege") or [None])[0]
    out["empfehlung"] = (_br.empfehlung(letzter, stunde, alter_s=_alter_s(letzter))
                         if _br.stunde_gueltig(stunde) else {"ok": False, "grund": "stunde_fehlt"})
    return JSONResponse(out)


@router.post("/api/blue_rose/befinden")
async def befinden_eintragen(request: Request):
    if not _darf(request):
        return _abgewiesen()
    client = _br.tabelle_aus_umgebung()
    if client is None:
        return JSONResponse({"ok": False, "grund": "kein_schluessel"})
    daten = await _koerper(request)
    return JSONResponse(await _br.befinden_eintragen(
        client, daten.get("stimmung"), daten.get("energie"), daten.get("stunde")))


@router.post("/api/blue_rose/einstellungen")
async def einstellungen_setzen(request: Request):
    if not _darf(request):
        return _abgewiesen()
    client = _br.tabelle_aus_umgebung()
    if client is None:
        return JSONResponse({"ok": False, "grund": "kein_schluessel"})
    daten = await _koerper(request)
    return JSONResponse(await _br.einstellung_setzen(client, "analyse", daten.get("analyse")))


@router.post("/api/blue_rose/loeschen")
async def loeschen(request: Request):
    if not _darf(request):
        return _abgewiesen()
    client = _br.tabelle_aus_umgebung()
    if client is None:
        return JSONResponse({"ok": False, "grund": "kein_schluessel"})
    daten = await _koerper(request)
    return JSONResponse(await _br.loeschen(client, daten.get("art"), daten.get("id")))
