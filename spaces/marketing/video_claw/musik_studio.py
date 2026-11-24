"""Client des Musik-Dienstes fuer den Video-Claw. Fail-soft wie laura_api: keine Funktion wirft, Rueckgabe
{"ok": ...}, Fehlertexte hoechstens 300 Zeichen.

Der Dienst (spaces/musik/studio, djset.dienst) prueft jeden Aufruf: POST nur mit Content-Type
application/json, kein Origin-Kopf, Host 127.0.0.1:<port>. urllib setzt den Host aus der URL und sendet
nie einen Origin - darum die Adresse mit 127.0.0.1 (localhost kostet am PC 2 s je Aufruf).
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request

FEHLER_MAXLAENGE = 300
ZEITLIMIT_S = 330        # in-laura darf im Dienst bis 300 s dauern
POLL_ZEITLIMIT_S = 15    # eine Statusabfrage; haengt der Dienst, soll das Warten nicht mitziehen
MAX_POLL_FEHLER = 2      # so viele Abfragen in Folge duerfen scheitern, die dritte beendet das Warten
SPEICHER_AUFGEBEN_S = 60  # wartet die Musik so lange auf Arbeitsspeicher, kommt sie fuer dieses Reel zu spaet
SPEICHER_MARKE = "zu wenig Arbeitsspeicher"   # so beginnt der Grund im Musik-Dienst (djset.dienst.laeufer)
KANAL = "video-claw"
FERTIG = ("fertig", "fertig_mit_befund")
ENDE_OHNE_MUSIK = ("gescheitert", "abgebrochen")
_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def _basis() -> str:
    return os.environ.get("MUSIK_STUDIO_URL", "http://127.0.0.1:8148").strip().rstrip("/")


def _fehler(text: str) -> dict:
    return {"ok": False, "fehler": str(text)[:FEHLER_MAXLAENGE]}


def _roh(methode: str, url: str, daten: bytes | None, kopf: dict,
         zeitlimit_s: float = ZEITLIMIT_S) -> tuple[int, bytes]:
    """Der einzige echte Netzgriff - Tests ersetzen genau diese Funktion."""
    req = urllib.request.Request(url, data=daten, method=methode, headers=kopf)
    try:
        with urllib.request.urlopen(req, timeout=zeitlimit_s) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def _ist_zeitlimit(e: BaseException) -> bool:
    """Lief die Frist des Clients ab (statt dass der Dienst nein sagte)?"""
    return isinstance(e, TimeoutError) or (
        isinstance(e, urllib.error.URLError) and isinstance(getattr(e, "reason", None), TimeoutError))


def anfrage(methode: str, pfad: str, daten: dict | None = None, zeitlimit_s: float = ZEITLIMIT_S) -> dict:
    """Ruft den Musik-Dienst. Gibt dessen JSON zurueck (sonst {"ok": False, "fehler"}); jeder Status
    ausserhalb 2xx und jeder Netzfehler wird zu einem Fehler. zeitlimit_s: Frist dieses einen Aufrufs."""
    kopf = {"Content-Type": "application/json"}
    roh = json.dumps(daten).encode("utf-8") if daten is not None else (b"{}" if methode == "POST" else None)
    try:
        status, rumpf = _roh(methode, _basis() + pfad, roh, kopf, zeitlimit_s=zeitlimit_s)
    except Exception as e:  # noqa: BLE001 - fail-soft
        fehler = _fehler(f"Musik-Dienst nicht erreichbar: {type(e).__name__}: {e}")
        if _ist_zeitlimit(e):
            fehler["zeitlimit"] = True    # der Dienst kann trotzdem weiterarbeiten
        return fehler
    text = rumpf.decode("utf-8", "replace")
    if not 200 <= status < 300:
        return _fehler(f"Musik-Dienst HTTP {status}: {text}")
    try:
        antwort = json.loads(text) if text.strip() else {}
    except ValueError:
        return _fehler("Musik-Dienst-Antwort ist kein JSON")
    if not isinstance(antwort, dict):
        return _fehler("Musik-Dienst-Antwort ist kein Objekt")
    if antwort.get("ok") is False:
        return _fehler(antwort.get("fehler") or "Musik-Dienst meldet einen Fehler")
    return antwort


def bestellen(timeline_id: str, wunsch: str) -> dict:
    """Videomusik fuer eine Laura-Zeitleiste bestellen (ohne Gesang). {"ok", "auftrag"}."""
    if not isinstance(timeline_id, str) or not _ID.fullmatch(timeline_id):
        return _fehler("timeline_id ungueltig")
    if not isinstance(wunsch, str) or not wunsch.strip():
        return _fehler("wunsch fehlt")
    r = anfrage("POST", "/auftraege/video", {"wunsch": wunsch.strip(), "laura_timeline_id": timeline_id,
                                              "gesang": False, "kanal": KANAL})
    if not r.get("ok"):
        return r
    if not isinstance(r.get("auftrag"), str) or not _ID.fullmatch(r["auftrag"]):
        return _fehler("Musik-Dienst lieferte keine Auftrags-ID")
    return {"ok": True, "auftrag": r["auftrag"]}


def warten(auftrag: str, max_s: float = 600, takt_s: float = 5.0, schlafen=None, uhr=None) -> dict:
    """Warten, bis die Musik da ist (fertig oder fertig_mit_befund). {"ok": True, "zustand"} sonst Fehler.
    Jede Abfrage hat hoechstens POLL_ZEITLIMIT_S und nie mehr als die Restzeit; bis zu MAX_POLL_FEHLER
    Abfragen in Folge duerfen scheitern (ein Aussetzer beendet das Warten nicht).
    Gibt das Warten selbst auf - Zeit um, oder die Musik wartet seit SPEICHER_AUFGEBEN_S auf
    Arbeitsspeicher -, traegt der Fehler "aufgegeben": True; dann sollte der Aufrufer die Musik abbrechen."""
    if not isinstance(auftrag, str) or not _ID.fullmatch(auftrag):
        return _fehler("musik_auftrag ungueltig")
    schlafen = schlafen or time.sleep
    uhr = uhr or time.monotonic
    ende = uhr() + max_s
    fehlversuche = 0
    speicher_seit: float | None = None
    while True:
        rest = ende - uhr()
        r = anfrage("GET", f"/auftraege/{auftrag}", zeitlimit_s=max(1.0, min(POLL_ZEITLIMIT_S, rest)))
        if not r.get("ok"):
            fehlversuche += 1
            if fehlversuche > MAX_POLL_FEHLER or uhr() >= ende:
                return r
            schlafen(takt_s)
            continue
        fehlversuche = 0
        zustand = r.get("zustand")
        if zustand in FERTIG:
            return {"ok": True, "zustand": zustand}
        if zustand in ENDE_OHNE_MUSIK:
            return _fehler(f"Musik {zustand}: {r.get('grund') or 'ohne Grund'}")
        grund = str(r.get("grund") or "")
        if zustand == "wartet" and SPEICHER_MARKE in grund:
            speicher_seit = uhr() if speicher_seit is None else speicher_seit
            if uhr() - speicher_seit >= SPEICHER_AUFGEBEN_S:
                return {**_fehler(f"Musik wartet auf Arbeitsspeicher: {grund}"), "aufgegeben": True}
        else:
            speicher_seit = None
        if uhr() >= ende:
            return {**_fehler(f"Musik nach {int(max_s)} s noch nicht fertig (Zustand {zustand})"),
                    "aufgegeben": True}
        schlafen(takt_s)


def abbrechen(auftrag: str) -> dict:
    """Den Musik-Auftrag abbrechen (POST /auftraege/{id}/abbrechen), z. B. wenn das Reel nicht mehr auf
    ihn wartet. Kurze Frist (POLL_ZEITLIMIT_S); fail-soft wie alles hier."""
    if not isinstance(auftrag, str) or not _ID.fullmatch(auftrag):
        return _fehler("musik_auftrag ungueltig")
    return anfrage("POST", f"/auftraege/{auftrag}/abbrechen", {}, zeitlimit_s=POLL_ZEITLIMIT_S)


def einlegen(auftrag: str, timeline_id: str, gain_percent: int, zeitlimit_s: float = ZEITLIMIT_S) -> dict:
    """Die fertige Musik in die Laura-Zeitleiste legen. {"ok", "clip_id", "asset_id"}."""
    if not isinstance(auftrag, str) or not _ID.fullmatch(auftrag):
        return _fehler("musik_auftrag ungueltig")
    if not isinstance(timeline_id, str) or not _ID.fullmatch(timeline_id):
        return _fehler("timeline_id ungueltig")
    return anfrage("POST", f"/auftraege/{auftrag}/in-laura",
                   {"timeline_id": timeline_id, "gain_percent": gain_percent}, zeitlimit_s=zeitlimit_s)
