"""Video-Werkbank (Stufe 1): Arbeiter-Routen fuer den PC-Worker (X-Bild-Key, /api/werkbank/arbeiter/ ohne
X-API-Key), Pult-Routen fuer die Szenenseite der Sales-UI (X-Pult-Key) und der Upload grosser Dateien in
Teilen. Die Regeln (Zustandsmaschine, Lease, Fassungen) stehen in den SQL-Funktionen marketing.video_* (099)
und auftrag.* (096/098); die API prueft Form, Verknuepfung und Dateien. Es werden ausschliesslich SQL-FUNKTIONEN
gerufen (`SELECT f(...) AS x`): `_schreiben` verpackt jede Anweisung als Unterabfrage, ein INSERT/UPDATE/WITH
dort faellt in Produktion durch, waehrend Fake-DB-Tests gruen bleiben. Der Mandant kommt immer aus der
Auftragszeile, nie aus dem Body; jede Route mit Auftrag prueft zuerst an='marketing' und art='video'.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil

from fastapi import APIRouter, Body, Header, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse

from spaces.marketing.api import medien_mandant
from spaces.marketing.api.bilder import _bild_schluessel, _ordner
from spaces.marketing.api.pult import (_lesen, _lesen_einer, _schluessel, _schreiben, _uuid_oder_404, lit)
from spaces.marketing.api.video import _ist_mp4, _markenstimme_aufgeloest

arbeiter_router = APIRouter(prefix="/api/werkbank/arbeiter")
pult_router = APIRouter(prefix="/api/pult/werkbank")

AN, ART = "marketing", "video"
LEASE_AUFTRAG = "10 minutes"
LEASE_SZENE = "20 minutes"
RENDERFRIST_MIN = 1440                      # Entscheidung 21: der Werkbank-Auftrag bekommt einen Tag Renderfrist
SZENEN_MIN, SZENEN_MAX = 8, 15
BILDARTEN = ("slide", "avatar_gross", "avatar_slide", "clip")
UEBERGAENGE = ("schnitt", "blende")
VORLAGEN = ("titel", "stichpunkte", "zahl", "zitat", "vergleich", "agenda")   # Slide-Vorlagen (Spec 5.4)
SCHRITTE_MIN, SCHRITTE_MAX = 2, 5
SPRECHTEXT_MAX, GRUND_MAX, SATZ_MAX, WUNSCH_MAX = 4000, 300, 300, 2000
TITEL_MAX, BESCHREIBUNG_MAX, MESSNOTIZ_MAX = 100, 5000, 1500
JSON_MAX = 200_000                           # Gliederung/Bild als Text, gegen Missbrauch
TEIL_MAX = 8 * 1024 * 1024                   # ein Teil des Uploads
MAX_BYTES_VORGABE = 2 * 1024 ** 3
RESERVE = 1024 ** 3                          # so viel Platz muss nach dem Upload noch frei sein
NAME_MUSTER = re.compile(r"wb-([0-9a-f]{8})-(s([0-9]{2,3})|video)-f([0-9]{1,3})\.mp4")
KENNUNG_MUSTER = re.compile(r"wb-[a-z2-7]{10}")
SHA_MUSTER = re.compile(r"[0-9a-f]{64}")
HINWEIS_KEY_MUSTER = re.compile(r"[a-z0-9_]{1,60}")


# ─── Gemeinsames ─────────────────────────────────────────────────────────


def _json(wert) -> str:
    return f"{lit(json.dumps(wert, ensure_ascii=False))}::jsonb"


def _text(wert, feld: str, maximal: int, pflicht: bool = True) -> str | None:
    """Text auf Laenge pruefen (Pflicht: 1..maximal nach dem Trimmen); None nur wenn nicht Pflicht."""
    if wert is None and not pflicht:
        return None
    if not isinstance(wert, str) or len(wert) > maximal or (pflicht and not wert.strip()):
        raise HTTPException(422, f"{feld}: 1 bis {maximal} Zeichen" if pflicht else f"{feld}: höchstens {maximal} Zeichen")
    return wert.strip()


def _zahl(wert, feld: str, minimum: float = 0, maximum: float = 10 ** 9) -> float:
    if isinstance(wert, bool) or not isinstance(wert, (int, float)) or not minimum <= wert <= maximum:
        raise HTTPException(422, f"{feld} muss eine Zahl sein")
    return wert


def _ganz(wert, feld: str, minimum: int = 0, maximum: int = 10 ** 12) -> int:
    if isinstance(wert, bool) or not isinstance(wert, int) or not minimum <= wert <= maximum:
        raise HTTPException(422, f"{feld} muss eine ganze Zahl sein")
    return wert


def _werk(aid: str) -> dict:
    """Auftragszeile eines Marketing-Videoauftrags ({mandant, stand, werkbank}); sonst 404 (vor jedem Schreiben)."""
    zeile = _lesen_einer(lambda:
        f"SELECT mandant, stand, ergebnis->'werkbank' AS werkbank FROM auftrag.auftraege "
        f"WHERE id = {lit(aid)}::uuid AND an = {lit(AN)} AND art = {lit(ART)}")
    if not zeile:
        raise HTTPException(404, "Unbekannter Auftrag")
    return zeile


def _szene_werk(sid: str) -> dict:
    """{auftrag, nr, mandant} der Szene, wenn ihr Auftrag ein Marketing-Videoauftrag ist; sonst 404."""
    zeile = _lesen_einer(lambda:
        f"SELECT s.auftrag, s.nr, a.mandant FROM marketing.video_szenen s "
        f"JOIN auftrag.auftraege a ON a.id = s.auftrag "
        f"WHERE s.id = {lit(sid)}::uuid AND a.an = {lit(AN)} AND a.art = {lit(ART)}")
    if not zeile:
        raise HTTPException(404, "Unbekannte Szene")
    return zeile


def _bool(zeile: dict | None, feld: str = "ok") -> bool:
    return bool(zeile and zeile.get(feld) is True)


def _liste(wert, feld: str, maximal: int = 200, element=None) -> list:
    """Optionale Liste (None -> []) mit hoechstens `maximal` Eintraegen; `element` prueft jeden Eintrag."""
    if wert is None:
        return []
    if not isinstance(wert, list) or len(wert) > maximal:
        raise HTTPException(422, f"{feld} muss eine Liste mit höchstens {maximal} Einträgen sein")
    if element and not all(element(x) for x in wert):
        raise HTTPException(422, f"{feld}: ungültiger Eintrag")
    _zu_gross(wert)
    return wert


def _quellen(wert) -> list:
    return _liste(wert, "quellen", 200, lambda x: isinstance(x, (str, dict)) and len(json.dumps(x)) <= 2000)


def _zeitmarken(wert) -> list:
    return _liste(wert, "zeitmarken", 100, lambda x: isinstance(x, dict) and len(json.dumps(x)) <= 500)


def _warnungen(wert) -> list:
    return _liste(wert, "warnungen", 50, lambda x: isinstance(x, str) and len(x) <= 500)


def _zu_gross(wert) -> None:
    if len(json.dumps(wert, ensure_ascii=False)) > JSON_MAX:
        raise HTTPException(422, "Eingabe ist zu gross")


def _szenen_zaehlen(gliederung: dict) -> int:
    """Zaehlt ALLE Eintraege in stufen[].szenen[]; jeder muss ein nichtleerer Text oder ein Objekt mit
    nichtleerem `kurz` sein (SQL macht aus jedem Eintrag eine Szene, auch aus Muell)."""
    n = 0
    for stufe in gliederung["stufen"]:
        szenen = stufe.get("szenen") if isinstance(stufe, dict) else None
        if not isinstance(szenen, list):
            raise HTTPException(422, "Jede Stufe braucht eine Liste szenen")
        for sz in szenen:
            kurz = sz.get("kurz") if isinstance(sz, dict) else sz
            if not isinstance(kurz, str) or not kurz.strip():
                raise HTTPException(422, "Jede Szene braucht einen Kurztitel")
            n += 1
    return n


def _gliederung_pruefen(g) -> dict:
    if not isinstance(g, dict) or not isinstance(g.get("stufen"), list) or not g["stufen"]:
        raise HTTPException(422, "gliederung.stufen darf nicht leer sein")
    _zu_gross(g)
    n = _szenen_zaehlen(g)
    if not SZENEN_MIN <= n <= SZENEN_MAX:
        raise HTTPException(422, f"Die Gliederung braucht {SZENEN_MIN} bis {SZENEN_MAX} Szenen")
    return g


def _bild_pruefen(bild) -> dict:
    """Jede Bildart braucht bild.schritte: Liste aus 2..5 Objekten, jedes mit bekannter vorlage (R11)."""
    if not isinstance(bild, dict):
        raise HTTPException(422, "bild muss ein Objekt sein")
    _zu_gross(bild)
    schritte = bild.get("schritte")
    if not isinstance(schritte, list) or not SCHRITTE_MIN <= len(schritte) <= SCHRITTE_MAX:
        raise HTTPException(422, f"bild.schritte: {SCHRITTE_MIN} bis {SCHRITTE_MAX} Schritte")
    for x in schritte:
        if not isinstance(x, dict) or x.get("vorlage") not in VORLAGEN:
            raise HTTPException(422, "Jeder Schritt braucht eine bekannte Vorlage")
    return bild


def _auswahl(wert, erlaubt: tuple, feld: str) -> str:
    if wert not in erlaubt:
        raise HTTPException(422, f"{feld} muss eines von {', '.join(erlaubt)} sein")
    return wert


def _name_pruefen(aid: str, name: str, fremd: int = 422) -> re.Match:
    """Name nach dem Muster und zu aid passend (erste 8 Hex-Zeichen); sonst 422 (Fremdname: `fremd`)."""
    m = NAME_MUSTER.fullmatch(name)
    if not m:
        raise HTTPException(422, "Ungültiger Dateiname")
    if m.group(1) != aid.replace("-", "")[:8]:
        raise HTTPException(fremd, "Der Dateiname gehört zu einem anderen Auftrag")
    return m


# ─── Arbeiter: Auftrag, Werk, Szenen ─────────────────────────────────────


@arbeiter_router.post("/naechster")
def naechster(x_bild_key: str | None = Header(None)):
    """Aeltesten uebernehmbaren Video-Auftrag vergeben und sein Werk beginnen ({auftrag} oder null)."""
    _bild_schluessel(x_bild_key)
    zeile = _schreiben(lambda: f"SELECT auftrag.video_naechster({lit(LEASE_AUFTRAG)}::interval) AS a")
    a = zeile.get("a")
    if isinstance(a, dict) and a.get("id"):
        aid = _uuid_oder_404(str(a["id"]))
        _schreiben(lambda: f"SELECT marketing.video_werk_beginnen({lit(aid)}::uuid) AS w")
        return {"auftrag": a}
    return {"auftrag": None}


@arbeiter_router.get("/arbeit")
def arbeit(x_bild_key: str | None = Header(None)):
    """Alle Werke in Arbeit (Werk-Form); `stimme` aus der Markenstimme, sonst 'standard'."""
    _bild_schluessel(x_bild_key)
    zeilen = _lesen(lambda: "SELECT marketing.video_arbeit() AS w")
    werke = []
    for z in zeilen:
        w = z.get("w") if isinstance(z, dict) else None
        if not isinstance(w, dict):
            continue
        stimme = None
        if w.get("mandant"):
            stimme, _hinweis = _markenstimme_aufgeloest(w["mandant"])
        werke.append({**w, "stimme": stimme or "standard"})
    return {"werke": werke}


@arbeiter_router.post("/{aid}/weiter")
def weiter(aid: str, x_bild_key: str | None = Header(None)):
    """Lease des Auftrags verlaengern; ok false = aufhoeren."""
    _bild_schluessel(x_bild_key)
    a = _uuid_oder_404(aid)
    _werk(a)
    zeile = _schreiben(lambda: f"SELECT auftrag.verlaengern({lit(a)}::uuid, {lit(LEASE_AUFTRAG)}::interval) AS ok")
    return {"ok": _bool(zeile)}


@arbeiter_router.post("/{aid}/frist")
def frist(aid: str, x_bild_key: str | None = Header(None)):
    """Renderfrist (1440 Minuten) fuer den Auftrag setzen."""
    _bild_schluessel(x_bild_key)
    a = _uuid_oder_404(aid)
    _werk(a)
    zeile = _schreiben(lambda: f"SELECT auftrag.renderfrist({lit(a)}::uuid, {RENDERFRIST_MIN}) AS ok")
    return {"ok": _bool(zeile)}


@arbeiter_router.post("/{aid}/gliederung")
def gliederung_setzen(aid: str, payload: dict = Body(...), x_bild_key: str | None = Header(None)):
    _bild_schluessel(x_bild_key)
    a = _uuid_oder_404(aid)
    _werk(a)
    g = _gliederung_pruefen(payload.get("gliederung"))
    q = _quellen(payload.get("quellen"))
    zeile = _schreiben(lambda: f"SELECT marketing.video_gliederung_setzen({lit(a)}::uuid, {_json(g)}, {_json(q)}) AS k")
    if not zeile.get("k"):
        raise HTTPException(409, "Die Gliederung ist gerade nicht dran")
    return {"kennung": zeile["k"]}


@arbeiter_router.post("/szene/{sid}/text")
def szene_text(sid: str, payload: dict = Body(...), x_bild_key: str | None = Header(None)):
    _bild_schluessel(x_bild_key)
    s = _uuid_oder_404(sid)
    _szene_werk(s)
    teil = {"sprechtext": _text(payload.get("sprechtext"), "sprechtext", SPRECHTEXT_MAX)}
    if payload.get("bildart") is not None:
        teil["bildart"] = _auswahl(payload["bildart"], BILDARTEN, "bildart")
    if payload.get("uebergang") is not None:
        teil["uebergang"] = _auswahl(payload["uebergang"], UEBERGAENGE, "uebergang")
    teil["bild"] = _bild_pruefen(payload.get("bild"))          # R11: Schritte sind immer Pflicht
    if payload.get("quellen") is not None:
        teil["quellen"] = _quellen(payload["quellen"])
    zeile = _schreiben(lambda: f"SELECT marketing.video_szene_text_setzen({lit(s)}::uuid, {_json(teil)}) AS k")
    if not zeile.get("k"):
        raise HTTPException(409, "Die Szene ist gerade nicht für einen Text dran")
    return {"kennung": zeile["k"]}


@arbeiter_router.post("/szene/{sid}/bild")
def szene_bild(sid: str, payload: dict = Body(...), x_bild_key: str | None = Header(None)):
    _bild_schluessel(x_bild_key)
    s = _uuid_oder_404(sid)
    _szene_werk(s)
    bildart = _auswahl(payload.get("bildart"), BILDARTEN, "bildart")
    bild = _bild_pruefen(payload.get("bild"))
    zeile = _schreiben(lambda:
        f"SELECT marketing.video_szene_bild_setzen({lit(s)}::uuid, {lit(bildart)}, {_json(bild)}) AS ok")
    if not _bool(zeile):
        raise HTTPException(409, "Das Bild kann gerade nicht geändert werden")
    return {"ok": True}


@arbeiter_router.post("/{aid}/szene-nehmen")
def szene_nehmen(aid: str, x_bild_key: str | None = Header(None)):
    _bild_schluessel(x_bild_key)
    a = _uuid_oder_404(aid)
    _werk(a)
    zeile = _schreiben(lambda: f"SELECT marketing.video_szene_nehmen({lit(a)}::uuid, {lit(LEASE_SZENE)}::interval) AS s")
    return {"szene": zeile.get("s")}


@arbeiter_router.post("/szene/{sid}/clip")
def szene_clip(sid: str, payload: dict = Body(...), x_bild_key: str | None = Header(None)):
    _bild_schluessel(x_bild_key)
    s = _uuid_oder_404(sid)
    z = _szene_werk(s)
    variante = _ganz(payload.get("variante"), "variante", 0, 1000)   # Szenen beginnen bei Variante 0 (099)
    datei = _text(payload.get("datei"), "datei", 100)
    dauer = _zahl(payload.get("dauer_s"), "dauer_s", 0, 3600)
    stimme = _text(payload.get("stimme"), "stimme", 80)
    engine = _text(payload.get("engine"), "engine", 80)
    m = NAME_MUSTER.fullmatch(datei)
    if not m or m.group(3) is None or m.group(1) != str(z["auftrag"]).replace("-", "")[:8] \
            or int(m.group(3)) != z["nr"] or int(m.group(4)) != variante:
        raise HTTPException(409, "Der Dateiname passt nicht zu Auftrag, Szene und Variante")
    if not os.path.isfile(os.path.join(_ordner(), datei)):
        raise HTTPException(409, "Die Datei liegt nicht im Ordner")
    zeile = _schreiben(lambda:
        f"SELECT marketing.video_szene_clip_setzen({lit(s)}::uuid, {variante}, {lit(datei)}, "
        f"{dauer}::numeric, {lit(stimme)}, {lit(engine)}) AS k")
    if not zeile.get("k"):
        raise HTTPException(409, "Die Szene nimmt gerade keinen Clip an")
    return {"kennung": zeile["k"]}


@arbeiter_router.post("/szene/{sid}/fehler")
def szene_fehler(sid: str, payload: dict = Body(...), x_bild_key: str | None = Header(None)):
    _bild_schluessel(x_bild_key)
    s = _uuid_oder_404(sid)
    grund = payload.get("grund")
    if not isinstance(grund, str):
        raise HTTPException(422, "grund muss Text sein")
    grund = (grund.strip() or "Kein Grund angegeben")[:GRUND_MAX]
    _szene_werk(s)
    zeile = _schreiben(lambda: f"SELECT marketing.video_szene_fehler({lit(s)}::uuid, {lit(grund)}) AS ok")
    return {"ok": _bool(zeile)}


@arbeiter_router.post("/{aid}/hinweis")
def hinweis(aid: str, payload: dict = Body(...), x_bild_key: str | None = Header(None)):
    _bild_schluessel(x_bild_key)
    a = _uuid_oder_404(aid)
    key = payload.get("key")
    if not isinstance(key, str) or not HINWEIS_KEY_MUSTER.fullmatch(key):
        raise HTTPException(422, "key: Kleinbuchstaben, Ziffern und _, höchstens 60 Zeichen")
    satz = _text(payload.get("satz"), "satz", SATZ_MAX)
    _werk(a)
    zeile = _schreiben(lambda: f"SELECT marketing.video_hinweis({lit(a)}::uuid, {lit(key)}, {lit(satz)}) AS ok")
    return {"ok": _bool(zeile)}


@arbeiter_router.post("/{aid}/aufruf")
def aufruf(aid: str, x_bild_key: str | None = Header(None)):
    _bild_schluessel(x_bild_key)
    a = _uuid_oder_404(aid)
    _werk(a)
    zeile = _schreiben(lambda: f"SELECT marketing.video_aufruf_zaehlen({lit(a)}::uuid) AS n")
    if zeile.get("n") is None:
        raise HTTPException(409, "Der Auftrag hat noch kein Werk")
    return {"aufrufe": int(zeile["n"])}


@arbeiter_router.post("/{aid}/fertig")
def fertig(aid: str, payload: dict = Body(...), x_bild_key: str | None = Header(None)):
    """Fertiges Video melden; der ganze geprüfte Körper geht als p_teil an die SQL-Funktion (Schluessel `datei`)."""
    _bild_schluessel(x_bild_key)
    a = _uuid_oder_404(aid)
    _werk(a)
    datei = _text(payload.get("datei"), "datei", 100)
    m = _name_pruefen(a, datei, 409)
    if m.group(3) is not None:
        raise HTTPException(409, "Das ist keine Videodatei")
    teil = {"datei": datei,
            "bytes": _ganz(payload.get("bytes"), "bytes"),
            "dauer_s": _zahl(payload.get("dauer_s"), "dauer_s", 0, 36000),
            "titel": _text(payload.get("titel"), "titel", TITEL_MAX),
            "beschreibung": _text(payload.get("beschreibung"), "beschreibung", BESCHREIBUNG_MAX),
            "messnotiz": _text(payload.get("messnotiz"), "messnotiz", MESSNOTIZ_MAX, pflicht=False) or "",
            "zeitmarken": _zeitmarken(payload.get("zeitmarken")),
            "warnungen": _warnungen(payload.get("warnungen"))}
    if not os.path.isfile(os.path.join(_ordner(), datei)):
        raise HTTPException(409, "Die Datei liegt nicht im Ordner")
    zeile = _schreiben(lambda: f"SELECT marketing.video_fertig_setzen({lit(a)}::uuid, {_json(teil)}) AS ok")
    if not _bool(zeile):
        raise HTTPException(409, "Der Auftrag nimmt gerade kein fertiges Video an")
    return {"ok": True}


# ─── Arbeiter: Dateien (Abholen, Upload in Teilen) ───────────────────────


@arbeiter_router.get("/{aid}/datei/{name}")
def datei_holen(aid: str, name: str, x_bild_key: str | None = Header(None)):
    """Eine Datei dieses Auftrags aus dem Medienordner streamen (nie ganz in den Speicher lesen)."""
    _bild_schluessel(x_bild_key)
    a = _uuid_oder_404(aid)
    _name_pruefen(a, name, 404)
    _werk(a)
    pfad = os.path.join(_ordner(), name)
    if not os.path.isfile(pfad):
        raise HTTPException(404, "Datei fehlt")
    return FileResponse(pfad, media_type="video/mp4")


def _teil_pfade(name: str) -> tuple[str, str, str]:
    ordner = _ordner()
    teil = os.path.join(ordner, name + ".teil")
    return ordner, teil, teil + ".groesse"


def _teil_loeschen(*pfade: str) -> None:
    for p in pfade:
        try:
            os.remove(p)
        except OSError:
            pass


def _angemeldet(meta: str) -> int:
    try:
        with open(meta, encoding="ascii") as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        raise HTTPException(409, "Der Upload wurde nicht begonnen")


@arbeiter_router.post("/{aid}/datei/{name}/beginnen")
def upload_beginnen(aid: str, name: str, payload: dict = Body(...), x_bild_key: str | None = Header(None)):
    _bild_schluessel(x_bild_key)
    a = _uuid_oder_404(aid)
    _name_pruefen(a, name)
    _werk(a)
    groesse = _ganz(payload.get("bytes"), "bytes", 1)
    try:
        maximal = int(os.environ.get("MARKETING_WERKBANK_MAX_BYTES", "") or MAX_BYTES_VORGABE)
    except ValueError:
        maximal = MAX_BYTES_VORGABE
    if groesse > maximal:
        raise HTTPException(413, "Die Datei ist zu gross")
    ordner, teil, meta = _teil_pfade(name)
    if shutil.disk_usage(ordner).free < groesse + RESERVE:
        raise HTTPException(507, "Auf dem Server ist nicht genug Platz frei")
    try:
        with open(teil, "wb"):
            pass
        with open(meta, "w", encoding="ascii") as f:
            f.write(str(groesse))
    except OSError:
        _teil_loeschen(teil, meta)
        raise HTTPException(503, "Upload konnte nicht angelegt werden")
    return {"ok": True, "laenge": 0}


@arbeiter_router.put("/{aid}/datei/{name}/teil")
async def upload_teil(aid: str, name: str, request: Request, offset: int = Query(..., ge=0),
                      x_bild_key: str | None = Header(None)):
    """Ein Teil (roher Koerper, bis 8 MB) an die .teil-Datei haengen. Das Limit gilt waehrend des Lesens."""
    _bild_schluessel(x_bild_key)
    a = _uuid_oder_404(aid)
    _name_pruefen(a, name)
    await run_in_threadpool(_werk, a)
    _ordner_, teil, meta = _teil_pfade(name)
    angemeldet = _angemeldet(meta)
    if not os.path.isfile(teil):
        raise HTTPException(409, "Der Upload wurde nicht begonnen")
    laenge = os.path.getsize(teil)
    if offset != laenge:
        return JSONResponse({"detail": "Falscher Offset", "laenge": laenge}, status_code=409)
    kopf = request.headers.get("content-length", "")
    if kopf.isdigit() and int(kopf) > TEIL_MAX:
        _teil_loeschen(teil, meta)
        raise HTTPException(413, "Der Teil ist zu gross")
    gelesen, zu_gross = 0, None
    try:
        with open(teil, "r+b") as f:
            f.seek(offset)
            async for stueck in request.stream():
                gelesen += len(stueck)
                if gelesen > TEIL_MAX:
                    zu_gross = "Der Teil ist zu gross"
                    break
                if offset + gelesen > angemeldet:
                    zu_gross = "Mehr Daten als angemeldet"
                    break
                f.write(stueck)
    except Exception:                      # Abbruch beim Lesen/Schreiben: zurueck auf den Stand vor dem Teil
        try:
            os.truncate(teil, offset)
        except OSError:
            _teil_loeschen(teil, meta)
        raise HTTPException(503, "Der Teil wurde nicht gespeichert")
    if zu_gross:
        _teil_loeschen(teil, meta)
        raise HTTPException(413, zu_gross)
    return {"laenge": offset + gelesen}


def _hash_und_kopf(pfad: str) -> tuple[str, bytes]:
    h = hashlib.sha256()
    kopf = b""
    with open(pfad, "rb") as f:
        while True:
            block = f.read(1024 * 1024)
            if not block:
                break
            if not kopf:
                kopf = block[:12]
            h.update(block)
    return h.hexdigest(), kopf


def _aeltere_loeschen(ordner: str, m: re.Match) -> None:
    """Aeltere Fassungen derselben Szene bzw. des Videos (kleineres f<n>) entfernen."""
    neu = int(m.group(4))
    try:
        namen = os.listdir(ordner)
    except OSError:
        return
    for n in namen:
        k = NAME_MUSTER.fullmatch(n)
        if k and k.group(1) == m.group(1) and k.group(2) == m.group(2) and int(k.group(4)) < neu:
            _teil_loeschen(os.path.join(ordner, n))


@arbeiter_router.post("/{aid}/datei/{name}/abschliessen")
def upload_abschliessen(aid: str, name: str, payload: dict = Body(...), x_bild_key: str | None = Header(None)):
    _bild_schluessel(x_bild_key)
    a = _uuid_oder_404(aid)
    m = _name_pruefen(a, name)
    werk = _werk(a)
    sha = payload.get("sha256")
    if not isinstance(sha, str) or not SHA_MUSTER.fullmatch(sha.lower()):
        raise HTTPException(422, "sha256: 64 Hexzeichen")
    ordner, teil, meta = _teil_pfade(name)
    try:
        angemeldet = _angemeldet(meta)
        if not os.path.isfile(teil) or os.path.getsize(teil) != angemeldet:
            raise HTTPException(422, "Die Länge stimmt nicht mit der Anmeldung überein")
        try:
            summe, kopf = _hash_und_kopf(teil)
        except OSError:
            raise HTTPException(503, "Die Datei konnte nicht gelesen werden")
        if summe != sha.lower():
            raise HTTPException(422, "Die Prüfsumme stimmt nicht")
        if not _ist_mp4(kopf):
            raise HTTPException(422, "Das ist keine MP4-Datei")
        medien_mandant.zuordnen([name], werk["mandant"])       # Mandant aus der Auftragszeile
        try:
            os.chmod(teil, 0o644)
            os.replace(teil, os.path.join(ordner, name))
        except OSError:
            raise HTTPException(503, "Die Datei konnte nicht abgelegt werden")
    except BaseException:
        _teil_loeschen(teil, meta)
        raise
    _teil_loeschen(meta)
    _aeltere_loeschen(ordner, m)
    return {"ok": True, "datei": name, "bytes": angemeldet}


# ─── Pult: Szenenseite der Sales-UI ──────────────────────────────────────


def _pult_werk(aid: str, mandant: str) -> dict:
    z = _werk(aid)
    if z.get("mandant") != mandant:
        raise HTTPException(404, "Unbekannter Auftrag")
    return z


def _vorschlag(kennung: str, mandant: str | None) -> dict:
    """Vorschlagszeile mit Auftrag, Mandant und Szenennummer; 404 bei unbekannter Kennung oder fremdem Mandanten."""
    if not KENNUNG_MUSTER.fullmatch(kennung):
        raise HTTPException(404, "Unbekannter Vorschlag")
    z = _lesen_einer(lambda:
        f"SELECT v.auftrag, v.szene, v.tor, v.stand, a.mandant, s.nr FROM marketing.video_vorschlaege v "
        f"JOIN auftrag.auftraege a ON a.id = v.auftrag "
        f"LEFT JOIN marketing.video_szenen s ON s.id = v.szene "
        f"WHERE v.kennung = {lit(kennung)} AND a.an = {lit(AN)} AND a.art = {lit(ART)}")
    if not z or (mandant is not None and z.get("mandant") != mandant):
        raise HTTPException(404, "Unbekannter Vorschlag")
    return z


def _kopf(payload: dict) -> str:
    """Mandant aus dem Body (nur zum Vergleich mit der Auftragszeile); `von` muss gesetzt sein."""
    _text(payload.get("von"), "von", 60)
    return _text(payload.get("mandant"), "mandant", 80)


def _szene_id(aid: str, nr: int) -> str:
    z = _lesen_einer(lambda:
        f"SELECT id FROM marketing.video_szenen WHERE auftrag = {lit(aid)}::uuid AND nr = {int(nr)}")
    if not z:
        raise HTTPException(404, "Unbekannte Szene")
    return str(z["id"])


@pult_router.get("/v/{kennung}")
def pult_vorschlag(kennung: str, x_pult_key: str | None = Header(None)):
    _schluessel(x_pult_key)
    z = _vorschlag(kennung, None)
    return {"auftrag": str(z["auftrag"]), "tor": z["tor"], "nr": z.get("nr"), "mandant": z["mandant"],
            "aktuell": z["stand"] in ("wartend", "offen")}


@pult_router.get("/{aid}")
def pult_werk(aid: str, mandant: str = Query(...), x_pult_key: str | None = Header(None)):
    _schluessel(x_pult_key)
    a = _uuid_oder_404(aid)
    kopf = _pult_werk(a, mandant)
    zeile = _lesen_einer(lambda: f"SELECT marketing._video_werk_json({lit(a)}::uuid) AS w")
    if not zeile or not zeile.get("w"):
        raise HTTPException(404, "Der Auftrag hat noch kein Werk")
    return {"werk": zeile["w"], "stand": kopf.get("stand"), "werkbank": kopf.get("werkbank")}


@pult_router.get("/{aid}/szene/{nr}")
def pult_szene(aid: str, nr: int, mandant: str = Query(...), x_pult_key: str | None = Header(None)):
    _schluessel(x_pult_key)
    a = _uuid_oder_404(aid)
    _pult_werk(a, mandant)
    z = _lesen_einer(lambda:
        f"SELECT to_jsonb(s) AS szene, (SELECT v.kennung FROM marketing.video_vorschlaege v "
        f"WHERE v.szene = s.id AND v.stand IN ('wartend', 'offen') ORDER BY v.angelegt DESC LIMIT 1) AS kennung "
        f"FROM marketing.video_szenen s WHERE s.auftrag = {lit(a)}::uuid AND s.nr = {int(nr)}")
    if not z:
        raise HTTPException(404, "Unbekannte Szene")
    return {"szene": z["szene"], "kennung": z.get("kennung")}


@pult_router.post("/v/{kennung}/wunsch")
def pult_wunsch(kennung: str, payload: dict = Body(...), x_pult_key: str | None = Header(None)):
    _schluessel(x_pult_key)
    mandant = _kopf(payload)
    wunsch = _text(payload.get("wunsch"), "wunsch", WUNSCH_MAX)
    _vorschlag(kennung, mandant)
    zeile = _schreiben(lambda: f"SELECT marketing.video_wunsch_setzen({lit(kennung)}, {lit(wunsch)}) AS ok")
    return {"ok": _bool(zeile)}


@pult_router.post("/v/{kennung}/urteil")
def pult_urteil(kennung: str, payload: dict = Body(...), x_pult_key: str | None = Header(None)):
    _schluessel(x_pult_key)
    mandant = _kopf(payload)
    urteil = _auswahl(payload.get("urteil"), ("passt", "nochmal"), "urteil")
    wunsch = _text(payload.get("wunsch"), "wunsch", WUNSCH_MAX, pflicht=False)
    _vorschlag(kennung, mandant)
    if wunsch:                                  # ein mitgegebener Wunsch gilt schon fuer dieses Urteil
        _schreiben(lambda: f"SELECT marketing.video_wunsch_setzen({lit(kennung)}, {lit(wunsch)}) AS ok")
    zeile = _schreiben(lambda:
        f"SELECT marketing.video_entscheiden({lit(kennung)}, {lit(urteil)}, 'seite') AS e")
    erg = zeile.get("e")
    if not isinstance(erg, dict) or erg.get("ok") is not True:
        raise HTTPException(409, "Der Vorschlag ist nicht mehr aktuell")
    return erg


@pult_router.post("/{aid}/szene/{nr}/nochmal-versuchen")
def pult_nochmal(aid: str, nr: int, payload: dict = Body(...), x_pult_key: str | None = Header(None)):
    _schluessel(x_pult_key)
    a = _uuid_oder_404(aid)
    _pult_werk(a, _kopf(payload))
    sid = _szene_id(a, nr)
    zeile = _schreiben(lambda: f"SELECT marketing.video_szene_nochmal({lit(sid)}::uuid) AS ok")
    if not _bool(zeile):
        raise HTTPException(409, "Die Szene kann gerade nicht neu versucht werden")
    return {"ok": True}


@pult_router.post("/{aid}/szene/{nr}/oeffnen")
def pult_oeffnen(aid: str, nr: int, payload: dict = Body(...), x_pult_key: str | None = Header(None)):
    _schluessel(x_pult_key)
    a = _uuid_oder_404(aid)
    mandant = _kopf(payload)
    wunsch = _text(payload.get("wunsch"), "wunsch", WUNSCH_MAX)
    _pult_werk(a, mandant)
    sid = _szene_id(a, nr)
    zeile = _schreiben(lambda: f"SELECT marketing.video_szene_oeffnen({lit(sid)}::uuid, {lit(wunsch)}) AS ok")
    if not _bool(zeile):
        raise HTTPException(409, "Die Szene kann gerade nicht geöffnet werden")
    return {"ok": True}
