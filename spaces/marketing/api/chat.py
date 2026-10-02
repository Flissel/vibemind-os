"""Chat- und Export-Routen des Gestaltungs-Agenten (sales-claw Spec
2026-10-02-newsletter-gestaltung-und-agent-design.md §4, §6).
  /api/pult/inhalte/{iid}/chat|export  Sales-Oberflaeche (X-Pult-Key, pult._schluessel)
  /api/chat/arbeiter/*                 Chat-Arbeiter am PC (X-Bild-Key, bilder._bild_schluessel)
Regeln (ein laufender Auftrag je Inhalt, Sperre, Vergabe) stehen in den DB-Funktionen
aus 060; hier nur Formen, Rechnen (Pillow), Dateiablage und Weitergabe."""
from __future__ import annotations

import io
import json
import os
import re
import tempfile

from fastapi import APIRouter, Body, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse

from spaces.marketing.api.bilder import (_BILDTYP, _PLATZ, _anlegen, _auftrag_id, _bild_schluessel,
                                         _im_ordner, _ordner)
from spaces.marketing.api.gestaltung import aufraeumen_falls_faellig, gestaltungen_rechnen, quellen
from spaces.marketing.api.pult import _lesen, _lesen_einer, _schluessel, _schreiben, _uuid_oder_404, lit
from spaces.marketing.claw import gestaltung, schoenheit

pult_router = APIRouter(prefix="/api/pult")
arbeiter_router = APIRouter(prefix="/api/chat/arbeiter")

FRIST = "5 minutes"
NACHRICHT_MAX = 2000
GERAETE = {"handy": "hoch", "tablet": "quadrat", "pc": "quer"}
EXPORT_MAX = 4 * 1024 * 1024
FLAECHEN_MAX = 20
BILDAUFTRAEGE_MAX = 10
MEDIEN_MAX = 500
DATEI_BREITE_MAX, DATEI_HOEHE_MAX, DATEI_KANTE_MIN = 1200, 20000, 64
NICHT_UMGESETZT = "Das habe ich nicht umsetzen können: "
GEAENDERT = "Der Newsletter wurde inzwischen geändert – bitte schick die Nachricht noch einmal."
_ENTWURF = re.compile(r"gs-[0-9a-f]{12}\.jpg")                                   # nur fullmatch
_MEDIEN_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,119}\.(png|jpe?g|gif|webp)")   # DB-URL-Regex ohne medien:
_EXPORT_NAME = re.compile(r"([a-z0-9-]{1,60})-(handy|tablet|pc)\.jpg")
_UMLAUTE = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})


def slug(titel: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (titel or "").lower().translate(_UMLAUTE)).strip("-")
    return s[:60].strip("-") or "newsletter"


# ─── Pult ───────────────────────────────────────────────────────────────


@pult_router.post("/inhalte/{iid}/chat")
def chat_anlegen(iid: str, payload: dict = Body(...), x_pult_key: str | None = Header(None)):
    _schluessel(x_pult_key)
    i = _uuid_oder_404(iid)
    nachricht = payload.get("nachricht")
    if not isinstance(nachricht, str) or not nachricht.strip():
        raise HTTPException(422, "Ohne Nachricht kein Auftrag")
    if len(nachricht) > NACHRICHT_MAX:
        raise HTTPException(422, "Die Nachricht ist zu lang (höchstens 2000 Zeichen)")
    kontext = payload.get("kontext") or {}
    if not isinstance(kontext, dict):
        raise HTTPException(422, "kontext muss ein Objekt sein")
    zeile = _schreiben(lambda:
        f"SELECT marketing.pult_chat_anlegen({lit(i)}::uuid, 'chat', {lit(nachricht)}, "
        f"{lit(json.dumps(kontext, ensure_ascii=False))}::jsonb) AS id")
    return {"auftrag": str(zeile["id"])}


@pult_router.get("/inhalte/{iid}/chat")
def chat_stand(iid: str, x_pult_key: str | None = Header(None)):
    _schluessel(x_pult_key)
    i = _uuid_oder_404(iid)
    _schreiben(lambda: f"SELECT marketing.pult_chat_aufraeumen({lit(i)}::uuid) IS NULL AS ok")
    verlauf = _lesen(lambda:
        "SELECT * FROM (SELECT id, art, nachricht, antwort, status, hinweise, ergebnis, fassung_vorher, "
        "fassung_nachher, erstellt_am::text AS erstellt_am, erstellt_am AS t "
        f"FROM marketing.chat_auftraege WHERE inhalt = {lit(i)}::uuid ORDER BY erstellt_am DESC LIMIT 30) q "
        "ORDER BY t")
    for z in verlauf:
        z.pop("t", None)
    return {"laeuft": any(z.get("status") in ("offen", "in_arbeit") for z in verlauf), "verlauf": verlauf}


@pult_router.post("/inhalte/{iid}/chat/rueckgaengig")
def chat_rueckgaengig(iid: str, payload: dict = Body(...), x_pult_key: str | None = Header(None)):
    _schluessel(x_pult_key)
    i = _uuid_oder_404(iid)
    a = payload.get("auftrag")
    if not isinstance(a, str):
        raise HTTPException(422, "auftrag fehlt")
    a = _auftrag_id(a)
    z = _lesen_einer(lambda:
        "SELECT a.fassung_vorher, a.fassung_nachher, f.bloecke, coalesce(f.felder->>'betreff', '') AS betreff, "
        "coalesce(f.felder->>'vorschautext', '') AS vorschautext, "
        "(SELECT max(fassung) FROM marketing.inhalt_fassungen WHERE inhalt = a.inhalt) AS neueste "
        "FROM marketing.chat_auftraege a JOIN marketing.inhalt_fassungen f "
        "ON f.inhalt = a.inhalt AND f.fassung = a.fassung_vorher "
        f"WHERE a.id = {lit(a)}::uuid AND a.inhalt = {lit(i)}::uuid "
        "AND a.status = 'fertig' AND a.fassung_nachher IS NOT NULL")
    if not z:
        raise HTTPException(422, "Dieser Auftrag hat nichts geändert, das sich rückgängig machen ließe")
    if not isinstance(z.get("bloecke"), dict):
        raise HTTPException(422, "Die vorige Fassung hat keine Blöcke")
    if z.get("fassung_nachher") != z.get("neueste"):
        raise HTTPException(422, "Seitdem gibt es eine neuere Fassung – Rückgängig ist nicht mehr möglich.")
    zeile = _schreiben(lambda:
        f"SELECT marketing.pult_bloecke_speichern({lit(i)}::uuid, {int(z['neueste'])}, {lit(z['betreff'])}, "
        f"{lit(z['vorschautext'])}, {lit(json.dumps(z['bloecke'], ensure_ascii=False))}::jsonb, "
        "'betreiber', false) AS fassung")
    return {"fassung": int(zeile["fassung"])}


def _inhalt_lesen(i: str) -> dict:
    z = _lesen_einer(lambda:
        "SELECT i.titel, f.bloecke FROM marketing.inhalte i LEFT JOIN LATERAL ("
        "SELECT bloecke FROM marketing.inhalt_fassungen WHERE inhalt = i.id ORDER BY fassung DESC LIMIT 1) f ON true "
        f"WHERE i.id = {lit(i)}::uuid")
    if not z:
        raise HTTPException(404, "Unbekannter Inhalt")
    return z


def _flaechen(payload_wert, bloecke) -> dict[str, dict]:
    """Block-ids -> Gestaltung; 422 bei Form-Fehler oder wenn ein Block keine Flaeche ist."""
    if not isinstance(payload_wert, list) or len(payload_wert) > FLAECHEN_MAX:
        raise HTTPException(422, f"flaechen muss eine Liste mit hoechstens {FLAECHEN_MAX} Block-IDs sein")
    bloecke = bloecke if isinstance(bloecke, dict) else {}
    erg: dict[str, dict] = {}
    for bid in payload_wert:
        if not isinstance(bid, str) or not _PLATZ.fullmatch(bid):
            raise HTTPException(422, "flaechen muss Block-IDs enthalten")
        b = bloecke.get(bid)
        props = ((b or {}).get("data") or {}).get("props") if isinstance(b, dict) else None
        g = props.get("gestaltung") if isinstance(props, dict) and b.get("type") == "Image" else None
        if not isinstance(g, dict):
            raise HTTPException(422, f"{bid} ist keine Fläche")
        erg[bid] = g
    return erg


def _geraet_gestaltung(bid: str, g: dict, geraet: str) -> dict:
    try:
        return gestaltung.umformatieren(g, GERAETE[geraet])
    except gestaltung.GestaltungFehler as e:
        raise HTTPException(422, f"{bid}: {e}")


@pult_router.post("/inhalte/{iid}/export/vorschau")
def export_vorschau(iid: str, payload: dict = Body(...), x_pult_key: str | None = Header(None)):
    _schluessel(x_pult_key)
    i = _uuid_oder_404(iid)
    flaechen = _flaechen(payload.get("flaechen"), _inhalt_lesen(i).get("bloecke"))
    ordner, erg = _ordner(), {}
    for bid, g in flaechen.items():
        erg[bid] = {}
        for geraet in GERAETE:
            try:
                erg[bid][geraet] = gestaltung.rechnen(_geraet_gestaltung(bid, g, geraet), quellen(), ordner)["url"]
            except gestaltung.GestaltungFehler as e:
                raise HTTPException(422, f"{bid}: {e}")
    aufraeumen_falls_faellig()
    return {"flaechen": erg}


def _jpeg(bild, grenze: int = EXPORT_MAX) -> bytes:
    for q in range(88, 63, -6):
        puffer = io.BytesIO()
        bild.save(puffer, "JPEG", quality=q, optimize=True)
        if puffer.tell() <= grenze:
            return puffer.getvalue()
    raise HTTPException(422, "Bild wird zu groß (über 4 MB)")


def _sichtbar_ablegen(ordner: str, stamm: str, roh: bytes) -> str:
    """Legt roh als <stamm>.jpg sichtbar ab; vorhandene Namen (in jedem Medienordner)
    bekommen -2, -3 ... os.link reserviert den Namen atomar (kein Ueberschreiben)."""
    fd, tmp = tempfile.mkstemp(dir=ordner, suffix=".teil")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(roh)
        os.chmod(tmp, 0o644)
        andere = [o for o in quellen() if os.path.realpath(o) != os.path.realpath(ordner)]
        for n in range(1, 1000):
            name = f"{stamm}.jpg" if n == 1 else f"{stamm}-{n}.jpg"
            if any(os.path.exists(os.path.join(o, name)) for o in andere):
                continue
            try:
                os.link(tmp, os.path.join(ordner, name))
            except FileExistsError:
                continue
            return name
    except OSError:
        raise HTTPException(503, "Bild konnte nicht abgelegt werden")
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass
    raise HTTPException(422, "Kein freier Dateiname mehr")


@pult_router.post("/inhalte/{iid}/export")
def export(iid: str, payload: dict = Body(...), x_pult_key: str | None = Header(None)):
    _schluessel(x_pult_key)
    i = _uuid_oder_404(iid)
    if payload.get("bestaetigt") is not True:
        raise HTTPException(422, "Export nur mit Bestätigung")
    newsletter = payload.get("newsletter", False)
    if not isinstance(newsletter, bool):
        raise HTTPException(422, "newsletter muss true oder false sein")
    roh_flaechen = payload.get("flaechen", [])
    if not newsletter and not roh_flaechen:
        raise HTTPException(422, "Nichts zum Exportieren ausgewählt")
    z = _inhalt_lesen(i)
    s = slug(z.get("titel") or "")
    flaechen = _flaechen(roh_flaechen, z.get("bloecke"))
    ordner = _ordner()
    # erst alles rechnen (ein Fehler laesst nichts zurueck), dann Auftrag, dann Dateien
    fertig: list[tuple[str, bytes]] = []
    for bid, g in flaechen.items():
        for geraet in GERAETE:
            try:
                bild, _ = gestaltung.bild_rechnen(_geraet_gestaltung(bid, g, geraet), quellen())
            except gestaltung.GestaltungFehler as e:
                raise HTTPException(422, f"{bid}: {e}")
            fertig.append((f"{s}-{bid}-{geraet}", _jpeg(bild)))
    auftrag = None
    if newsletter:
        kontext = {"geraete": list(GERAETE), "slug": s}
        zeile = _schreiben(lambda:
            f"SELECT marketing.pult_chat_anlegen({lit(i)}::uuid, 'export', '', "
            f"{lit(json.dumps(kontext, ensure_ascii=False))}::jsonb) AS id")
        auftrag = str(zeile["id"])
    return {"dateien": [_sichtbar_ablegen(ordner, stamm, roh) for stamm, roh in fertig], "auftrag": auftrag}


# ─── Arbeiter ───────────────────────────────────────────────────────────


def _in_arbeit(a: str, status_sonst: int = 409) -> dict:
    """Auftrag lesen; nur in_arbeit mit gueltiger Vergabe zaehlt (sonst 404/status_sonst)."""
    z = _lesen_einer(lambda:
        "SELECT inhalt, art, status, kontext, coalesce(vergeben_bis > now(), false) AS gueltig "
        f"FROM marketing.chat_auftraege WHERE id = {lit(a)}::uuid")
    if not z:
        raise HTTPException(404, "Unbekannter Auftrag")
    if z.get("status") != "in_arbeit" or not z.get("gueltig"):
        raise HTTPException(status_sonst, "Auftrag ist nicht (mehr) in Arbeit")
    return z


def _medien() -> list[str]:
    namen: set[str] = set()
    for o in quellen():
        try:
            eintraege = os.listdir(o)
        except OSError:
            continue
        for n in eintraege:
            if _MEDIEN_NAME.fullmatch(n) and not _ENTWURF.fullmatch(n) and os.path.isfile(os.path.join(o, n)):
                namen.add(n)
    return sorted(namen)[:MEDIEN_MAX]


@arbeiter_router.post("/naechster")
def arbeiter_naechster(x_bild_key: str | None = Header(None)):
    _bild_schluessel(x_bild_key)
    zeile = _schreiben(lambda: f"SELECT marketing.pult_chat_naechster({lit(FRIST)}::interval) AS a")
    a = zeile.get("a")
    if not (isinstance(a, dict) and a.get("id")):
        return {"auftrag": None}
    a["medien"] = _medien()
    return {"auftrag": a}


@arbeiter_router.post("/{aid}/weiter")
def arbeiter_weiter(aid: str, x_bild_key: str | None = Header(None)):
    _bild_schluessel(x_bild_key)
    a = _auftrag_id(aid)
    zeile = _schreiben(lambda:
        f"SELECT marketing.pult_chat_verlaengern({lit(a)}::uuid, {lit(FRIST)}::interval) AS ok")
    return {"ok": bool(zeile.get("ok"))}


def _zurueck(a: str, antwort: str) -> dict:
    zeile = _schreiben(lambda:
        f"SELECT marketing.pult_chat_zurueck({lit(a)}::uuid, {lit(antwort[:4000])}) AS s")
    return {"status": zeile.get("s")}


def _gestaltung_fehler(bloecke: dict) -> str | None:
    for bid, b in bloecke.items():
        if not isinstance(b, dict) or b.get("type") != "Image":
            continue
        props = (b.get("data") or {}).get("props") if isinstance(b.get("data"), dict) else None
        if isinstance(props, dict) and props.get("gestaltung") is not None:
            try:
                gestaltung.pruefen(props["gestaltung"])
            except gestaltung.GestaltungFehler as e:
                return f"{bid}: {e}"
    return None


def _bloecke_fehler(bloecke: dict) -> str | None:
    z = _lesen_einer(lambda:
        f"SELECT marketing.pult_bloecke_fehler({lit(json.dumps(bloecke, ensure_ascii=False))}::jsonb) AS f")
    if z is None:
        raise HTTPException(503, "Marketing-Datenbank nicht erreichbar")
    return z.get("f") or None


@arbeiter_router.post("/{aid}/pruefen")
def arbeiter_pruefen(aid: str, payload: dict = Body(...), x_bild_key: str | None = Header(None)):
    _bild_schluessel(x_bild_key)
    a = _auftrag_id(aid)
    bloecke = payload.get("bloecke")
    if not isinstance(bloecke, dict):
        raise HTTPException(422, "bloecke muss ein Objekt sein")
    _in_arbeit(a)
    return {"fehler": _gestaltung_fehler(bloecke) or _bloecke_fehler(bloecke)}


def _bildauftraege_formen(roh) -> list[dict]:
    """Form-Vorpruefung; ungueltige Eintraege bekommen einen grund (nicht fatal)."""
    if not isinstance(roh, list) or len(roh) > BILDAUFTRAEGE_MAX:
        raise HTTPException(422, f"bildauftraege muss eine Liste mit hoechstens {BILDAUFTRAEGE_MAX} Eintraegen sein")
    erg = []
    for b in roh:
        platz = b.get("platz") if isinstance(b, dict) else None
        modus = b.get("modus") if isinstance(b, dict) else None
        hinweis = b.get("hinweis", "") if isinstance(b, dict) else None
        if not isinstance(platz, str) or not _PLATZ.fullmatch(platz):
            erg.append({"grund": "Bildauftrag ohne gültigen Bildplatz"})
        elif modus not in ("neu", "freistellen") or not isinstance(hinweis, str) or len(hinweis) > 500:
            erg.append({"platz": platz, "grund": "Bildauftrag ungültig"})
        else:
            erg.append({"platz": platz, "modus": modus, "hinweis": hinweis})
    return erg


@arbeiter_router.post("/{aid}/fertig")
def arbeiter_fertig(aid: str, payload: dict = Body(...), x_bild_key: str | None = Header(None)):
    _bild_schluessel(x_bild_key)
    a = _auftrag_id(aid)
    antwort, bloecke = payload.get("antwort"), payload.get("bloecke")
    export_vorschlag, notiz = payload.get("export_vorschlag"), payload.get("notiz", "")
    if not isinstance(antwort, str):
        raise HTTPException(422, "antwort muss Text sein")
    if bloecke is not None and not isinstance(bloecke, dict):
        raise HTTPException(422, "bloecke muss ein Objekt oder null sein")
    if export_vorschlag is not None and not isinstance(export_vorschlag, dict):
        raise HTTPException(422, "export_vorschlag muss ein Objekt oder null sein")
    if not isinstance(notiz, str) or len(notiz) > 500:
        raise HTTPException(422, "notiz muss Text mit hoechstens 500 Zeichen sein")
    auftraege = _bildauftraege_formen(payload.get("bildauftraege", []))
    job = _in_arbeit(a)
    hinweise: list[str] = []
    if bloecke is not None:
        try:
            bloecke, h = gestaltungen_rechnen(bloecke)
        except gestaltung.GestaltungFehler as e:
            return _zurueck(a, NICHT_UMGESETZT + str(e))
        fehler = _bloecke_fehler(bloecke)
        if fehler:
            return _zurueck(a, NICHT_UMGESETZT + str(fehler))
        hinweise += h + [b["satz"] for b in schoenheit.bloecke_pruefen(bloecke)]
    hinweise += [f"{b.get('platz', 'Bild')}: {b['grund']}" for b in auftraege if "grund" in b]
    ergebnis = {"export_vorschlag": export_vorschlag, "notiz": notiz, "bildauftraege": auftraege}
    try:
        zeile = _schreiben(lambda:
            f"SELECT marketing.pult_chat_fertig({lit(a)}::uuid, {lit(antwort[:4000])}, "
            + (f"{lit(json.dumps(bloecke, ensure_ascii=False))}::jsonb" if bloecke is not None else "NULL")
            + f", {lit(json.dumps(hinweise, ensure_ascii=False))}::jsonb, "
            f"{lit(json.dumps(ergebnis, ensure_ascii=False))}::jsonb) AS e")
    except HTTPException as e:
        if e.status_code == 422 and str(e.detail).startswith("Inzwischen gibt es Fassung"):
            return _zurueck(a, GEAENDERT)
        raise
    fassung = (zeile.get("e") or {}).get("fassung")
    ids: list[str | None] = []
    for b in auftraege:
        if "grund" in b:
            ids.append(None)
            continue
        try:
            ids.append(_anlegen(str(job["inhalt"]), {"platz": b["platz"], "hinweis": b["hinweis"],
                                                     "modus": b["modus"]}, "agent")["auftrag"])
        except HTTPException as e:
            ids.append(None)
            hinweise.append(f"{b['platz']}: Bild nicht beauftragt – {e.detail}")
    return {"fassung": fassung, "hinweise": hinweise, "bildauftraege": ids}


@arbeiter_router.post("/{aid}/zurueck")
def arbeiter_zurueck(aid: str, payload: dict = Body(...), x_bild_key: str | None = Header(None)):
    _bild_schluessel(x_bild_key)
    a = _auftrag_id(aid)
    antwort = payload.get("antwort")
    if not isinstance(antwort, str):
        raise HTTPException(422, "antwort muss Text sein")
    return _zurueck(a, antwort)


@arbeiter_router.get("/{aid}/medien/{name}")
def arbeiter_medien(aid: str, name: str, x_bild_key: str | None = Header(None)):
    _bild_schluessel(x_bild_key)
    a = _auftrag_id(aid)
    if not _MEDIEN_NAME.fullmatch(name):
        raise HTTPException(404, "Unbekannte Datei")
    _in_arbeit(a, 404)
    for o in quellen():
        pfad = _im_ordner(o, name)
        if pfad:
            return FileResponse(pfad, media_type=_BILDTYP.get(os.path.splitext(name)[1].lower(), "image/jpeg"),
                                headers={"Cache-Control": "no-store"})
    raise HTTPException(404, "Unbekannte Datei")


def _export_jpeg_pruefen(roh: bytes) -> None:
    if not roh.startswith(b"\xff\xd8\xff"):
        raise HTTPException(422, "Nur JPEG")
    from PIL import Image, UnidentifiedImageError
    try:
        with Image.open(io.BytesIO(roh)) as bild:
            fmt, (w, h) = bild.format, bild.size
        if fmt != "JPEG" or not (DATEI_KANTE_MIN <= w <= DATEI_BREITE_MAX and DATEI_KANTE_MIN <= h <= DATEI_HOEHE_MAX):
            raise HTTPException(422, f"JPEG bis {DATEI_BREITE_MAX}×{DATEI_HOEHE_MAX} px noetig")
        with Image.open(io.BytesIO(roh)) as bild:
            bild.verify()
        with Image.open(io.BytesIO(roh)) as bild:
            bild.load()   # ganz dekodieren: abgeschnittene JPEGs bestehen verify()
    except HTTPException:
        raise
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        raise HTTPException(422, "Bilddatei ist kaputt")


@arbeiter_router.post("/{aid}/datei")
async def arbeiter_datei(aid: str, request: Request, name: str = Query(""), x_bild_key: str | None = Header(None)):
    _bild_schluessel(x_bild_key)
    a = _auftrag_id(aid)
    m = _EXPORT_NAME.fullmatch(name or "")
    if not m:
        raise HTTPException(422, "name muss <slug>-<handy|tablet|pc>.jpg sein")
    ordner = _ordner()
    try:
        laenge = int(request.headers.get("content-length") or 0)
    except ValueError:
        laenge = 0
    if laenge > EXPORT_MAX:
        raise HTTPException(422, "Bild größer als 4 MB")
    roh = bytearray()
    async for stueck in request.stream():
        roh += stueck
        if len(roh) > EXPORT_MAX:
            raise HTTPException(422, "Bild größer als 4 MB")
    _export_jpeg_pruefen(bytes(roh))
    job = _in_arbeit(a)
    if job.get("art") != "export" or (job.get("kontext") or {}).get("slug") != m.group(1):
        raise HTTPException(422, "Datei passt nicht zu diesem Export-Auftrag")
    return {"name": _sichtbar_ablegen(ordner, name[:-len(".jpg")], bytes(roh))}
