"""Marken-Routen (sales-claw Spec 2026-10-07-marke-per-chat-design.md §3-§4, Migration 064).
  /api/pult/marke...                    Sales-Oberflaeche (X-Pult-Key, pult._schluessel)
  /api/pult/inhalte/{iid}/marke_hinweis_aus
  /api/marke/arbeiter/*                 Marken-Arbeiter am PC (X-Bild-Key, bilder._bild_schluessel)
Die Regeln (ein laufender Auftrag je Firma, Vorschlag offen/ersetzt, Spiegel ins Standard-Layout)
stehen in den DB-Funktionen aus 064; hier nur Formen, Bildpruefung (Pillow), Dateiablage und
Weitergabe. Schreiben laeuft ueber die DB-Funktionen (nie eigene Tabellen-Updates)."""
from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import re
import tempfile

from fastapi import APIRouter, Body, Header, HTTPException, Request
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool

from spaces.marketing.api import medien_mandant
from spaces.marketing.api.bilder import _auftrag_id, _bild_schluessel, _im_ordner, _ordner
from spaces.marketing.api.chat import (_ARBEITER_DATEI, SPUR_KOERPER_MAX, _json_gekappt, _medien_ausliefern,
                                       _nachricht_und_kontext, spur_pruefen)
from spaces.marketing.api.gestaltung import quellen
from spaces.marketing.api.medien_mandant import _mandant_pflicht, ist_fremd, sicht
from spaces.marketing.api.pult import (_auswahl, _bild_basis, _bloecke_html, _lesen,
                                       _lesen_einer, _schluessel, _schreiben, _uuid_oder_404,
                                       _vorlage_fuellen, lit)
from spaces.marketing.claw import schriften

log = logging.getLogger(__name__)
pult_router = APIRouter(prefix="/api/pult")
arbeiter_router = APIRouter(prefix="/api/marke/arbeiter")

FRIST = "5 minutes"
LOGO_MAX = 2 * 1024 * 1024
LOGO_PIXEL_MAX = 25_000_000
SPIEGEL_KOERPER_MAX = 400 * 1024          # Logo <= 140 KB als data-URL, Rest klein
SPIEGEL_SCHLUESSEL = ("akzent", "flaeche", "logo", "schriften")
_PNG, _JPEG = b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff"
_LOGO_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,119}\.(png|jpe?g)")
MUSTER_MAX = {"betreff": 200, "ueberschrift": 200, "absatz": 1000}
MUSTER_VORLAGE = "studio"
# Textplaetze der Vorlage studio (Ueberschrift und Absatz des Heldenblocks)
_MUSTER_BLOECKE = {"ueberschrift": "held_titel", "absatz": "held_text"}
VORLAGE_FEHLT = "Die Vorlage studio ist nicht verfügbar"


def _vorschlag_id(wert: str) -> str:
    try:
        return _uuid_oder_404(wert)
    except HTTPException:
        raise HTTPException(404, "Unbekannter Vorschlag")


def _von(payload: dict) -> str:
    von = payload.get("von")
    if not isinstance(von, str) or not von.strip() or len(von) > 200:
        raise HTTPException(422, "von fehlt")
    return von.strip()


# ─── Pult ───────────────────────────────────────────────────────────────


@pult_router.get("/marke")
def marke_stand(mandant: str | None = None, x_pult_key: str | None = Header(None)):
    _schluessel(x_pult_key)
    m = _mandant_pflicht(mandant)
    _schreiben(lambda: f"SELECT marketing._marke_aufraeumen({lit(m)}) IS NULL AS ok")
    kopf = _lesen_einer(lambda:
        "SELECT m.name, s.stand, s.gespiegelt_am::text AS gespiegelt_am, s.fehler, s.hinweise, "
        "(SELECT l.gestalt FROM marketing.layout_vorlagen l WHERE l.mandant = m.id AND l.standard "
        "AND l.inhaltsart = 'newsletter' AND l.art = 'layout') AS gestalt "
        "FROM marketing.mandanten m LEFT JOIN marketing.marken_spiegel s ON s.mandant = m.id "
        f"WHERE m.id = {lit(m)}")
    if not kopf:
        raise HTTPException(404, "Unbekannter Mandant")
    gestalt = kopf.get("gestalt") if isinstance(kopf.get("gestalt"), dict) else {}
    auftraege = _lesen(lambda:
        "SELECT * FROM (SELECT id, nachricht, antwort, status, hinweise, vorschlag, "
        "coalesce(denken, '') AS denken, coalesce(schritte, '[]'::jsonb) AS schritte, "
        "erstellt_am::text AS erstellt_am, erstellt_am AS sortiert_am FROM marketing.marken_auftraege "
        f"WHERE mandant = {lit(m)} AND art = 'chat' ORDER BY erstellt_am DESC LIMIT 10) q ORDER BY sortiert_am")
    for z in auftraege:
        z.pop("sortiert_am", None)
    offen = _lesen(lambda:
        "SELECT art, erstellt_am::text AS erstellt_am FROM marketing.marken_auftraege "
        f"WHERE mandant = {lit(m)} AND status IN ('offen', 'in_arbeit') ORDER BY erstellt_am")
    arten = {z.get("art") for z in offen if isinstance(z, dict)}
    # Alter des Uebernahme-Auftrags (aeltester offener): die Oberflaeche sagt nach 60 s "sobald der PC laeuft".
    seit = next((z.get("erstellt_am") for z in offen
                 if isinstance(z, dict) and z.get("art") == "uebernehmen"), None)
    v = _lesen_einer(lambda:
        "SELECT id, vorschlag, erstellt_am::text AS erstellt_am FROM marketing.marken_vorschlaege "
        f"WHERE mandant = {lit(m)} AND status = 'offen'")
    # Das zuletzt uebernommene Profil (Rowboat-Text selbst liefert die API nicht): Abschnitte + Werte.
    # Nur ein Vorschlag, dessen Uebernahme fertig ist - solange sie laeuft, steht er noch nicht in Rowboat.
    ang = _lesen_einer(lambda:
        "SELECT v.vorschlag FROM marketing.marken_vorschlaege v "
        f"WHERE v.mandant = {lit(m)} AND v.status = 'angenommen' AND EXISTS (SELECT 1 FROM "
        "marketing.marken_auftraege a WHERE a.vorschlag = v.id AND a.art = 'uebernehmen' AND a.status = 'fertig') "
        "ORDER BY v.entschieden_am DESC LIMIT 1")
    ang_v = ang.get("vorschlag") if isinstance(ang, dict) and isinstance(ang.get("vorschlag"), dict) else None
    aktuell = ({"abschnitte": ang_v.get("abschnitte") if isinstance(ang_v.get("abschnitte"), dict) else {},
                "werte": {k: x for k, x in ang_v.items() if k not in ("abschnitte", "mustertext")}}
               if ang_v is not None else None)
    # Ergebnis der letzten abgeschlossenen Uebernahme: ein Fehlschlag hat sonst keinen Ort auf der Seite (I6)
    letzte = _lesen_einer(lambda:
        "SELECT status, antwort, hinweise, coalesce(schritte, '[]'::jsonb) AS schritte, "
        "geaendert_am::text AS geaendert_am FROM marketing.marken_auftraege "
        f"WHERE mandant = {lit(m)} AND art = 'uebernehmen' AND status IN ('fertig', 'fehler') "
        "ORDER BY geaendert_am DESC LIMIT 1")
    laufend = _lesen_einer(lambda:
        "SELECT art, coalesce(denken, '') AS denken, coalesce(schritte, '[]'::jsonb) AS schritte "
        f"FROM marketing.marken_auftraege WHERE mandant = {lit(m)} AND status = 'in_arbeit' "
        "ORDER BY geaendert_am DESC LIMIT 1")
    hinweise = kopf.get("hinweise") if isinstance(kopf.get("hinweise"), list) else []
    return {"mandant": m, "name": kopf.get("name"),
            "spiegel": {"gestalt": {k: gestalt[k] for k in SPIEGEL_SCHLUESSEL if k in gestalt},
                        "stand": kopf.get("stand") or "", "gespiegelt_am": kopf.get("gespiegelt_am"),
                        "fehler": kopf.get("fehler")},
            "profil_hinweise": [str(h) for h in hinweise],
            "auftraege": auftraege, "laeuft": "chat" in arten,
            "vorschlag": ({"id": str(v["id"]), "vorschlag": v.get("vorschlag"), "erstellt_am": v.get("erstellt_am")}
                          if v else None),
            "uebernahme": "laeuft" if "uebernehmen" in arten else None,
            "uebernahme_seit": seit if "uebernehmen" in arten else None, "aktuell": aktuell,
            "letzte_uebernahme": letzte or None, "laufend": laufend or None}


@pult_router.post("/marke/chat")
def marke_chat(payload: dict = Body(...), x_pult_key: str | None = Header(None)):
    _schluessel(x_pult_key)
    m = _mandant_pflicht(payload.get("mandant"))
    nachricht, kontext = _nachricht_und_kontext(payload)
    zeile = _schreiben(lambda:
        f"SELECT marketing.pult_marke_anlegen({lit(m)}, {lit(nachricht)}, "
        f"{lit(json.dumps(kontext, ensure_ascii=False))}::jsonb) AS id")
    return {"auftrag": str(zeile["id"])}


@pult_router.post("/marke/vorschlaege/{vid}/uebernehmen")
def marke_uebernehmen(vid: str, payload: dict = Body(...), x_pult_key: str | None = Header(None)):
    """Ein neuerer oder schon entschiedener Vorschlag ist 422 mit der Meldung der DB
    ("Inzwischen gibt es ein neueres Profil – bitte neu laden")."""
    _schluessel(x_pult_key)
    v = _vorschlag_id(vid)
    von = _von(payload)
    m = _mandant_pflicht(payload.get("mandant"))   # Firma der Oberflaeche; die DB vergleicht (Minor 4)
    zeile = _schreiben(lambda:
        f"SELECT marketing.pult_marke_uebernehmen({lit(v)}::uuid, {lit(von)}, {lit(m)}) AS id")
    return {"auftrag": str(zeile["id"])}


@pult_router.post("/marke/vorschlaege/{vid}/verwerfen")
def marke_verwerfen(vid: str, payload: dict = Body(...), x_pult_key: str | None = Header(None)):
    _schluessel(x_pult_key)
    v = _vorschlag_id(vid)
    von = _von(payload)
    m = _mandant_pflicht(payload.get("mandant"))
    zeile = _schreiben(lambda:
        f"SELECT marketing.pult_marke_verwerfen({lit(v)}::uuid, {lit(von)}, {lit(m)}) AS status")
    return {"status": zeile["status"]}


@pult_router.post("/inhalte/{iid}/marke_hinweis_aus")
def marke_hinweis_aus(iid: str, x_pult_key: str | None = Header(None)):
    _schluessel(x_pult_key)
    i = _uuid_oder_404(iid)
    zeile = _schreiben(lambda: f"SELECT marketing.pult_marke_hinweis_aus({lit(i)}::uuid) AS ok")
    if not zeile.get("ok"):
        raise HTTPException(404, "Unbekannter Inhalt")
    return {"ok": True}


def _logo_verweis(vorschlag: dict, mandant: str) -> str | None:
    """Logo des Vorschlags als Medienname der Firma: "anhang:<name>" oder ein ueber /logo
    abgelegter Name. "web:<n>" (noch nicht geladen) und fremde Bilder gibt es nicht."""
    roh = vorschlag.get("logo")
    if not isinstance(roh, str):
        return None
    name = roh[len("anhang:"):] if roh.startswith("anhang:") else roh
    if not _LOGO_NAME.fullmatch(name) or ist_fremd(name, mandant, sicht(mandant)):
        return None
    if not any(_im_ordner(o, name) for o in quellen()):
        return None                                  # Datei fehlt: Wortmarke statt kaputtem Bild
    return name


def _muster_einsetzen(dok: dict, muster) -> str:
    """Ueberschrift und Absatz in die Textplaetze der Vorlage studio, Betreff als Rueckgabe."""
    if not isinstance(muster, dict):
        return ""
    for feld, bid in _MUSTER_BLOECKE.items():
        text = muster.get(feld)
        props = ((dok.get(bid) or {}).get("data") or {}).get("props") if isinstance(dok.get(bid), dict) else None
        if isinstance(text, str) and text.strip() and isinstance(props, dict):
            props["text"] = text.strip()[:MUSTER_MAX[feld]]
    betreff = muster.get("betreff")
    return betreff.strip()[:MUSTER_MAX["betreff"]] if isinstance(betreff, str) else ""


@pult_router.get("/marke/vorschlaege/{vid}/vorschau")
def marke_vorschau(vid: str, mandant: str | None = None, format: str = "mail", bild_basis: str = "",
                   x_pult_key: str | None = Header(None)):
    """Nur ein Vorschlag der Firma, die die Oberflaeche zeigt (Minor 4); sonst 404 wie unbekannt."""
    _schluessel(x_pult_key)
    v = _vorschlag_id(vid)
    firma = _mandant_pflicht(mandant)
    fmt = _auswahl(format, ("mail", "handy"), "format") or "mail"
    basis = _bild_basis(bild_basis)
    z = _lesen_einer(lambda:
        "SELECT v.vorschlag, v.mandant, m.name, m.pflichtteil FROM marketing.marken_vorschlaege v "
        f"JOIN marketing.mandanten m ON m.id = v.mandant WHERE v.id = {lit(v)}::uuid AND v.mandant = {lit(firma)}")
    if not z:
        raise HTTPException(404, "Unbekannter Vorschlag")
    m, vorschlag = z["mandant"], z.get("vorschlag") if isinstance(z.get("vorschlag"), dict) else {}
    vorlage = _lesen_einer(lambda:
        "SELECT bloecke FROM marketing.newsletter_vorlagen "
        f"WHERE name = {lit(MUSTER_VORLAGE)} AND status = 'freigegeben' AND (mandant = {lit(m)} OR fuer_alle)")
    if not vorlage:
        raise HTTPException(422, VORLAGE_FEHLT)
    gestalt: dict = {"akzent": vorschlag.get("akzent"), "flaeche": vorschlag.get("zweitfarbe")}
    anzeige, text = vorschlag.get("schrift_anzeige"), vorschlag.get("schrift_text")
    if anzeige in schriften.REGISTER and text in schriften.REGISTER:
        gestalt["schriften"] = {"anzeige": anzeige, "text": text}
    logo = _logo_verweis(vorschlag, m)
    fertig, _ = _vorlage_fuellen(vorlage["bloecke"], m, {"laden": z.get("name"), "layout": None, "gestalt": gestalt},
                                 logo=f"medien:{logo}" if logo else None)
    betreff = _muster_einsetzen(fertig, vorschlag.get("mustertext"))
    return _bloecke_html(fertig, betreff, "", z.get("pflichtteil"), fmt, basis)


# ─── Arbeiter ───────────────────────────────────────────────────────────


def _in_arbeit(a: str, status_sonst: int = 409) -> dict:
    """Auftrag lesen; nur in_arbeit mit gueltiger Vergabe zaehlt (sonst 404/status_sonst)."""
    z = _lesen_einer(lambda:
        "SELECT mandant, art, status, coalesce(vergeben_bis > now(), false) AS gueltig "
        f"FROM marketing.marken_auftraege WHERE id = {lit(a)}::uuid")
    if not z:
        raise HTTPException(404, "Unbekannter Auftrag")
    if z.get("status") != "in_arbeit" or not z.get("gueltig"):
        raise HTTPException(status_sonst, "Auftrag ist nicht (mehr) in Arbeit")
    return z


def _hinweise(payload: dict) -> list:
    h = payload.get("hinweise", [])
    if not isinstance(h, list) or not all(isinstance(x, str) for x in h) or len(h) > 50:
        raise HTTPException(422, "hinweise muss eine Liste von Texten sein")
    return h


def _antwort(payload: dict) -> str:
    a = payload.get("antwort")
    if not isinstance(a, str):
        raise HTTPException(422, "antwort muss Text sein")
    return a[:4000]


@arbeiter_router.post("/naechster")
def arbeiter_naechster(x_bild_key: str | None = Header(None)):
    _bild_schluessel(x_bild_key)
    zeile = _schreiben(lambda: f"SELECT marketing.pult_marke_naechster({lit(FRIST)}::interval) AS a")
    a = zeile.get("a")
    if not (isinstance(a, dict) and a.get("id")):
        return {"auftrag": None}
    v = a.get("vorschlag") if isinstance(a.get("vorschlag"), dict) else {}
    if a.get("art") == "uebernehmen" and v.get("id"):
        # Urheber fuer "stand: ... von <name>" in der Marke.md. Der Auftrag ist schon vergeben:
        # scheitert das Lesen, geht er ohne Urheber raus (der PC schreibt dann "von Marken-Chat").
        try:
            von = _lesen_einer(lambda: "SELECT entschieden_von AS von FROM marketing.marken_vorschlaege "
                                       f"WHERE id = {lit(str(v['id']))}::uuid")
        except HTTPException:
            von = None
        if von and von.get("von"):
            a["von"] = von["von"]
    return {"auftrag": a}


@arbeiter_router.get("/firmen")
def arbeiter_firmen(x_bild_key: str | None = Header(None)):
    """Aktive Firmen mit dem Spiegel-Teil ihres Standard-Newsletter-Layouts, fuer den Abgleich am PC."""
    _bild_schluessel(x_bild_key)
    zeilen = _lesen(lambda:
        "SELECT m.id, m.name, s.stand, s.hinweise, s.fehler, "
        "(SELECT l.gestalt FROM marketing.layout_vorlagen l WHERE l.mandant = m.id AND l.standard "
        "AND l.inhaltsart = 'newsletter' AND l.art = 'layout') AS gestalt "
        "FROM marketing.mandanten m LEFT JOIN marketing.marken_spiegel s ON s.mandant = m.id "
        "WHERE m.aktiv ORDER BY m.id")
    firmen = []
    for z in zeilen:
        gestalt = z.get("gestalt") if isinstance(z.get("gestalt"), dict) else {}
        firmen.append({"id": z.get("id"), "name": z.get("name"), "stand": z.get("stand") or "",
                       "gestalt": {k: gestalt[k] for k in SPIEGEL_SCHLUESSEL if k in gestalt},
                       "hinweise": z.get("hinweise") if isinstance(z.get("hinweise"), list) else [],
                       "fehler": z.get("fehler")})
    return {"firmen": firmen}


@arbeiter_router.post("/hinweise")
def arbeiter_hinweise(payload: dict = Body(...), x_bild_key: str | None = Header(None)):
    """Lese-Hinweise der Marke.md einer Firma ("Marke.md: akzent ungültig"), vom Abgleich gemeldet (I3)."""
    _bild_schluessel(x_bild_key)
    m = _mandant_pflicht(payload.get("mandant"))
    hinweise = payload.get("hinweise")
    if not isinstance(hinweise, list):
        raise HTTPException(422, "hinweise muss eine Liste von Texten sein")
    hinweise = _hinweise(payload)
    zeile = _schreiben(lambda:
        f"SELECT marketing.pult_marke_profil_hinweise({lit(m)}, "
        f"{lit(json.dumps([h[:300] for h in hinweise], ensure_ascii=False))}::jsonb) AS ok")
    return {"ok": bool(zeile.get("ok"))}


@arbeiter_router.post("/{aid}/weiter")
def arbeiter_weiter(aid: str, x_bild_key: str | None = Header(None)):
    _bild_schluessel(x_bild_key)
    a = _auftrag_id(aid)
    zeile = _schreiben(lambda:
        f"SELECT marketing.pult_marke_verlaengern({lit(a)}::uuid, {lit(FRIST)}::interval) AS ok")
    return {"ok": bool(zeile.get("ok"))}


@arbeiter_router.post("/{aid}/denken")
async def arbeiter_denken(aid: str, request: Request, x_bild_key: str | None = Header(None)):
    """Denkspur des Arbeiters; 409 wenn der Auftrag nicht mehr in Arbeit ist."""
    _bild_schluessel(x_bild_key)
    a = _auftrag_id(aid)
    denken, schritte = spur_pruefen(await _json_gekappt(request, SPUR_KOERPER_MAX))
    zeile = await run_in_threadpool(_schreiben, lambda:
        f"SELECT marketing.pult_marke_denken({lit(a)}::uuid, {lit(denken)}, "
        f"{lit(json.dumps(schritte, ensure_ascii=False))}::jsonb) AS ok")
    if not zeile.get("ok"):
        raise HTTPException(409, "Auftrag nicht mehr in Arbeit")
    return {"ok": True}


@arbeiter_router.post("/{aid}/vorschlag")
def arbeiter_vorschlag(aid: str, payload: dict = Body(...), x_bild_key: str | None = Header(None)):
    _bild_schluessel(x_bild_key)
    a = _auftrag_id(aid)
    vorschlag = payload.get("vorschlag")
    if not isinstance(vorschlag, dict):
        raise HTTPException(422, "vorschlag muss ein Objekt sein")
    antwort, hinweise = _antwort(payload), _hinweise(payload)
    zeile = _schreiben(lambda:
        f"SELECT marketing.pult_marke_vorschlag({lit(a)}::uuid, "
        f"{lit(json.dumps(vorschlag, ensure_ascii=False))}::jsonb, {lit(antwort)}, "
        f"{lit(json.dumps(hinweise, ensure_ascii=False))}::jsonb) AS id")
    return {"vorschlag": str(zeile["id"])}


@arbeiter_router.post("/{aid}/fertig")
def arbeiter_fertig(aid: str, payload: dict = Body(...), x_bild_key: str | None = Header(None)):
    _bild_schluessel(x_bild_key)
    a = _auftrag_id(aid)
    antwort, hinweise = _antwort(payload), _hinweise(payload)
    zeile = _schreiben(lambda:
        f"SELECT marketing.pult_marke_fertig({lit(a)}::uuid, {lit(antwort)}, "
        f"{lit(json.dumps(hinweise, ensure_ascii=False))}::jsonb) AS n")
    return {"markiert": int(zeile.get("n") or 0)}


@arbeiter_router.post("/{aid}/zurueck")
def arbeiter_zurueck(aid: str, payload: dict = Body(...), x_bild_key: str | None = Header(None)):
    _bild_schluessel(x_bild_key)
    a = _auftrag_id(aid)
    antwort = _antwort(payload)
    zeile = _schreiben(lambda: f"SELECT marketing.pult_marke_zurueck({lit(a)}::uuid, {lit(antwort)}) AS s")
    return {"status": zeile.get("s")}


@arbeiter_router.get("/{aid}/medien/{name}")
def arbeiter_medien(aid: str, name: str, x_bild_key: str | None = Header(None)):
    """Nur Medien der Firma des Auftrags und Gemeinsames (geteilte Regel: chat._medien_ausliefern)."""
    _bild_schluessel(x_bild_key)
    a = _auftrag_id(aid)
    if not _ARBEITER_DATEI.fullmatch(name):
        raise HTTPException(404, "Unbekannte Datei")
    job = _in_arbeit(a, 404)
    return _medien_ausliefern(name, job.get("mandant"))


def _logo_pruefen(roh: bytes) -> str:
    """PNG/JPEG per Signatur und Pillow (ganz dekodiert) -> Endung png|jpg."""
    if roh.startswith(_PNG):
        fmt, endung = "PNG", "png"
    elif roh.startswith(_JPEG):
        fmt, endung = "JPEG", "jpg"
    else:
        raise HTTPException(422, "Nur PNG oder JPEG")
    from PIL import Image, UnidentifiedImageError
    try:
        with Image.open(io.BytesIO(roh)) as bild:
            if bild.format != fmt:
                raise HTTPException(422, "Logo ist kein gültiges PNG/JPEG")
            if bild.size[0] * bild.size[1] > LOGO_PIXEL_MAX:
                raise HTTPException(422, "Logo hat zu viele Bildpunkte (höchstens 25 Megapixel)")
            bild.verify()
        with Image.open(io.BytesIO(roh)) as bild:
            bild.load()   # ganz dekodieren: abgeschnittene Dateien bestehen verify()
    except HTTPException:
        raise
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        raise HTTPException(422, "Logo ist kaputt")
    return endung


def _logo_ablegen(ordner: str, name: str, roh: bytes) -> bool:
    """Legt die Datei atomar ab (temp + replace). -> True, wenn sie neu angelegt wurde."""
    ziel = os.path.join(ordner, name)
    if os.path.exists(ziel):
        return False
    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(dir=ordner, suffix=".teil")
        with os.fdopen(fd, "wb") as f:
            f.write(roh)
        os.chmod(tmp, 0o644)
        os.replace(tmp, ziel)
        return True
    except OSError:
        if tmp:
            try:
                os.remove(tmp)
            except OSError:
                pass
        raise HTTPException(503, "Logo konnte nicht abgelegt werden")


@arbeiter_router.post("/{aid}/logo")
async def arbeiter_logo(aid: str, request: Request, x_bild_key: str | None = Header(None)):
    """Web-Logo des PC (Body = Bytes) in die Medien der Firma des Auftrags. Antwort: der Medienname
    (marke-<mandant>-logo-<hash10>.<png|jpg>), der als vorschlag.logo benutzt wird. Fail-closed:
    scheitert die Zuordnung, wird die eben angelegte Datei wieder geloescht."""
    _bild_schluessel(x_bild_key)
    a = _auftrag_id(aid)
    ordner = _ordner()
    try:
        laenge = int(request.headers.get("content-length") or 0)
    except ValueError:
        laenge = 0
    if laenge > LOGO_MAX:
        raise HTTPException(422, "Logo größer als 2 MB")
    roh = bytearray()
    async for stueck in request.stream():
        roh += stueck
        if len(roh) > LOGO_MAX:
            raise HTTPException(422, "Logo größer als 2 MB")
    roh = bytes(roh)
    endung = await run_in_threadpool(_logo_pruefen, roh)
    job = await run_in_threadpool(_in_arbeit, a)
    if job.get("art") != "chat":
        raise HTTPException(422, "Logos gibt es nur im Chat-Auftrag")
    name = f"marke-{job['mandant']}-logo-{hashlib.sha256(roh).hexdigest()[:10]}.{endung}"

    def ablegen_und_zuordnen() -> None:
        neu = _logo_ablegen(ordner, name, roh)
        try:
            medien_mandant.zuordnen([name], job["mandant"])
        except Exception:
            if neu:
                try:
                    os.remove(os.path.join(ordner, name))
                except OSError:
                    pass
            raise
    await run_in_threadpool(ablegen_und_zuordnen)
    return {"name": name}


@arbeiter_router.post("/spiegeln")
async def arbeiter_spiegeln(request: Request, x_bild_key: str | None = Header(None)):
    """Gestalt der Marke ins Standard-Layout der Firma spiegeln. Ungueltige Werte spiegelt die DB
    nicht (NULL + Fehler in marken_spiegel): Antwort {ok: false, fehler} mit 200, damit der PC
    es protokollieren kann; der Spiegel behaelt seinen letzten gueltigen Stand."""
    _bild_schluessel(x_bild_key)
    payload = await _json_gekappt(request, SPIEGEL_KOERPER_MAX, "Gestalt zu groß")
    m = _mandant_pflicht(payload.get("mandant"))
    gestalt, stand = payload.get("gestalt"), payload.get("stand")
    if not isinstance(gestalt, dict):
        raise HTTPException(422, "gestalt muss ein Objekt sein")
    if not isinstance(stand, str) or len(stand) > 200:
        raise HTTPException(422, "stand muss Text mit hoechstens 200 Zeichen sein")

    def spiegeln() -> dict:
        zeile = _schreiben(lambda:
            f"SELECT marketing.pult_marke_spiegeln({lit(m)}, {lit(json.dumps(gestalt, ensure_ascii=False))}::jsonb, "
            f"{lit(stand)}) AS n")
        if zeile.get("n") is not None:
            return {"ok": True, "fassung": int(zeile["n"])}
        grund = _lesen_einer(lambda: f"SELECT fehler FROM marketing.marken_spiegel WHERE mandant = {lit(m)}")
        return {"ok": False, "fehler": (grund or {}).get("fehler") or "Gestalt ungültig"}
    return await run_in_threadpool(spiegeln)


@arbeiter_router.post("/markieren")
def arbeiter_markieren(payload: dict = Body(...), x_bild_key: str | None = Header(None)):
    _bild_schluessel(x_bild_key)
    m = _mandant_pflicht(payload.get("mandant"))
    zeile = _schreiben(lambda: f"SELECT marketing.pult_marke_markieren({lit(m)}) AS n")
    return {"markiert": int(zeile.get("n") or 0)}
