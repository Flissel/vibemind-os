"""Chat- und Export-Routen des Gestaltungs-Agenten (sales-claw Spec
2026-10-02-newsletter-gestaltung-und-agent-design.md §4, §6).
  /api/pult/inhalte/{iid}/chat|export  Sales-Oberflaeche (X-Pult-Key, pult._schluessel)
  /api/chat/arbeiter/*                 Chat-Arbeiter am PC (X-Bild-Key, bilder._bild_schluessel)
Regeln (ein laufender Auftrag je Inhalt, Sperre, Vergabe) stehen in den DB-Funktionen
aus 060; hier nur Formen, Rechnen (Pillow), Dateiablage und Weitergabe.
Live-Lauf (Spec 2026-10-02-newsletter-agent-live-design.md §2/§3, Migration 061):
Zwischenstand, Vormerken und Stopp; gestoppte Auftraege schliesst der Arbeiter
(/gestoppt) oder nach 15 s die VM beim naechsten Stand-Abruf ab."""
from __future__ import annotations

import io
import json
import logging
import os
import re
import tempfile
import uuid
from collections.abc import Callable

from fastapi import APIRouter, Body, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool

from spaces.marketing.api.bilder import (_BILDTYP, _PLATZ, _anlegen, _auftrag_id, _bild_schluessel,
                                         _im_ordner, _ordner)
from spaces.marketing.api.gestaltung import aufraeumen_falls_faellig, gestaltungen_rechnen, quellen
from spaces.marketing.api.pult import _lesen, _lesen_einer, _schluessel, _schreiben, _uuid_oder_404, lit
from spaces.marketing.api.medien_mandant import ist_fremd, sicht, zuordnen_fuer_inhalt
from spaces.marketing.claw import gestaltung, schoenheit

log = logging.getLogger(__name__)
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
ZWISCHENSTAND_KOERPER_MAX = 300 * 1024    # ganzer Body; die Bloecke selbst prueft die DB (<= 256 KB)
SCHRITT_MAX = 80
STOPPS_JE_ABRUF = 2                        # Rechnen kostet; der Rest kommt beim naechsten Abruf
STOPP_ARTEN = ("behalten", "verwerfen")
STOPP_UNGUELTIG = "Zwischenstand nicht übernommen: "
STOPP_GESPEICHERT = "Inzwischen gespeichert – Zwischenstand verworfen"
_ENTWURF = re.compile(r"gs-[0-9a-f]{12}\.jpg")                                   # nur fullmatch
_MEDIEN_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,119}\.(png|jpe?g|gif|webp)")   # DB-URL-Regex ohne medien:
# nur Arbeiter-Medienroute und Anhaenge: wie _MEDIEN_NAME, dazu Dokumente (Medienliste/DB bleiben bilderrein)
_ARBEITER_DATEI = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,119}\.(png|jpe?g|gif|webp|pdf|docx|txt|md)")
_DOK_TYP = {".pdf": "application/pdf", ".txt": "text/plain; charset=utf-8", ".md": "text/markdown; charset=utf-8",
            ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}
_KONTEXT_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")
KONTEXT_MAX = 4096
AUSWAHL_MAX, ANHAENGE_MAX, KURZ_MAX = 8, 5, 80
_EXPORT_NAME = re.compile(r"([a-z0-9-]{1,60})-(handy|tablet|pc)\.jpg")
_MEDIEN_VERWEIS = re.compile(r'"medien:([^"\\]{1,200})"')
BILD_FREMD = "Bild nicht verfügbar: "
ZUORDNUNG_FEHLT = "Bildzuordnung nicht erreichbar"
_UMLAUTE = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})


def slug(titel: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (titel or "").lower().translate(_UMLAUTE)).strip("-")
    return s[:60].strip("-") or "newsletter"


# ─── Pult ───────────────────────────────────────────────────────────────


def _nachricht_und_kontext(payload: dict) -> tuple[str, dict]:
    nachricht = payload.get("nachricht")
    if not isinstance(nachricht, str) or not nachricht.strip():
        raise HTTPException(422, "Ohne Nachricht kein Auftrag")
    if len(nachricht) > NACHRICHT_MAX:
        raise HTTPException(422, "Die Nachricht ist zu lang (höchstens 2000 Zeichen)")
    kontext = payload.get("kontext") or {}
    if not isinstance(kontext, dict):
        raise HTTPException(422, "kontext muss ein Objekt sein")
    if len(json.dumps(kontext, ensure_ascii=False).encode("utf-8")) > KONTEXT_MAX:
        raise HTTPException(422, "kontext ist zu groß (höchstens 4 KB)")
    _auswahl_pruefen(kontext.get("auswahl"))
    if kontext.get("anhaenge") is not None:
        _anhaenge_pruefen(kontext["anhaenge"])
    return nachricht, kontext


def _auswahl_pruefen(auswahl) -> None:
    if auswahl is None or isinstance(auswahl, str):   # Altform
        return
    if not isinstance(auswahl, list) or len(auswahl) > AUSWAHL_MAX:
        raise HTTPException(422, f"kontext.auswahl: höchstens {AUSWAHL_MAX} Elemente")
    for e in auswahl:
        if not isinstance(e, dict) or e.get("art") not in ("block", "ebene"):
            raise HTTPException(422, "kontext.auswahl: art muss block oder ebene sein")
        for feld in ("id", "flaeche"):
            w = e.get(feld)
            if (feld == "id" or w is not None) and not (isinstance(w, str) and _KONTEXT_ID.fullmatch(w)):
                raise HTTPException(422, f"kontext.auswahl: {feld} ungültig")
        kurz = e.get("kurz", "")
        if not isinstance(kurz, str) or len(kurz) > KURZ_MAX:
            raise HTTPException(422, f"kontext.auswahl: kurz höchstens {KURZ_MAX} Zeichen")


def _anhaenge_pruefen(anhaenge) -> None:
    if not isinstance(anhaenge, list) or len(anhaenge) > ANHAENGE_MAX:
        raise HTTPException(422, f"kontext.anhaenge: höchstens {ANHAENGE_MAX} Anhänge")
    for e in anhaenge:
        if not isinstance(e, dict) or e.get("art") not in ("bild", "dokument"):
            raise HTTPException(422, "kontext.anhaenge: art muss bild oder dokument sein")
        n = e.get("name")
        if not (isinstance(n, str) and _ARBEITER_DATEI.fullmatch(n)):
            raise HTTPException(422, "kontext.anhaenge: Dateiname ungültig")


@pult_router.post("/inhalte/{iid}/chat")
def chat_anlegen(iid: str, payload: dict = Body(...), x_pult_key: str | None = Header(None)):
    _schluessel(x_pult_key)
    i = _uuid_oder_404(iid)
    nachricht, kontext = _nachricht_und_kontext(payload)
    zeile = _schreiben(lambda:
        f"SELECT marketing.pult_chat_anlegen({lit(i)}::uuid, 'chat', {lit(nachricht)}, "
        f"{lit(json.dumps(kontext, ensure_ascii=False))}::jsonb) AS id")
    return {"auftrag": str(zeile["id"])}


@pult_router.get("/inhalte/{iid}/chat")
def chat_stand(iid: str, x_pult_key: str | None = Header(None)):
    _schluessel(x_pult_key)
    i = _uuid_oder_404(iid)
    _schreiben(lambda: f"SELECT marketing.pult_chat_aufraeumen({lit(i)}::uuid) IS NULL AS ok")
    _stopps_abschliessen()
    verlauf = _lesen(lambda:
        "SELECT * FROM (SELECT id, art, nachricht, antwort, status, hinweise, ergebnis, fassung_vorher, "
        "fassung_nachher, erstellt_am::text AS erstellt_am, erstellt_am AS sortiert_am "
        f"FROM marketing.chat_auftraege WHERE inhalt = {lit(i)}::uuid AND status <> 'wartet' "
        "ORDER BY erstellt_am DESC LIMIT 30) q ORDER BY sortiert_am")
    for z in verlauf:
        z.pop("sortiert_am", None)
    live, vorgemerkt = None, None
    for z in _lesen(lambda:
            "SELECT id, status, nachricht, schritt, schritt_nr, zwischenstand, stopp FROM marketing.chat_auftraege "
            f"WHERE inhalt = {lit(i)}::uuid AND status IN ('in_arbeit', 'wartet')"):
        if z.get("status") == "in_arbeit":
            live = {"schritt": z.get("schritt") or "", "schritt_nr": z.get("schritt_nr") or 0,
                    "zwischenstand": z.get("zwischenstand"), "stopp": z.get("stopp")}
        else:
            vorgemerkt = {"id": str(z.get("id")), "nachricht": z.get("nachricht")}
    return {"laeuft": any(z.get("status") in ("offen", "in_arbeit") for z in verlauf), "verlauf": verlauf,
            "live": live, "vorgemerkt": vorgemerkt}


@pult_router.put("/inhalte/{iid}/chat/vormerkung")
def chat_vormerken(iid: str, payload: dict = Body(...), x_pult_key: str | None = Header(None)):
    _schluessel(x_pult_key)
    i = _uuid_oder_404(iid)
    nachricht, kontext = _nachricht_und_kontext(payload)
    v = _schreiben(lambda:
        f"SELECT marketing.pult_chat_vormerken({lit(i)}::uuid, {lit(nachricht)}, "
        f"{lit(json.dumps(kontext, ensure_ascii=False))}::jsonb) AS v").get("v") or {}
    return {"id": str(v.get("id")), "status": v.get("status")}


@pult_router.delete("/inhalte/{iid}/chat/vormerkung")
def chat_vormerkung_loeschen(iid: str, x_pult_key: str | None = Header(None)):
    _schluessel(x_pult_key)
    i = _uuid_oder_404(iid)
    zeile = _schreiben(lambda: f"SELECT marketing.pult_chat_vormerkung_loeschen({lit(i)}::uuid) AS g")
    return {"geloescht": bool(zeile.get("g"))}


@pult_router.post("/inhalte/{iid}/chat/vormerkung/starten")
def chat_vormerkung_starten(iid: str, x_pult_key: str | None = Header(None)):
    _schluessel(x_pult_key)
    i = _uuid_oder_404(iid)
    zeile = _schreiben(lambda: f"SELECT marketing.pult_chat_vormerkung_starten({lit(i)}::uuid) AS id")
    return {"auftrag": str(zeile["id"])}


@pult_router.post("/inhalte/{iid}/chat/stopp")
def chat_stoppen(iid: str, payload: dict = Body(...), x_pult_key: str | None = Header(None)):
    """Laeuft inzwischen ein anderer als der mitgegebene Auftrag, passiert nichts ({veraltet: true})."""
    _schluessel(x_pult_key)
    i = _uuid_oder_404(iid)
    art, auftrag = payload.get("art"), payload.get("auftrag")
    if art not in STOPP_ARTEN:
        raise HTTPException(422, "art muss behalten oder verwerfen sein")
    if auftrag is not None and not isinstance(auftrag, str):
        raise HTTPException(422, "auftrag muss eine Auftrags-ID sein")
    if auftrag is not None:
        try:
            auftrag = str(uuid.UUID(auftrag))
        except ValueError:
            raise HTTPException(422, "auftrag muss eine Auftrags-ID sein")
    a = f"{lit(auftrag)}::uuid" if auftrag is not None else "NULL"
    s = _schreiben(lambda: f"SELECT marketing.pult_chat_stoppen({lit(i)}::uuid, {lit(art)}, {a}) AS s").get("s") or {}
    erg = {"abgeschlossen": bool(s.get("abgeschlossen"))}
    if s.get("veraltet"):
        erg["veraltet"] = True
    return erg


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


def _ablegen_und_zuordnen(ordner: str, iid: str, stuecke: list[tuple[str, bytes]]) -> list[str]:
    """Legt die Dateien ab und ordnet sie dem Mandanten des Inhalts zu. Fail-closed: scheitert
    eines von beiden, werden die eben abgelegten Dateien geloescht (sonst blieben sie ohne
    Zuordnung = Gemeinsam liegen) und der Fehler weitergeworfen."""
    namen: list[str] = []
    try:
        for stamm, roh in stuecke:
            namen.append(_sichtbar_ablegen(ordner, stamm, roh))
        if namen:
            zuordnen_fuer_inhalt(iid, namen)
    except Exception:
        for n in namen:
            try:
                os.remove(os.path.join(ordner, n))
            except OSError:
                pass
        raise
    return namen


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
    return {"dateien": _ablegen_und_zuordnen(ordner, i, fertig), "auftrag": auftrag}


# ─── Arbeiter ───────────────────────────────────────────────────────────


def _in_arbeit(a: str, status_sonst: int = 409) -> dict:
    """Auftrag lesen; nur in_arbeit mit gueltiger Vergabe zaehlt (sonst 404/status_sonst)."""
    z = _lesen_einer(lambda:
        "SELECT inhalt, art, status, kontext, coalesce(vergeben_bis > now(), false) AS gueltig, "
        "(SELECT mandant FROM marketing.inhalte WHERE id = chat_auftraege.inhalt) AS mandant "
        f"FROM marketing.chat_auftraege WHERE id = {lit(a)}::uuid")
    if not z:
        raise HTTPException(404, "Unbekannter Auftrag")
    if z.get("status") != "in_arbeit" or not z.get("gueltig"):
        raise HTTPException(status_sonst, "Auftrag ist nicht (mehr) in Arbeit")
    return z


def _medien(sichtbar: Callable[[str], bool]) -> list[str]:
    namen: set[str] = set()
    for o in quellen():
        try:
            eintraege = os.listdir(o)
        except OSError:
            continue
        for n in eintraege:
            if _MEDIEN_NAME.fullmatch(n) and not _ENTWURF.fullmatch(n) and os.path.isfile(os.path.join(o, n))                     and sichtbar(n):
                namen.add(n)
    return sorted(namen)[:MEDIEN_MAX]   # erst filtern, dann kappen


@arbeiter_router.post("/naechster")
def arbeiter_naechster(x_bild_key: str | None = Header(None)):
    _bild_schluessel(x_bild_key)
    zeile = _schreiben(lambda: f"SELECT marketing.pult_chat_naechster({lit(FRIST)}::interval) AS a")
    a = zeile.get("a")
    if isinstance(a, dict) and a.get("id"):
        m = a.get("mandant")
        try:
            s = sicht(m)
            a["medien"] = _medien(lambda n: not ist_fremd(n, m, s))
            a["mandant_name"] = s["name"]
        except HTTPException:      # der Auftrag ist schon vergeben: nie scheitern lassen, nur ohne Bilder
            a["medien"] = []
            a["mandant_name"] = m
            a["medien_hinweis"] = ZUORDNUNG_FEHLT
        antwort = {"auftrag": a}
    else:
        antwort = {"auftrag": None}
    _stopps_abschliessen()             # auch ohne offenen Editor; wirft nie
    return antwort


@arbeiter_router.post("/{aid}/weiter")
def arbeiter_weiter(aid: str, x_bild_key: str | None = Header(None)):
    _bild_schluessel(x_bild_key)
    a = _auftrag_id(aid)
    zeile = _schreiben(lambda:
        f"SELECT marketing.pult_chat_verlaengern({lit(a)}::uuid, {lit(FRIST)}::interval) AS ok")
    return {"ok": bool(zeile.get("ok"))}


def _fremde_neu(a: str, job: dict, bloecke: dict) -> str | None:
    """Verweist die neue Fassung auf ein Bild einer anderen Firma, das die Basisfassung nicht
    schon hatte? Altbestand bleibt unbeanstandet (sonst waere jede Aenderung blockiert)."""
    verweise = set(_MEDIEN_VERWEIS.findall(json.dumps(bloecke, ensure_ascii=False)))
    if not verweise:
        return None
    m = job.get("mandant")
    s = sicht(m)
    fremd = {n for n in verweise if ist_fremd(n, m, s)}
    if not fremd:
        return None
    basis = _lesen_einer(lambda:
        "SELECT f.bloecke FROM marketing.chat_auftraege a JOIN marketing.inhalt_fassungen f "
        f"ON f.inhalt = a.inhalt AND f.fassung = a.fassung_vorher WHERE a.id = {lit(a)}::uuid")
    alt = set(_MEDIEN_VERWEIS.findall(json.dumps((basis or {}).get("bloecke"), ensure_ascii=False)))
    rest = fremd - alt
    return BILD_FREMD + ", ".join(sorted(rest)) if rest else None


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


def _rechnen_und_pruefen(bloecke: dict) -> tuple[dict | None, list[str], str | None]:
    """Weg einer neuen Fassung (fertig und Stopp-Behalten): Flaechen rechnen, Validator,
    Schoenheit als Hinweise. -> (bloecke, hinweise, None) oder (None, [], fehler)."""
    try:
        bloecke, h = gestaltungen_rechnen(bloecke)
    except gestaltung.GestaltungFehler as e:
        return None, [], str(e)
    fehler = _bloecke_fehler(bloecke)
    if fehler:
        return None, [], str(fehler)
    return bloecke, h + [b["satz"] for b in schoenheit.bloecke_pruefen(bloecke)], None


def _abschliessen_sql(a: str, bloecke: dict | None, hinweis: str) -> dict:
    zeile = _schreiben(lambda:
        f"SELECT marketing.pult_chat_stopp_abschliessen({lit(a)}::uuid, "
        + (f"{lit(json.dumps(bloecke, ensure_ascii=False))}::jsonb" if bloecke is not None else "NULL")
        + f", {lit(hinweis)}) AS e")
    return zeile.get("e") or {}


def _stopp_abschliessen(a: str, job: dict, bloecke) -> dict:
    """Schliesst einen gestoppten Auftrag ab. Nur bei 'behalten' (und noch in_arbeit) wird
    gerechnet und geprueft; ungueltig => NULL mit Hinweis. Hat der Betreiber inzwischen
    gespeichert oder lehnt die DB das Speichern ab, zweiter Versuch mit NULL - der Auftrag darf nie in_arbeit haengen bleiben."""
    hinweis = ""
    if job.get("status") != "in_arbeit" or job.get("stopp") != "behalten" or not isinstance(bloecke, dict):
        bloecke = None
    else:
        try:
            bloecke, hinweise, fehler = _rechnen_und_pruefen(bloecke)
        except HTTPException:
            raise                      # z.B. 503: DB weg, spaeter erneut versuchen
        except Exception as e:         # noqa: BLE001 - kaputter Zwischenstand darf nie haengen bleiben
            bloecke, hinweise, fehler = None, [], f"{type(e).__name__}: {e}"
        hinweis = STOPP_UNGUELTIG + fehler if fehler else "; ".join(hinweise)
    try:
        return _abschliessen_sql(a, bloecke, hinweis)
    except HTTPException as e:
        if bloecke is None or e.status_code != 422:
            raise                      # 503 bleibt wiederholbar
        if str(e.detail).startswith("Inzwischen gibt es Fassung"):
            return _abschliessen_sql(a, None, STOPP_GESPEICHERT)
        # Jede andere Ablehnung der DB beim Speichern: wie Verwerfen mit Hinweis, nie in_arbeit lassen
        return _abschliessen_sql(a, None, STOPP_UNGUELTIG + str(e.detail))


def _gestoppter_auftrag(a: str) -> dict | None:
    return _lesen_einer(lambda:
        f"SELECT stopp, status, zwischenstand FROM marketing.chat_auftraege WHERE id = {lit(a)}::uuid")


def _stopps_abschliessen() -> None:
    """Gestoppte Auftraege, deren Arbeiter sich binnen 15 s nicht gemeldet hat, aus dem
    letzten Zwischenstand abschliessen. Wirft nie (der Stand-Abruf darf daran nicht scheitern)."""
    try:
        faellig = _lesen(lambda: f"SELECT id FROM marketing.pult_chat_stopp_faellig() AS id LIMIT {STOPPS_JE_ABRUF}")
    except HTTPException as e:
        log.warning("Faellige Stopps nicht lesbar: %s", e.detail)
        return
    for z in faellig[:STOPPS_JE_ABRUF]:
        a = str(z.get("id"))
        try:
            job = _gestoppter_auftrag(a)
            if job:
                _stopp_abschliessen(a, job, job.get("zwischenstand"))
        except Exception as e:   # noqa: BLE001 - naechster Abruf versucht es erneut
            log.warning("Stopp von %s nicht abgeschlossen: %s", a, getattr(e, "detail", e))


@arbeiter_router.post("/{aid}/pruefen")
def arbeiter_pruefen(aid: str, payload: dict = Body(...), x_bild_key: str | None = Header(None)):
    _bild_schluessel(x_bild_key)
    a = _auftrag_id(aid)
    bloecke = payload.get("bloecke")
    if not isinstance(bloecke, dict):
        raise HTTPException(422, "bloecke muss ein Objekt sein")
    job = _in_arbeit(a)
    return {"fehler": _fremde_neu(a, job, bloecke) or _gestaltung_fehler(bloecke) or _bloecke_fehler(bloecke)}


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
        f = _fremde_neu(a, job, bloecke)
        if f:
            return _zurueck(a, NICHT_UMGESETZT + f)
        bloecke, h, fehler = _rechnen_und_pruefen(bloecke)
        if fehler:
            return _zurueck(a, NICHT_UMGESETZT + fehler)
        hinweise += h
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


async def _json_gekappt(request: Request, grenze: int) -> dict:
    """JSON-Objekt aus dem Body, hoechstens grenze Bytes (Content-Length und gekappt gelesen)."""
    try:
        laenge = int(request.headers.get("content-length") or 0)
    except ValueError:
        laenge = 0
    if laenge > grenze:
        raise HTTPException(413, "Zwischenstand zu groß")
    roh = bytearray()
    async for stueck in request.stream():
        roh += stueck
        if len(roh) > grenze:
            raise HTTPException(413, "Zwischenstand zu groß")
    try:
        payload = json.loads(bytes(roh))
    except (ValueError, UnicodeDecodeError, RecursionError):
        raise HTTPException(422, "Body muss JSON sein")
    if not isinstance(payload, dict):
        raise HTTPException(422, "Body muss ein Objekt sein")
    return payload


@arbeiter_router.post("/{aid}/zwischenstand")
async def arbeiter_zwischenstand(aid: str, request: Request, x_bild_key: str | None = Header(None)):
    """Nur Form und Groesse, kein Validator, nie eine Fassung; verlaengert die Vergabe und
    meldet einen Stopp ({weiter: false, grund: "stopp", stopp})."""
    _bild_schluessel(x_bild_key)
    a = _auftrag_id(aid)
    payload = await _json_gekappt(request, ZWISCHENSTAND_KOERPER_MAX)
    bloecke, schritt, nr = payload.get("bloecke"), payload.get("schritt", ""), payload.get("nr", 0)
    if not isinstance(bloecke, dict):
        raise HTTPException(422, "bloecke muss ein Objekt sein")
    if not isinstance(schritt, str) or len(schritt) > SCHRITT_MAX:
        raise HTTPException(422, f"schritt muss Text mit hoechstens {SCHRITT_MAX} Zeichen sein")
    if isinstance(nr, bool) or not isinstance(nr, int) or not 0 <= nr < 2 ** 31:
        raise HTTPException(422, "nr muss eine ganze Zahl >= 0 sein")
    zeile = await run_in_threadpool(_schreiben, lambda:
        f"SELECT marketing.pult_chat_zwischenstand({lit(a)}::uuid, "
        f"{lit(json.dumps(bloecke, ensure_ascii=False))}::jsonb, {lit(schritt)}, {nr}, {lit(FRIST)}::interval) AS z")
    return zeile.get("z") or {"weiter": False, "grund": "verloren"}


@arbeiter_router.post("/{aid}/gestoppt")
async def arbeiter_gestoppt(aid: str, request: Request, x_bild_key: str | None = Header(None)):
    """Arbeiter meldet nach einem Stopp ab. bloecke = sein letzter Stand; null => der letzte
    gemeldete Zwischenstand. Kam die VM (15 s) zuvor, meldet die DB nur den Endstand."""
    _bild_schluessel(x_bild_key)
    a = _auftrag_id(aid)
    payload = await _json_gekappt(request, ZWISCHENSTAND_KOERPER_MAX)
    bloecke = payload.get("bloecke")
    if bloecke is not None and not isinstance(bloecke, dict):
        raise HTTPException(422, "bloecke muss ein Objekt oder null sein")

    def abschliessen() -> dict:
        job = _gestoppter_auftrag(a)
        if not job:
            raise HTTPException(404, "Unbekannter Auftrag")
        return _stopp_abschliessen(a, job, bloecke if bloecke is not None else job.get("zwischenstand"))
    return await run_in_threadpool(abschliessen)


def _dateityp(name: str) -> str:
    endung = os.path.splitext(name)[1].lower()
    return _BILDTYP.get(endung) or _DOK_TYP.get(endung, "application/octet-stream")


@arbeiter_router.get("/{aid}/medien/{name}")
def arbeiter_medien(aid: str, name: str, x_bild_key: str | None = Header(None)):
    _bild_schluessel(x_bild_key)
    a = _auftrag_id(aid)
    if not _ARBEITER_DATEI.fullmatch(name):
        raise HTTPException(404, "Unbekannte Datei")
    job = _in_arbeit(a, 404)
    if ist_fremd(name, job.get("mandant"), sicht(job.get("mandant"))):
        raise HTTPException(404, "Unbekannte Datei")
    for o in quellen():
        pfad = _im_ordner(o, name)
        if pfad:
            return FileResponse(pfad, media_type=_dateityp(name),
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
    return {"name": _ablegen_und_zuordnen(ordner, str(job["inhalt"]), [(name[:-len(".jpg")], bytes(roh))])[0]}
