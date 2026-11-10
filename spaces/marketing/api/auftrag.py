"""Auftrags-Arbeiter (M8 Stufe A, Auftragsbuch): der PC-Arbeiter holt Auftraege fuer Marketing ab (Reel),
stoesst das KI-Reel an und meldet das Ergebnis zurueck. Alle Routen verlangen X-Bild-Key (wie der
Bild-Arbeiter; der Praefix /api/auftrag/arbeiter/ ist vom globalen X-API-Key ausgenommen). Die Regeln
(Lease, Versuche, erlaubte Standwechsel) stehen in den SQL-Funktionen auftrag.* (096); die API prueft Form
und Verknuepfung. Der Mandant kommt immer aus der Auftragszeile, nie aus dem Body.
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Body, Header, HTTPException

from spaces.marketing.api.bilder import _bild_schluessel
from spaces.marketing.api.pult import _lesen, _lesen_einer, _schreiben, _uuid_oder_404, lit
from spaces.marketing.api.video import TITEL_MAX, _stimme_einsetzen, _text, befund_satz, briefing_pruefen

router = APIRouter(prefix="/api/auftrag/arbeiter")

AN = "marketing"
LEASE = "10 minutes"
TITEL_LAENGE, TEXT_LANG_MAX, TEXT_KURZ_MAX, GRUND_MAX = 90, 1300, 280, 300
KI_ANZAHL = 4
RENDER_FRIST_MIN = 120                    # die Renderphase bekommt ab Reel-Anlage ein eigenes Budget


def _ok(zeile: dict | None) -> bool:
    return bool(zeile and zeile.get("ok"))


def _text_pflicht(payload: dict, feld: str, maximal: int) -> str:
    wert = payload.get(feld)
    if not isinstance(wert, str) or not 1 <= len(wert.strip()) <= maximal:
        raise HTTPException(422, f"{feld}: 1 bis {maximal} Zeichen")
    return wert.strip()


def _eigener(aid: str) -> None:
    """404, wenn der Auftrag nicht existiert oder nicht an Marketing geht (vor jedem Schreiben)."""
    if not _lesen_einer(lambda:
            f"SELECT id FROM auftrag.auftraege WHERE id = {lit(aid)}::uuid AND an = {lit(AN)}"):
        raise HTTPException(404, "Unbekannter Auftrag")


def _mischen(aid: str, teil: dict) -> bool:
    roh = json.dumps(teil, ensure_ascii=False)
    return _ok(_schreiben(lambda: f"SELECT auftrag.ergebnis_mischen({lit(aid)}::uuid, {lit(roh)}::jsonb) AS ok"))


@router.post("/naechster")
def naechster(x_bild_key: str | None = Header(None)):
    """Aeltesten uebernehmbaren Marketing-Auftrag vergeben ({id, art, mandant, eingabe, ergebnis, versuch}) oder None."""
    _bild_schluessel(x_bild_key)
    zeile = _schreiben(lambda: f"SELECT auftrag.naechster({lit(AN)}, {lit(LEASE)}::interval) AS a")
    return {"auftrag": zeile.get("a")}


@router.get("/laufend")
def laufend(x_bild_key: str | None = Header(None)):
    """Auftraege in Arbeit, die auf den Render warten (ergebnis.video gesetzt). Aendert nichts."""
    _bild_schluessel(x_bild_key)
    zeilen = _lesen(lambda: f"SELECT auftrag.laufende({lit(AN)}) AS a")
    return {"auftraege": [z["a"] for z in zeilen if isinstance(z, dict) and z.get("a")]}


@router.post("/{aid}/weiter")
def weiter(aid: str, x_bild_key: str | None = Header(None)):
    """Lease verlaengern; ok false = aufhoeren (Auftrag nicht mehr in Arbeit)."""
    _bild_schluessel(x_bild_key)
    a = _uuid_oder_404(aid)
    _eigener(a)
    zeile = _schreiben(lambda: f"SELECT auftrag.verlaengern({lit(a)}::uuid, {lit(LEASE)}::interval) AS ok")
    return {"ok": _ok(zeile)}


@router.post("/{aid}/reel")
def reel_anlegen(aid: str, payload: dict = Body(...), x_bild_key: str | None = Header(None)):
    """KI-Reel fuer den Auftrag anlegen (Video-Auftrag art bilder, beginnt bei den Bildideen) und ins
    Ergebnis eintragen. 409, wenn der Auftrag schon ein Video hat oder nicht in Arbeit ist."""
    _bild_schluessel(x_bild_key)
    a = _uuid_oder_404(aid)
    titel = _text(payload.get("titel"), "titel", TITEL_MAX)
    briefing = briefing_pruefen("bilder", {**payload, "ki": {"anzahl": KI_ANZAHL}, "format": "reel"})
    briefing["auftrag"] = a                     # nach der Pruefung: briefing_pruefen kuerzt Unbekanntes
    zeile = _lesen_einer(lambda:
        f"SELECT mandant, stand, ergebnis FROM auftrag.auftraege "
        f"WHERE id = {lit(a)}::uuid AND an = {lit(AN)}")
    if not zeile:
        raise HTTPException(404, "Unbekannter Auftrag")
    ergebnis = zeile.get("ergebnis")
    if isinstance(ergebnis, dict) and ergebnis.get("video"):
        raise HTTPException(409, "Der Auftrag hat schon ein Video")
    if zeile.get("stand") != "in_arbeit":
        raise HTTPException(409, "Der Auftrag ist nicht in Arbeit")
    mandant = zeile.get("mandant")              # immer aus der Auftragszeile, nie aus dem Body
    _stimme_einsetzen(mandant, briefing)        # wie beim Anlegen im Pult: Markenstimme gilt vor dem Briefing
    roh = json.dumps(briefing, ensure_ascii=False)
    angelegt = _schreiben(lambda:
        f"SELECT marketing.video_auftrag_anlegen({lit(mandant)}, 'bilder', {lit(titel)}, "
        f"{lit(roh)}::jsonb, 'agent', 'ideen') AS id")
    video = str(angelegt.get("id"))
    drehbuch = {k: briefing[k] for k in ("thema", "sprechertext", "hook", "dauer_s")}
    drehbuch["titel"] = titel
    if not _mischen(a, {"video": video, "drehbuch": drehbuch}):
        raise HTTPException(409, "Der Auftrag ist nicht mehr in Arbeit")
    _schreiben(lambda:                          # Renderzeit zaehlt nicht gegen die Frist des Auftrags (I6)
        f"SELECT auftrag.renderfrist({lit(a)}::uuid, {RENDER_FRIST_MIN}) AS ok")
    return {"video": video}


@router.get("/{aid}/reel")
def reel_lesen(aid: str, x_bild_key: str | None = Header(None)):
    """Stand des verknuepften Video-Auftrags (befund als Satz, nie roh); 404 ohne Verknuepfung."""
    _bild_schluessel(x_bild_key)
    a = _uuid_oder_404(aid)
    zeile = _lesen_einer(lambda:
        "SELECT v.status, v.datei, v.befund FROM auftrag.auftraege a "
        "JOIN marketing.video_auftraege v ON v.id = (a.ergebnis->>'video')::uuid "
        f"WHERE a.id = {lit(a)}::uuid AND a.an = {lit(AN)}")
    if not zeile:
        raise HTTPException(404, "Kein Reel verknuepft")
    return {"status": zeile.get("status"), "datei": zeile.get("datei"), "befund": befund_satz(zeile.get("befund"))}


@router.post("/{aid}/erzeugt")
def erzeugt(aid: str, payload: dict = Body(...), x_bild_key: str | None = Header(None)):
    """Texte + fertiges Video ins Ergebnis, Stand in_arbeit -> erzeugt. Die Datei kommt aus der Video-Zeile."""
    _bild_schluessel(x_bild_key)
    a = _uuid_oder_404(aid)
    teil = {"titel": _text_pflicht(payload, "titel", TITEL_LAENGE),
            "text_lang": _text_pflicht(payload, "text_lang", TEXT_LANG_MAX),
            "text_kurz": _text_pflicht(payload, "text_kurz", TEXT_KURZ_MAX)}
    zeile = _lesen_einer(lambda:
        "SELECT a.stand, v.status, v.datei FROM auftrag.auftraege a "
        "LEFT JOIN marketing.video_auftraege v ON v.id = (a.ergebnis->>'video')::uuid "
        f"WHERE a.id = {lit(a)}::uuid AND a.an = {lit(AN)}")
    if not zeile:
        raise HTTPException(404, "Unbekannter Auftrag")
    if zeile.get("stand") != "in_arbeit":
        raise HTTPException(409, "Der Auftrag ist nicht in Arbeit")
    if zeile.get("status") != "fertig" or not zeile.get("datei"):
        raise HTTPException(409, "Das Video ist noch nicht fertig")
    if not _mischen(a, {"datei": zeile["datei"], **teil}):
        raise HTTPException(409, "Der Auftrag ist nicht mehr in Arbeit")
    gesetzt = _schreiben(lambda:
        f"SELECT auftrag.stand_setzen({lit(a)}::uuid, 'in_arbeit', 'erzeugt') AS ok")
    if not _ok(gesetzt):
        raise HTTPException(409, "Der Auftrag ist nicht mehr in Arbeit")
    return {"ok": True}


@router.post("/{aid}/fehler")
def fehler(aid: str, payload: dict = Body(...), x_bild_key: str | None = Header(None)):
    """Auftrag als gescheitert melden (in_arbeit -> fehler); der Grund wird auf 300 Zeichen gekuerzt."""
    _bild_schluessel(x_bild_key)
    a = _uuid_oder_404(aid)
    grund = payload.get("grund")
    if not isinstance(grund, str):
        raise HTTPException(422, "grund muss Text sein")
    grund = (grund.strip() or "Kein Grund angegeben")[:GRUND_MAX]
    _eigener(a)
    zeile = _schreiben(lambda:
        f"SELECT auftrag.stand_setzen({lit(a)}::uuid, 'in_arbeit', 'fehler', {lit(grund)}) AS ok")
    return {"ok": _ok(zeile)}
