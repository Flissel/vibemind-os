"""Pult-Router der Marketing-API (sales-claw Spec 2026-09-29-marketing-pult-
design.md §3.4). Einziger Gespraechspartner: die Sales-Oberflaeche, mit Header
X-Pult-Key. Ohne konfigurierten Schluessel ist der Router zu (503), nie offen.
Regeln (Fassungen unveraenderlich, ein Urteil, Gestalt-Pruefung) stehen in den
DB-Funktionen aus 050/051 -- hier nur Formen und Weitergabe.

Abweichung vom Brief (task-3-brief.md, siehe task-3-report.md): 051 aenderte
marketing.pult_entscheiden auf die 5-arg-Form (p_inhalt, p_fassung, p_urteil,
p_von, p_grund) -- die frueher geplante 4-arg-Form gibt es nicht mehr. Die
Fassung, die der Mensch tatsaechlich gesehen hat, kommt jetzt als Pflichtfeld
"fassung" im Entscheiden-Body und wird VOR jedem DB-Zugriff als int >= 1
geprueft (422 ohne SQL bei fehlend/ungueltig) -- die DB-Funktion selbst weist
danach noch eine inzwischen veraltete Fassung zurueck (422 mit ihrem
deutschen Grund)."""
from __future__ import annotations

import hmac
import json
import os
import re
import uuid as _uuid

from fastapi import APIRouter, Body, Header, HTTPException, Query
from fastapi.responses import HTMLResponse, Response

from spaces.marketing.claw import pult_render
from spaces.marketing.sync import _db

router = APIRouter(prefix="/api/pult")
_ARTEN = ("newsletter", "post", "material")
_STATUS = ("entwurf", "freigegeben", "abgelehnt")
_FORMATE = ("mail", "handy", "pdf")
_NAME = re.compile(r"^[a-z][a-z0-9_-]{0,40}$")


def _schluessel(x_pult_key: str | None) -> None:
    erwartet = os.environ.get("MARKETING_PULT_KEY", "").strip()
    if not erwartet:
        raise HTTPException(503, "misconfigured: MARKETING_PULT_KEY fehlt")
    if not x_pult_key or not hmac.compare_digest(x_pult_key.strip(), erwartet):
        raise HTTPException(401, "Pult-Schluessel fehlt oder falsch")


def _uuid_oder_404(wert: str) -> str:
    try:
        return str(_uuid.UUID(wert))
    except ValueError:
        raise HTTPException(404, "Unbekannter Inhalt")


def _auswahl(wert: str | None, erlaubt: tuple, name: str) -> str | None:
    if wert is None or wert == "":
        return None
    if wert not in erlaubt:
        raise HTTPException(422, f"{name} muss eines von {', '.join(erlaubt)} sein")
    return wert


def _mandant(wert: str | None) -> str:
    wert = wert or "vibemind"
    if not _NAME.match(wert):
        raise HTTPException(422, "Unbekannter Mandant")
    return wert


def _fassung_pflicht(wert) -> int:
    if isinstance(wert, bool) or not isinstance(wert, int) or wert < 1:
        raise HTTPException(422, "fassung fehlt oder muss eine ganze Zahl >= 1 sein")
    return wert


def _db_grund(fehler: Exception) -> str:
    text = str(fehler)
    treffer = re.search(r"ERROR:\s*(.+)", text)
    return (treffer.group(1) if treffer else text).strip().splitlines()[0][:300]


def _db_einer(sql: str) -> dict | None:
    try:
        return _db.query_one(sql, streng=True)
    except Exception as e:  # DB lehnt ab -> deutscher Grund an den Aufrufer
        raise HTTPException(422, _db_grund(e))


lit = _db._sql_literal


@router.get("/uebersicht")
def uebersicht(mandant: str | None = None, x_pult_key: str | None = Header(None)):
    _schluessel(x_pult_key)
    m = _mandant(mandant)
    mandanten = _db.query_via_docker(
        "SELECT id, name, aktiv FROM marketing.mandanten ORDER BY aktiv DESC, name")
    zahlen = _db.query_via_docker(
        f"SELECT status, count(*)::int AS n FROM marketing.inhalte WHERE mandant = {lit(m)} GROUP BY status")
    zaehler = {s: 0 for s in _STATUS}
    zaehler.update({z["status"]: z["n"] for z in zahlen})
    return {"mandanten": mandanten, "zaehler": zaehler}


@router.get("/inhalte")
def inhalte(mandant: str | None = None, art: str | None = None, status: str | None = None,
            x_pult_key: str | None = Header(None)):
    _schluessel(x_pult_key)
    m = _mandant(mandant)
    wo = [f"i.mandant = {lit(m)}"]
    if (a := _auswahl(art, _ARTEN, "art")):
        wo.append(f"i.art = {lit(a)}")
    if (s := _auswahl(status, _STATUS, "status")):
        wo.append(f"i.status = {lit(s)}")
    zeilen = _db.query_via_docker(
        "SELECT i.id, i.art, i.titel, i.status, i.erstellt_am::text AS erstellt_am, "
        "  (SELECT count(*) FROM marketing.inhalt_fassungen f WHERE f.inhalt = i.id)::int AS fassungen, "
        "  (SELECT f.layout FROM marketing.inhalt_fassungen f WHERE f.inhalt = i.id "
        "     ORDER BY f.fassung DESC LIMIT 1) AS layout "
        f"FROM marketing.inhalte i WHERE {' AND '.join(wo)} ORDER BY i.erstellt_am DESC")
    return {"inhalte": zeilen}


@router.get("/inhalte/{iid}")
def inhalt(iid: str, x_pult_key: str | None = Header(None)):
    _schluessel(x_pult_key)
    i = _uuid_oder_404(iid)
    kopf = _db.query_one(
        "SELECT id, mandant, art, titel, status, freigegebene_fassung, entschieden_von, "
        f"entschieden_am::text AS entschieden_am, grund FROM marketing.inhalte WHERE id = {lit(i)}::uuid")
    if not kopf:
        raise HTTPException(404, "Unbekannter Inhalt")
    fassungen = _db.query_via_docker(
        "SELECT fassung, felder, layout, urheber, erstellt_am::text AS erstellt_am "
        f"FROM marketing.inhalt_fassungen WHERE inhalt = {lit(i)}::uuid ORDER BY fassung DESC")
    return {"inhalt": kopf, "fassungen": fassungen}


@router.post("/inhalte/{iid}/fassungen")
def fassung_speichern(iid: str, payload: dict = Body(...), x_pult_key: str | None = Header(None)):
    _schluessel(x_pult_key)
    i = _uuid_oder_404(iid)
    felder = payload.get("felder")
    if not isinstance(felder, dict):
        raise HTTPException(422, "felder fehlt")
    zeile = _db_einer(
        f"SELECT marketing.pult_fassung_speichern({lit(i)}::uuid, "
        f"{lit(json.dumps(felder, ensure_ascii=False))}::jsonb, "
        f"{lit(str(payload.get('layout') or ''))}, 'betreiber') AS fassung")
    return {"fassung": int(zeile["fassung"])}


@router.get("/inhalte/{iid}/vorschau")
def vorschau(iid: str, fassung: int = Query(..., ge=1), format: str = "mail",
             x_pult_key: str | None = Header(None)):
    _schluessel(x_pult_key)
    i = _uuid_oder_404(iid)
    fmt = _auswahl(format, _FORMATE, "format")
    zeile = _db.query_one(
        "SELECT f.felder, l.gestalt, m.pflichtteil FROM marketing.inhalt_fassungen f "
        "JOIN marketing.inhalte i ON i.id = f.inhalt "
        "JOIN marketing.mandanten m ON m.id = i.mandant "
        "JOIN marketing.layout_vorlagen l ON l.name = f.layout "
        f"WHERE f.inhalt = {lit(i)}::uuid AND f.fassung = {int(fassung)}")
    if not zeile:
        raise HTTPException(404, "Unbekannte Fassung")
    return _rendern(zeile["felder"], zeile["gestalt"], zeile["pflichtteil"], fmt)


def _rendern(felder: dict, gestalt: dict, pflichtteil: dict, fmt: str) -> Response:
    if fmt == "pdf":
        return Response(pult_render.pdf_bytes(felder, gestalt), media_type="application/pdf")
    baue = pult_render.handy_html if fmt == "handy" else pult_render.mail_html
    return HTMLResponse(baue(felder, gestalt, pflichtteil))


@router.post("/inhalte/{iid}/entscheiden")
def entscheiden(iid: str, payload: dict = Body(...), x_pult_key: str | None = Header(None)):
    _schluessel(x_pult_key)
    i = _uuid_oder_404(iid)
    f = _fassung_pflicht(payload.get("fassung"))
    zeile = _db_einer(
        f"SELECT marketing.pult_entscheiden({lit(i)}::uuid, {f}, {lit(str(payload.get('urteil') or ''))}, "
        f"{lit(str(payload.get('von') or ''))}, {lit(str(payload.get('grund') or ''))}) AS status")
    return {"status": zeile["status"]}


@router.get("/layouts")
def layouts(mandant: str | None = None, x_pult_key: str | None = Header(None)):
    _schluessel(x_pult_key)
    m = _mandant(mandant)
    return {"layouts": _db.query_via_docker(
        "SELECT name, beschreibung, inhaltsart, fassung, standard, status, gestalt "
        f"FROM marketing.layout_vorlagen WHERE art = 'layout' AND mandant = {lit(m)} "
        "ORDER BY inhaltsart, standard DESC, name")}


@router.post("/layouts/vorschau")
def layout_vorschau(payload: dict = Body(...), x_pult_key: str | None = Header(None)):
    _schluessel(x_pult_key)
    gestalt = payload.get("gestalt")
    if not isinstance(gestalt, dict):
        raise HTTPException(422, "gestalt fehlt")
    fmt = _auswahl(payload.get("format") or "mail", _FORMATE, "format")
    fehler = _db.query_one(
        f"SELECT marketing.pult_gestalt_fehler({lit(json.dumps(gestalt, ensure_ascii=False))}::jsonb) AS fehler")
    if fehler and fehler.get("fehler"):
        raise HTTPException(422, fehler["fehler"])
    m = _db.query_one(
        f"SELECT pflichtteil FROM marketing.mandanten WHERE id = {lit(_mandant(payload.get('mandant')))}")
    return _rendern(pult_render.BEISPIEL_FELDER, gestalt, (m or {}).get("pflichtteil") or {}, fmt)


@router.post("/layouts/{name}/fassungen")
def layout_speichern(name: str, payload: dict = Body(...), x_pult_key: str | None = Header(None)):
    _schluessel(x_pult_key)
    if not _NAME.match(name):
        raise HTTPException(404, "Unbekanntes Layout")
    zeile = _db_einer(
        f"SELECT marketing.pult_layout_speichern({lit(name)}, "
        f"{lit(json.dumps(payload.get('gestalt') or {}, ensure_ascii=False))}::jsonb, "
        f"{lit(str(payload.get('von') or 'betreiber'))}) AS fassung")
    return {"fassung": int(zeile["fassung"])}


@router.post("/layouts/{name}/standard")
def layout_standard(name: str, x_pult_key: str | None = Header(None)):
    _schluessel(x_pult_key)
    if not _NAME.match(name):
        raise HTTPException(404, "Unbekanntes Layout")
    _db_einer(f"SELECT marketing.pult_layout_als_standard({lit(name)}) IS NULL AS ok")
    return {"ok": True}
