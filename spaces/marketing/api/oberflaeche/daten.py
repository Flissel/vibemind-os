"""Datenzugang der Marketing-Oberflaeche: ruft die vorhandenen Pult-Funktionen derselben API im
Prozess auf (keine neue Tabelle, kein eigenes SQL). Die Pult-Funktionen pruefen weiter ihren
eigenen Schluessel (MARKETING_PULT_KEY); den reicht dieses Modul aus der Umgebung durch.

Stufe A liest nur; Stufe B schreibt ueber dieselben Pult-Funktionen (nie eigenes SQL).
Fehler werden zu DatenFehler mit einer Art, die die Seite in Worte fasst:
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


# --- Stufe B, Tag 1: Vorlagen und Vorschau (schreibt nur einen Entwurf, versendet nichts) --------

def _html(antwort) -> str:
    """Body einer HTMLResponse der Pult-Vorschau; alles andere (z. B. PDF) ist hier ein Fehler."""
    roh = getattr(antwort, "body", None)
    art = str(getattr(antwort, "media_type", "") or "")
    if not isinstance(roh, (bytes, bytearray)) or not art.startswith("text/html"):
        raise DatenFehler("nicht_erreichbar")
    return bytes(roh).decode("utf-8", errors="replace")


def vorlagen(mandant: str) -> list[dict]:
    """Freigegebene Vorlagen dieser Firma (eigene und fuer alle)."""
    from spaces.marketing.api import pult
    return [v for v in _liste(_rufen(pult.vorlagen, mandant=mandant, status="freigegeben"), "vorlagen")
            if isinstance(v.get("name"), str)]


def vorlage_vorschau(mandant: str, name: str, fmt: str) -> str:
    """Vorschau einer Vorlage, gefuellt fuer diese Firma. Ohne Bildbasis: Bilder stehen als [Bild: name]."""
    from spaces.marketing.api import pult
    return _html(_rufen(pult.vorlage_vorschau, name=name, format=fmt, bild_basis="", mandant=mandant))


def aus_vorlage(mandant: str, vorlage: str, titel: str) -> str:
    """Neuer Newsletter-Entwurf aus einer Vorlage; gibt seine id zurueck. Status bleibt 'entwurf'."""
    from spaces.marketing.api import pult
    antwort = _rufen(pult.inhalt_aus_vorlage, payload={"vorlage": vorlage, "titel": titel, "mandant": mandant})
    iid = antwort.get("id") if isinstance(antwort, dict) else None
    if not isinstance(iid, str) or not iid:
        raise DatenFehler("nicht_erreichbar")
    return iid


def entwurf(iid: str) -> dict:
    from spaces.marketing.api import pult
    antwort = _rufen(pult.inhalt, iid=iid)
    return antwort if isinstance(antwort, dict) else {}


def entwurf_vorschau(iid: str, fassung: int, fmt: str) -> str:
    from spaces.marketing.api import pult
    return _html(_rufen(pult.vorschau, iid=iid, fassung=fassung, format=fmt, bild_basis=""))


# --- Stufe B, Tag 2: Entwuerfe bearbeiten und einreichen (Einreichen = Status, versendet nichts) --

def bloecke_speichern(iid: str, basis: int, betreff: str, vorschautext: str, bloecke: dict) -> int:
    """Neue Fassung (Urheber betreiber); die DB prueft, dass basis die neueste ist. Gibt ihre Nummer zurueck."""
    from spaces.marketing.api import pult
    antwort = _rufen(pult.bloecke_speichern, iid=iid, payload={
        "basis_fassung": basis, "betreff": betreff, "vorschautext": vorschautext, "bloecke": bloecke,
        "urheber": "betreiber"})
    n = antwort.get("fassung") if isinstance(antwort, dict) else None
    if not isinstance(n, int) or isinstance(n, bool):
        raise DatenFehler("nicht_erreichbar")
    return n


def einreichen(iid: str, von: str) -> None:
    """Legt den Entwurf in die Freigaben von Sales (Status 'eingereicht'). Versendet nichts."""
    from spaces.marketing.api import freigabe
    _rufen(freigabe.einreichen, iid=iid, payload={"von": von})


def zurueckziehen(iid: str, von: str) -> None:
    from spaces.marketing.api import freigabe
    _rufen(freigabe.zurueckziehen, iid=iid, payload={"von": von})


# --- Stufe B, Tag 3: Plan, Kampagnen und Marke bearbeiten (planen und vorschlagen, versendet nichts) --

def plan_anlegen(mandant: str, datum: str, art: str, titel: str, notiz: str) -> str:
    """Neuer Planeintrag als Betreiber (Status 'geplant'). Ein Planeintrag versendet nichts."""
    from spaces.marketing.api import plan as _plan
    antwort = _rufen(_plan.pult_anlegen, payload={"mandant": mandant, "datum": datum, "art": art,
                                                   "titel": titel, "notiz": notiz})
    pid = antwort.get("id") if isinstance(antwort, dict) else None
    if not isinstance(pid, str) or not pid:
        raise DatenFehler("nicht_erreichbar")
    return pid


def plan_aendern(pid: str, aenderung: dict) -> str:
    """Nur die genannten Felder (status, datum, titel, notiz). Ein neues Datum ohne Status macht aus
    'geplant' 'verschoben' (DB); ein Vorschlag bleibt Vorschlag, bis jemand ihn bestaetigt."""
    from spaces.marketing.api import plan as _plan
    erlaubt = {k: v for k, v in aenderung.items() if k in ("status", "datum", "titel", "notiz") and v is not None}
    antwort = _rufen(_plan.pult_aendern, pid=pid, payload=erlaubt)
    return str(antwort.get("status") or "") if isinstance(antwort, dict) else ""


def kampagne_anlegen(mandant: str, name: str, ziel: str, zielgruppe: str, von: str, bis: str, wer: str) -> str:
    """Neue Kampagne ('in_planung'): der Kampagnen-Arbeiter schlaegt danach Beitraege vor (immer 'vorschlag')."""
    from spaces.marketing.api import kampagnen as _kampagnen
    antwort = _rufen(_kampagnen.pult_anlegen, payload={
        "mandant": mandant, "name": name, "ziel": ziel, "zielgruppe": zielgruppe, "filter": {},
        "von": von, "bis": bis, "von_wer": wer})
    kid = antwort.get("id") if isinstance(antwort, dict) else None
    if not isinstance(kid, str) or not kid:
        raise DatenFehler("nicht_erreichbar")
    return kid


def kampagne_status(kid: str, status: str) -> None:
    from spaces.marketing.api import kampagnen as _kampagnen
    _rufen(_kampagnen.pult_status, kid=kid, payload={"status": status})


def marke_bearbeiten(mandant: str, formular: dict) -> str:
    """Legt nur einen Bearbeitungs-Auftrag an: der Marken-Assistent macht daraus einen Vorschlag, den der
    Betreiber uebernimmt oder verwirft. Direkt gespeichert wird nichts."""
    from spaces.marketing.api import marke as _marke
    antwort = _rufen(_marke.marke_bearbeiten, payload={"mandant": mandant, "formular": formular})
    return str(antwort.get("auftrag") or "") if isinstance(antwort, dict) else ""


def marke_uebernehmen(mandant: str, vid: str, von: str) -> None:
    from spaces.marketing.api import marke as _marke
    _rufen(_marke.marke_uebernehmen, vid=vid, payload={"von": von, "mandant": mandant})


def marke_verwerfen(mandant: str, vid: str, von: str) -> None:
    from spaces.marketing.api import marke as _marke
    _rufen(_marke.marke_verwerfen, vid=vid, payload={"von": von, "mandant": mandant})
