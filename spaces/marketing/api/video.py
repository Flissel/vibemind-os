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
import struct
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FutureTimeout

from fastapi import APIRouter, Body, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool

from spaces.marketing.api import medien_mandant
from spaces.marketing.api.bilder import _agent_schluessel, _auftrag_id, _bild_schluessel, _ordner
from spaces.marketing.api.pult import _lesen, _lesen_einer, _schluessel, _schreiben, _uuid_oder_404, lit
from spaces.marketing.api.chat import _markenlogos
from spaces.marketing.api.ton import _slug
from spaces.marketing.claw import laura, schriften
from spaces.marketing.claw.stimmen import STANDARD, stimmen_liste

router = APIRouter(prefix="/api/video")
pult_router = APIRouter(prefix="/api/pult/video")

ARTEN = ("material", "bilder", "kurzfassung")
FORMATE = ("reel", "quadrat", "quer")   # Groessen stehen nur in Laura (render/formate.py); reel = Vorgabe 9:16
FRIST = "15 minutes"
LINK_GUELTIG_S = 30 * 60
MP4_MAX = 15 * 1024 * 1024          # sales-claw nimmt hoechstens 15 MB (medien.MAX_BYTES)
POSTER_MAX = 1024 * 1024            # Vorschaubild (JPEG, 540 px breit) des Video-Claws
BILDER_MAX = 200                    # Bildauswahl der gefuehrten Bestellung
TITEL_MAX, THEMA_MAX, TEXT_MAX, HOOK_MAX = 120, 300, 900, 120
WUNSCH_MAX = 500                    # Aenderungswunsch (085: char_length <= 500)
KI_MIN, KI_MAX = 3, 5                # KI-Bilder je Reel (090: briefing.ki.anzahl)
LAURA_AUS = "Laura ist gerade nicht erreichbar."
LAURA_LANGSAM = "Laura antwortet gerade zu langsam – versuch es gleich nochmal."
LAURA_TEILWEISE = "Einige Laura-Projekte antworten gerade nicht."
LAURA_NAME_MAX = 120
MATERIAL_FRIST_S = 6.0              # so lange wartet /material hoechstens auf Laura
MATERIAL_SPEICHER_S = 60.0          # so lange gilt das letzte gute Ergebnis
_MANDANT = re.compile(r"^[a-z][a-z0-9_-]{0,40}$")
_LAURA_ID = re.compile(r"^[0-9a-f][0-9a-f-]{7,63}$")
_STIMME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
_FARBE = re.compile(r"^#[0-9a-fA-F]{6}$")
_BILD = (".png", ".jpg", ".jpeg", ".webp")
_DATEI = re.compile(r"^vid-[0-9a-f]{8}-[a-z0-9-]{1,40}\.mp4$")
_POSTER = re.compile(r"^vid-[0-9a-f]{8}-[a-z0-9-]{1,40}\.jpg$")
_JPEG = b"\xff\xd8\xff"
_SPALTEN = ("id, mandant, art, titel, briefing, status, datei, dauer_s, bytes, befund, protokoll, "
            "angelegt_von, versuche, erstellt_am, geaendert_am, vorgaenger, aenderungswunsch, archiviert, poster")

# Befund als Satz fuer die Seite (E4): erstes passendes Muster gewinnt, ohne Gross/Klein.
_BEFUND_SAETZE = (
    (re.compile(r"Zwei Versuche ohne Ergebnis", re.I), "Der Bau ist zweimal hängen geblieben."),
    (re.compile(r"Stimmprobe", re.I), "Die gewählte Stimme gibt es nicht."),
    (re.compile(r"Render", re.I), "Laura konnte das Video nicht fertig rendern."),
    (re.compile(r"zeitlimit|timeout|zu lange", re.I), "Der Bau hat zu lange gedauert."),
    (re.compile(r"abgebrochen", re.I), "Der Auftrag wurde abgebrochen."),
)
_BEFUND_SONST = "Das Reel konnte nicht gebaut werden."


def befund_satz(befund: str) -> str:
    """Rohbefund (Claw, Laura, DB) -> ein verstaendlicher Satz; leer bleibt leer."""
    if not isinstance(befund, str) or not befund.strip():
        return ""
    for muster, satz in _BEFUND_SAETZE:
        if muster.search(befund):
            return satz
    return _BEFUND_SONST


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
    if art != "bilder" and b.get("ki") is not None:
        raise HTTPException(422, "ki: KI-Bilder nur bei art bilder")
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
    fmt = b.get("format")
    fmt = "reel" if fmt is None or fmt == "" else fmt      # nur None und "" gelten als fehlend
    if not isinstance(fmt, str) or fmt not in FORMATE:
        raise HTTPException(422, "format: reel, quadrat oder quer")
    if fmt != "reel":                   # reel bleibt ohne Feld: heutige Briefings unveraendert
        aus["format"] = fmt
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
        ki = b.get("ki")
        if ki is not None:
            # KI-Bilder (090, E2): der Bild-Arbeiter entwirft und erzeugt 3..5 Bilder; die Liste kommt erst
            # mit der Freigabe (video_ki_freigeben) ins Briefing.
            anzahl = ki.get("anzahl") if isinstance(ki, dict) else None
            if isinstance(anzahl, bool) or not isinstance(anzahl, int) or not KI_MIN <= anzahl <= KI_MAX:
                raise HTTPException(422, f"ki: {{anzahl}} mit {KI_MIN} bis {KI_MAX} Bildern")
            if b.get("bilder"):
                raise HTTPException(422, "ki: mit KI-Bildern keine eigene Bilderliste")
            aus["ki"] = {"anzahl": anzahl}
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


def _markenstimme(mandant: str) -> str | None:
    """Stimme aus dem Markenprofil der Firma (marken_spiegel.profil->werte->stimme) oder None."""
    zeile = _lesen_einer(lambda: "SELECT profil->'werte'->>'stimme' AS s FROM marketing.marken_spiegel "
                                 f"WHERE mandant = {lit(mandant)}") or {}
    s = zeile.get("s")
    return s.strip() if isinstance(s, str) and s.strip() else None


def _markenstimme_aufgeloest(mandant: str, liste: list[str] | None = None) -> tuple[str | None, str | None]:
    """(stimme, hinweis) aus der Markenstimme: bekannt oder 'standard' -> (marke, None); nicht mehr in
    MARKETING_STIMMEN -> ('standard', Hinweis); keine Markenstimme -> (None, None)."""
    marke = _markenstimme(mandant)
    if marke is None:
        return None, None
    if marke == STANDARD or marke in (stimmen_liste() if liste is None else liste):
        return marke, None
    return STANDARD, f"Die Markenstimme '{marke[:40]}' gibt es nicht mehr – Standard genommen."


def _stimme_einsetzen(mandant: str, briefing: dict) -> None:
    """E2 + Review Focus 3: Die Markenstimme gilt vor dem Briefing. Gibt es sie nicht mehr (nicht in
    MARKETING_STIMMEN), wird 'standard' genommen und vermerkt - der Auftrag scheitert daran nicht.
    Ohne Markenstimme muss eine Briefing-Stimme ausser 'standard' in der Liste stehen."""
    liste = stimmen_liste()
    stimme, hinweis = _markenstimme_aufgeloest(mandant, liste)
    if stimme is not None:
        briefing["stimme"] = stimme
        if hinweis:
            briefing["stimme_hinweis"] = hinweis
    elif briefing["stimme"] != STANDARD and briefing["stimme"] not in liste:
        raise HTTPException(422, "Diese Stimme gibt es nicht")


def _anlegen(payload: dict, mandant_feld: str, von: str) -> str:
    mandant = _mandant(payload.get(mandant_feld), mandant_feld)
    art = payload.get("art")
    titel = _text(payload.get("titel"), "titel", TITEL_MAX)
    briefing = briefing_pruefen(art, payload.get("briefing"))
    _bilder_der_firma(mandant, briefing)
    _stimme_einsetzen(mandant, briefing)
    # KI-Bilder beginnen bei den Bildideen (090), alle anderen wie bisher offen (Vorgabe der Funktion)
    status = ", 'ideen'" if "ki" in briefing else ""
    zeile = _schreiben(lambda:
        f"SELECT marketing.video_auftrag_anlegen({lit(mandant)}, {lit(art)}, {lit(titel)}, "
        f"{lit(json.dumps(briefing, ensure_ascii=False))}::jsonb, {lit(von)}{status}) AS id")
    return str(zeile.get("id"))


def _liste(mandant: str, limit: int, archiv: bool | None = None) -> list:
    """Auftraege der Firma, neueste zuerst. archiv None = alle (Agent), sonst nur (nicht) archivierte.
    vorgaenger bleibt die uuid; vorgaenger_titel kommt per Join dazu, befund_text aus befund_satz."""
    spalten = ", ".join(f"a.{s.strip()}" for s in _SPALTEN.split(","))
    filter_archiv = "" if archiv is None else f" AND a.archiviert = {'true' if archiv else 'false'}"
    zeilen = _lesen(lambda:
        f"SELECT {spalten}, v.titel AS vorgaenger_titel FROM marketing.video_auftraege a "
        f"LEFT JOIN marketing.video_auftraege v ON v.id = a.vorgaenger "
        f"WHERE a.mandant = {lit(mandant)}{filter_archiv} ORDER BY a.erstellt_am DESC LIMIT {int(limit)}")
    for z in zeilen:
        if isinstance(z, dict):
            z["befund_text"] = befund_satz(z.get("befund") or "")
    _ki_bilder_anhaengen(zeilen)
    return zeilen


def _briefing_dict(briefing) -> dict:
    if isinstance(briefing, str):
        try:
            briefing = json.loads(briefing)
        except ValueError:
            return {}
    return briefing if isinstance(briefing, dict) else {}


def _ki_bilder_anhaengen(zeilen: list) -> None:
    """E6: KI-Auftraege (briefing.ki) bekommen ki_bilder [{id, nr, idee, status, datei, befund}] ohne die
    verworfenen, nach nr. Eine Abfrage fuer alle; ohne KI-Auftrag keine."""
    ki = {}
    for z in zeilen:
        if isinstance(z, dict) and "ki" in _briefing_dict(z.get("briefing")) and z.get("id"):
            ki[str(z["id"])] = z
            z["ki_bilder"] = []
    if not ki:
        return
    ids = ", ".join(f"{lit(i)}::uuid" for i in ki)
    for b in _lesen(lambda:
            "SELECT id, video_auftrag, nr, idee, status, datei, befund FROM marketing.reel_bilder "
            f"WHERE video_auftrag IN ({ids}) AND status <> 'verworfen' ORDER BY video_auftrag, nr"):
        z = ki.get(str(b.get("video_auftrag"))) if isinstance(b, dict) else None
        if z is not None:
            z["ki_bilder"].append({k: b.get(k) for k in ("id", "nr", "idee", "status", "datei", "befund")})


def _auftrag_der_firma(aid: str, mandant) -> str:
    """Review Focus 1: Der Auftrag muss der angegebenen Firma gehoeren, sonst 404 (die SQL-Funktionen
    nochmal/archivieren/abbrechen pruefen den Mandanten nicht)."""
    m = _mandant(mandant, "mandant")
    a = _uuid_oder_404(aid)
    zeile = _lesen_einer(lambda: f"SELECT mandant FROM marketing.video_auftraege WHERE id = {lit(a)}::uuid")
    if not zeile or zeile.get("mandant") != m:
        raise HTTPException(404, "Unbekannter Auftrag")
    return a


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
def pult_liste(mandant: str = Query(...), limit: int = Query(50, ge=1, le=200), archiv: bool = Query(False),
               x_pult_key: str | None = Header(None)):
    _schluessel(x_pult_key)
    return {"auftraege": _liste(_mandant(mandant, "mandant"), limit, archiv)}


@pult_router.post("")
def pult_anlegen(payload: dict = Body(...), x_pult_key: str | None = Header(None)):
    _schluessel(x_pult_key)
    return {"auftrag": _anlegen(payload, "mandant", "betreiber")}


@pult_router.post("/{aid}/abbrechen")
def pult_abbrechen(aid: str, payload: dict | None = Body(None), x_pult_key: str | None = Header(None)):
    """Mit {mandant} im Koerper nur fuer einen Auftrag dieser Firma (sonst 404); ohne wie bisher."""
    _schluessel(x_pult_key)
    if isinstance(payload, dict) and payload.get("mandant") is not None:
        a = _auftrag_der_firma(aid, payload.get("mandant"))
    else:
        a = _uuid_oder_404(aid)
    zeile = _schreiben(lambda: f"SELECT marketing.video_abbrechen({lit(a)}::uuid) AS s")
    return {"status": zeile.get("s")}


@pult_router.post("/{aid}/nochmal")
def pult_nochmal(aid: str, payload: dict = Body(...), x_pult_key: str | None = Header(None)):
    """Neu bauen: aus fertig mit Aenderungswunsch, aus fehler auch ohne (neu versuchen). Regeln in 085.
    Die Stimme wird wie beim Anlegen aus der AKTUELLEN Markenstimme aufgeloest; ohne Markenstimme bleibt die
    des alten Briefings (NULL an video_nochmal)."""
    _schluessel(x_pult_key)
    wunsch = payload.get("wunsch")
    wunsch = "" if wunsch is None else wunsch
    if not isinstance(wunsch, str) or len(wunsch.strip()) > WUNSCH_MAX:
        raise HTTPException(422, f"wunsch: höchstens {WUNSCH_MAX} Zeichen")
    a = _auftrag_der_firma(aid, payload.get("mandant"))
    stimme, hinweis = _markenstimme_aufgeloest(_mandant(payload.get("mandant"), "mandant"))

    def oder_null(wert: str | None) -> str:
        return lit(wert) if wert is not None else "NULL"
    zeile = _schreiben(lambda:
        f"SELECT marketing.video_nochmal({lit(a)}::uuid, {lit(wunsch.strip())}, 'betreiber', "
        f"{oder_null(stimme)}, {oder_null(hinweis)}) AS id")
    return {"auftrag": str(zeile.get("id"))}


@pult_router.post("/{aid}/archivieren")
def pult_archivieren(aid: str, payload: dict = Body(...), x_pult_key: str | None = Header(None)):
    """{an: true} archiviert, {an: false} zeigt wieder an; nur erledigte Auftraege (sonst ok false)."""
    _schluessel(x_pult_key)
    an = payload.get("an")
    if not isinstance(an, bool):
        raise HTTPException(422, "an muss true oder false sein")
    a = _auftrag_der_firma(aid, payload.get("mandant"))
    zeile = _schreiben(lambda:
        f"SELECT marketing.video_archivieren({lit(a)}::uuid, {'true' if an else 'false'}) AS ok")
    return {"ok": bool(zeile.get("ok"))}


def _medienbilder(mandant: str) -> list[str]:
    """Bilder in den Medienordnern, die keiner anderen Firma gehoeren (Regel wie _bilder_der_firma),
    neueste zuerst, hoechstens BILDER_MAX. Vorschaubilder der Reels (vid-*.jpg) sind keine Auswahl."""
    s = medien_mandant.sicht(mandant)
    gefunden: dict[str, float] = {}
    for o in (os.environ.get("MARKETING_MEDIEN_ORDNER", "").strip(),
              os.environ.get("MARKETING_BILD_ORDNER", "").strip()):
        if not o or not os.path.isdir(o):
            continue
        try:
            namen = os.listdir(o)
        except OSError:
            continue
        for n in namen:
            pfad = os.path.join(o, n)
            if (n in gefunden or ":" in n or not medien_mandant._gueltig(n) or not n.lower().endswith(_BILD)
                    or _POSTER.fullmatch(n) or not os.path.isfile(pfad) or os.path.islink(pfad)
                    or medien_mandant.ist_fremd(n, mandant, s)):
                continue
            try:
                gefunden[n] = os.path.getmtime(pfad)
            except OSError:
                continue
    return sorted(gefunden, key=lambda n: (-gefunden[n], n))[:BILDER_MAX]


@pult_router.get("/bilder")
def pult_bilder(mandant: str = Query(...), x_pult_key: str | None = Header(None)):
    """Medienbilder der Firma fuer die Bestellung 'Aus Bildern' (E3)."""
    _schluessel(x_pult_key)
    return {"bilder": [{"name": n} for n in _medienbilder(_mandant(mandant, "mandant"))]}


def _material_aufbereiten(antwort) -> dict | None:
    """Antwort von laura.alle_videos -> {"videos": [...], ["hinweis"]}; None, wenn Laura nicht lieferte."""
    if not isinstance(antwort, dict) or not antwort.get("ok") or not isinstance(antwort.get("daten"), list):
        return None
    videos = []
    for v in antwort["daten"]:
        if not isinstance(v, dict):
            continue
        dauer, kennung = v.get("dauer_s"), v.get("kennung")
        if (isinstance(dauer, bool) or not isinstance(dauer, (int, float)) or dauer <= 0
                or not isinstance(kennung, str) or not _LAURA_ID.fullmatch(kennung)):
            continue
        videos.append({"kennung": kennung, "name": str(v.get("name") or "?")[:LAURA_NAME_MAX],
                       "projekt": str(v.get("projekt") or "")[:LAURA_NAME_MAX], "dauer_s": dauer})
    ergebnis: dict = {"videos": videos}
    if antwort.get("stumme_projekte"):
        ergebnis["hinweis"] = LAURA_TEILWEISE
    return ergebnis


# Ein Laura-Scan laeuft ueber alle Projekte (je bis zu 20 s). Darum: hoechstens ein Lauf zugleich, im
# eigenen Faden; die Route wartet hoechstens MATERIAL_FRIST_S; das letzte gute Ergebnis gilt 60 s.
_MATERIAL_SPERRE = threading.Lock()
_MATERIAL: dict = {"zeit": 0.0, "ergebnis": None, "lauf": None}
_jetzt = time.monotonic


def _material_zuruecksetzen() -> None:
    with _MATERIAL_SPERRE:
        _MATERIAL.update(zeit=0.0, ergebnis=None, lauf=None)


def _material_lauf(lauf: Future) -> None:
    try:
        ergebnis = _material_aufbereiten(laura.alle_videos())
    except Exception:  # noqa: BLE001 - die Bestellung bleibt ohne Auswahl moeglich (Review Focus 5)
        ergebnis = None
    if ergebnis is not None:
        with _MATERIAL_SPERRE:
            _MATERIAL.update(zeit=_jetzt(), ergebnis=ergebnis)
    lauf.set_result(ergebnis if ergebnis is not None else {"videos": [], "hinweis": LAURA_AUS})


def _material_holen() -> dict:
    with _MATERIAL_SPERRE:
        alt = _MATERIAL["ergebnis"]
        if alt is not None and _jetzt() - _MATERIAL["zeit"] < MATERIAL_SPEICHER_S:
            return alt
        lauf = _MATERIAL["lauf"]
        if lauf is None or lauf.done():
            lauf = Future()
            _MATERIAL["lauf"] = lauf
            threading.Thread(target=_material_lauf, args=(lauf,), name="laura-material", daemon=True).start()
        elif alt is not None:
            return alt                      # es laeuft schon ein Scan: alter Stand statt eines zweiten
    try:
        return lauf.result(timeout=MATERIAL_FRIST_S)
    except FutureTimeout:
        return alt if alt is not None else {"videos": [], "hinweis": LAURA_LANGSAM}


@pult_router.get("/material")
def pult_material(x_pult_key: str | None = Header(None)):
    """Laura-Videos (E3). Nur Eintraege mit Dauer > 0 sind Videos. Laura weg oder zu langsam = 200 mit
    Hinweis; die Bestellung bleibt ohne Auswahl moeglich (Review Focus 5)."""
    _schluessel(x_pult_key)
    return _material_holen()


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
    """Pfad nur, wenn GENAU dieser Name im Ordner steht (Sicherheitspruefung 17.10.2026).

    Die Firmenpruefung (medien_mandant.ist_fremd) vergleicht Namen exakt; NTFS am PC oeffnet
    'Fremd.PNG' aber als 'fremd.png' und 'x:y.png' als Datenstrom von 'x'. Darum zaehlt nur ein
    exakter Treffer in der Verzeichnisliste - derselbe Name, den die Pruefung gesehen hat."""
    if ":" in name:
        return None
    for o in (os.environ.get("MARKETING_MEDIEN_ORDNER", "").strip(),
              os.environ.get("MARKETING_BILD_ORDNER", "").strip()):
        if o and os.path.isdir(o):
            try:
                vorhanden = name in os.listdir(o)
            except OSError:
                continue
            pfad = os.path.join(o, name)
            if vorhanden and os.path.isfile(pfad) and not os.path.islink(pfad):
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


_FARBE_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


@router.post("/arbeiter/{aid}/marke")
def arbeiter_marke(aid: str, x_bild_key: str | None = Header(None)):
    """Marke der Auftragsfirma fuer den Video-Claw (Paket C1): Akzentfarbe, Anzeige-/Textschrift, Logo-Medienname.
    Nur gueltige Werte; fehlt etwas, ist es None und das Reel bleibt dort beim bisherigen Aussehen.
    Dazu das Format des Auftrags (Reels Stufe 2): briefing.format oder 'reel' - daraus nehmen die Werkzeuge
    das Format, wenn der Claw keines angibt."""
    _bild_schluessel(x_bild_key)
    a = _auftrag_id(aid)
    auftrag = _in_arbeit(a)
    mandant = str(auftrag["mandant"])
    zeile = _lesen_einer(lambda: "SELECT profil->'werte' AS w FROM marketing.marken_spiegel "
                                 f"WHERE mandant = {lit(mandant)}") or {}
    werte = zeile.get("w") if isinstance(zeile.get("w"), dict) else {}
    akzent = werte.get("akzent")

    def schrift(k: str):
        v = werte.get(k)
        return v if isinstance(v, str) and v in schriften.REGISTER else None
    logo, _ = _markenlogos(mandant)
    return {"mandant": mandant,
            "akzent": akzent if isinstance(akzent, str) and _FARBE_HEX.match(akzent) else None,
            "schrift_anzeige": schrift("schrift_anzeige"), "schrift_text": schrift("schrift_text"),
            "logo": logo[len("medien:"):] if logo else None,
            "format": _format_des_auftrags(auftrag.get("briefing"))}


def _format_des_auftrags(briefing) -> str:
    """briefing.format, wenn gueltig, sonst 'reel' (fehlt bei allen Auftraegen vor Stufe 2)."""
    if isinstance(briefing, str):
        try:
            briefing = json.loads(briefing)
        except ValueError:
            briefing = None
    fmt = briefing.get("format") if isinstance(briefing, dict) else None
    return fmt if isinstance(fmt, str) and fmt in FORMATE else "reel"


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


def mp4_dauer(roh: bytes) -> float | None:
    """Dauer aus dem mvhd-Kasten (moov/mvhd), ohne ffprobe. None, wenn nicht lesbar."""
    def kaesten(daten: bytes, start: int, ende: int):
        i = start
        while i + 8 <= ende:
            groesse, typ = struct.unpack(">I4s", daten[i:i + 8])
            kopf = 8
            if groesse == 1 and i + 16 <= ende:
                groesse, kopf = struct.unpack(">Q", daten[i + 8:i + 16])[0], 16
            elif groesse == 0:
                groesse = ende - i
            if groesse < kopf or i + groesse > ende:
                return
            yield typ, i + kopf, i + groesse
            i += groesse
    for typ, a, e in kaesten(roh, 0, len(roh)):
        if typ != b"moov":
            continue
        for typ2, a2, _e2 in kaesten(roh, a, e):
            if typ2 != b"mvhd" or a2 + 32 > len(roh):
                continue
            version = roh[a2]
            if version == 1:
                skala, dauer = struct.unpack(">IQ", roh[a2 + 20:a2 + 32])
            else:
                skala, dauer = struct.unpack(">II", roh[a2 + 12:a2 + 20])
            return round(dauer / skala, 2) if skala else None
    return None


def _speichern(a: str, roh: bytes) -> dict:
    """Pruefen (ftyp, Groesse), der Firma zuordnen, ablegen. Gemeinsam fuer Upload und Laura-Abholung."""
    if len(roh) > MP4_MAX:
        raise HTTPException(413, "MP4 groesser als 15 MB")
    if not _ist_mp4(roh):
        raise HTTPException(422, "Nur MP4")
    ordner = _ordner()
    auftrag = _in_arbeit(a)
    name = f"{_stamm(a, auftrag.get('titel'))}.mp4"
    medien_mandant.zuordnen([name], str(auftrag.get("mandant")))
    _ablegen(ordner, name, roh, "MP4 konnte nicht abgelegt werden")
    return {"name": name, "bytes": len(roh), "dauer_s": mp4_dauer(roh)}


def _stamm(a: str, titel) -> str:
    """Dateistamm eines Reels: vid-<id[:8]>-<Titel-Slug> (MP4 und Vorschaubild teilen ihn)."""
    return f"vid-{a[:8]}-{_slug(str(titel or ''))[:40].strip('-') or 'reel'}"


def _ablegen(ordner: str, name: str, roh: bytes, fehler: str) -> None:
    """Atomar ablegen (.teil + os.replace); jeder OSError -> 503."""
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
        raise HTTPException(503, fehler)


@router.post("/arbeiter/{aid}/datei")
async def arbeiter_datei(aid: str, request: Request, x_bild_key: str | None = Header(None)):
    _bild_schluessel(x_bild_key)
    a = _auftrag_id(aid)
    roh = bytearray()
    async for stueck in request.stream():
        roh += stueck
        if len(roh) > MP4_MAX:
            raise HTTPException(413, "MP4 groesser als 15 MB")
    return _speichern(a, bytes(roh))


@router.get("/arbeiter/{aid}/datei")
def arbeiter_datei_holen(aid: str, x_bild_key: str | None = Header(None)):
    """Das ueber /aus-laura (oder /datei) abgelegte MP4 dieses Auftrags, fuer das Vorschaubild am PC: der PC
    erreicht Lauras Export-Link nicht (VM-intern). Nur in Arbeit oder fertig; fehlt die Datei, 404."""
    _bild_schluessel(x_bild_key)
    a = _auftrag_id(aid)
    auftrag = _lesen_einer(lambda: f"SELECT titel, status FROM marketing.video_auftraege WHERE id = {lit(a)}::uuid")
    if not auftrag:
        raise HTTPException(404, "Unbekannter Auftrag")
    if auftrag.get("status") not in ("in_arbeit", "fertig"):
        raise HTTPException(409, "Auftrag ist weder in Arbeit noch fertig")
    name = f"{_stamm(a, auftrag.get('titel'))}.mp4"
    pfad = os.path.join(_ordner(), name)
    if not os.path.isfile(pfad) or os.path.islink(pfad):
        raise HTTPException(404, "MP4 fehlt")
    return FileResponse(pfad, media_type="video/mp4", headers={"Cache-Control": "no-store"})


@router.post("/arbeiter/{aid}/poster")
async def arbeiter_poster(aid: str, request: Request, x_bild_key: str | None = Header(None)):
    """Vorschaubild (rohes JPEG, hoechstens 1 MiB) neben das MP4: vid-<aid8>-<stamm>.jpg mit dem Stamm von
    datei, sonst dem Titel-Slug. Der Firma zuordnen, ablegen, dann marketing.video_poster (085)."""
    _bild_schluessel(x_bild_key)
    a = _auftrag_id(aid)
    roh = bytearray()
    async for stueck in request.stream():
        roh += stueck
        if len(roh) > POSTER_MAX:
            raise HTTPException(413, "Vorschaubild groesser als 1 MiB")
    if not bytes(roh[:3]) == _JPEG:
        raise HTTPException(422, "Nur JPEG")
    return await run_in_threadpool(_poster_speichern, a, bytes(roh))


def _poster_speichern(a: str, roh: bytes) -> dict:
    ordner = _ordner()
    auftrag = _lesen_einer(lambda:
        f"SELECT titel, mandant, status, datei FROM marketing.video_auftraege WHERE id = {lit(a)}::uuid")
    if not auftrag:
        raise HTTPException(404, "Unbekannter Auftrag")
    if auftrag.get("status") not in ("in_arbeit", "fertig"):
        raise HTTPException(409, "Auftrag ist weder in Arbeit noch fertig")
    datei = auftrag.get("datei")
    if isinstance(datei, str) and _DATEI.fullmatch(datei) and datei.startswith(f"vid-{a[:8]}-"):
        name = datei[:-len(".mp4")] + ".jpg"
    else:
        name = f"{_stamm(a, auftrag.get('titel'))}.jpg"
    medien_mandant.zuordnen([name], str(auftrag.get("mandant")))
    _ablegen(ordner, name, roh, "Vorschaubild konnte nicht abgelegt werden")
    # Verwaistes Vorschaubild: lehnt video_poster danach ab (false/Ausnahme, z. B. der Auftrag wurde inzwischen
    # abgebrochen), bleibt die Datei ohne Verweis liegen. Das ist gewollt harmlos: sie ist der Firma des
    # Auftrags zugeordnet, steht in keiner Bildauswahl (vid-*.jpg faellt aus /bilder) und ein spaeterer
    # Versuch ueberschreibt sie unter demselben Namen. Erst ablegen, dann verweisen: nie ein Verweis ohne Datei.
    zeile = _schreiben(lambda: f"SELECT marketing.video_poster({lit(a)}::uuid, {lit(name)}) AS ok")
    if not zeile.get("ok"):
        raise HTTPException(409, "Vorschaubild passt nicht zum Auftrag")
    return {"name": name}


_LAURA_EXPORT_PFAD = re.compile(r"^/object/sign/laura/exports/[A-Za-z0-9_-]{1,64}\.mp4$")
_LAURA_TOKEN = re.compile(r"^token=[A-Za-z0-9._-]{10,4000}$")


def _laura_quelle_erlaubt(url: str) -> bool:
    """Nur Lauras signierte Export-Links: Host aus MARKETING_VIDEO_LAURA_QUELLEN UND genau der Pfad
    /object/sign/laura/exports/<id>.mp4?token=... (Sicherheitspruefung 17.10.2026: Host allein liess
    jeden Pfad des Speicherdienstes zu). Sonst waere die Route ein Abruf-Tor (SSRF) fuer jeden mit dem
    Arbeiter-Schluessel."""
    erlaubt = {h.strip().lower() for h in os.environ.get("MARKETING_VIDEO_LAURA_QUELLEN", "").split(",") if h.strip()}
    if "%" in url or ".." in url or "\\" in url:
        return False
    teile = urllib.parse.urlsplit(url)
    return (teile.scheme in ("http", "https") and bool(erlaubt) and teile.netloc.lower() in erlaubt
            and not teile.username and not teile.password and not teile.fragment
            and bool(_LAURA_EXPORT_PFAD.fullmatch(teile.path)) and bool(_LAURA_TOKEN.fullmatch(teile.query)))


def _abrufen(url: str) -> bytes:
    """Der einzige Netzgriff dieser Route - Tests ersetzen ihn. Keine Umleitungen folgen."""
    class _KeineUmleitung(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):  # noqa: D102
            return None
    oeffner = urllib.request.build_opener(_KeineUmleitung)
    with oeffner.open(urllib.request.Request(url, method="GET"), timeout=60) as r:
        roh = r.read(MP4_MAX + 1)
    return roh


@router.post("/arbeiter/{aid}/aus-laura")
def arbeiter_aus_laura(aid: str, payload: dict = Body(...), x_bild_key: str | None = Header(None)):
    """Fertiges Reel direkt aus Lauras Objektspeicher holen (die Marketing-API laeuft auf derselben VM,
    der PC erreicht die interne Speicheradresse nicht). Nur erlaubte Hosts, keine Umleitungen."""
    _bild_schluessel(x_bild_key)
    a = _auftrag_id(aid)
    url = payload.get("url")
    if not isinstance(url, str) or len(url) > 4000 or not _laura_quelle_erlaubt(url):
        raise HTTPException(422, "url: nur Lauras Objektspeicher (MARKETING_VIDEO_LAURA_QUELLEN)")
    _in_arbeit(a)
    try:
        roh = _abrufen(url)
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        raise HTTPException(502, f"Laura-Speicher nicht erreichbar: {type(e).__name__}")
    return _speichern(a, roh)


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
