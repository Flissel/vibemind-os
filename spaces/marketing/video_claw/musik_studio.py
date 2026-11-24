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
KANAL = "video-claw"
FERTIG = ("fertig", "fertig_mit_befund")
ENDE_OHNE_MUSIK = ("gescheitert", "abgebrochen")
_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def _basis() -> str:
    return os.environ.get("MUSIK_STUDIO_URL", "http://127.0.0.1:8148").strip().rstrip("/")


def _fehler(text: str) -> dict:
    return {"ok": False, "fehler": str(text)[:FEHLER_MAXLAENGE]}


def _roh(methode: str, url: str, daten: bytes | None, kopf: dict) -> tuple[int, bytes]:
    """Der einzige echte Netzgriff - Tests ersetzen genau diese Funktion."""
    req = urllib.request.Request(url, data=daten, method=methode, headers=kopf)
    try:
        with urllib.request.urlopen(req, timeout=ZEITLIMIT_S) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def anfrage(methode: str, pfad: str, daten: dict | None = None) -> dict:
    """Ruft den Musik-Dienst. Gibt dessen JSON zurueck (sonst {"ok": False, "fehler"}); jeder Status
    ausserhalb 2xx und jeder Netzfehler wird zu einem Fehler."""
    kopf = {"Content-Type": "application/json"}
    roh = json.dumps(daten).encode("utf-8") if daten is not None else (b"{}" if methode == "POST" else None)
    try:
        status, rumpf = _roh(methode, _basis() + pfad, roh, kopf)
    except Exception as e:  # noqa: BLE001 - fail-soft
        return _fehler(f"Musik-Dienst nicht erreichbar: {type(e).__name__}: {e}")
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


def warten(auftrag: str, max_s: float = 600, takt_s: float = 5.0, schlafen=time.sleep,
           uhr=time.monotonic) -> dict:
    """Warten, bis die Musik da ist (fertig oder fertig_mit_befund). {"ok": True, "zustand"} sonst Fehler."""
    if not isinstance(auftrag, str) or not _ID.fullmatch(auftrag):
        return _fehler("musik_auftrag ungueltig")
    ende = uhr() + max_s
    while True:
        r = anfrage("GET", f"/auftraege/{auftrag}")
        if not r.get("ok"):
            return r
        zustand = r.get("zustand")
        if zustand in FERTIG:
            return {"ok": True, "zustand": zustand}
        if zustand in ENDE_OHNE_MUSIK:
            return _fehler(f"Musik {zustand}: {r.get('grund') or 'ohne Grund'}")
        if uhr() >= ende:
            return _fehler(f"Musik nach {int(max_s)} s noch nicht fertig (Zustand {zustand})")
        schlafen(takt_s)


def einlegen(auftrag: str, timeline_id: str, gain_percent: int) -> dict:
    """Die fertige Musik in die Laura-Zeitleiste legen. {"ok", "clip_id", "asset_id"}."""
    if not isinstance(auftrag, str) or not _ID.fullmatch(auftrag):
        return _fehler("musik_auftrag ungueltig")
    if not isinstance(timeline_id, str) or not _ID.fullmatch(timeline_id):
        return _fehler("timeline_id ungueltig")
    return anfrage("POST", f"/auftraege/{auftrag}/in-laura",
                   {"timeline_id": timeline_id, "gain_percent": gain_percent})
