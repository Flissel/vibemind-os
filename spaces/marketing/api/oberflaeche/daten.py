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


# --- Stufe B, Tag 5: Bilder zuordnen und Collagen bearbeiten (speichert Fassungen, versendet nichts) --

def _bilder_lesen(mandant: str, x_pult_key: str | None) -> list[str]:
    from spaces.marketing.api import chat, medien_mandant, pult
    pult._schluessel(x_pult_key)
    s = medien_mandant.sicht(mandant)
    return chat._medien(lambda n: not medien_mandant.ist_fremd(n, mandant, s))


def bilder(mandant: str) -> list[str]:
    """Bilder, die diese Firma nutzen darf (eigene und gemeinsame, nie fremde), sortiert, hoechstens 500:
    dieselbe Regel wie fuer den Assistenten (chat._medien mit medien_mandant.ist_fremd)."""
    roh = _rufen(_bilder_lesen, mandant=mandant)
    return [n for n in roh if isinstance(n, str)] if isinstance(roh, list) else []


def seite_speichern(iid: str, basis: int, betreff: str, vorschautext: str, seite: dict) -> int:
    """Neue Collage-Fassung (Urheber betreiber); die DB prueft Entwurf und Basis = neueste."""
    from spaces.marketing.api import pult
    antwort = _rufen(pult.seite_speichern, iid=iid, payload={
        "basis_fassung": basis, "betreff": betreff, "vorschautext": vorschautext, "seite": seite,
        "urheber": "betreiber"})
    n = antwort.get("fassung") if isinstance(antwort, dict) else None
    if not isinstance(n, int) or isinstance(n, bool):
        raise DatenFehler("nicht_erreichbar")
    return n


# --- Stufe B, Tag 6: Assistent, Flaechen, Feld-Newsletter umwandeln, neue Collage (versendet nichts) ---

def _fassung(antwort, feld: str = "fassung") -> int:
    n = antwort.get(feld) if isinstance(antwort, dict) else None
    if not isinstance(n, int) or isinstance(n, bool):
        raise DatenFehler("nicht_erreichbar")
    return n


def chat_senden(iid: str, nachricht: str) -> dict:
    """Bitte an den Gestaltungs-Assistenten: dieselbe Chat-Runde wie im Editor von Sales (Abo, Arbeiter am PC)."""
    from spaces.marketing.api import chat
    antwort = _rufen(chat.chat_anlegen, iid=iid, payload={"nachricht": nachricht, "kontext": {}})
    return antwort if isinstance(antwort, dict) else {}


def chat_stand(iid: str) -> dict:
    from spaces.marketing.api import chat
    antwort = _rufen(chat.chat_stand, iid=iid)
    return antwort if isinstance(antwort, dict) else {}


def chat_stoppen(iid: str, art: str, auftrag: str) -> None:
    from spaces.marketing.api import chat
    _rufen(chat.chat_stoppen, iid=iid, payload={"art": art, "auftrag": auftrag})


def chat_rueckgaengig(iid: str, auftrag: str) -> int:
    from spaces.marketing.api import chat
    return _fassung(_rufen(chat.chat_rueckgaengig, iid=iid, payload={"auftrag": auftrag}))


def gestaltung_rechnen(iid: str, gestaltung: dict) -> dict:
    """Flaeche neu rechnen (wie der Editor in Sales): {url, width, height, hinweise}."""
    from spaces.marketing.api import gestaltung as gestaltung_api
    antwort = _rufen(gestaltung_api.pult_gestaltung, iid=iid, payload={"gestaltung": gestaltung})
    if not isinstance(antwort, dict) or not isinstance(antwort.get("url"), str):
        raise DatenFehler("nicht_erreichbar")
    return antwort


def in_bloecke(iid: str, von: str) -> int:
    """Feld-Newsletter in Bloecke uebernehmen: neue Block-Fassung (die DB lehnt alles andere ab)."""
    from spaces.marketing.api import pult
    return _fassung(_rufen(pult.in_bloecke, iid=iid, payload={"von": von}))


def collage_anlegen(mandant: str, titel: str, betreff: str) -> str:
    """Neuer Collage-Newsletter mit der Startseite in den Farben der Marke; gibt die Inhalts-ID zurueck."""
    from spaces.marketing.api import pult
    antwort = _rufen(pult.collage_anlegen, payload={"mandant": mandant, "titel": titel, "betreff": betreff})
    iid = antwort.get("inhalt") if isinstance(antwort, dict) else None
    if not isinstance(iid, str) or not iid:
        raise DatenFehler("nicht_erreichbar")
    return iid


# --- Stufe B, Punkt 2: Bestellen (hinter dem Kosten-Schalter in bestellen.py; versendet nichts) -------

def _auftrag_id(antwort) -> str:
    aid = antwort.get("auftrag") if isinstance(antwort, dict) else None
    if not isinstance(aid, str) or not aid or aid == "None":
        raise DatenFehler("nicht_erreichbar")
    return aid


def reel_bestellen(mandant: str, titel: str, briefing: dict) -> str:
    """Neues Reel aus Bildern (art bilder) als Betreiber; die Markenstimme setzt video.py selbst ein."""
    from spaces.marketing.api import video
    return _auftrag_id(_rufen(video.pult_anlegen, payload={"mandant": mandant, "art": "bilder", "titel": titel,
                                                             "briefing": briefing}))


def reel_nochmal(mandant: str, aid: str, wunsch: str) -> str:
    from spaces.marketing.api import video
    return _auftrag_id(_rufen(video.pult_nochmal, aid=aid, payload={"mandant": mandant, "wunsch": wunsch}))


def reel_abbrechen(mandant: str, aid: str) -> None:
    """Mit mandant im Koerper prueft video.py, dass der Auftrag dieser Firma gehoert (sonst 404)."""
    from spaces.marketing.api import video
    _rufen(video.pult_abbrechen, aid=aid, payload={"mandant": mandant})


def folge_bestellen(mandant: str, titel: str, sprache: str, skript: list[dict]) -> str:
    from spaces.marketing.api import ton
    return _auftrag_id(_rufen(ton.pult_anlegen, payload={"mandant": mandant, "titel": titel, "sprache": sprache,
                                                           "skript": skript}))


def folge_abbrechen(aid: str) -> None:
    """ton.pult_abbrechen prueft die Firma nicht: das tut bestellen.folge_abbrechen vorher."""
    from spaces.marketing.api import ton
    _rufen(ton.pult_abbrechen, aid=aid)


# --- Stufe B, Punkt 3: Werkbank (lange Videos abnehmen; Arbeit ausloesen nur hinter dem Kosten-Schalter) ---

VON = "marketing-oberflaeche"     # wer entschieden hat (Pult-Feld von)


def _ok(antwort) -> bool:
    return not (isinstance(antwort, dict) and antwort.get("ok") is False)


def werke(mandant: str) -> list[dict]:
    from spaces.marketing.api import werkbank
    return _liste(_rufen(werkbank.pult_liste, mandant=mandant, limit=werkbank.LISTE_MAX), "werke")


def werk(mandant: str, aid: str) -> dict:
    """{werk, stand, werkbank, gliederung_kennung}; fremder oder unbekannter Auftrag -> abgelehnt (404)."""
    from spaces.marketing.api import werkbank
    antwort = _rufen(werkbank.pult_werk, aid=aid, mandant=mandant)
    return antwort if isinstance(antwort, dict) else {}


def werk_szene(mandant: str, aid: str, nr: int) -> dict:
    from spaces.marketing.api import werkbank
    antwort = _rufen(werkbank.pult_szene, aid=aid, nr=nr, mandant=mandant)
    return antwort if isinstance(antwort, dict) else {}


def werk_wunsch(mandant: str, kennung: str, wunsch: str) -> bool:
    from spaces.marketing.api import werkbank
    return _ok(_rufen(werkbank.pult_wunsch, kennung=kennung,
                      payload={"mandant": mandant, "von": VON, "wunsch": wunsch}))


def werk_urteil(mandant: str, kennung: str, urteil: str, wunsch: str) -> bool:
    from spaces.marketing.api import werkbank
    return _ok(_rufen(werkbank.pult_urteil, kennung=kennung,
                      payload={"mandant": mandant, "von": VON, "urteil": urteil, "wunsch": wunsch or None}))


def werk_szene_nochmal(mandant: str, aid: str, nr: int) -> bool:
    from spaces.marketing.api import werkbank
    return _ok(_rufen(werkbank.pult_nochmal, aid=aid, nr=nr, payload={"mandant": mandant, "von": VON}))


def werk_szene_oeffnen(mandant: str, aid: str, nr: int, wunsch: str) -> bool:
    from spaces.marketing.api import werkbank
    return _ok(_rufen(werkbank.pult_oeffnen, aid=aid, nr=nr,
                      payload={"mandant": mandant, "von": VON, "wunsch": wunsch}))


def werk_neu_schreiben(mandant: str, aid: str, nr: int | None, wunsch: str) -> bool:
    from spaces.marketing.api import werkbank
    payload = {"mandant": mandant, "von": VON, "wunsch": wunsch}
    if nr is None:
        return _ok(_rufen(werkbank.pult_gliederung_wunsch, aid=aid, payload=payload))
    return _ok(_rufen(werkbank.pult_szene_wunsch, aid=aid, nr=nr, payload=payload))


def werk_neu_bauen(mandant: str, aid: str) -> bool:
    from spaces.marketing.api import werkbank
    return _ok(_rufen(werkbank.pult_neu_bauen, aid=aid, payload={"mandant": mandant, "von": VON}))


# --- Stufe C, Punkt 5: Teilen als Versandauftrag (hinter dem Kosten-Schalter in teilen.py; versendet nichts) ---

def teilen(mandant: str, art: str, aid: str, kanal: str, empfaenger: str, betreff: str, nachricht: str) -> dict:
    """{ok, id, wiederholung, grund}: eine Absage der DB-Funktion ist ok=False mit Grund."""
    from spaces.marketing.api import teilen as _teilen
    antwort = _rufen(_teilen.pult_teilen, payload={"mandant": mandant, "art": art, "id": aid, "kanal": kanal,
                                                    "empfaenger": empfaenger, "betreff": betreff,
                                                    "nachricht": nachricht})
    return antwort if isinstance(antwort, dict) else {"ok": False, "grund": ""}


def geteilt(mandant: str, art: str) -> list[dict]:
    from spaces.marketing.api import teilen as _teilen
    return _liste(_rufen(_teilen.pult_stand, mandant=mandant, art=art), "auftraege")


# --- F5: weitere Bestellarten vor dem Abbau in Sales (hinter dem Kosten-Schalter in bestellen.py) ---------

def reel_bestellen_art(mandant: str, art: str, titel: str, briefing: dict) -> str:
    """Material, Kurzfassung oder KI-Bilder (art bilder mit briefing.ki); Regeln in video.briefing_pruefen."""
    from spaces.marketing.api import video
    return _auftrag_id(_rufen(video.pult_anlegen, payload={"mandant": mandant, "art": art, "titel": titel,
                                                             "briefing": briefing}))


def material() -> dict:
    """Laura-Videos {videos: [{kennung, name, projekt, dauer_s}], hinweis?}; Laura weg = leere Liste mit Hinweis."""
    from spaces.marketing.api import video
    antwort = _rufen(video.pult_material)
    return antwort if isinstance(antwort, dict) else {"videos": []}


def reel_ki_neu(mandant: str, aid: str, bild: str, idee: str) -> None:
    from spaces.marketing.api import reel_bilder
    _rufen(reel_bilder.pult_ki_neu, aid=aid, payload={"mandant": mandant, "bild": bild, "idee": idee})


def reel_ki_freigeben(mandant: str, aid: str) -> bool:
    from spaces.marketing.api import reel_bilder
    antwort = _rufen(reel_bilder.pult_ki_freigeben, aid=aid, payload={"mandant": mandant})
    return bool(isinstance(antwort, dict) and antwort.get("ok"))
