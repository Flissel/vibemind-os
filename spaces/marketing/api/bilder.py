"""Bild-Auftraege der Newsletter (sales-claw Spec 2026-09-29-newsletter-bilder-
und-gestaltung-design.md §6.2). Drei Gespraechspartner, drei Schluessel:
  /api/pult/inhalte/{iid}/bilder  Sales-Oberflaeche (X-Pult-Key, pult._schluessel)
  /api/bilder/agent/*             Marketing-Agent am PC (X-API-Key, globale Middleware)
  /api/bilder/arbeiter/*          Bild-Arbeiter am PC gegen die VM (X-Bild-Key)
Regeln (Vergabe, Einsetzen, wer gewinnt) stehen in den DB-Funktionen aus 056;
hier nur Formen, Dateiablage und Weitergabe - fail-closed wie pult.py."""
from __future__ import annotations

import hmac
import io
import json
import os
import re
import uuid as _uuid

from fastapi import APIRouter, Body, Header, HTTPException, Query, Request

from fastapi.responses import FileResponse

from spaces.marketing.api import medien_mandant
from spaces.marketing.api.pult import _lesen, _lesen_einer, _schluessel, _schreiben, _uuid_oder_404, lit
from spaces.marketing.claw import bildplaetze

router = APIRouter(prefix="/api/bilder")
pult_router = APIRouter(prefix="/api/pult")
_PLATZ = re.compile(r"[A-Za-z0-9_-]{1,64}")          # nur mit fullmatch benutzen
_NAME = re.compile(r"nl-[0-9a-f]{8}-[A-Za-z0-9_-]{1,64}(?:\.jpg|-frei\.png)")   # nur mit fullmatch benutzen
_BILDTYP = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp"}
_MESSWERTE = ("aehnlich_original", "naeher_am_hinweis")
BILD_MAX = 1024 * 1024
PNG_MAX = 4 * 1024 * 1024
PNG_KOPF = bytes((137, 80, 78, 71, 13, 10, 26, 10))   # PNG-Signatur
KANTE_MIN, KANTE_MAX = 64, 2400
FRIST = "10 minutes"
_STAND_SQL = ("SELECT id, platz, nur_leere, hinweis, status, befund, versuche, urheber, "
              "staerke, modus, messung, "
              "erstellt_am::text AS erstellt_am, geaendert_am::text AS geaendert_am "
              "FROM marketing.bild_auftraege WHERE inhalt = {i}::uuid ORDER BY erstellt_am DESC LIMIT 30")


def _bild_schluessel(x_bild_key: str | None) -> None:
    erwartet = os.environ.get("MARKETING_BILD_KEY", "").strip()
    if not erwartet:
        raise HTTPException(503, "misconfigured: MARKETING_BILD_KEY fehlt")
    k = (x_bild_key or "").strip()
    if not k or not hmac.compare_digest(k.encode("utf-8", "replace"), erwartet.encode("utf-8", "replace")):
        raise HTTPException(401, "Bild-Schluessel fehlt oder falsch")


def _ordner() -> str:
    o = os.environ.get("MARKETING_BILD_ORDNER", "").strip()
    if not o or not os.path.isdir(o):
        raise HTTPException(503, "misconfigured: MARKETING_BILD_ORDNER fehlt")
    if not os.access(o, os.W_OK):
        raise HTTPException(503, "misconfigured: MARKETING_BILD_ORDNER nicht beschreibbar")
    return o


def _agent_schluessel() -> None:
    """Fail-closed: ohne MARKETING_API_KEY laesst die Middleware alles durch -
    die Agent-Routen legen aber Auftraege an. Gelesen wie die Middleware
    (server.API_KEY zur Anfragezeit); Import hier, sonst Kreisimport."""
    from spaces.marketing.api import server
    if not server.API_KEY:
        raise HTTPException(503, "misconfigured: MARKETING_API_KEY fehlt")


def _auftrag_id(wert: str) -> str:
    try:
        return str(_uuid.UUID(wert))
    except ValueError:
        raise HTTPException(404, "Unbekannter Auftrag")


def _echtes_bild_pruefen(i: str, platz: str) -> None:
    """Freistellen nur fuer einen Platz der neuesten Fassung, der ein echtes Bild
    traegt (kein Platzhalter, keine erzeugte Grafik - finde() laesst Grafiken weg)."""
    f = _lesen_einer(lambda:
        f"SELECT bloecke FROM marketing.inhalt_fassungen WHERE inhalt = {lit(i)}::uuid "
        "ORDER BY fassung DESC LIMIT 1")
    if not f:
        raise HTTPException(404, "Unbekannter Inhalt")
    echt = [p for p in bildplaetze.finde(f.get("bloecke") or {}) if p.id == platz and not bildplaetze.ist_leer(p.url)]
    if not echt:
        raise HTTPException(422, "Freistellen geht nur bei einem Bildplatz mit echtem Bild")


def _anlegen(iid: str, payload, urheber: str) -> dict:
    i = _uuid_oder_404(iid)
    if not isinstance(payload, dict):
        raise HTTPException(422, "Body muss ein Objekt sein")
    platz = payload.get("platz")
    if platz is not None and (not isinstance(platz, str) or not _PLATZ.fullmatch(platz)):
        raise HTTPException(422, "platz muss eine Block-ID sein")
    hinweis = payload.get("hinweis", "")
    if not isinstance(hinweis, str) or len(hinweis) > 500:
        raise HTTPException(422, "hinweis muss Text mit hoechstens 500 Zeichen sein")
    nur_leere = payload.get("nur_leere", False)
    if not isinstance(nur_leere, bool):
        raise HTTPException(422, "nur_leere muss true oder false sein")
    staerke = payload.get("staerke", 55)
    if isinstance(staerke, bool) or not isinstance(staerke, int) or not 0 <= staerke <= 100:
        raise HTTPException(422, "staerke muss eine ganze Zahl von 0 bis 100 sein")
    modus = payload.get("modus", "ueberarbeiten")
    if modus not in ("neu", "ueberarbeiten", "freistellen"):
        raise HTTPException(422, "modus muss neu, ueberarbeiten oder freistellen sein")
    if modus == "freistellen":
        if not platz:
            raise HTTPException(422, "Freistellen braucht einen Bildplatz (platz)")
        _echtes_bild_pruefen(i, platz)
        staerke, nur_leere = 0, False
    zeile = _schreiben(lambda:
        f"SELECT marketing.pult_bild_auftrag({lit(i)}::uuid, {lit(platz) if platz else 'NULL'}, "
        f"{'true' if nur_leere else 'false'}, {lit(hinweis.strip())}, {lit(urheber)}, {int(staerke)}, {lit(modus)}) AS id")
    return {"auftrag": str(zeile["id"])}


def _stand(i: str) -> list:
    return _lesen(lambda: _STAND_SQL.format(i=lit(i)))


@pult_router.post("/inhalte/{iid}/bilder")
def pult_auftrag(iid: str, payload: dict = Body(default={}), x_pult_key: str | None = Header(None)):
    _schluessel(x_pult_key)
    return _anlegen(iid, payload, "mensch")


@pult_router.get("/inhalte/{iid}/bilder")
def pult_stand(iid: str, x_pult_key: str | None = Header(None)):
    _schluessel(x_pult_key)
    return {"auftraege": _stand(_uuid_oder_404(iid))}


@router.get("/agent/{iid}/plaetze")
def agent_plaetze(iid: str):
    _agent_schluessel()
    i = _uuid_oder_404(iid)
    f = _lesen_einer(lambda:
        f"SELECT fassung, bloecke FROM marketing.inhalt_fassungen WHERE inhalt = {lit(i)}::uuid "
        "ORDER BY fassung DESC LIMIT 1")
    if not f:
        raise HTTPException(404, "Unbekannter Inhalt")
    plaetze = [p.als_dict() for p in bildplaetze.finde(f.get("bloecke") or {})]
    return {"fassung": int(f["fassung"]), "plaetze": plaetze, "auftraege": _stand(i)}


@router.post("/agent/{iid}/auftrag")
def agent_auftrag(iid: str, payload: dict = Body(default={})):
    _agent_schluessel()
    return _anlegen(iid, payload, "agent")


def _png_pruefen(roh: bytes) -> None:
    if not roh.startswith(PNG_KOPF):
        raise HTTPException(422, "Kein PNG")
    try:
        from PIL import Image
        with Image.open(io.BytesIO(roh)) as b:
            b.verify()
        with Image.open(io.BytesIO(roh)) as b:
            if b.mode not in ("RGBA", "LA") and "transparency" not in b.info:
                raise HTTPException(422, "PNG ohne Transparenz")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(422, "PNG nicht lesbar")


@router.post("/arbeiter/naechster")
def arbeiter_naechster(x_bild_key: str | None = Header(None)):
    _bild_schluessel(x_bild_key)
    zeile = _schreiben(lambda: f"SELECT marketing.pult_bild_naechster({lit(FRIST)}::interval) AS a")
    a = zeile.get("a")
    if isinstance(a, dict) and a.get("id"):
        z = _lesen_einer(lambda:
            "SELECT b.staerke, b.modus, i.mandant, (SELECT m.name FROM marketing.mandanten m "
            "WHERE m.id = i.mandant) AS mandant_name FROM marketing.bild_auftraege b "
            f"JOIN marketing.inhalte i ON i.id = b.inhalt WHERE b.id = {lit(a['id'])}::uuid")
        a.update(z or {})
    return {"auftrag": a}


@router.post("/arbeiter/{aid}/weiter")
def arbeiter_weiter(aid: str, x_bild_key: str | None = Header(None)):
    _bild_schluessel(x_bild_key)
    a = _auftrag_id(aid)
    zeile = _schreiben(lambda:
        f"SELECT marketing.pult_bild_verlaengern({lit(a)}::uuid, {lit(FRIST)}::interval) AS ok")
    return {"ok": bool(zeile.get("ok"))}


def _jpeg_pruefen(roh: bytes) -> None:
    if not roh.startswith(b"\xff\xd8\xff"):
        raise HTTPException(422, "Nur JPEG")
    from PIL import Image, UnidentifiedImageError
    try:
        with Image.open(io.BytesIO(roh)) as bild:
            bild.verify()
        with Image.open(io.BytesIO(roh)) as bild:
            fmt, (w, h) = bild.format, bild.size
            bild.load()   # ganz dekodieren: abgeschnittene JPEGs bestehen verify()
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        raise HTTPException(422, "Bilddatei ist kaputt")
    if fmt != "JPEG" or not (KANTE_MIN <= w <= KANTE_MAX and KANTE_MIN <= h <= KANTE_MAX):
        raise HTTPException(422, f"JPEG mit Kanten {KANTE_MIN}-{KANTE_MAX} px noetig")


@router.get("/arbeiter/{aid}/quelle")
def arbeiter_quelle(aid: str, platz: str = Query(""), x_bild_key: str | None = Header(None)):
    _bild_schluessel(x_bild_key)
    a = _auftrag_id(aid)
    if not _PLATZ.fullmatch(platz or ""):
        raise HTTPException(422, "platz muss eine Block-ID sein")
    fehler = _lesen_einer(lambda: f"SELECT marketing.pult_bild_datei_fehler({lit(a)}::uuid, {lit(platz)}) AS f")
    if fehler is None:
        raise HTTPException(503, "Marketing-Datenbank nicht erreichbar")
    if fehler.get("f"):
        raise HTTPException(422, str(fehler["f"]))
    z = _lesen_einer(lambda:
        "SELECT f.bloecke #>> ARRAY[" + lit(platz) + ", 'data', 'props', 'url'] AS url "
        "FROM marketing.bild_auftraege b JOIN marketing.inhalt_fassungen f ON f.inhalt = b.inhalt "
        f"WHERE b.id = {lit(a)}::uuid ORDER BY f.fassung DESC LIMIT 1")
    url = str((z or {}).get("url") or "")
    if not url.startswith("medien:") or bildplaetze.ist_leer(url):
        raise HTTPException(404, "Kein Bild im Platz")
    name = url[len("medien:"):]
    if not _bilddatei_name_ok(name):
        raise HTTPException(404, "Kein Bild im Platz")
    ordner = [os.environ.get("MARKETING_MEDIEN_ORDNER", "").strip(), _ordner()]
    for o in ordner:
        pfad = _im_ordner(o, name) if o else None
        if pfad:
            return FileResponse(pfad, media_type=_BILDTYP[os.path.splitext(name)[1].lower()],
                                headers={"Cache-Control": "no-store"})
    raise HTTPException(404, "Bilddatei fehlt")


def _bilddatei_name_ok(name: str) -> bool:
    """Regel wie sales-ui: schlichter Dateiname (kein / \\ : NUL, nicht mit '.'
    beginnend, kein '..'), Endung png/jpg/jpeg/gif/webp. Leerzeichen, Umlaute,
    Klammern erlaubt (Menschen legen Dateien so in media/ ab)."""
    if not name or len(name) > 255 or name.startswith(".") or ".." in name:
        return False
    if any(z in name for z in "/\\:\x00") or any(ord(z) < 32 for z in name):
        return False
    return os.path.splitext(name)[1].lower() in _BILDTYP


def _im_ordner(ordner: str, name: str) -> str | None:
    """Datei nur liefern, wenn sie aufgeloest (Symlinks) im aufgeloesten Ordner liegt."""
    basis = os.path.realpath(ordner)
    pfad = os.path.realpath(os.path.join(basis, name))
    try:
        drin = os.path.commonpath([basis, pfad]) == basis and pfad != basis
    except ValueError:   # verschiedene Laufwerke
        return None
    return pfad if drin and os.path.isfile(pfad) else None


@router.post("/arbeiter/{aid}/bild")
async def arbeiter_bild(aid: str, request: Request, platz: str = Query(""), format: str = Query("jpg"),
                        x_bild_key: str | None = Header(None)):
    _bild_schluessel(x_bild_key)
    a = _auftrag_id(aid)
    if not _PLATZ.fullmatch(platz or ""):
        raise HTTPException(422, "platz muss eine Block-ID sein")
    if format not in ("jpg", "png"):
        raise HTTPException(422, "format muss jpg oder png sein")
    png = format == "png"
    grenze, grenztext = (PNG_MAX, "4 MB") if png else (BILD_MAX, "1 MB")
    ordner = _ordner()
    try:
        laenge = int(request.headers.get("content-length") or 0)
    except ValueError:
        laenge = 0
    if laenge > grenze:
        raise HTTPException(413, f"Bild groesser als {grenztext}")
    roh = bytearray()
    async for stueck in request.stream():
        roh += stueck
        if len(roh) > grenze:
            raise HTTPException(413, f"Bild groesser als {grenztext}")
    (_png_pruefen if png else _jpeg_pruefen)(bytes(roh))
    fehler = _lesen_einer(lambda:
        f"SELECT marketing.pult_bild_datei_fehler({lit(a)}::uuid, {lit(platz)}) AS f")
    if fehler is None:
        raise HTTPException(503, "Marketing-Datenbank nicht erreichbar")
    if fehler.get("f"):
        raise HTTPException(422, str(fehler["f"]))
    name = f"nl-{a[:8]}-{platz}-frei.png" if png else f"nl-{a[:8]}-{platz}.jpg"
    # Erst der Firma des Newsletters zuordnen, dann ablegen: scheitert die Zuordnung,
    # entsteht keine Datei - nie ein Zeitfenster, in dem sie als Gemeinsam sichtbar ist.
    medien_mandant.zuordnen_fuer_bildauftrag(a, [name])
    ziel = os.path.join(ordner, name)
    zwischen = ziel + ".teil"
    try:
        with open(zwischen, "wb") as f:
            f.write(roh)
        os.chmod(zwischen, 0o644)
        os.replace(zwischen, ziel)
    except OSError:
        try:
            os.remove(zwischen)
        except OSError:
            pass
        raise HTTPException(503, "Bild konnte nicht abgelegt werden")
    return {"name": name}


@router.post("/arbeiter/{aid}/fertig")
def arbeiter_fertig(aid: str, payload: dict = Body(...), x_bild_key: str | None = Header(None)):
    _bild_schluessel(x_bild_key)
    a = _auftrag_id(aid)
    ergebnis = payload.get("ergebnis")
    befund = payload.get("befund", "")
    if not isinstance(ergebnis, dict) or not isinstance(befund, str):
        raise HTTPException(422, "ergebnis (Objekt) und befund (Text) noetig")
    ordner = _ordner()
    medien = {}
    for platz, name in ergebnis.items():
        if (not isinstance(platz, str) or not _PLATZ.fullmatch(platz) or not isinstance(name, str)
                or not _NAME.fullmatch(name) or not name.startswith(f"nl-{a[:8]}-")
                or not os.path.isfile(os.path.join(ordner, name))):
            raise HTTPException(422, f"Ungueltiges Ergebnis fuer {str(platz)[:64]}")
        medien[platz] = "medien:" + name
    messung = payload.get("messung") or {}
    if not isinstance(messung, dict):
        raise HTTPException(422, "messung muss ein Objekt sein")
    for platz, werte in messung.items():
        if platz not in medien or not isinstance(werte, dict) or set(werte) - set(_MESSWERTE):
            raise HTTPException(422, f"Ungueltige Messung fuer {str(platz)[:64]}")
        for w in werte.values():
            if w is not None and (isinstance(w, bool) or not isinstance(w, (int, float)) or not -1 <= w <= 1):
                raise HTTPException(422, f"Ungueltige Messung fuer {platz}")
    # Zuordnung schon beim Hochladen; hier noch einmal (idempotent) als Sicherheitsnetz.
    # Scheitert sie, geht der Fehler an den Bild-Arbeiter (Wiederholung), die Datei bleibt unverwiesen.
    medien_mandant.zuordnen_fuer_bildauftrag(a, list(ergebnis.values()))
    zeile = _schreiben(lambda:
        f"SELECT marketing.pult_bild_einsetzen({lit(a)}::uuid, "
        f"{lit(json.dumps(medien, ensure_ascii=False))}::jsonb, {lit(befund[:500])}, "
        f"{lit(json.dumps(messung, ensure_ascii=False))}::jsonb) AS e")
    return zeile.get("e") or {}


@router.post("/arbeiter/{aid}/zurueck")
def arbeiter_zurueck(aid: str, payload: dict = Body(...), x_bild_key: str | None = Header(None)):
    _bild_schluessel(x_bild_key)
    a = _auftrag_id(aid)
    befund, endgueltig = payload.get("befund", ""), payload.get("endgueltig", False)
    if not isinstance(befund, str) or not isinstance(endgueltig, bool):
        raise HTTPException(422, "befund (Text) und endgueltig (true/false) noetig")
    zeile = _schreiben(lambda:
        f"SELECT marketing.pult_bild_zurueck({lit(a)}::uuid, {lit(befund[:500])}, "
        f"{'true' if endgueltig else 'false'}) AS s")
    return {"status": zeile.get("s")}
