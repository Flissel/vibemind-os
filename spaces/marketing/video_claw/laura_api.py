"""Laura-Client mit Schreibrechten fuer den Video-Claw. Fail-soft wie claw/laura.py: keine Funktion
wirft, Rueckgabe {"ok": ...}, in keinem Fehlertext steht der Token."""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

FEHLER_MAXLAENGE = 300
ZEITLIMIT_S = 30


def _basis() -> str:
    return os.environ.get("LAURA_API_URL", "http://127.0.0.1:8765").strip().rstrip("/")


def _ohne_token(text: str) -> str:
    wert = os.environ.get("LAURA_TOKEN", "")
    return text.replace(wert, "<token>") if wert else text


def _roh(methode: str, url: str, daten: bytes | None, kopf: dict) -> tuple[int, bytes]:
    """Der einzige echte Netzgriff - Tests ersetzen genau diese Funktion."""
    req = urllib.request.Request(url, data=daten, method=methode, headers=kopf)
    try:
        with urllib.request.urlopen(req, timeout=ZEITLIMIT_S) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def anfrage(methode: str, pfad: str, daten: dict | None = None) -> dict:
    kopf = {"Content-Type": "application/json"}
    token = os.environ.get("LAURA_TOKEN", "").strip()
    if token:
        kopf["X-Laura-Token"] = token
    roh = json.dumps(daten).encode("utf-8") if daten is not None else (b"{}" if methode == "POST" else None)
    try:
        status, rumpf = _roh(methode, _basis() + pfad, roh, kopf)
    except Exception as e:  # noqa: BLE001 - fail-soft
        return {"ok": False, "fehler": _ohne_token(f"Laura nicht erreichbar: {type(e).__name__}: {e}")[:FEHLER_MAXLAENGE]}
    text = rumpf.decode("utf-8", "replace")
    if not 200 <= status < 300:
        return {"ok": False, "fehler": _ohne_token(f"Laura HTTP {status}: {text[:FEHLER_MAXLAENGE]}")}
    try:
        return {"ok": True, "daten": json.loads(text) if text.strip() else {}}
    except ValueError:
        return {"ok": False, "fehler": "Laura-Antwort ist kein JSON"}


def job_warten(job_id: str, max_s: float = 300, takt_s: float = 3.0, schlafen=time.sleep,
               uhr=time.monotonic) -> dict:
    """Auf einen Laura-Job warten. {"ok": True, "ergebnis": {...}} bei succeeded."""
    ende = uhr() + max_s
    while True:
        r = anfrage("GET", f"/jobs/{job_id}")
        if not r["ok"]:
            return r
        job = r["daten"] or {}
        status = job.get("status")
        if status == "succeeded":
            ergebnis = job.get("result_json") or {}
            if isinstance(ergebnis, str):
                try:
                    ergebnis = json.loads(ergebnis)
                except ValueError:
                    ergebnis = {}
            return {"ok": True, "ergebnis": ergebnis}
        if status in ("failed", "cancelled"):
            grund = str(job.get("error_json") or "")[:FEHLER_MAXLAENGE]
            return {"ok": False, "fehler": f"Laura-Job {status}: {grund}"}
        if uhr() >= ende:
            return {"ok": False, "fehler": f"Laura-Job nach {int(max_s)} s noch nicht fertig (Status {status})"}
        schlafen(takt_s)


def asset_warten(asset_id: str, max_s: float = 180, takt_s: float = 2.0, schlafen=time.sleep,
                 uhr=time.monotonic) -> dict:
    """Warten, bis ein importiertes Asset geprobt ist (sha256 gesetzt). {"ok", "asset"}."""
    ende = uhr() + max_s
    while True:
        r = anfrage("GET", f"/assets/{asset_id}")
        if not r["ok"]:
            return r
        asset = r["daten"] or {}
        if asset.get("sha256"):
            return {"ok": True, "asset": asset}
        s = anfrage("GET", f"/assets/{asset_id}/import-status")
        if s["ok"] and (s["daten"] or {}).get("phase") in ("error", "cancelled"):
            return {"ok": False, "fehler": f"Import fehlgeschlagen: {(s['daten'] or {}).get('error') or ''}"[:FEHLER_MAXLAENGE]}
        if uhr() >= ende:
            return {"ok": False, "fehler": f"Import nach {int(max_s)} s nicht fertig"}
        schlafen(takt_s)
