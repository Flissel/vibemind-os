"""Auftrags-Arbeiter am PC (M8 Stufe A, Auftragsbuch). Holt Marketing-Auftraege (Reel) ueber die Marketing-API der VM
(/api/auftrag/arbeiter, X-Bild-Key), laesst Claude ueber den lokalen Shim :8117 das Drehbuch schreiben, legt das
KI-Reel an und meldet nach dem Render die Beitragstexte zurueck. Sales macht daraus Kanal-Entwuerfe.
Versendet wird nichts. Gestartet von marketing-dienste-starten.ps1; Gesundheits-Port 8147."""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

from spaces.marketing.claw import auftrag_texte, markenwissen
from spaces.marketing.workers.bild_worker import ApiFehler, _grund, tls_kontext, umgebung_laden
from spaces.marketing.workers.chat_worker import MODELL, frage

LOG = logging.getLogger("auftrag_arbeiter")
PORT = 8147
TAKT_S = 30
NOCH_EINMAL_PAUSE_S = 5
LLM_URL = os.environ.get("MARKETING_AUFTRAG_LLM_URL", "http://127.0.0.1:8117/v1")
STAND: dict = {"letzter_lauf": None, "letztes_ergebnis": None}


class AuftragApi:
    """Die Auftrags-Routen der Marketing-API (Task 3); jede Methode entspricht einer Route."""
    PRAEFIX = "/api/auftrag/arbeiter"

    def __init__(self, basis: str, schluessel: str):
        self.basis, self.schluessel = basis.rstrip("/"), schluessel
        self.tls = tls_kontext()

    def _anfrage(self, methode: str, pfad: str, daten: dict | None = None) -> dict:
        koerper = json.dumps(daten, ensure_ascii=False).encode("utf-8") if daten is not None else None
        req = urllib.request.Request(self.basis + self.PRAEFIX + pfad, data=koerper, method=methode,
                                     headers={"Content-Type": "application/json"})
        req.add_unredirected_header("X-Bild-Key", self.schluessel)
        try:
            with urllib.request.urlopen(req, timeout=60, context=self.tls) as r:
                return json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            raise ApiFehler(e.code, _grund(e)) from None

    def naechster(self) -> dict | None:
        return self._anfrage("POST", "/naechster").get("auftrag")

    def laufend(self) -> list[dict]:
        return self._anfrage("GET", "/laufend").get("auftraege") or []

    def weiter(self, aid: str) -> bool:
        return bool(self._anfrage("POST", f"/{aid}/weiter").get("ok"))

    def reel_anlegen(self, aid: str, drehbuch: dict) -> str:
        return self._anfrage("POST", f"/{aid}/reel", drehbuch).get("video", "")

    def reel_lesen(self, aid: str) -> dict:
        return self._anfrage("GET", f"/{aid}/reel")

    def erzeugt(self, aid: str, texte: dict) -> bool:
        return bool(self._anfrage("POST", f"/{aid}/erzeugt", texte).get("ok"))

    def fehler(self, aid: str, grund: str) -> bool:
        return bool(self._anfrage("POST", f"/{aid}/fehler", {"grund": grund}).get("ok"))


def shim_fragen(system: str, nachricht: str) -> str:
    """Eine Frage an Claude ueber den Shim (ohne Werkzeuge, nicht streamend)."""
    return frage(system, [{"role": "user", "content": nachricht}], url=LLM_URL, modell=MODELL)


def marke_laden(mandant: str, thema: str) -> str:
    """Markenwissen der Firma als Prompt-Text; leer, wenn es keins gibt (wirft nie)."""
    try:
        return markenwissen.laden(markenwissen.wurzel(), mandant, mandant, thema).text
    except Exception:  # noqa: BLE001 - ohne Markenwissen schreibt Claude trotzdem
        return ""


class _Verloren(Exception):
    """Die Lease ist weg: der Auftrag gehoert uns nicht mehr."""


def _antwort(fragen, warte, system: str, nachricht: str, lesen, weiter=None):
    """Claude fragen und lesen; bei unbrauchbarer Antwort einmal neu fragen (vorher Lease erneuern), danach None."""
    for versuch in range(2):
        if versuch:
            warte(NOCH_EINMAL_PAUSE_S)
            if weiter is not None and not weiter():
                raise _Verloren
        ergebnis = lesen(fragen(system, nachricht))
        if ergebnis is not None:
            return ergebnis
    return None


def _neu(api, auftrag: dict, fragen, warte, marke) -> str:
    """Schritt 1: Drehbuch erfragen, Reel anlegen."""
    aid, mandant = auftrag["id"], str(auftrag.get("mandant") or "")
    if auftrag.get("art") != "reel":
        return _fehler_melden(api, aid, "Unbekannte Art")
    thema = str((auftrag.get("eingabe") or {}).get("thema") or "").strip()
    if not api.weiter(aid):
        return "leer"                      # Lease verloren: der Auftrag gehoert uns nicht mehr
    if not thema:
        return _fehler_melden(api, aid, "Kein Thema im Auftrag")
    system, nachricht = auftrag_texte.drehbuch_prompt(thema, marke(mandant, thema))
    try:
        drehbuch = _antwort(fragen, warte, system, nachricht, auftrag_texte.drehbuch_lesen,
                            lambda: api.weiter(aid))
    except _Verloren:
        return "leer"
    if drehbuch is None:
        return _fehler_melden(api, aid, "Drehbuch unbrauchbar")
    drehbuch["thema"] = thema[:auftrag_texte.THEMA_MAX]
    if not api.weiter(aid):                # die Anfrage an Claude kann lange dauern
        return "leer"
    try:
        api.reel_anlegen(aid, drehbuch)
    except ApiFehler as e:
        if e.code == 409:                  # hat schon ein Video oder ist nicht mehr in Arbeit
            return "leer"
        raise
    return "reel_beauftragt"


def _laufend(api, auftrag: dict, fragen, warte, marke) -> str:
    """Schritt 2: Stand des Reels lesen; fertig -> Beitragstexte, gescheitert -> Fehler, sonst warten."""
    aid, mandant = auftrag["id"], str(auftrag.get("mandant") or "")
    reel = api.reel_lesen(aid)
    status = reel.get("status")
    if status in ("fehler", "abgebrochen"):
        return _fehler_melden(api, aid, f"Reel: {reel.get('befund') or status}")
    if status != "fertig":
        return "wartet"
    thema = str((auftrag.get("eingabe") or {}).get("thema") or "").strip()
    drehbuch = (auftrag.get("ergebnis") or {}).get("drehbuch")    # steht seit POST reel im Ergebnis
    if isinstance(drehbuch, dict):
        thema = str(drehbuch.get("thema") or thema).strip()
        sprechertext = str(drehbuch.get("sprechertext") or "")
    else:
        sprechertext = ""
    system, nachricht = auftrag_texte.beitrag_prompt(thema, sprechertext, marke(mandant, thema))
    texte = _antwort(fragen, warte, system, nachricht, auftrag_texte.beitrag_lesen)
    if texte is None:
        return _fehler_melden(api, aid, "Beitragstext unbrauchbar")
    if not api.erzeugt(aid, texte):
        LOG.warning("erzeugt abgelehnt: Auftrag nicht mehr in Arbeit")
        return "leer"
    return "erzeugt"


def _fehler_melden(api, aid: str, grund: str) -> str:
    """Auftrag als gescheitert melden; False = der Stand war schon weg (dann kein Fehler gemeldet)."""
    if api.fehler(aid, grund):
        return "fehler"
    LOG.warning("fehler abgelehnt: Auftrag nicht mehr in Arbeit")
    return "leer"


def _isoliert(api, auftrag: dict, schritt, *args) -> str:
    """Einen Auftrag fuer sich bearbeiten: ein Fehler hier darf die anderen nicht aufhalten.
    API-4xx (ausser 409 = nicht mehr unser) -> der Auftrag scheitert mit kurzem Grund; Netz-, Shim-, 5xx- und
    sonstige Fehler werden nur mit dem Typnamen geloggt, der Auftrag bleibt (Lease/Frist regeln es)."""
    aid = str(auftrag.get("id") or "")
    try:
        return schritt(api, auftrag, *args)
    except ApiFehler as e:
        if e.code == 409:
            return "leer"
        if 400 <= e.code < 500:
            LOG.warning("Auftrag abgelehnt: API %s", e.code)
            try:
                return _fehler_melden(api, aid, f"Auftrag nicht verarbeitbar (API {e.code})")
            except Exception as e2:  # noqa: BLE001
                LOG.warning("fehler melden scheiterte: %s", type(e2).__name__)
                return "fehler"
        LOG.warning("API-Fehler %s, Auftrag bleibt", e.code)
        return "fehler"
    except Exception as e:  # noqa: BLE001 - Typname genuegt; Texte koennen Prompts enthalten
        LOG.warning("Auftrag bleibt liegen: %s", type(e).__name__)
        return "fehler"

def ein_durchlauf(api, fragen=shim_fragen, warte=time.sleep, jetzt=time.monotonic, marke=marke_laden) -> str:  # noqa: ARG001 - jetzt: Signatur laut Plan
    """Erst alle laufenden Reels pruefen, dann hoechstens einen neuen Auftrag uebernehmen.
    Rueckgabe: Ergebnis des letzten Schritts - leer, reel_beauftragt, wartet, erzeugt oder fehler."""
    ergebnis = "leer"
    for auftrag in api.laufend():
        ergebnis = _isoliert(api, auftrag, _laufend, fragen, warte, marke)
    auftrag = api.naechster()
    if auftrag:    # naechster liefert nie einen Auftrag mit Video (SQL), der wartet unter laufend
        return _isoliert(api, auftrag, _neu, fragen, warte, marke)
    return ergebnis


class _Gesundheit(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        rumpf = json.dumps(STAND, default=str).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(rumpf)

    def log_message(self, *_):
        pass


def main() -> None:
    umgebung_laden()
    basis, schluessel = os.environ.get("MARKETING_BILD_URL", ""), os.environ.get("MARKETING_BILD_KEY", "")
    if not basis or not schluessel:
        raise SystemExit("MARKETING_BILD_URL/MARKETING_BILD_KEY fehlen in Vibemind_V1/.env")
    api = AuftragApi(basis, schluessel)
    threading.Thread(target=HTTPServer(("127.0.0.1", PORT), _Gesundheit).serve_forever, daemon=True).start()
    while True:
        try:
            STAND["letztes_ergebnis"] = ein_durchlauf(api)
        except Exception as e:  # noqa: BLE001 - nichts darf die Schleife toeten (auch http.client.HTTPException)
            STAND["letztes_ergebnis"] = f"fehler: {type(e).__name__}"
        STAND["letzter_lauf"] = time.strftime("%Y-%m-%d %H:%M:%S")
        print(STAND, flush=True)
        time.sleep(TAKT_S)


if __name__ == "__main__":
    main()
