"""Reels aus KI-Bildern (23.10.2026, Migration 090, Plan 2026-10-23-reels-stufe3-ki-bilder, E5/E6).

Arbeiter-Routen (X-Bild-Key; der Praefix /api/video/arbeiter/ ist vom globalen X-API-Key ausgenommen):
der Bild-Arbeiter am PC holt Auftraege bei den Bildideen ab, schreibt die Ideen zurueck, erzeugt dann Bild
fuer Bild und laedt jedes als rohes JPEG hoch. Pult-Routen (X-Pult-Key, mit Firmenpruefung): der Betreiber
laesst einzelne Bilder neu erzeugen und gibt den Reel-Bau frei. Die Regeln stehen in den SQL-Funktionen; die
API prueft Form, Firma und Datei.
"""
from __future__ import annotations

import json
import os
import re

from fastapi import APIRouter, Body, Header, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from spaces.marketing.api import medien_mandant
from spaces.marketing.api.bilder import _auftrag_id, _bild_schluessel, _jpeg_pruefen, _ordner
from spaces.marketing.api.pult import _lesen_einer, _schluessel, _schreiben, _uuid_oder_404, lit
from spaces.marketing.api.video import KI_MAX, KI_MIN, _ablegen, _auftrag_der_firma, _mandant

router = APIRouter(prefix="/api/video/arbeiter")
pult_router = APIRouter(prefix="/api/pult/video")

FRIST_IDEEN = "10 minutes"          # ein Aufruf an die Video-Shim, Ausweichweg Ollama (E3)
FRIST_BILD = "10 minutes"           # je Bild, der Arbeiter verlaengert per weiter (E9)
BILD_MAX = 1024 * 1024              # rohes JPEG je Reel-Bild (E4)
IDEE_MAX = 600                      # wie reel_bilder.idee (090)
BEFUND_MAX = 500
_NAME = re.compile(r"^ki-[0-9a-f]{8}-[1-5]-[0-9]{1,3}\.jpg$")   # wie der Check in 090


def _bild_id(wert: str) -> str:
    try:
        return _auftrag_id(wert)
    except HTTPException:
        raise HTTPException(404, "Unbekanntes Bild")


def _befund(payload) -> str:
    befund = payload.get("befund", "") if isinstance(payload, dict) else ""
    befund = "" if befund is None else befund
    if not isinstance(befund, str):
        raise HTTPException(422, "befund muss Text sein")
    return befund.strip()[:BEFUND_MAX]


# --- Arbeiter: Bildideen -----------------------------------------------------------------------------------

@router.post("/ideen/naechster")
def ideen_naechster(x_bild_key: str | None = Header(None)):
    """Naechster Auftrag bei den Bildideen ({id, mandant, titel, briefing}) oder None."""
    _bild_schluessel(x_bild_key)
    zeile = _schreiben(lambda: f"SELECT marketing.video_ideen_naechster({lit(FRIST_IDEEN)}::interval) AS a")
    return {"auftrag": zeile.get("a")}


@router.post("/{aid}/ideen")
def ideen_setzen(aid: str, payload: dict = Body(...), x_bild_key: str | None = Header(None)):
    """{ideen: [3..5 Texte]} - die Anzahl muss zu briefing.ki.anzahl passen (prueft 090, sonst 422)."""
    _bild_schluessel(x_bild_key)
    a = _auftrag_id(aid)
    ideen = payload.get("ideen")
    if (not isinstance(ideen, list) or not KI_MIN <= len(ideen) <= KI_MAX
            or any(not isinstance(i, str) or not 1 <= len(i.strip()) <= IDEE_MAX for i in ideen)):
        raise HTTPException(422, f"ideen: {KI_MIN} bis {KI_MAX} Texte mit 1 bis {IDEE_MAX} Zeichen")
    roh = json.dumps([i.strip() for i in ideen], ensure_ascii=False)
    zeile = _schreiben(lambda: f"SELECT marketing.video_ideen_setzen({lit(a)}::uuid, {lit(roh)}::jsonb) AS n")
    return {"anzahl": zeile.get("n")}


@router.post("/{aid}/ideen-fehler")
def ideen_fehler(aid: str, payload: dict | None = Body(None), x_bild_key: str | None = Header(None)):
    """Ideen gescheitert: neuer Status (ideen = nochmal abholbar, fehler = aufgegeben) oder None, wenn der
    Auftrag nicht mehr bei den Ideen steht (z. B. abgebrochen)."""
    _bild_schluessel(x_bild_key)
    a = _auftrag_id(aid)
    befund = _befund(payload)
    zeile = _schreiben(lambda: f"SELECT marketing.video_ideen_fehler({lit(a)}::uuid, {lit(befund)}) AS s")
    return {"status": zeile.get("s")}


# --- Arbeiter: Reel-Bilder ---------------------------------------------------------------------------------

@router.post("/reel-bild/naechster")
def reel_bild_naechster(x_bild_key: str | None = Header(None)):
    """Naechstes Bild {id, video_auftrag, nr, idee, versuch, mandant, format, titel} oder None."""
    _bild_schluessel(x_bild_key)
    zeile = _schreiben(lambda: f"SELECT marketing.reel_bild_naechster({lit(FRIST_BILD)}::interval) AS b")
    return {"bild": zeile.get("b")}


@router.post("/reel-bild/{bid}/weiter")
def reel_bild_weiter(bid: str, x_bild_key: str | None = Header(None)):
    """Frist verlaengern; ok false = aufhoeren (Bild nicht mehr in Arbeit oder Auftrag laeuft nicht mehr)."""
    _bild_schluessel(x_bild_key)
    b = _bild_id(bid)
    zeile = _schreiben(lambda:
        f"SELECT marketing.reel_bild_verlaengern({lit(b)}::uuid, {lit(FRIST_BILD)}::interval) AS ok")
    return {"ok": bool(zeile.get("ok"))}


@router.post("/reel-bild/{bid}/fehler")
def reel_bild_fehler(bid: str, payload: dict | None = Body(None), x_bild_key: str | None = Header(None)):
    _bild_schluessel(x_bild_key)
    b = _bild_id(bid)
    befund = _befund(payload)
    zeile = _schreiben(lambda: f"SELECT marketing.reel_bild_fehler({lit(b)}::uuid, {lit(befund)}) AS ok")
    return {"ok": bool(zeile.get("ok"))}


@router.post("/reel-bild/{bid}")
async def reel_bild_hochladen(bid: str, request: Request, x_bild_key: str | None = Header(None)):
    """Rohes JPEG (hoechstens 1 MiB) fuer ein Bild in Arbeit. Name ki-<auftrag8>-<nr>-<versuch>.jpg aus der
    Zeile (je Versuch neu), der Firma des Auftrags zugeordnet, dann reel_bild_fertig."""
    _bild_schluessel(x_bild_key)
    b = _bild_id(bid)
    try:
        laenge = int(request.headers.get("content-length") or 0)
    except ValueError:
        laenge = 0
    if laenge > BILD_MAX:
        raise HTTPException(413, "Bild groesser als 1 MiB")
    roh = bytearray()
    async for stueck in request.stream():
        roh += stueck
        if len(roh) > BILD_MAX:
            raise HTTPException(413, "Bild groesser als 1 MiB")
    return await run_in_threadpool(_bild_speichern, b, bytes(roh))


def _bild_speichern(b: str, roh: bytes) -> dict:
    _jpeg_pruefen(roh)
    ordner = _ordner()
    zeile = _lesen_einer(lambda:
        "SELECT r.status, r.nr, r.versuch, r.video_auftrag, a.mandant, a.status AS auftrag_status "
        "FROM marketing.reel_bilder r JOIN marketing.video_auftraege a ON a.id = r.video_auftrag "
        f"WHERE r.id = {lit(b)}::uuid")
    if not zeile:
        raise HTTPException(404, "Unbekanntes Bild")
    # Nur ein Bild in Arbeit eines laufenden Auftrags: eine fertige Datei wird nie ueberschrieben.
    if zeile.get("status") != "in_arbeit" or zeile.get("auftrag_status") != "bilder_laufen":
        raise HTTPException(409, "Bild ist nicht in Arbeit")
    name = f"ki-{str(zeile.get('video_auftrag'))[:8]}-{zeile.get('nr')}-{zeile.get('versuch')}.jpg"
    if not _NAME.fullmatch(name):
        raise HTTPException(409, "Bild passt nicht zum Auftrag")
    # Erst der Firma zuordnen, dann ablegen: nie ein Zeitfenster, in dem die Datei als Gemeinsam sichtbar ist.
    medien_mandant.zuordnen([name], str(zeile.get("mandant")))
    _ablegen(ordner, name, roh, "Bild konnte nicht abgelegt werden")
    try:
        fertig = _schreiben(lambda: f"SELECT marketing.reel_bild_fertig({lit(b)}::uuid, {lit(name)}) AS ok")
    except HTTPException as e:
        if e.status_code == 422:        # klare Ablehnung: Datei weg. 503 = Ausgang unbekannt -> Datei bleibt
            _entfernen(ordner, name)
        raise
    if not fertig.get("ok"):
        _entfernen(ordner, name)
        raise HTTPException(409, "Bild ist nicht mehr in Arbeit")
    return {"name": name}


def _entfernen(ordner: str, name: str) -> None:
    try:
        os.remove(os.path.join(ordner, name))
    except OSError:
        pass


# --- Pult (Betreiber) ----------------------------------------------------------------------------------

def _pult_schreiben(sql) -> dict:
    """Wie _schreiben; die Firmen-Rueckhalte der SQL-Funktionen ("... nicht gefunden") werden 404."""
    try:
        return _schreiben(sql)
    except HTTPException as e:
        if e.status_code == 422 and "nicht gefunden" in str(e.detail):
            raise HTTPException(404, str(e.detail))
        raise


@pult_router.post("/{aid}/ki/neu")
def pult_ki_neu(aid: str, payload: dict = Body(...), x_pult_key: str | None = Header(None)):
    """{bild, mandant, idee?}: ein fertiges oder fehlgeschlagenes Bild neu erzeugen (leere Idee = dieselbe).
    Auftrag und Bild muessen der Firma gehoeren, sonst 404."""
    _schluessel(x_pult_key)
    idee = payload.get("idee")
    idee = "" if idee is None else idee
    if not isinstance(idee, str) or len(idee.strip()) > IDEE_MAX:
        raise HTTPException(422, f"idee: höchstens {IDEE_MAX} Zeichen")
    mandant = _mandant(payload.get("mandant"), "mandant")
    a = _auftrag_der_firma(aid, mandant)
    bild = payload.get("bild")
    b = _uuid_oder_404(bild if isinstance(bild, str) else "")
    zeile = _lesen_einer(lambda: f"SELECT video_auftrag FROM marketing.reel_bilder WHERE id = {lit(b)}::uuid")
    if not zeile or str(zeile.get("video_auftrag")) != a:
        raise HTTPException(404, "Unbekanntes Bild")
    i = idee.strip()
    zeile = _pult_schreiben(lambda:
        f"SELECT marketing.reel_bild_neu({lit(b)}::uuid, {lit(mandant)}, {lit(i) if i else 'NULL'}) AS id")
    return {"bild": str(zeile.get("id"))}


@pult_router.post("/{aid}/ki/freigeben")
def pult_ki_freigeben(aid: str, payload: dict = Body(...), x_pult_key: str | None = Header(None)):
    """{mandant}: die fertigen Bilder (nach nr) werden briefing.bilder, der Auftrag geht auf offen."""
    _schluessel(x_pult_key)
    mandant = _mandant(payload.get("mandant"), "mandant")
    a = _auftrag_der_firma(aid, mandant)
    zeile = _pult_schreiben(lambda: f"SELECT marketing.video_ki_freigeben({lit(a)}::uuid, {lit(mandant)}) AS ok")
    return {"ok": bool(zeile.get("ok"))}
