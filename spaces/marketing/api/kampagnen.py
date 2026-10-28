"""Kampagnen (28.10.2026, Migration 095): Ziel, Zielgruppe, Zeitraum, darunter die Beitraege.

Pult (X-Pult-Key, sales-ui /marketing/plan): lesen (mit Beitraegen und deren Stand), anlegen, Status.
Agent (X-Agent-Key, Kampagnen-Arbeiter und Marketing-Agent): offene Kampagnen lesen, Beitraege
vorschlagen (immer 'vorschlag'), Planung abschliessen. Schreiben nur ueber die DB-Funktionen."""
from __future__ import annotations

import json

from fastapi import APIRouter, Body, Header, HTTPException, Query

from spaces.marketing.api.bilder import _agent_schluessel
from spaces.marketing.api.plan import ARTEN, NOTIZ_MAX, TITEL_MAX, _datum, _mandant
from spaces.marketing.api.pult import _lesen, _schluessel, _schreiben, _uuid_oder_404, lit

pult_router = APIRouter(prefix="/api/pult/kampagnen")
agent_router = APIRouter(prefix="/api/plan/kampagnen")
ZIELGRUPPEN = ("sales", "oeffentlich", "verteiler")
STATUS = ("in_planung", "geplant", "laeuft", "abgeschlossen", "abgesagt")
NAME_MAX, ZIEL_MAX = 120, 2000
_SPALTEN = "id, mandant, name, ziel, zielgruppe, filter, von, bis, status, angelegt_von, erstellt_am"


def _beitraege(ids: list[str]) -> dict[str, list]:
    if not ids:
        return {}
    liste = ", ".join(f"{lit(i)}::uuid" for i in ids)
    zeilen = _lesen(lambda:
        "SELECT p.id, p.kampagne, p.datum, p.art, p.titel, p.notiz, p.status, p.inhalt, "
        "       i.status AS inhalt_status "
        "  FROM marketing.kampagnen_plan p LEFT JOIN marketing.inhalte i ON i.id = p.inhalt "
        f" WHERE p.kampagne IN ({liste}) ORDER BY p.datum, p.erstellt_am")
    je: dict[str, list] = {}
    for z in zeilen:
        je.setdefault(str(z["kampagne"]), []).append(z)
    return je


@pult_router.get("")
def pult_lesen(mandant: str = Query(...), x_pult_key: str | None = Header(None)):
    _schluessel(x_pult_key)
    m = _mandant(mandant)
    kampagnen = _lesen(lambda:
        f"SELECT {_SPALTEN} FROM marketing.kampagnen WHERE mandant = {lit(m)} "
        "ORDER BY (status IN ('abgeschlossen','abgesagt')), von DESC LIMIT 100")
    je = _beitraege([str(k["id"]) for k in kampagnen])
    for k in kampagnen:
        k["beitraege"] = je.get(str(k["id"]), [])
    return {"kampagnen": kampagnen}


@pult_router.post("")
def pult_anlegen(payload: dict = Body(...), x_pult_key: str | None = Header(None)):
    _schluessel(x_pult_key)
    m = _mandant(payload.get("mandant"))
    name = payload.get("name")
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= NAME_MAX:
        raise HTTPException(422, f"name: 1 bis {NAME_MAX} Zeichen")
    ziel = payload.get("ziel") or ""
    if not isinstance(ziel, str) or len(ziel) > ZIEL_MAX:
        raise HTTPException(422, f"ziel: hoechstens {ZIEL_MAX} Zeichen")
    gruppe = payload.get("zielgruppe")
    if gruppe not in ZIELGRUPPEN:
        raise HTTPException(422, f"zielgruppe muss eine von {', '.join(ZIELGRUPPEN)} sein")
    filt = payload.get("filter") or {}
    if not isinstance(filt, dict) or len(json.dumps(filt)) > 2000:
        raise HTTPException(422, "filter muss ein kleines Objekt sein")
    von, bis = _datum(payload.get("von"), "von"), _datum(payload.get("bis"), "bis")
    wer = str(payload.get("von_wer") or "betreiber").strip()[:100] or "betreiber"
    zeile = _schreiben(lambda:
        f"SELECT marketing.kampagne_anlegen({lit(m)}, {lit(name.strip())}, {lit(ziel)}, {lit(gruppe)}, "
        f"{lit(json.dumps(filt))}::jsonb, {lit(von)}::date, {lit(bis)}::date, {lit(wer)}) AS id")
    return {"id": str(zeile.get("id")), "status": "in_planung"}


@pult_router.post("/{kid}/status")
def pult_status(kid: str, payload: dict = Body(...), x_pult_key: str | None = Header(None)):
    _schluessel(x_pult_key)
    k = _uuid_oder_404(kid)
    status = payload.get("status")
    if status not in STATUS:
        raise HTTPException(422, f"status muss einer von {', '.join(STATUS)} sein")
    zeile = _schreiben(lambda: f"SELECT marketing.kampagne_status({lit(k)}::uuid, {lit(status)}) AS s")
    return {"status": zeile.get("s")}


@agent_router.get("")
def agent_lesen(status: str = Query("in_planung"), x_agent_key: str | None = Header(None)):
    """Kampagnen eines Status ueber alle Firmen - der Arbeiter plant die 'in_planung'."""
    _agent_schluessel(x_agent_key)
    if status not in STATUS:
        raise HTTPException(422, "unbekannter status")
    kampagnen = _lesen(lambda:
        f"SELECT {_SPALTEN} FROM marketing.kampagnen WHERE status = {lit(status)} "
        "ORDER BY erstellt_am LIMIT 20")
    je = _beitraege([str(k["id"]) for k in kampagnen])
    for k in kampagnen:
        k["beitraege"] = je.get(str(k["id"]), [])
    return {"kampagnen": kampagnen}


@agent_router.post("/{kid}/beitrag")
def agent_beitrag(kid: str, payload: dict = Body(...), x_agent_key: str | None = Header(None)):
    _agent_schluessel(x_agent_key)
    k = _uuid_oder_404(kid)
    datum = _datum(payload.get("datum"))
    art = payload.get("art")
    if art not in ARTEN:
        raise HTTPException(422, f"art muss eine von {', '.join(ARTEN)} sein")
    titel, notiz = payload.get("titel"), payload.get("notiz", "") or ""
    if not isinstance(titel, str) or not 1 <= len(titel.strip()) <= TITEL_MAX:
        raise HTTPException(422, f"titel: 1 bis {TITEL_MAX} Zeichen")
    if not isinstance(notiz, str) or len(notiz) > NOTIZ_MAX:
        raise HTTPException(422, f"notiz: hoechstens {NOTIZ_MAX} Zeichen")
    zeile = _schreiben(lambda:
        f"SELECT marketing.kampagne_beitrag({lit(k)}::uuid, {lit(datum)}::date, {lit(art)}, "
        f"{lit(titel.strip())}, {lit(notiz)}, 'agent') AS id")
    return {"id": str(zeile.get("id")), "status": "vorschlag"}


@agent_router.post("/{kid}/geplant")
def agent_geplant(kid: str, x_agent_key: str | None = Header(None)):
    _agent_schluessel(x_agent_key)
    k = _uuid_oder_404(kid)
    zeile = _schreiben(lambda: f"SELECT marketing.kampagne_status({lit(k)}::uuid, 'geplant') AS s")
    return {"status": zeile.get("s")}
