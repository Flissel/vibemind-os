"""Gestaltungs-Flaechen der Newsletter (sales-claw Spec 2026-10-02): rechnet eine
Gestaltung zu einem Entwurfsbild (gs-<hash12>.jpg im MARKETING_BILD_ORDNER).
  POST /api/pult/inhalte/{iid}/gestaltung  (X-Pult-Key, pult._schluessel)
Nur lesende DB-Zugriffe; Aufraeumen unverwiesener Entwurfsbilder hoechstens
einmal je Stunde und nie zulasten der Anfrage."""
from __future__ import annotations

import copy
import logging
import os
import time

from fastapi import APIRouter, Body, Header, HTTPException

from spaces.marketing.api.bilder import _ordner
from spaces.marketing.api.pult import _lesen, _schluessel, _uuid_oder_404, lit
from spaces.marketing.claw import gestaltung

log = logging.getLogger(__name__)

pult_router = APIRouter(prefix="/api/pult")
AUFRAEUM_ABSTAND_S = 3600
_zuletzt = 0.0


def quellen() -> list[str]:
    """Ordner, aus denen Bild-Ebenen ihre Quellen lesen (leere weglassen)."""
    rohe = (os.environ.get("MARKETING_MEDIEN_ORDNER", ""), os.environ.get("MARKETING_BILD_ORDNER", ""))
    return [o.strip() for o in rohe if o.strip()]


def gestaltungen_rechnen(dok: dict) -> tuple[dict, list[str]]:
    """Rechnet jede Image-Flaeche, deren url noch nicht zur Gestaltung passt.
    Gibt eine Kopie des Dokuments und die gesammelten Hinweise zurueck."""
    neu = copy.deepcopy(dok)
    hinweise: list[str] = []
    ordner = _ordner()
    for bid, b in neu.items():
        if not isinstance(b, dict) or b.get("type") != "Image":
            continue
        data = b.get("data")
        props = data.get("props") if isinstance(data, dict) else None
        g = props.get("gestaltung") if isinstance(props, dict) else None
        if not isinstance(g, dict):
            continue
        try:
            name = gestaltung.name_fuer(g)
            if props.get("url") == "medien:" + name and os.path.exists(os.path.join(ordner, name)):
                continue
            erg = gestaltung.rechnen(g, quellen(), ordner)
        except gestaltung.GestaltungFehler as e:
            raise gestaltung.GestaltungFehler(f"{bid}: {e}") from e
        props["url"], props["width"], props["height"] = erg["url"], erg["width"], erg["height"]
        hinweise += [f"{bid}: {h}" for h in erg["hinweise"]]
    return neu, hinweise


def verwiesene_gs() -> set[str]:
    """Alle Entwurfsbilder, auf die Fassungen, Vorlagen oder Vorlagen-Fassungen verweisen."""
    zeilen = _lesen(lambda:
        "SELECT DISTINCT m[1] AS m FROM ("
        "SELECT bloecke FROM marketing.inhalt_fassungen UNION ALL "
        "SELECT bloecke FROM marketing.newsletter_vorlagen UNION ALL "
        "SELECT bloecke FROM marketing.newsletter_vorlagen_fassungen) q, "
        "LATERAL regexp_matches(q.bloecke::text, 'medien:(gs-[0-9a-f]{12}\\.jpg)', 'g') AS m")
    return {str(z["m"]) for z in zeilen if z.get("m")}


def aufraeumen_falls_faellig(jetzt: float | None = None) -> None:
    global _zuletzt
    t = time.time() if jetzt is None else jetzt
    if t - _zuletzt < AUFRAEUM_ABSTAND_S:
        return
    try:
        gestaltung.aufraeumen(_ordner(), verwiesene_gs(), t)
        _zuletzt = t   # erst nach Erfolg: ein Fehler sperrt nicht fuer eine Stunde
    except Exception:   # nie in die Anfrage hinein
        log.warning("Aufraeumen der Entwurfsbilder fehlgeschlagen", exc_info=True)


@pult_router.post("/inhalte/{iid}/gestaltung")
def pult_gestaltung(iid: str, payload: dict = Body(...), x_pult_key: str | None = Header(None)):
    _schluessel(x_pult_key)
    i = _uuid_oder_404(iid)
    g = payload.get("gestaltung")
    if not isinstance(g, dict):
        raise HTTPException(422, "gestaltung muss ein Objekt sein")
    if not _lesen(lambda: f"SELECT id FROM marketing.inhalte WHERE id = {lit(i)}::uuid"):
        raise HTTPException(404, "Unbekannter Inhalt")
    ordner = _ordner()
    try:
        erg = gestaltung.rechnen(g, quellen(), ordner)
    except gestaltung.GestaltungFehler as e:
        raise HTTPException(422, str(e))
    aufraeumen_falls_faellig()
    return {k: erg[k] for k in ("url", "width", "height", "hinweise")}
