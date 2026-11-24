"""Datenzugang der Marketing-Oberflaeche: ruft die vorhandenen Pult-Funktionen derselben API im
Prozess auf (keine neue Tabelle, kein eigenes SQL). Die Pult-Funktionen pruefen weiter ihren
eigenen Schluessel (MARKETING_PULT_KEY); den reicht dieses Modul aus der Umgebung durch.

Stufe A liest nur. Fehler werden zu DatenFehler mit einer Art, die die Seite in Worte fasst:
- nicht_verbunden: Pult-Schluessel fehlt oder passt nicht
- nicht_erreichbar: Datenbank oder Dienst antwortet nicht (Rohtext wird nie gezeigt)
- abgelehnt: die API lehnt die Anfrage mit Grund ab (z. B. unbekannte Firma)

Die Route-Funktionen haben FastAPI-Vorgaben (Query/Header) als Standardwerte; darum geht jeder
Aufruf hier mit allen Argumenten."""
from __future__ import annotations

import logging
import os
from typing import Callable

from fastapi import HTTPException

log = logging.getLogger(__name__)

PULT_ENV = "MARKETING_PULT_KEY"
REELS_MAX = 200     # Hoechstwert von /api/pult/video
PODCAST_MAX = 50


class DatenFehler(Exception):
    def __init__(self, art: str, grund: str = ""):
        super().__init__(art)
        self.art = art
        self.grund = grund


def _rufen(funktion: Callable, **werte):
    try:
        return funktion(**werte, x_pult_key=os.environ.get(PULT_ENV, "").strip())
    except HTTPException as f:
        detail = str(f.detail or "")
        if f.status_code == 401 or (f.status_code == 503 and detail.startswith("misconfigured")):
            raise DatenFehler("nicht_verbunden")
        if f.status_code >= 500:
            raise DatenFehler("nicht_erreichbar")
        raise DatenFehler("abgelehnt", detail[:300])
    except DatenFehler:
        raise
    except Exception as f:  # noqa: BLE001 - nie den Rohtext zeigen, nur die Art ins Log
        log.warning("Marketing-Oberflaeche: Daten nicht lesbar (%s)", type(f).__name__)
        raise DatenFehler("nicht_erreichbar")


def _liste(antwort, feld: str) -> list[dict]:
    roh = antwort.get(feld) if isinstance(antwort, dict) else None
    return [z for z in roh if isinstance(z, dict)] if isinstance(roh, list) else []


def firmen() -> list[dict]:
    from spaces.marketing.api import medien_mandant
    return [f for f in _liste(_rufen(medien_mandant.mandanten_liste, einrichtung=False), "mandanten")
            if isinstance(f.get("id"), str)]


def newsletter(mandant: str) -> list[dict]:
    from spaces.marketing.api import pult
    return _liste(_rufen(pult.inhalte, mandant=mandant, art="newsletter", status=None), "inhalte")


def plan(mandant: str, von: str, bis: str) -> list[dict]:
    from spaces.marketing.api import plan as _plan
    return _liste(_rufen(_plan.pult_lesen, mandant=mandant, von=von, bis=bis), "eintraege")


def kampagnen(mandant: str) -> list[dict]:
    from spaces.marketing.api import kampagnen as _kampagnen
    return _liste(_rufen(_kampagnen.pult_lesen, mandant=mandant), "kampagnen")


def marke(mandant: str) -> dict:
    from spaces.marketing.api import marke as _marke
    antwort = _rufen(_marke.marke_stand, mandant=mandant, alle=False)
    return antwort if isinstance(antwort, dict) else {}


def reels(mandant: str, archiv: bool) -> list[dict]:
    from spaces.marketing.api import video
    return _liste(_rufen(video.pult_liste, mandant=mandant, limit=REELS_MAX, archiv=archiv), "auftraege")


def podcast(mandant: str) -> list[dict]:
    from spaces.marketing.api import ton
    return _liste(_rufen(ton.pult_liste, mandant=mandant, limit=PODCAST_MAX), "auftraege")
