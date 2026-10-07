"""Freigabe-Routen des Pults (sales-claw Spec 2026-10-07-newsletter-freigabe-und-entwurfsseite-design.md
§1/§2, Migration 063). Alle mit X-Pult-Key; Fehler wie pult.py (DB-Ablehnung 422 mit Grund, DB weg 503).
Die Uebergaenge selbst (einreichen, zurueckziehen, zurueckgeben, entscheiden) setzt die DB durch;
hier nur Formen, der Kommentar-Vorcheck (ohne SQL) und der Export nach dem Freigeben.

Freigeben = pult_entscheiden(... 'freigeben' ...) und DANACH, in einem try, der Export: alle Gestaltungs-
flaechen (Image-Bloecke mit props.gestaltung) der festgeschriebenen Fassung x 3 Geraete in die Medien der
Firma und - nur fuer Newsletter im Block-Format - der Export-Auftrag (art=export). Jeder Fehler dort macht
die Freigabe NICHT ungueltig: Antwort 200 mit export_fehler, export_nachholen holt es nach."""
from __future__ import annotations

import logging
import os

from fastapi import APIRouter, Body, Header, HTTPException, Query

from spaces.marketing.api.chat import FLAECHEN_MAX, GERAETE, export_ausfuehren, slug
from spaces.marketing.api.gestaltung import quellen
from spaces.marketing.api.pult import _fassung_pflicht, _lesen, _lesen_einer, _schluessel, _schreiben, _uuid_oder_404, lit

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/pult")

KOMMENTAR_MAX = 2000
KOMMENTAR_LEER = "Bitte sag kurz, was fehlt"
KOMMENTAR_ZU_LANG = "Der Kommentar ist zu lang (höchstens 2000 Zeichen)"
EXPORT_GENERISCH = "Export fehlgeschlagen"
_FREIGABEN_STATUS = ("eingereicht", "entschieden")


def _von(payload) -> str:
    wert = payload.get("von") if isinstance(payload, dict) else None
    return wert.strip() if isinstance(wert, str) else ""


@router.post("/inhalte/{iid}/einreichen")
def einreichen(iid: str, payload: dict = Body(default={}), x_pult_key: str | None = Header(None)):
    _schluessel(x_pult_key)
    i = _uuid_oder_404(iid)
    von = _von(payload)
    zeile = _schreiben(lambda: f"SELECT marketing.pult_einreichen({lit(i)}::uuid, {lit(von)}) AS fassung")
    return {"status": "eingereicht", "fassung": int(zeile["fassung"])}


@router.post("/inhalte/{iid}/zurueckziehen")
def zurueckziehen(iid: str, payload: dict = Body(default={}), x_pult_key: str | None = Header(None)):
    _schluessel(x_pult_key)
    i = _uuid_oder_404(iid)
    von = _von(payload)
    _schreiben(lambda: f"SELECT marketing.pult_zurueckziehen({lit(i)}::uuid, {lit(von)}) AS s")
    return {"status": "entwurf"}


@router.post("/inhalte/{iid}/zurueckgeben")
def zurueckgeben(iid: str, payload: dict = Body(...), x_pult_key: str | None = Header(None)):
    _schluessel(x_pult_key)
    i = _uuid_oder_404(iid)
    f = _fassung_pflicht(payload.get("fassung"))
    text = payload.get("text")
    text = text.strip() if isinstance(text, str) else ""
    if not text:
        raise HTTPException(422, KOMMENTAR_LEER)              # ohne SQL
    if len(text) > KOMMENTAR_MAX:
        raise HTTPException(422, KOMMENTAR_ZU_LANG)
    von = _von(payload)
    zeile = _schreiben(lambda:
        f"SELECT marketing.pult_zurueckgeben({lit(i)}::uuid, {f}, {lit(von)}, {lit(text)}) AS id")
    return {"status": "entwurf", "rueckmeldung": str(zeile["id"])}


def _festgeschrieben(i: str) -> dict | None:
    """Status, Art, Titel und die Fassung, die freigegeben wurde (Format + Bloecke); None = unbekannt."""
    return _lesen_einer(lambda:
        "SELECT i.status, i.art, i.titel, f.format, f.bloecke FROM marketing.inhalte i "
        "LEFT JOIN marketing.inhalt_fassungen f ON f.inhalt = i.id AND f.fassung = i.freigegebene_fassung "
        f"WHERE i.id = {lit(i)}::uuid")


def _flaechen_ids(bloecke) -> list[str]:
    """Image-Bloecke mit props.gestaltung (genau die, die _flaechen als Flaeche akzeptiert)."""
    if not isinstance(bloecke, dict):
        return []
    erg = []
    for bid, b in bloecke.items():
        if not isinstance(b, dict) or b.get("type") != "Image":
            continue
        data = b.get("data")
        props = data.get("props") if isinstance(data, dict) else None
        if isinstance(props, dict) and isinstance(props.get("gestaltung"), dict):
            erg.append(bid)
    return erg


def _vorhandene_flaechen(titel: str, ids: list[str]) -> set[str]:
    """Flaechen, zu denen schon eine Geraetedatei <slug>-<id>-<geraet>.jpg liegt (in irgendeinem Medienordner)."""
    s = slug(titel or "")
    return {bid for bid in ids
            if any(os.path.exists(os.path.join(o, f"{s}-{bid}-{g}.jpg")) for o in quellen() for g in GERAETE)}


def _export_auftrag_vorhanden(i: str) -> bool:
    """Gibt es fuer die Freigabe schon einen Export-Auftrag (offen/in_arbeit, oder fertig seit der Entscheidung)?"""
    z = _lesen_einer(lambda:
        "SELECT EXISTS (SELECT 1 FROM marketing.chat_auftraege a JOIN marketing.inhalte n ON n.id = a.inhalt "
        f"WHERE a.inhalt = {lit(i)}::uuid AND a.art = 'export' AND (a.status IN ('offen', 'in_arbeit') "
        "OR (a.status = 'fertig' AND a.erstellt_am >= n.entschieden_am))) AS vorhanden")
    return bool(z and z.get("vorhanden"))


def _export_nach_freigabe(i: str, z: dict | None = None, nachholen: bool = False) -> dict:
    """Flaechen-Export (+ Newsletter-Auftrag) der festgeschriebenen Fassung; wirft nie.
    z = schon gelesene Zeile aus _festgeschrieben (sonst wird sie gelesen).
    nachholen: nur, was noch fehlt (Flaeche ohne Geraetedatei, Auftrag ohne laufenden/fertigen Export);
    die Antwort nennt zusaetzlich, was uebersprungen wurde."""
    erg = {"status": "freigegeben", "flaechen": [], "export_auftrag": None, "export_fehler": None}
    if nachholen:
        erg.update(flaechen_uebersprungen=[], auftrag_vorhanden=False)
    try:
        z = z if z is not None else _festgeschrieben(i)
        if z is None or z.get("status") != "freigegeben":
            raise HTTPException(422, "Nur freigegebene Inhalte lassen sich exportieren")
        ids = _flaechen_ids(z.get("bloecke"))
        if nachholen:
            da = _vorhandene_flaechen(z.get("titel"), ids)
            erg["flaechen_uebersprungen"] = [b for b in ids if b in da]
            ids = [b for b in ids if b not in da]
        for n in range(0, len(ids), FLAECHEN_MAX):          # eine Anfrage nimmt hoechstens FLAECHEN_MAX Flaechen
            erg["flaechen"] += export_ausfuehren(i, ids[n:n + FLAECHEN_MAX], False)["dateien"]
        if z.get("art") == "newsletter" and z.get("format") == "bloecke":
            if nachholen and _export_auftrag_vorhanden(i):
                erg["auftrag_vorhanden"] = True
            else:
                erg["export_auftrag"] = export_ausfuehren(i, [], True)["auftrag"]
    except HTTPException as e:
        erg["export_fehler"] = str(e.detail)
    except Exception as e:  # noqa: BLE001 - die Freigabe steht; nie den Rohtext nach aussen
        log.warning("Export nach Freigabe von %s fehlgeschlagen: %s", i, e)
        erg["export_fehler"] = EXPORT_GENERISCH
    return erg


@router.post("/inhalte/{iid}/freigeben")
def freigeben(iid: str, payload: dict = Body(...), x_pult_key: str | None = Header(None)):
    _schluessel(x_pult_key)
    i = _uuid_oder_404(iid)
    f = _fassung_pflicht(payload.get("fassung"))
    von = _von(payload)
    _schreiben(lambda:
        f"SELECT marketing.pult_entscheiden({lit(i)}::uuid, {f}, 'freigeben', {lit(von)}, '') AS status")
    return _export_nach_freigabe(i)


@router.post("/inhalte/{iid}/export_nachholen")
def export_nachholen(iid: str, payload: dict = Body(default={}), x_pult_key: str | None = Header(None)):
    _schluessel(x_pult_key)
    i = _uuid_oder_404(iid)
    z = _festgeschrieben(i)
    if z is None:
        raise HTTPException(404, "Unbekannter Inhalt")
    if z.get("status") != "freigegeben":
        raise HTTPException(422, "Nur freigegebene Inhalte lassen sich exportieren")
    return _export_nach_freigabe(i, z, nachholen=True)


_FREIGABE_SPALTEN = (
    "i.id::text AS id, i.mandant, m.name AS mandant_name, i.art, i.titel, "
    "(SELECT f.felder->>'betreff' FROM marketing.inhalt_fassungen f WHERE f.inhalt = i.id "
    "  ORDER BY f.fassung DESC LIMIT 1) AS betreff, "
    "i.status, i.eingereichte_fassung, i.eingereicht_am::text AS eingereicht_am, i.eingereicht_von, "
    "i.entschieden_von, i.entschieden_am::text AS entschieden_am, i.grund, "
    "coalesce((SELECT jsonb_agg(jsonb_build_object('text', r.text, 'von', r.von, 'am', r.am, "
    "  'fassung', r.fassung, 'erledigt', r.erledigt_am IS NOT NULL) ORDER BY r.am DESC) "
    "  FROM (SELECT * FROM marketing.rueckmeldungen WHERE inhalt = i.id ORDER BY am DESC LIMIT 3) r), "
    "  '[]'::jsonb) AS rueckmeldungen, "
    "jsonb_build_object('auftrag_status', (SELECT a.status FROM marketing.chat_auftraege a "
    "  WHERE a.inhalt = i.id AND a.art = 'export' ORDER BY a.erstellt_am DESC LIMIT 1)) AS export")
_LETZTE_RUECKMELDUNG = "(SELECT max(am) FROM marketing.rueckmeldungen WHERE inhalt = i.id)"


@router.get("/freigaben")
def freigaben(status: str = "eingereicht", limit: int = Query(20, ge=1, le=100),
              x_pult_key: str | None = Header(None)):
    _schluessel(x_pult_key)
    if status not in _FREIGABEN_STATUS:
        raise HTTPException(422, f"status muss eines von {', '.join(_FREIGABEN_STATUS)} sein")
    if status == "eingereicht":
        wo, ordnung = "i.status = 'eingereicht'", "i.eingereicht_am DESC"
    else:
        # freigegeben/verworfen plus zurueckgegebene (wieder Entwurf, mit Rueckmeldung) der letzten 30 Tage
        wo = ("((i.status IN ('freigegeben', 'abgelehnt') AND i.entschieden_am > now() - interval '30 days') "
              f"OR (i.status = 'entwurf' AND {_LETZTE_RUECKMELDUNG} > now() - interval '30 days' "
              "AND EXISTS (SELECT 1 FROM marketing.rueckmeldungen WHERE inhalt = i.id AND erledigt_am IS NULL)))")
        ordnung = f"coalesce(i.entschieden_am, {_LETZTE_RUECKMELDUNG}) DESC"
    zeilen = _lesen(lambda:
        f"SELECT {_FREIGABE_SPALTEN} FROM marketing.inhalte i "
        f"JOIN marketing.mandanten m ON m.id = i.mandant WHERE {wo} ORDER BY {ordnung} LIMIT {int(limit)}")
    return {"freigaben": zeilen}
