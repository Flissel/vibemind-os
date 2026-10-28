"""Higgsfield fuer Reels (Betreiber 28.10.2026: „Reels sollen Higgsfield bekommen (laura) (mcp)").

VORBEREITET, ABER AUS: ohne HIGGSFIELD_API_KEY_ID + HIGGSFIELD_API_KEY_SECRET tut nichts hier etwas.
Gemessen an docs.higgsfield.ai (28.10.2026):
  * Basis https://api.higgsfield.ai, Kopf `Authorization: Key <id>:<secret>`.
  * Ein Auftrag ist ein POST auf den Modellpfad; die Antwort traegt request_id und status_url
    („die URLs aus der Antwort benutzen, nicht selbst bauen").
  * Status: queued, in_progress (laufend), completed, failed, nsfw, canceled (Ende).
  * Ergebnis eines Videomodells unter `video.url`.
Der Modellpfad ist je Modell verschieden (Seedance, Wan, Kling ...) und kommt deshalb aus
HIGGSFIELD_MODELL_PFAD; die Felder (image_url, prompt, duration ...) reicht der Aufrufer durch.

Einbau in Laura/Reels: `bild_zu_video(bild_url, prompt)` liefert die Video-URL; der Reel-Arbeiter laedt
sie wie jedes andere Stueck. Laura bekommt es als MCP-Werkzeug, sobald Schluessel und Budget stehen.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

BASIS = "https://api.higgsfield.ai"
ENDE = {"completed", "failed", "nsfw", "canceled"}
ZEIT_S = 60
WARTEN_MAX_S = 600


class HiggsfieldFehler(Exception):
    pass


def bereit() -> bool:
    return bool(os.environ.get("HIGGSFIELD_API_KEY_ID", "").strip()
                and os.environ.get("HIGGSFIELD_API_KEY_SECRET", "").strip()
                and os.environ.get("HIGGSFIELD_MODELL_PFAD", "").strip())


def _kopf() -> dict:
    kid = os.environ.get("HIGGSFIELD_API_KEY_ID", "").strip()
    geheim = os.environ.get("HIGGSFIELD_API_KEY_SECRET", "").strip()
    if not (kid and geheim):
        raise HiggsfieldFehler("Higgsfield nicht eingerichtet (HIGGSFIELD_API_KEY_ID/_SECRET fehlen)")
    return {"Authorization": f"Key {kid}:{geheim}", "Content-Type": "application/json",
            "Accept": "application/json"}


def _anfrage(url: str, daten: dict | None, oeffnen) -> dict:
    if not url.startswith(BASIS + "/"):
        raise HiggsfieldFehler("fremde Adresse abgelehnt")      # nie den Schluessel woanders hinschicken
    rumpf = json.dumps(daten).encode("utf-8") if daten is not None else None
    req = urllib.request.Request(url, data=rumpf, method="POST" if rumpf else "GET", headers=_kopf())
    try:
        with (oeffnen or urllib.request.urlopen)(req, timeout=ZEIT_S) as antwort:
            return json.loads(antwort.read() or b"{}")
    except urllib.error.HTTPError as e:
        raise HiggsfieldFehler(f"Higgsfield HTTP {e.code}") from None
    except (OSError, ValueError) as e:
        raise HiggsfieldFehler(f"Higgsfield nicht erreichbar ({type(e).__name__})") from None


def auftrag(felder: dict, oeffnen=None) -> dict:
    pfad = os.environ.get("HIGGSFIELD_MODELL_PFAD", "").strip().strip("/")
    if not pfad:
        raise HiggsfieldFehler("HIGGSFIELD_MODELL_PFAD fehlt (Modellseite in docs.higgsfield.ai)")
    antwort = _anfrage(f"{BASIS}/{pfad}", felder, oeffnen)
    if not antwort.get("status_url"):
        raise HiggsfieldFehler("Antwort ohne status_url")
    return antwort


def warten(status_url: str, oeffnen=None, schlafen=time.sleep, takt_s: float = 5) -> dict:
    begonnen = time.monotonic()
    while True:
        stand = _anfrage(status_url, None, oeffnen)
        if stand.get("status") in ENDE:
            return stand
        if time.monotonic() - begonnen > WARTEN_MAX_S:
            raise HiggsfieldFehler("Higgsfield brauchte zu lange")
        schlafen(takt_s)


def bild_zu_video(bild_url: str, prompt: str, mehr: dict | None = None, oeffnen=None,
                  schlafen=time.sleep) -> str:
    """Ein Standbild zu einem kurzen Clip -> URL des Videos. Wirft HiggsfieldFehler."""
    felder = {"image_url": bild_url, "prompt": prompt, **(mehr or {})}
    stand = warten(auftrag(felder, oeffnen)["status_url"], oeffnen, schlafen)
    if stand.get("status") != "completed":
        raise HiggsfieldFehler(f"Higgsfield: {stand.get('status')} {stand.get('error') or ''}".strip())
    url = (stand.get("video") or {}).get("url")
    if not url:
        raise HiggsfieldFehler("fertig, aber ohne video.url")
    return url
