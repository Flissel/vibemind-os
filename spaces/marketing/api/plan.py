"""Kampagnenkalender (17.10.2026, Migration 071). Ein Eintrag ist ein PLAN - versendet wird dadurch nichts.

Pult-Routen (X-Pult-Key, sales-ui /marketing/plan): lesen, anlegen (als Betreiber -> geplant), aendern.
Agent-Routen (X-API-Key bzw. X-Agent-Key): lesen und vorschlagen - vom Agenten kommt immer 'vorschlag',
bis ein Mensch im Pult bestaetigt (Betreiber-Entscheid). Schreiben nur ueber die DB-Funktionen."""
from __future__ import annotations

import datetime as _dt
import re

from fastapi import APIRouter, Body, Header, HTTPException, Query

from spaces.marketing.api.bilder import _agent_schluessel
from spaces.marketing.api.pult import _lesen, _schluessel, _schreiben, _uuid_oder_404, lit

pult_router = APIRouter(prefix="/api/pult/plan")
agent_router = APIRouter(prefix="/api/plan")
ARTEN = ("newsletter", "post", "podcast", "video", "aktion", "sonstiges")
STATUS_BETREIBER = ("geplant", "erledigt", "verschoben", "abgesagt")
TITEL_MAX, NOTIZ_MAX = 120, 1000
_MANDANT = re.compile(r"^[a-z][a-z0-9_-]{0,40}$")
_SPALTEN = ("id, mandant, datum, art, titel, notiz, inhalt, status, angelegt_von, erstellt_am, geaendert_am")


def _datum(wert, feld: str = "datum") -> str:
    if not isinstance(wert, str):
        raise HTTPException(422, f"{feld} fehlt (JJJJ-MM-TT)")
    try:
        return _dt.date.fromisoformat(wert).isoformat()
    except ValueError:
        raise HTTPException(422, f"{feld} muss JJJJ-MM-TT sein")


def _mandant(wert, feld: str = "mandant") -> str:
    if not isinstance(wert, str) or not _MANDANT.fullmatch(wert.strip()):
        raise HTTPException(422, f"{feld} fehlt oder ist ungueltig")
    return wert.strip()


def _eintrag_pruefen(payload: dict, mandant_feld: str) -> tuple:
    mandant = _mandant(payload.get(mandant_feld), mandant_feld)
    datum = _datum(payload.get("datum"))
    art = payload.get("art")
    if art not in ARTEN:
        raise HTTPException(422, f"art muss eine von {', '.join(ARTEN)} sein")
    titel, notiz = payload.get("titel"), payload.get("notiz", "") or ""
    if not isinstance(titel, str) or not 1 <= len(titel.strip()) <= TITEL_MAX:
        raise HTTPException(422, f"titel: 1 bis {TITEL_MAX} Zeichen")
    if not isinstance(notiz, str) or len(notiz) > NOTIZ_MAX:
        raise HTTPException(422, f"notiz: hoechstens {NOTIZ_MAX} Zeichen")
    inhalt = payload.get("inhalt")
    inhalt_sql = f"{lit(_uuid_oder_404(inhalt))}::uuid" if inhalt else "NULL"
    return mandant, datum, art, titel.strip(), notiz, inhalt_sql


def _anlegen(payload: dict, mandant_feld: str, von: str) -> str:
    mandant, datum, art, titel, notiz, inhalt_sql = _eintrag_pruefen(payload, mandant_feld)
    zeile = _schreiben(lambda:
        f"SELECT marketing.plan_anlegen({lit(mandant)}, {lit(datum)}::date, {lit(art)}, {lit(titel)}, "
        f"{lit(notiz)}, {inhalt_sql}, {lit(von)}) AS id")
    return str(zeile.get("id"))


@pult_router.get("")
def pult_lesen(mandant: str = Query(...), von: str = Query(""), bis: str = Query(""),
               x_pult_key: str | None = Header(None)):
    _schluessel(x_pult_key)
    m = _mandant(mandant)
    heute = _dt.date.today()
    v = _datum(von, "von") if von else heute.replace(day=1).isoformat()
    b = _datum(bis, "bis") if bis else (heute + _dt.timedelta(days=62)).isoformat()
    return {"eintraege": _lesen(lambda:
        f"SELECT {_SPALTEN} FROM marketing.kampagnen_plan WHERE mandant = {lit(m)} "
        f"AND datum BETWEEN {lit(v)}::date AND {lit(b)}::date ORDER BY datum, erstellt_am LIMIT 500")}


@pult_router.post("")
def pult_anlegen(payload: dict = Body(...), x_pult_key: str | None = Header(None)):
    _schluessel(x_pult_key)
    return {"id": _anlegen(payload, "mandant", "betreiber")}


@pult_router.post("/{pid}")
def pult_aendern(pid: str, payload: dict = Body(...), x_pult_key: str | None = Header(None)):
    _schluessel(x_pult_key)
    p = _uuid_oder_404(pid)
    status = payload.get("status")
    if status is not None and status not in STATUS_BETREIBER:
        raise HTTPException(422, f"status muss eine von {', '.join(STATUS_BETREIBER)} sein")
    datum = _datum(payload["datum"]) if payload.get("datum") else None
    titel, notiz = payload.get("titel"), payload.get("notiz")
    if titel is not None and (not isinstance(titel, str) or not 1 <= len(titel.strip()) <= TITEL_MAX):
        raise HTTPException(422, f"titel: 1 bis {TITEL_MAX} Zeichen")
    if notiz is not None and (not isinstance(notiz, str) or len(notiz) > NOTIZ_MAX):
        raise HTTPException(422, f"notiz: hoechstens {NOTIZ_MAX} Zeichen")
    inhalt = payload.get("inhalt")

    def opt(wert, typ: str = "") -> str:
        return "NULL" if wert is None else f"{lit(wert)}{typ}"
    zeile = _schreiben(lambda:
        f"SELECT marketing.plan_aendern({lit(p)}::uuid, {opt(status)}, {opt(datum, '::date')}, {opt(titel)}, "
        f"{opt(notiz)}, {opt(_uuid_oder_404(inhalt) if inhalt else None, '::uuid')}) AS s")
    return {"status": zeile.get("s")}


@agent_router.get("")
def agent_lesen(firma: str = Query(...), tage: int = Query(30, ge=1, le=120),
                x_agent_key: str | None = Header(None)):
    _agent_schluessel(x_agent_key)
    m = _mandant(firma, "firma")
    return {"eintraege": _lesen(lambda:
        f"SELECT {_SPALTEN} FROM marketing.kampagnen_plan WHERE mandant = {lit(m)} "
        f"AND datum BETWEEN current_date AND current_date + {int(tage)} "
        "AND status IN ('vorschlag','geplant','verschoben') ORDER BY datum LIMIT 200")}


@agent_router.post("/vorschlag")
def agent_vorschlag(payload: dict = Body(...), x_agent_key: str | None = Header(None)):
    """Der Agent schlaegt vor; ein mitgeschickter status wird ignoriert - es ist immer 'vorschlag'."""
    _agent_schluessel(x_agent_key)
    return {"id": _anlegen(payload, "firma", "agent"), "status": "vorschlag"}
