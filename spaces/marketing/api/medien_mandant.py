"""Sichtbarkeitsregel fuer Bilder je Mandant (Besitzerin der Regel).

Keine Zeile in marketing.medien_mandant = Gemeinsam; Zeile mit mandant NULL =
ausdruecklich Gemeinsam; sichtbar fuer M = eigene + Gemeinsam. Eine Zeile
gewinnt immer vor der Logo-Namensregel (logo-<mandant>-<hex10>.png|jpg
gehoert <mandant>, solange keine Zeile existiert). Alles fail-closed ueber die
Helfer aus api.pult (DB-Fehler -> 503, DB-Ablehnung -> 422)."""
from __future__ import annotations

import json
import os
import re

from fastapi import APIRouter, Body, Header, HTTPException
from fastapi.responses import JSONResponse

from spaces.marketing.api.pult import (_NICHT_ERREICHBAR, _UNGUELTIGE_EINGABE, _datei_existiert, _db_grund,
                                       _lesen, _lesen_einer, _mandant_pflicht, _schluessel, _schreiben, _uuid_oder_404,
                                       _von, lit, verweise_lesen)
# _mandant_pflicht hat nur eine Fassung (pult.py); marke.py importiert sie weiter ueber dieses Modul.
from spaces.marketing.claw import bildplaetze
from spaces.marketing.sync import _db

router = APIRouter(prefix="/api/pult")

GEMEINSAM = None
_LOGO = re.compile(r"logo-([a-z][a-z0-9_-]{0,40})-[0-9a-f]{10}\.(?:png|jpg)", re.IGNORECASE)
# Gleich der DB-CHECK '^[^/\\"[:cntrl:]]{1,200}$': [:cntrl:] = 0x00-0x1f und 0x7f.
_DATEI = re.compile(r'[^/\\"\x00-\x1f\x7f]{1,200}')
NAMEN_MAX, ZUORDNEN_MAX = 2000, 20


def _gueltig(name) -> bool:
    return isinstance(name, str) and _DATEI.fullmatch(name) is not None and ".." not in name


def _pruefen(namen: list) -> list[str]:
    if any(not _gueltig(n) for n in namen):
        raise HTTPException(422, "Unbekannter Dateiname")
    return namen


def sicht(mandant: str) -> dict:
    zeile = _lesen_einer(lambda: (
        f"SELECT (SELECT name FROM marketing.mandanten WHERE id = {lit(mandant)}) AS name, "
        f"coalesce((SELECT jsonb_agg(dateiname) FROM marketing.medien_mandant "
        f"WHERE mandant = {lit(mandant)}), '[]') AS eigene, "
        f"coalesce((SELECT jsonb_agg(dateiname) FROM marketing.medien_mandant "
        f"WHERE mandant IS NOT NULL AND mandant <> {lit(mandant)}), '[]') AS fremde, "
        f"coalesce((SELECT jsonb_agg(dateiname) FROM marketing.medien_mandant "
        f"WHERE mandant IS NULL), '[]') AS gemeinsam"))
    if zeile is None:
        raise HTTPException(503, "Marketing-Datenbank nicht erreichbar")
    if zeile.get("name") is None:
        raise HTTPException(422, "Unbekannter Mandant")
    return {"name": zeile["name"], "eigene": set(zeile.get("eigene") or []),
            "fremde": set(zeile.get("fremde") or []), "gemeinsam": set(zeile.get("gemeinsam") or [])}


def _gefaltet(s: dict) -> tuple[set, set, set]:
    """(eigene, gemeinsam, fremde) ohne Gross/Klein; einmal je Sicht berechnet und in s gemerkt."""
    merk = s.get("_gefaltet")
    quellen = (s["eigene"], s["gemeinsam"], s["fremde"])
    if merk is None or merk[0] != tuple(map(id, quellen)):
        merk = (tuple(map(id, quellen)), tuple({n.casefold() for n in q} for q in quellen))
        s["_gefaltet"] = merk
    return merk[1]


def ist_fremd(name: str, mandant: str, s: dict) -> bool:
    """Ohne Gross/Klein (Schluss-Fix I1): NTFS am PC oeffnet FOTO.JPG als foto.jpg. Fremd zuerst (fail-closed):
    ein Treffer bei einer anderen Firma gewinnt auch dann, wenn eine Variante eigen oder gemeinsam ist."""
    eigene, gemeinsam, fremde = _gefaltet(s)
    n = name.casefold()
    if n in fremde:
        return True
    if n in eigene or n in gemeinsam:
        return False
    treffer = _LOGO.fullmatch(name)
    return bool(treffer) and treffer.group(1).casefold() != mandant.casefold()


def _array(namen: list[str]) -> str:
    return "ARRAY[" + ",".join(lit(n) for n in namen) + "]::text[]"


def _schreiben_zahl(bauen) -> int:
    """Upsert-Anweisung mit datenmodifizierender CTE ausfuehren, Zeilenzahl zurueck.

    Nicht ueber _schreiben/query_one: die verpacken jede Abfrage als
    `SELECT ... FROM (<sql>) t`, und Postgres lehnt eine datenmodifizierende
    WITH-Klausel in einer Unterabfrage ab (muss oberste Ebene sein). Deshalb
    direkt ueber _db._run_psql (streng, ON_ERROR_STOP); Fehlerabbildung wie
    _schreiben: ERROR:-Ablehnung -> 422, alles andere -> 503 (fail-closed)."""
    try:
        sql = bauen()
        aus = _db._run_psql(sql, None, streng=True)
    except ValueError:
        raise HTTPException(422, _UNGUELTIGE_EINGABE)
    except Exception as e:
        grund = _db_grund(e)
        if grund is None:
            raise HTTPException(503, _NICHT_ERREICHBAR)
        raise HTTPException(422, grund)
    zeilen = [z.strip() for z in (aus or "").splitlines() if z.strip()]
    if not zeilen or not zeilen[-1].isdigit():
        raise HTTPException(503, _NICHT_ERREICHBAR)
    return int(zeilen[-1])


def zuordnen(namen: list[str], mandant: str | None) -> int:
    if not namen:
        return 0
    namen = list(dict.fromkeys(_pruefen(namen)))   # ON CONFLICT trifft eine Zeile nur einmal
    m = "NULL::text" if mandant is None else lit(mandant)
    return _schreiben_zahl(lambda: (
        f"WITH x AS (INSERT INTO marketing.medien_mandant (dateiname, mandant) "
        f"SELECT n, {m} FROM unnest({_array(namen)}) AS n "
        f"ON CONFLICT (dateiname) DO UPDATE SET mandant = EXCLUDED.mandant, geaendert_am = now() "
        f"RETURNING 1) SELECT count(*)::int AS n FROM x"))


def _zuordnen_ueber(namen: list[str], quelle: str, bedingung: str, fehlt: str) -> int:
    if not namen:
        return 0
    namen = list(dict.fromkeys(_pruefen(namen)))
    n = _schreiben_zahl(lambda: (
        f"WITH x AS (INSERT INTO marketing.medien_mandant (dateiname, mandant) "
        f"SELECT n, i.mandant FROM unnest({_array(namen)}) AS n, {quelle} WHERE {bedingung} "
        f"ON CONFLICT (dateiname) DO UPDATE SET mandant = EXCLUDED.mandant, geaendert_am = now() "
        f"RETURNING 1) SELECT count(*)::int AS n FROM x"))
    if n == 0:
        raise HTTPException(404, fehlt)
    return n


def zuordnen_fuer_inhalt(iid: str, namen: list[str]) -> int:
    return _zuordnen_ueber(namen, "marketing.inhalte i", f"i.id = {lit(iid)}::uuid", "Unbekannter Inhalt")


def zuordnen_fuer_bildauftrag(aid: str, namen: list[str]) -> int:
    return _zuordnen_ueber(
        namen, "marketing.inhalte i JOIN marketing.bild_auftraege b ON b.inhalt = i.id",
        f"b.id = {lit(aid)}::uuid", "Unbekannter Bildauftrag")


# ─── Routen ─────────────────────────────────────────────────────────────


@router.get("/mandanten")
def mandanten_liste(x_pult_key: str | None = Header(None)):
    _schluessel(x_pult_key)
    return {"mandanten": _lesen(lambda:
        "SELECT id, name, aktiv FROM marketing.mandanten ORDER BY aktiv DESC, name")}


@router.post("/medien/sichtbar")
def medien_sichtbar(payload: dict = Body(...), x_pult_key: str | None = Header(None)):
    _schluessel(x_pult_key)
    m = _mandant_pflicht(payload.get("mandant"))
    namen = payload.get("namen")
    if not isinstance(namen, list) or len(namen) > NAMEN_MAX:
        raise HTTPException(422, f"namen muss eine Liste mit hoechstens {NAMEN_MAX} Eintraegen sein")
    s = sicht(m)
    eigene, gemeinsam, _ = _gefaltet(s)
    sichtbar, zuordnung = [], {}
    for n in namen:
        if not _gueltig(n) or ist_fremd(n, m, s):
            continue
        sichtbar.append(n)
        if n.casefold() in eigene:
            zuordnung[n] = m
        elif n.casefold() in gemeinsam:
            zuordnung[n] = None
        else:
            logo = _LOGO.fullmatch(n)
            if logo:
                zuordnung[n] = logo.group(1).casefold()
    aktive = _lesen(lambda:
        "SELECT id, name FROM marketing.mandanten WHERE aktiv ORDER BY name")
    return {"mandant": m, "name": s["name"], "sichtbar": sichtbar, "zuordnung": zuordnung,
            "mandanten": aktive}


@router.post("/medien/zuordnung")
def medien_zuordnung(payload: dict = Body(...), x_pult_key: str | None = Header(None)):
    _schluessel(x_pult_key)
    datei = payload.get("dateiname")
    if not _gueltig(datei):
        raise HTTPException(422, "Unbekannter Dateiname")
    if "mandant" not in payload:
        raise HTTPException(422, "mandant fehlt (null = Gemeinsam)")
    m = payload["mandant"]
    if m is not None:   # nur JSON null heisst Gemeinsam
        m = _mandant_pflicht(m)
        sicht(m)   # unbekannter Mandant -> 422
    zuordnen([datei], m)
    return {"dateiname": datei, "mandant": m}


@router.post("/inhalte/{iid}/medien/zuordnen")
def inhalt_medien_zuordnen(iid: str, payload: dict = Body(...), x_pult_key: str | None = Header(None)):
    _schluessel(x_pult_key)
    iid = _uuid_oder_404(iid)
    namen = payload.get("namen")
    if not isinstance(namen, list) or not 1 <= len(namen) <= ZUORDNEN_MAX:
        raise HTTPException(422, f"namen muss 1 bis {ZUORDNEN_MAX} Eintraege haben")
    return {"zugeordnet": zuordnen_fuer_inhalt(iid, namen)}


# --- Bilder loeschen trotz Verwendung (Plan 2026-10-19 E4-E7) -----------------------------------
# Reihenfolge beim Aufrufer (sales-ui): pruefen -> /medien/platzhalter -> Datei loeschen -> /medien/vergessen.
# Ist "gesperrt" nicht leer, wird die Datei NICHT geloescht (fail-closed); schon umgestellte Entwuerfe tragen
# dann harmlose Platzhalter.

_KEIN_PLATZ = "Das Bild steckt an einer Stelle, die kein Bildplatz ist"


def _sperrgrund(v: dict) -> str | None:
    """Grund, warum ein Verweis nicht umgestellt wird (E5), oder None (eigener Editor-Entwurf)."""
    if not v.get("eigen"):
        return "Entwurf einer anderen Firma"
    if v.get("status") == "freigegeben" or v.get("wo") == "freigegeben":
        return "Freigegeben – der Versand braucht das Bild"
    if v.get("status") == "eingereicht":
        return "Liegt zur Freigabe – erst zurückziehen"
    if v.get("status") != "entwurf":
        return "Nur Entwürfe lassen sich umstellen"
    if v.get("format") != "bloecke":
        return "Alter Newsletter ohne Editor – das Bild dort erst von Hand ersetzen"
    return None


def _umstellen(iid: str, name: str) -> tuple[int | None, str | None]:
    """Neueste Fassung lesen, Plaetze ersetzen, als neue Fassung (Urheber betreiber) speichern.
    (fassung, None) bei Erfolg; (None, grund) wenn gesperrt; (None, None) wenn das Bild dort nicht mehr steckt."""
    f = _lesen_einer(lambda:
        "SELECT fassung, format, bloecke, felder FROM marketing.inhalt_fassungen "
        f"WHERE inhalt = {lit(iid)}::uuid ORDER BY fassung DESC LIMIT 1")
    if not f or f.get("format") != "bloecke" or not isinstance(f.get("bloecke"), dict):
        return None, "Alter Newsletter ohne Editor – das Bild dort erst von Hand ersetzen"
    neu, n = bildplaetze.platzhalter_einsetzen(f["bloecke"], name)
    rest = re.compile(r"medien:" + re.escape(name) + r"(?![\w.\-])", re.IGNORECASE)
    if rest.search(json.dumps(neu, ensure_ascii=False)):
        return None, _KEIN_PLATZ
    if n == 0:
        return None, None
    felder = f.get("felder") if isinstance(f.get("felder"), dict) else {}
    betreff, vorschau = str(felder.get("betreff") or ""), str(felder.get("vorschautext") or "")
    basis = int(f["fassung"])
    try:
        zeile = _schreiben(lambda:
            f"SELECT marketing.pult_bloecke_speichern({lit(iid)}::uuid, {basis}, {lit(betreff)}, "
            f"{lit(vorschau)}, {lit(json.dumps(neu, ensure_ascii=False))}::jsonb, 'betreiber', false) AS fassung")
    except HTTPException as e:
        if e.status_code == 422:
            return None, str(e.detail)
        raise
    return int(zeile["fassung"]), None


@router.post("/medien/platzhalter")
def medien_platzhalter(payload: dict = Body(...), x_pult_key: str | None = Header(None)):
    """Vor dem Loeschen eines Bildes: eigene Editor-Entwuerfe, deren neueste Fassung es traegt, bekommen eine
    neue Fassung mit Platzhaltern. Alles andere mit Treffer landet mit Grund in "gesperrt"."""
    _schluessel(x_pult_key)
    name = payload.get("name")
    if not _gueltig(name):
        raise HTTPException(422, "Unbekannter Dateiname")
    m = _mandant_pflicht(payload.get("mandant"))
    _von(payload)
    if ist_fremd(name, m, sicht(m)):
        raise HTTPException(403, "Das Bild gehört einer anderen Firma")
    umgestellt, gesperrt = [], []
    for v in verweise_lesen(name, m):
        grund = _sperrgrund(v)
        fassung = None
        if grund is None:
            try:
                fassung, grund = _umstellen(str(v["id"]), name)
            except HTTPException as e:
                if e.status_code != 503:
                    raise
                # E7/Review M4: DB mitten in der Schleife weg - 503 (Datei NICHT loeschen), aber die schon
                # umgestellten Entwuerfe nennen (sie tragen jetzt Platzhalter)
                return JSONResponse(status_code=503, content={"detail": str(e.detail), "umgestellt": umgestellt,
                                                              "gesperrt": gesperrt})
        if grund is not None:
            gesperrt.append({"id": v.get("id"), "titel": v.get("titel") or "", "grund": grund})
        elif fassung is not None:
            umgestellt.append({"id": v.get("id"), "titel": v.get("titel") or "", "fassung": fassung})
    return {"umgestellt": umgestellt, "gesperrt": gesperrt}


@router.post("/medien/vergessen")
def medien_vergessen(payload: dict = Body(...), x_pult_key: str | None = Header(None)):
    """Nach dem Loeschen der Datei: Firmenzuordnung entfernen (exakter Name), sonst erbte eine spaetere
    Datei gleichen Namens die alte Firma. Nur Aufraeumen (Controller-Ruling I2): steht die Datei noch in einem
    Medienordner, 409 - sonst machte "vergessen" ein fremdes Bild fuer alle Firmen sichtbar."""
    _schluessel(x_pult_key)
    name = payload.get("name")
    if not _gueltig(name):
        raise HTTPException(422, "Unbekannter Dateiname")
    if _datei_existiert(name):
        raise HTTPException(409, "Datei existiert noch – erst löschen")
    return {"entfernt": _schreiben_zahl(lambda: (
        f"WITH x AS (DELETE FROM marketing.medien_mandant WHERE dateiname = {lit(name)} RETURNING 1) "
        f"SELECT count(*)::int AS n FROM x"))}