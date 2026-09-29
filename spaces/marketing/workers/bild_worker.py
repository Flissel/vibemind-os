"""Bild-Arbeiter am PC (sales-claw Spec 2026-09-29-newsletter-bilder-und-
gestaltung-design.md §7.2). Holt Auftraege von der Marketing-API der VM
(Tailnet, X-Bild-Key), erzeugt mit ComfyUI, prueft mit Ollama, liefert JPEGs ab.
Die VM schreibt die Fassung. Gestartet von marketing-dienste-starten.ps1;
Gesundheits-Port 8133."""
from __future__ import annotations

import io
import json
import os
import random
import subprocess
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from spaces.marketing.claw import bild_comfy, bild_prompt, bildplaetze

PORT = 8133
TAKT_S = 20
VERSUCHE_JE_PLATZ = 3
ERZEUGUNG_ZEITLIMIT_S = 540
JPEG_ZIEL = 250 * 1024
_HIER = Path(__file__).resolve()
REPO_ROOT = next((p for p in _HIER.parents if (p / "vibemind-os").is_dir()), _HIER.parents[3])
STARTER = _HIER.parents[1] / "claw" / "scripts" / "marketing-dienste-starten.ps1"
STAND = {"letzter_lauf": None, "letztes_ergebnis": None}


class ApiFehler(Exception):
    def __init__(self, code: int, grund: str):
        super().__init__(f"{code}: {grund}")
        self.code, self.grund = code, grund


def umgebung_laden() -> None:
    """MARKETING_BILD_URL/KEY aus Vibemind_V1/.env, wenn nicht gesetzt (Muster claw/server.py)."""
    datei = REPO_ROOT / ".env"
    fehlend = [k for k in ("MARKETING_BILD_URL", "MARKETING_BILD_KEY") if not os.environ.get(k)]
    if not fehlend or not datei.exists():
        return
    for zeile in datei.read_text(encoding="utf-8", errors="replace").splitlines():
        for k in fehlend:
            if zeile.strip().startswith(k + "="):
                os.environ[k] = zeile.split("=", 1)[1].strip().strip('"').strip("'")


class ArbeiterApi:
    def __init__(self, basis: str, schluessel: str):
        self.basis, self.schluessel = basis.rstrip("/"), schluessel

    def _post(self, pfad: str, daten=None, roh: bytes | None = None, typ="application/json"):
        koerper = roh if roh is not None else (json.dumps(daten).encode("utf-8") if daten is not None else b"")
        req = urllib.request.Request(self.basis + "/api/bilder/arbeiter" + pfad, data=koerper, method="POST",
                                     headers={"Content-Type": typ})
        req.add_unredirected_header("X-Bild-Key", self.schluessel)
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            try:
                grund = json.loads(e.read() or b"{}").get("detail", "")
            except (ValueError, OSError, AttributeError):
                grund = ""
            raise ApiFehler(e.code, str(grund)[:300]) from None

    def naechster(self):
        return self._post("/naechster").get("auftrag")

    def weiter(self, aid):
        return bool(self._post(f"/{aid}/weiter").get("ok"))

    def bild(self, aid, platz, jpeg: bytes) -> str:
        return self._post(f"/{aid}/bild?platz={urllib.request.quote(platz)}", roh=jpeg, typ="image/jpeg")["name"]

    def fertig(self, aid, ergebnis: dict, befund: str) -> dict:
        return self._post(f"/{aid}/fertig", {"ergebnis": ergebnis, "befund": befund})

    def zurueck(self, aid, befund: str, endgueltig: bool) -> str:
        return self._post(f"/{aid}/zurueck", {"befund": befund[:500], "endgueltig": endgueltig}).get("status", "")


def verkleinern(png: bytes, breite: int, hoehe: int) -> bytes:
    from PIL import Image
    with Image.open(io.BytesIO(png)) as bild:
        bild = bild.convert("RGB").resize((breite, hoehe), Image.LANCZOS)
        for qualitaet in (85, 78, 70, 62, 55):
            b = io.BytesIO()
            bild.save(b, "JPEG", quality=qualitaet, optimize=True, progressive=True)
            if b.tell() <= JPEG_ZIEL:
                break
        return b.getvalue()


def dienste_starten() -> None:
    subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(STARTER)],
                   capture_output=True, timeout=240)
    time.sleep(60)   # ComfyUI braucht ~60 s bis /system_stats antwortet


def ein_durchlauf(api, comfy=bild_comfy, prompt=bild_prompt, starten=dienste_starten) -> str:
    auftrag = api.naechster()
    if not auftrag:
        return "leer"
    aid = str(auftrag["id"])
    if not (comfy.laeuft() and prompt.laeuft()):
        starten()
        if not (comfy.laeuft() and prompt.laeuft()):
            api.zurueck(aid, "ComfyUI oder Ollama laeuft nicht", False)
            return "zurueck"
    ziele = [p for p in bildplaetze.finde(auftrag.get("bloecke") or {})
             if (auftrag.get("platz") in (None, p.id)) and (not auftrag.get("nur_leere") or p.leer)]
    if not ziele:
        api.zurueck(aid, "Keine passenden Bildplaetze", True)
        return "zurueck"
    ergebnis, befunde = {}, []
    try:
        for platz in ziele:
            text = prompt.prompt_schreiben(platz.als_dict(), str(auftrag.get("titel") or ""),
                                           str(auftrag.get("hinweis") or ""))
            letzter = ""
            for _ in range(VERSUCHE_JE_PLATZ):
                api.weiter(aid)   # Vergabe je Versuch verlaengern (Kaltstart ~300 s, Vergabe 10 min)
                png = comfy.erzeugen(text, platz.erzeug_breite, platz.erzeug_hoehe, random.randrange(2**31),
                                     zeitlimit_s=ERZEUGUNG_ZEITLIMIT_S)
                comfy.freigeben()
                ok, letzter = prompt.pruefen(png, text)
                if ok:
                    ergebnis[platz.id] = api.bild(aid, platz.id, verkleinern(png, platz.erzeug_breite, platz.erzeug_hoehe))
                    if letzter:
                        befunde.append(f"{platz.id}: {letzter}")
                    break
            else:
                befunde.append(f"{platz.id}: {letzter}")
    except (bild_comfy.ComfyFehler, OSError, TimeoutError) as e:
        api.zurueck(aid, f"Erzeugung unterbrochen: {e}", False)
        return "zurueck"
    if not ergebnis:
        api.zurueck(aid, "; ".join(befunde) or "Kein Bild bestanden", True)
        return "zurueck"
    try:
        api.fertig(aid, ergebnis, "; ".join(befunde))
    except ApiFehler as e:
        if e.code == 422 and "nicht in Arbeit" in e.grund:
            return "verworfen"
        raise
    return "fertig"


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
    api = ArbeiterApi(basis, schluessel)
    threading.Thread(target=HTTPServer(("127.0.0.1", PORT), _Gesundheit).serve_forever, daemon=True).start()
    while True:
        try:
            STAND["letztes_ergebnis"] = ein_durchlauf(api)
        except Exception as e:  # noqa: BLE001 - ein Fehler darf die Schleife nicht toeten
            STAND["letztes_ergebnis"] = f"fehler: {type(e).__name__}: {e}"[:300]
        STAND["letzter_lauf"] = time.strftime("%Y-%m-%d %H:%M:%S")
        print(STAND, flush=True)
        time.sleep(TAKT_S)


if __name__ == "__main__":
    main()
