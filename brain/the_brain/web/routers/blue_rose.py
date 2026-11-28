"""Blue Rose als Personal Space, Stufe 1 (core/blue_rose.py).

* ``GET    /api/blue_rose/uebersicht?tage=14&stunde=<0-23>``  alles fuer die Seite
* ``POST   /api/blue_rose/befinden``       ``{"stimmung", "energie", "stunde"}``
* ``POST   /api/blue_rose/einstellungen``  ``{"analyse": true|false}``
* ``POST   /api/blue_rose/loeschen``       ``{"art": "befinden"|"alt_..."|"alles", "id"?}``

Wer darf: nur der Hauptprozess der App. Drei Bedingungen, alle noetig:

1. Host-Kopf localhost/127.0.0.1/[::1] (gegen DNS-Rebinding);
2. keine Anfrage aus einer Webseite (web/routers/desktop_klicks.py: Browser schicken immer
   ``Sec-Fetch-Site`` oder ``Origin``; das Gehirn antwortet CORS fuer jeden Ursprung). Auch eine
   Seite vom selben Ursprung bekommt nichts: Befinden ist persoenlich;
3. das Zeichen der App im Kopf ``X-Vibemind-Eingabe`` ueber genau diese Anfrage, mit dem
   Einmalwert aus ``X-Blue-Rose-Einmal`` (core/blue_rose.beleg_pruefen). 1 und 2 kann jeder im
   Netz vortaeuschen, wenn der Port von aussen erreichbar ist; darum traegt 3 den Schutz.
   Ohne Schluessel im Gehirn: nie (403 ``nicht_belegt``).

Die Antworten tragen Anzahlen, die fuenf Stimmungswoerter und Zahlen. Nie Satztext.
"""
from __future__ import annotations

import json
import time
from datetime import datetime
from typing import Any, Dict, Optional, Tuple

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from core import blue_rose as _br
from web.routers.desktop_klicks import aus_einer_webseite, host_ist_lokal

router = APIRouter()


async def _pruefen(request: Request) -> Tuple[Optional[JSONResponse], Dict[str, Any]]:
    """(Absage oder None, Koerper als dict). Liest den Koerper genau einmal."""
    if not host_ist_lokal(request) or aus_einer_webseite(request, gleicher_ursprung_ok=False):
        return JSONResponse({"ok": False, "grund": "nur_lokal"}, status_code=403), {}
    roh = await request.body()
    pfad = request.url.path + (f"?{request.url.query}" if request.url.query else "")
    if not _br.beleg_pruefen(request.method, pfad, roh,
                             request.headers.get("x-blue-rose-einmal"),
                             request.headers.get("x-vibemind-eingabe")):
        return JSONResponse({"ok": False, "grund": "nicht_belegt"}, status_code=403), {}
    try:
        daten = json.loads(roh) if roh else {}
    except Exception:  # noqa: BLE001
        daten = {}
    return None, daten if isinstance(daten, dict) else {}


def _alter_s(eintrag: Optional[Dict[str, Any]]) -> Optional[float]:
    try:
        angelegt = datetime.fromisoformat(str(eintrag["angelegt"]).replace("Z", "+00:00"))
        return time.time() - angelegt.timestamp()
    except Exception:  # noqa: BLE001
        return None


_KEIN_SCHLUESSEL = {"ok": False, "grund": "kein_schluessel"}


@router.get("/api/blue_rose/uebersicht")
async def uebersicht(request: Request, tage: int = 14, stunde: Optional[int] = None):
    absage, _ = await _pruefen(request)
    if absage is not None:
        return absage
    client = _br.tabelle_aus_umgebung()
    if client is None:
        return JSONResponse(_KEIN_SCHLUESSEL)
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
    absage, daten = await _pruefen(request)
    if absage is not None:
        return absage
    client = _br.tabelle_aus_umgebung()
    if client is None:
        return JSONResponse(_KEIN_SCHLUESSEL)
    return JSONResponse(await _br.befinden_eintragen(
        client, daten.get("stimmung"), daten.get("energie"), daten.get("stunde")))


@router.post("/api/blue_rose/einstellungen")
async def einstellungen_setzen(request: Request):
    absage, daten = await _pruefen(request)
    if absage is not None:
        return absage
    client = _br.tabelle_aus_umgebung()
    if client is None:
        return JSONResponse(_KEIN_SCHLUESSEL)
    return JSONResponse(await _br.einstellung_setzen(client, "analyse", daten.get("analyse")))


@router.post("/api/blue_rose/loeschen")
async def loeschen(request: Request):
    absage, daten = await _pruefen(request)
    if absage is not None:
        return absage
    client = _br.tabelle_aus_umgebung()
    if client is None:
        return JSONResponse(_KEIN_SCHLUESSEL)
    return JSONResponse(await _br.loeschen(client, daten.get("art"), daten.get("id")))
