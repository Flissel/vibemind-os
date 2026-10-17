"""Podcast-Auftraege (17.10.2026, Migration 069). Der Agent beauftragt eine Folge, der Ton-Arbeiter am PC
spricht sie lokal (Chatterbox Multilingual) und legt eine MP3 in die erzeugten Medien
(MARKETING_BILD_ORDNER = media-erzeugt der Laeden). Versendet wird hier nichts: die Datei geht nur ueber
versand_beauftragen -> sales-claw mit Freigabe durch einen Menschen.

Agent-Routen: globaler X-API-Key (Middleware). Arbeiter-Routen: X-Bild-Key wie der Bild-Arbeiter."""
from __future__ import annotations

import json
import os
import re
import unicodedata

from fastapi import APIRouter, Body, Header, HTTPException, Query, Request

from spaces.marketing.api import medien_mandant
from spaces.marketing.api.bilder import _agent_schluessel, _auftrag_id, _bild_schluessel, _ordner
from spaces.marketing.api.pult import _lesen, _lesen_einer, _schreiben, lit

router = APIRouter(prefix="/api/ton")
FRIST = "20 minutes"
TITEL_MAX, TEXT_MAX, ABSCHNITTE_MAX, GESAMT_MAX = 120, 1000, 60, 12000
MP3_MAX = 25 * 1024 * 1024
_SPRECHER = re.compile(r"^[A-Za-zÄÖÜäöüß][A-Za-zÄÖÜäöüß .-]{0,29}$")
_SPRACHE = re.compile(r"^[a-z]{2}$")
_DATEI = re.compile(r"^pod-[0-9a-f]{8}-[a-z0-9-]{1,60}\.mp3$")


def _slug(titel: str) -> str:
    for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("Ä", "Ae"), ("Ö", "Oe"), ("Ü", "Ue"), ("ß", "ss")):
        titel = titel.replace(a, b)
    roh = unicodedata.normalize("NFKD", titel).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", roh).strip("-")[:60] or "folge"


def _skript_pruefen(skript) -> list[dict]:
    if not isinstance(skript, list) or not 1 <= len(skript) <= ABSCHNITTE_MAX:
        raise HTTPException(422, f"skript muss 1 bis {ABSCHNITTE_MAX} Abschnitte haben")
    aus, gesamt = [], 0
    for a in skript:
        if not isinstance(a, dict):
            raise HTTPException(422, "skript: jeder Abschnitt ist ein Objekt {sprecher, text}")
        sprecher, text = a.get("sprecher"), a.get("text")
        if not isinstance(sprecher, str) or not _SPRECHER.fullmatch(sprecher.strip()):
            raise HTTPException(422, "sprecher: Name aus Buchstaben, hoechstens 30 Zeichen")
        if not isinstance(text, str) or not text.strip() or len(text) > TEXT_MAX:
            raise HTTPException(422, f"text: 1 bis {TEXT_MAX} Zeichen je Abschnitt")
        gesamt += len(text)
        aus.append({"sprecher": sprecher.strip(), "text": text.strip()})
    if gesamt > GESAMT_MAX:
        raise HTTPException(422, f"skript: hoechstens {GESAMT_MAX} Zeichen insgesamt (etwa 12 Minuten)")
    return aus


@router.post("/auftraege")
def auftrag_anlegen(payload: dict = Body(...)):
    _agent_schluessel()
    firma, titel, sprache = payload.get("firma"), payload.get("titel"), payload.get("sprache", "de")
    if not isinstance(firma, str) or not firma.strip():
        raise HTTPException(422, "firma (Mandant) fehlt")
    if not isinstance(titel, str) or not 1 <= len(titel.strip()) <= TITEL_MAX:
        raise HTTPException(422, f"titel: 1 bis {TITEL_MAX} Zeichen")
    if not isinstance(sprache, str) or not _SPRACHE.fullmatch(sprache):
        raise HTTPException(422, "sprache: zwei Kleinbuchstaben, z. B. de")
    skript = _skript_pruefen(payload.get("skript"))
    if not _lesen_einer(lambda: f"SELECT id FROM marketing.mandanten WHERE id = {lit(firma.strip())}"):
        raise HTTPException(422, "Unbekannte firma")
    zeile = _schreiben(lambda:
        "INSERT INTO marketing.ton_auftraege (mandant, titel, sprache, skript) VALUES "
        f"({lit(firma.strip())}, {lit(titel.strip())}, {lit(sprache)}, "
        f"{lit(json.dumps(skript, ensure_ascii=False))}::jsonb) RETURNING id")
    return {"auftrag": str(zeile.get("id"))}


@router.get("/auftraege")
def auftraege_lesen(limit: int = Query(20, ge=1, le=100)):
    _agent_schluessel()
    zeilen = _lesen(lambda:
        "SELECT id, mandant, titel, status, datei, dauer_s, befund, erstellt_am, geaendert_am "
        f"FROM marketing.ton_auftraege ORDER BY erstellt_am DESC LIMIT {int(limit)}")
    return {"auftraege": zeilen}


@router.post("/arbeiter/naechster")
def arbeiter_naechster(x_bild_key: str | None = Header(None)):
    _bild_schluessel(x_bild_key)
    zeile = _schreiben(lambda: f"SELECT marketing.ton_naechster({lit(FRIST)}::interval) AS a")
    return {"auftrag": zeile.get("a")}


@router.post("/arbeiter/{aid}/weiter")
def arbeiter_weiter(aid: str, x_bild_key: str | None = Header(None)):
    _bild_schluessel(x_bild_key)
    a = _auftrag_id(aid)
    zeile = _schreiben(lambda: f"SELECT marketing.ton_verlaengern({lit(a)}::uuid, {lit(FRIST)}::interval) AS ok")
    return {"ok": bool(zeile.get("ok"))}


def _ist_mp3(roh: bytes) -> bool:
    if roh.startswith(b"ID3") and len(roh) > 10:
        groesse = (roh[6] << 21) | (roh[7] << 14) | (roh[8] << 7) | roh[9]
        roh = roh[10 + groesse:]
    return len(roh) >= 4 and roh[0] == 0xFF and (roh[1] & 0xE0) == 0xE0


@router.post("/arbeiter/{aid}/datei")
async def arbeiter_datei(aid: str, request: Request, x_bild_key: str | None = Header(None)):
    _bild_schluessel(x_bild_key)
    a = _auftrag_id(aid)
    ordner = _ordner()
    roh = bytearray()
    async for stueck in request.stream():
        roh += stueck
        if len(roh) > MP3_MAX:
            raise HTTPException(413, "MP3 groesser als 25 MB")
    if not _ist_mp3(bytes(roh)):
        raise HTTPException(422, "Nur MP3")
    auftrag = _lesen_einer(lambda:
        f"SELECT titel, mandant, status FROM marketing.ton_auftraege WHERE id = {lit(a)}::uuid")
    if not auftrag:
        raise HTTPException(404, "Unbekannter Auftrag")
    if auftrag.get("status") != "in_arbeit":
        raise HTTPException(409, "Auftrag ist nicht in Arbeit")
    name = f"pod-{a[:8]}-{_slug(str(auftrag.get('titel') or ''))}.mp3"
    # Erst der Firma zuordnen, dann ablegen (wie der Bild-Arbeiter): nie als "Gemeinsam" sichtbar.
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
        raise HTTPException(503, "MP3 konnte nicht abgelegt werden")
    return {"name": name}


@router.post("/arbeiter/{aid}/fertig")
def arbeiter_fertig(aid: str, payload: dict = Body(...), x_bild_key: str | None = Header(None)):
    """{datei, dauer_s} = fertig; ohne datei, mit befund = fehler."""
    _bild_schluessel(x_bild_key)
    a = _auftrag_id(aid)
    datei, dauer, befund = payload.get("datei"), payload.get("dauer_s"), payload.get("befund", "")
    if not isinstance(befund, str):
        raise HTTPException(422, "befund muss Text sein")
    if datei is not None:
        if (not isinstance(datei, str) or not _DATEI.fullmatch(datei) or not datei.startswith(f"pod-{a[:8]}-")
                or not os.path.isfile(os.path.join(_ordner(), datei))):
            raise HTTPException(422, "Ungueltige Datei")
        if isinstance(dauer, bool) or not isinstance(dauer, (int, float)) or not 0 <= dauer <= 3600:
            raise HTTPException(422, "dauer_s 0 bis 3600")
    zeile = _schreiben(lambda:
        f"SELECT marketing.ton_abschliessen({lit(a)}::uuid, "
        f"{lit(datei) if datei else 'NULL'}, {repr(float(dauer)) if datei else 'NULL'}, "
        f"{lit(befund[:500])}) AS v")
    return {"status": zeile.get("v")}
