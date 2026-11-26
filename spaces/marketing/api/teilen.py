"""Teilen fertiger Reels und Folgen (Stufe C, Punkt 5): legt einen Versandauftrag an, nie mehr.

sales-claw ist der einzige Versandweg (db/043, 092, 093). Hier entsteht nur eine BITTE in
marketing.versandauftraege; sales-claw macht daraus hoechstens einen Entwurf, und den gibt der Betreiber
in Sales frei. Die Pruefung von Kanal, Text, Verbotsliste und Wiederholung steht in der DB-Funktion
marketing.versandauftrag_anlegen und gilt fuer jeden Rufer.

Was diese Schicht dazu tut:
- nur ein FERTIGES Reel oder eine fertige Folge DIESER Firma, und deren Datei geht als medien_datei mit
  (nie ein Dateiname vom Aufrufer);
- die Firma steht in ``quelle`` (``marketing-oberflaeche:<firma>:<art>:<id>``), weil der Versandauftrag
  keine eigene Firmen-Spalte hat; daran liest ``pult_stand`` den Stand je Karte zurueck, nur fuer
  Quellen dieser Firma, ohne den Text.
Schluessel: X-Pult-Key (MARKETING_PULT_KEY) wie alle Pult-Wege."""
from __future__ import annotations

import json

from fastapi import APIRouter, Body, Header, HTTPException, Query

from spaces.marketing.api.pult import (_lesen, _lesen_einer, _mandant_pflicht, _schluessel, _schreiben,
                                       _uuid_oder_404, lit)

pult_router = APIRouter(prefix="/api/pult/teilen")

# Wie in 093: Beitrag auf den eigenen Kanal (ohne Empfaenger) und Nachricht an einen Kontakt.
KANAELE_BEITRAG = ("linkedin_post", "bluesky_post", "mastodon_post", "telegram_kanal_post", "discord_post",
                   "devto_post", "youtube_post")
KANAELE_NACHRICHT = ("email", "whatsapp", "linkedin", "telegram")
KANAELE = KANAELE_BEITRAG + KANAELE_NACHRICHT
ARTEN = {"reel": "video_auftraege", "folge": "ton_auftraege"}
QUELLE = "marketing-oberflaeche"
NACHRICHT_MAX, BETREFF_MAX, EMPFAENGER_MAX = 5000, 300, 300
STAND_MAX = 200


def quelle(mandant: str, art: str, aid: str) -> str:
    return f"{QUELLE}:{mandant}:{art}:{aid}"


def _art(wert) -> str:
    if wert not in ARTEN:
        raise HTTPException(422, "art muss reel oder folge sein")
    return wert


def _feld(payload: dict, name: str, maximal: int, pflicht: bool = False) -> str:
    wert = payload.get(name)
    if wert is None:
        wert = ""
    if not isinstance(wert, str):
        raise HTTPException(422, f"{name} muss Text sein")
    wert = wert.strip()
    if pflicht and not wert:
        raise HTTPException(422, f"{name} fehlt")
    if len(wert) > maximal:
        raise HTTPException(422, f"{name}: hoechstens {maximal} Zeichen")
    return wert


def _fertige_datei(art: str, aid: str, mandant: str) -> str:
    """Dateiname des fertigen Auftrags dieser Firma; fremd oder unbekannt 404, nicht fertig 422."""
    a = _uuid_oder_404(aid)
    zeile = _lesen_einer(lambda: f"SELECT mandant, status, datei FROM marketing.{ARTEN[art]} WHERE id = {lit(a)}::uuid")
    if not zeile or zeile.get("mandant") != mandant:
        raise HTTPException(404, "Unbekannter Auftrag")
    datei = str(zeile.get("datei") or "").strip()
    if zeile.get("status") != "fertig" or not datei:
        raise HTTPException(422, "Nur Fertiges laesst sich teilen")
    return datei


def _ergebnis(roh) -> dict:
    if isinstance(roh, str):
        try:
            roh = json.loads(roh)
        except ValueError:
            roh = None
    if not isinstance(roh, dict):
        raise HTTPException(503, "Marketing-Datenbank nicht erreichbar")
    return {"ok": roh.get("ok") is True, "id": roh.get("id"), "wiederholung": roh.get("wiederholung") is True,
            "grund": str(roh.get("grund") or "")}


@pult_router.post("")
def pult_teilen(payload: dict = Body(...), x_pult_key: str | None = Header(None)):
    """Body: mandant, art (reel|folge), id, kanal, nachricht, optional betreff, empfaenger.
    Antwort {ok, id, wiederholung, grund}; eine Absage der DB-Funktion ist ok=false mit Grund, kein Fehler."""
    _schluessel(x_pult_key)
    m = _mandant_pflicht(payload.get("mandant"))
    art = _art(payload.get("art"))
    kanal = payload.get("kanal")
    if kanal not in KANAELE:
        raise HTTPException(422, "Unbekannter Kanal")
    nachricht = _feld(payload, "nachricht", NACHRICHT_MAX, pflicht=True)
    betreff = _feld(payload, "betreff", BETREFF_MAX)
    empfaenger = _feld(payload, "empfaenger", EMPFAENGER_MAX)
    aid = str(payload.get("id") or "")
    datei = _fertige_datei(art, aid, m)
    q = quelle(m, art, _uuid_oder_404(aid))
    zeile = _schreiben(lambda:
        f"SELECT marketing.versandauftrag_anlegen({lit(kanal)}, {lit(empfaenger)}, {lit(nachricht)}, "
        f"{lit(betreff)}, {lit(datei)}, '', {lit(q)}) AS ergebnis")
    return _ergebnis(zeile.get("ergebnis"))


@pult_router.get("")
def pult_stand(mandant: str = Query(...), art: str = Query(...), x_pult_key: str | None = Header(None)):
    """Stand der Versandauftraege aus dieser Oberflaeche fuer EINE Firma und Art, neueste zuerst.
    Ohne Text und Empfaenger: die Karte zeigt nur Kanal, Stand und den Grund einer Absage."""
    _schluessel(x_pult_key)
    m = _mandant_pflicht(mandant)
    a = _art(art)
    vorsatz = f"{QUELLE}:{m}:{a}:"
    zeilen = _lesen(lambda:
        f"SELECT id::text AS id, kanal, status, grund, draft_id, quelle, created_at::text AS angelegt, "
        f"erledigt_am::text AS erledigt FROM marketing.versandauftraege "
        f"WHERE left(quelle, {len(vorsatz)}) = {lit(vorsatz)} ORDER BY created_at DESC LIMIT {STAND_MAX}")
    auftraege = []
    for z in zeilen:
        if not isinstance(z, dict):
            continue
        q = str(z.pop("quelle", "") or "")
        auftraege.append({**z, "auftrag": q[len(vorsatz):]})
    return {"auftraege": auftraege}
