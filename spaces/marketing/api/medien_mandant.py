"""Sichtbarkeitsregel fuer Bilder je Mandant (Besitzerin der Regel).

Keine Zeile in marketing.medien_mandant = Gemeinsam; Zeile mit mandant NULL =
ausdruecklich Gemeinsam; sichtbar fuer M = eigene + Gemeinsam. Eine Zeile
gewinnt immer vor der Logo-Namensregel (logo-<mandant>-<hex10>.png|jpg
gehoert <mandant>, solange keine Zeile existiert). Alles fail-closed ueber die
Helfer aus api.pult (DB-Fehler -> 503, DB-Ablehnung -> 422)."""
from __future__ import annotations

import re

from fastapi import APIRouter, Body, Header, HTTPException

from spaces.marketing.api.pult import (_NICHT_ERREICHBAR, _UNGUELTIGE_EINGABE, _db_grund, _lesen,
                                       _lesen_einer, _mandant, _schluessel, _uuid_oder_404, lit)
from spaces.marketing.sync import _db

router = APIRouter(prefix="/api/pult")

GEMEINSAM = None
_LOGO = re.compile(r"logo-([a-z][a-z0-9_-]{0,40})-[0-9a-f]{10}\.(?:png|jpg)")
# Gleich der DB-CHECK '^[^/\\"[:cntrl:]]{1,200}$': [:cntrl:] = 0x00-0x1f und 0x7f.
_DATEI = re.compile(r'[^/\\"\x00-\x1f\x7f]{1,200}')
NAMEN_MAX, ZUORDNEN_MAX = 2000, 20


def _gueltig(name) -> bool:
    return isinstance(name, str) and _DATEI.fullmatch(name) is not None and ".." not in name


def _mandant_pflicht(wert) -> str:
    """Mandant ausdruecklich angeben: kein Rueckfall auf vibemind (fail-closed)."""
    if not isinstance(wert, str) or not wert:
        raise HTTPException(422, "Unbekannter Mandant")
    return _mandant(wert)


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


def ist_fremd(name: str, mandant: str, s: dict) -> bool:
    if name in s["eigene"] or name in s["gemeinsam"]:
        return False
    if name in s["fremde"]:
        return True
    treffer = _LOGO.fullmatch(name)
    return bool(treffer) and treffer.group(1) != mandant


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
    sichtbar, zuordnung = [], {}
    for n in namen:
        if not _gueltig(n) or ist_fremd(n, m, s):
            continue
        sichtbar.append(n)
        if n in s["eigene"]:
            zuordnung[n] = m
        elif n in s["gemeinsam"]:
            zuordnung[n] = None
        else:
            logo = _LOGO.fullmatch(n)
            if logo:
                zuordnung[n] = logo.group(1)
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
