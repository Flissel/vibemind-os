"""Reel-Auftraege fuer den Video-Claw (17.10.2026, Migration 072, Plan 2026-10-17-video-claw-reels).

Bestellt wird von zwei Seiten (E4): der Marketing-Agent ueber die Agent-Routen (/api/video/auftraege,
X-API-Key bzw. X-Agent-Key), der Betreiber im Pult (/api/pult/video, X-Pult-Key). Der Video-Claw am PC
holt Auftraege ueber die Arbeiter-Routen (X-Bild-Key), baut das Reel mit Laura und legt ein MP4 in die
erzeugten Medien (MARKETING_BILD_ORDNER). Versendet wird hier nichts: die Datei geht nur ueber
versand_beauftragen -> sales-claw mit Freigabe durch einen Menschen.

Markenbilder holt Laura (VM) per URL. Laura schickt beim Import keine Kopfzeilen; darum gibt es einen
kurzlebigen, signierten Link (/api/video/medien/<ablauf>/<signatur>/<name>), den nur der Arbeiter fuer ein
Bild der Firma SEINES Auftrags ausstellen laesst.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import time
import urllib.parse

from fastapi import APIRouter, Body, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse

from spaces.marketing.api import medien_mandant
from spaces.marketing.api.bilder import _agent_schluessel, _auftrag_id, _bild_schluessel, _ordner
from spaces.marketing.api.pult import _lesen, _lesen_einer, _schluessel, _schreiben, _uuid_oder_404, lit
from spaces.marketing.api.ton import _slug

router = APIRouter(prefix="/api/video")
pult_router = APIRouter(prefix="/api/pult/video")

ARTEN = ("material", "bilder", "kurzfassung")
FRIST = "15 minutes"
LINK_GUELTIG_S = 30 * 60
MP4_MAX = 15 * 1024 * 1024          # sales-claw nimmt hoechstens 15 MB (medien.MAX_BYTES)
TITEL_MAX, THEMA_MAX, TEXT_MAX, HOOK_MAX = 120, 300, 900, 120
_MANDANT = re.compile(r"^[a-z][a-z0-9_-]{0,40}$")
_LAURA_ID = re.compile(r"^[0-9a-f][0-9a-f-]{7,63}$")
_STIMME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
_FARBE = re.compile(r"^#[0-9a-fA-F]{6}$")
_BILD = (".png", ".jpg", ".jpeg", ".webp")
_DATEI = re.compile(r"^vid-[0-9a-f]{8}-[a-z0-9-]{1,40}\.mp4$")
_SPALTEN = ("id, mandant, art, titel, briefing, status, datei, dauer_s, bytes, befund, protokoll, "
            "angelegt_von, versuche, erstellt_am, geaendert_am")


# --- Briefing ---------------------------------------------------------------------------------------

def _text(wert, feld: str, maximal: int, pflicht: bool = True) -> str:
    if wert is None and not pflicht:
        return ""
    if not isinstance(wert, str) or (pflicht and not wert.strip()) or len(wert) > maximal:
        raise HTTPException(422, f"{feld}: {'1' if pflicht else '0'} bis {maximal} Zeichen")
    return wert.strip()


def _bildname(name, feld: str) -> str:
    if not isinstance(name, str) or not medien_mandant._gueltig(name) or not name.lower().endswith(_BILD):
        raise HTTPException(422, f"{feld}: Bilddatei (png, jpg, webp) aus den Medien")
    return name


def briefing_pruefen(art: str, b) -> dict:
    """Briefing je Art pruefen und auf bekannte Felder kuerzen (nichts Unbekanntes landet in der DB)."""
    if art not in ARTEN:
        raise HTTPException(422, f"art muss eine von {', '.join(ARTEN)} sein")
    if not isinstance(b, dict):
        raise HTTPException(422, "briefing muss ein Objekt sein")
    aus: dict = {"thema": _text(b.get("thema"), "thema", THEMA_MAX),
                 "sprechertext": _text(b.get("sprechertext"), "sprechertext", TEXT_MAX,
                                       pflicht=art != "kurzfassung"),
                 "hook": _text(b.get("hook"), "hook", HOOK_MAX, pflicht=False)}
    dauer = b.get("dauer_s", 30)
    if isinstance(dauer, bool) or not isinstance(dauer, int) or not 15 <= dauer <= 60:
        raise HTTPException(422, "dauer_s: 15 bis 60 Sekunden")
    aus["dauer_s"] = dauer
    stimme = b.get("stimme") or "standard"
    if not isinstance(stimme, str) or not _STIMME.fullmatch(stimme):
        raise HTTPException(422, "stimme: Name der Stimmprobe oder 'standard'")
    aus["stimme"] = stimme
    if art == "material":
        videos = b.get("videos") or []
        if not isinstance(videos, list) or len(videos) > 8 or any(
                not isinstance(v, str) or not _LAURA_ID.fullmatch(v) for v in videos):
            raise HTTPException(422, "videos: bis zu 8 Laura-Asset-IDs (leer = der Claw sucht passend)")
        aus["videos"] = videos
    elif art == "kurzfassung":
        video = b.get("video")
        if not isinstance(video, str) or not _LAURA_ID.fullmatch(video):
            raise HTTPException(422, "video: Laura-Asset-ID des langen Videos")
        aus["video"] = video
    else:
        bilder = b.get("bilder")
        if not isinstance(bilder, list) or not 1 <= len(bilder) <= 8:
            raise HTTPException(422, "bilder: 1 bis 8 Bilddateien aus den Medien")
        aus["bilder"] = [_bildname(n, "bilder") for n in bilder]
        if b.get("logo"):
            aus["logo"] = _bildname(b["logo"], "logo")
        farben = b.get("farben") or {}
        if not isinstance(farben, dict) or any(k not in ("hintergrund", "text") for k in farben) or any(
                not isinstance(v, str) or not _FARBE.fullmatch(v) for v in farben.values()):
            raise HTTPException(422, "farben: {hintergrund, text} als #rrggbb")
        aus["farben"] = farben
    return aus


def _bilder_der_firma(mandant: str, briefing: dict) -> None:
    """Bilder eines Briefings duerfen keiner anderen Firma gehoeren (wie ueberall in den Medien)."""
    namen = list(briefing.get("bilder") or []) + ([briefing["logo"]] if briefing.get("logo") else [])
    if not namen:
        return
    s = medien_mandant.sicht(mandant)
    fremd = [n for n in namen if medien_mandant.ist_fremd(n, mandant, s)]
    if fremd:
        raise HTTPException(422, f"Bild gehoert einer anderen Firma: {fremd[0]}")


def _mandant(wert, feld: str) -> str:
    if not isinstance(wert, str) or not _MANDANT.fullmatch(wert.strip()):
        raise HTTPException(422, f"{feld} fehlt oder ist ungueltig")
    return wert.strip()


def _anlegen(payload: dict, mandant_feld: str, von: str) -> str:
    mandant = _mandant(payload.get(mandant_feld), mandant_feld)
    art = payload.get("art")
    titel = _text(payload.get("titel"), "titel", TITEL_MAX)
    briefing = briefing_pruefen(art, payload.get("briefing"))
    _bilder_der_firma(mandant, briefing)
    zeile = _schreiben(lambda:
        f"SELECT marketing.video_auftrag_anlegen({lit(mandant)}, {lit(art)}, {lit(titel)}, "
        f"{lit(json.dumps(briefing, ensure_ascii=False))}::jsonb, {lit(von)}) AS id")
    return str(zeile.get("id"))


def _liste(mandant: str, limit: int) -> list:
    return _lesen(lambda:
        f"SELECT {_SPALTEN} FROM marketing.video_auftraege WHERE mandant = {lit(mandant)} "
        f"ORDER BY erstellt_am DESC LIMIT {int(limit)}")


# --- Agent ------------------------------------------------------------------------------------------

@router.post("/auftraege")
def agent_anlegen(payload: dict = Body(...), x_agent_key: str | None = Header(None)):
    _agent_schluessel(x_agent_key)
    return {"auftrag": _anlegen(payload, "firma", "agent")}


@router.get("/auftraege")
def agent_liste(firma: str = Query(...), limit: int = Query(20, ge=1, le=100),
                x_agent_key: str | None = Header(None)):
    _agent_schluessel(x_agent_key)
    return {"auftraege": _liste(_mandant(firma, "firma"), limit)}


# --- Pult (Betreiber) -------------------------------------------------------------------------------

@pult_router.get("")
def pult_liste(mandant: str = Query(...), limit: int = Query(50, ge=1, le=200),
               x_pult_key: str | None = Header(None)):
    _schluessel(x_pult_key)
    return {"auftraege": _liste(_mandant(mandant, "mandant"), limit)}


@pult_router.post("")
def pult_anlegen(payload: dict = Body(...), x_pult_key: str | None = Header(None)):
    _schluessel(x_pult_key)
    return {"auftrag": _anlegen(payload, "mandant", "betreiber")}


@pult_router.post("/{aid}/abbrechen")
def pult_abbrechen(aid: str, x_pult_key: str | None = Header(None)):
    _schluessel(x_pult_key)
    a = _uuid_oder_404(aid)
    zeile = _schreiben(lambda: f"SELECT marketing.video_abbrechen({lit(a)}::uuid) AS s")
    return {"status": zeile.get("s")}


# --- Arbeiter (Video-Claw am PC) --------------------------------------------------------------------

def _in_arbeit(a: str) -> dict:
    auftrag = _lesen_einer(lambda:
        f"SELECT titel, mandant, status, briefing FROM marketing.video_auftraege WHERE id = {lit(a)}::uuid")
    if not auftrag:
        raise HTTPException(404, "Unbekannter Auftrag")
    if auftrag.get("status") != "in_arbeit":
        raise HTTPException(409, "Auftrag ist nicht in Arbeit")
    return auftrag


@router.post("/arbeiter/naechster")
def arbeiter_naechster(x_bild_key: str | None = Header(None)):
    _bild_schluessel(x_bild_key)
    zeile = _schreiben(lambda: f"SELECT marketing.video_naechster({lit(FRIST)}::interval) AS a")
    return {"auftrag": zeile.get("a")}


@router.post("/arbeiter/{aid}/weiter")
def arbeiter_weiter(aid: str, x_bild_key: str | None = Header(None)):
    _bild_schluessel(x_bild_key)
    a = _auftrag_id(aid)
    zeile = _schreiben(lambda: f"SELECT marketing.video_verlaengern({lit(a)}::uuid, {lit(FRIST)}::interval) AS ok")
    return {"ok": bool(zeile.get("ok"))}


@router.post("/arbeiter/{aid}/notiz")
def arbeiter_notiz(aid: str, payload: dict = Body(...), x_bild_key: str | None = Header(None)):
    _bild_schluessel(x_bild_key)
    a = _auftrag_id(aid)
    text = _text(payload.get("text"), "text", 500)
    zeile = _schreiben(lambda: f"SELECT marketing.video_notiz({lit(a)}::uuid, {lit(text)}) AS ok")
    return {"ok": bool(zeile.get("ok"))}


def _geheimnis() -> bytes:
    k = os.environ.get("MARKETING_BILD_KEY", "").strip()
    if not k:
        raise HTTPException(503, "misconfigured: MARKETING_BILD_KEY fehlt")
    return hashlib.sha256(b"video-medien|" + k.encode("utf-8")).digest()


def _signatur(ablauf: int, name: str) -> str:
    return hmac.new(_geheimnis(), f"{ablauf}|{name}".encode("utf-8"), hashlib.sha256).hexdigest()[:40]


def _medien_pfad(name: str) -> str | None:
    for o in (os.environ.get("MARKETING_MEDIEN_ORDNER", "").strip(),
              os.environ.get("MARKETING_BILD_ORDNER", "").strip()):
        if o and os.path.isdir(o):
            pfad = os.path.join(o, name)
            if os.path.realpath(os.path.dirname(pfad)) == os.path.realpath(o) and os.path.isfile(pfad):
                return pfad
    return None


@router.post("/arbeiter/{aid}/medien-link")
def arbeiter_medien_link(aid: str, payload: dict = Body(...), x_bild_key: str | None = Header(None)):
    """Kurzlebiger Link auf ein Bild der Auftragsfirma, fuer Lauras URL-Import (ohne Kopfzeilen)."""
    _bild_schluessel(x_bild_key)
    a = _auftrag_id(aid)
    auftrag = _in_arbeit(a)
    name = _bildname(payload.get("name"), "name")
    mandant = str(auftrag["mandant"])
    if medien_mandant.ist_fremd(name, mandant, medien_mandant.sicht(mandant)):
        raise HTTPException(422, "Bild gehoert einer anderen Firma")
    if _medien_pfad(name) is None:
        raise HTTPException(404, "Bilddatei fehlt")
    ablauf = int(time.time()) + LINK_GUELTIG_S
    pfad = f"/api/video/medien/{ablauf}/{_signatur(ablauf, name)}/{urllib.parse.quote(name, safe='')}"
    basis = os.environ.get("MARKETING_VIDEO_MEDIEN_BASIS", "").strip().rstrip("/")
    return {"url": f"{basis}{pfad}" if basis else pfad, "gueltig_bis": ablauf}


@router.get("/medien/{ablauf}/{sig}/{name}")
def medien_abrufen(ablauf: int, sig: str, name: str):
    """Ohne Schluessel - die Signatur IST die Berechtigung (Name + Ablauf, HMAC)."""
    if ablauf < time.time() or ablauf > time.time() + LINK_GUELTIG_S + 60:
        raise HTTPException(403, "Link abgelaufen")
    if not medien_mandant._gueltig(name) or not name.lower().endswith(_BILD):
        raise HTTPException(404, "Unbekannt")
    if not hmac.compare_digest(sig, _signatur(ablauf, name)):
        raise HTTPException(403, "Signatur falsch")
    pfad = _medien_pfad(name)
    if pfad is None:
        raise HTTPException(404, "Bilddatei fehlt")
    typ = {".png": "image/png", ".webp": "image/webp"}.get(os.path.splitext(name)[1].lower(), "image/jpeg")
    return FileResponse(pfad, media_type=typ, headers={"Cache-Control": "no-store"})


def _ist_mp4(roh: bytes) -> bool:
    return len(roh) >= 12 and roh[4:8] == b"ftyp"


@router.post("/arbeiter/{aid}/datei")
async def arbeiter_datei(aid: str, request: Request, x_bild_key: str | None = Header(None)):
    _bild_schluessel(x_bild_key)
    a = _auftrag_id(aid)
    ordner = _ordner()
    roh = bytearray()
    async for stueck in request.stream():
        roh += stueck
        if len(roh) > MP4_MAX:
            raise HTTPException(413, "MP4 groesser als 15 MB")
    if not _ist_mp4(bytes(roh)):
        raise HTTPException(422, "Nur MP4")
    auftrag = _in_arbeit(a)
    name = f"vid-{a[:8]}-{_slug(str(auftrag.get('titel') or ''))[:40].strip('-') or 'reel'}.mp4"
    medien_mandant.zuordnen([name], str(auftrag.get("mandant")))
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
        raise HTTPException(503, "MP4 konnte nicht abgelegt werden")
    return {"name": name, "bytes": len(roh)}


@router.post("/arbeiter/{aid}/fertig")
def arbeiter_fertig(aid: str, payload: dict = Body(...), x_bild_key: str | None = Header(None)):
    """{datei, dauer_s, laura_export_id} = fertig; ohne datei, mit befund = fehler."""
    _bild_schluessel(x_bild_key)
    a = _auftrag_id(aid)
    datei, dauer = payload.get("datei"), payload.get("dauer_s")
    befund = payload.get("befund", "")
    export = payload.get("laura_export_id")
    if not isinstance(befund, str):
        raise HTTPException(422, "befund muss Text sein")
    if export is not None and (not isinstance(export, str) or not _LAURA_ID.fullmatch(export)):
        raise HTTPException(422, "laura_export_id ungueltig")
    groesse = None
    if datei is not None:
        pfad = os.path.join(_ordner(), datei) if isinstance(datei, str) else ""
        if (not isinstance(datei, str) or not _DATEI.fullmatch(datei) or not datei.startswith(f"vid-{a[:8]}-")
                or not os.path.isfile(pfad)):
            raise HTTPException(422, "Ungueltige Datei")
        if isinstance(dauer, bool) or not isinstance(dauer, (int, float)) or not 0 < dauer <= 120:
            raise HTTPException(422, "dauer_s 0 bis 120")
        groesse = os.path.getsize(pfad)
    zeile = _schreiben(lambda:
        f"SELECT marketing.video_abschliessen({lit(a)}::uuid, "
        f"{lit(datei) if datei else 'NULL'}, {repr(float(dauer)) if datei else 'NULL'}, "
        f"{int(groesse) if groesse else 'NULL'}, {lit(export) if export else 'NULL'}, "
        f"{lit(befund[:500])}) AS v")
    return {"status": zeile.get("v")}
