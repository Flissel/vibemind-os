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
import ssl
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
START_SCHONFRIST_S = 120      # nach Arbeiterstart: Starter-Skript startet die Dienste gerade selbst
START_ABSTAND_S = 600         # hoechstens ein Dienststart je 10 Minuten
START = {"arbeiter": None, "dienste": None}   # time.monotonic()-Zeitpunkte


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


def tls_kontext() -> ssl.SSLContext:
    """TLS gegen die Tailnet-Adresse der VM. Gemessen 30.09.2026: deren Let's-
    Encrypt-Kette (YE1 -> Root YE -> ISRG Root X2) endet fuer das Windows-
    Python ueber die abgelaufene X2/X1-Querzertifizierung ("certificate has
    expired"); Windows selbst laedt X2 bei Bedarf nach, OpenSSL nicht. Das
    certifi-Buendel enthaelt X2 - also das nehmen, wenn es da ist."""
    try:
        import certifi
    except ImportError:
        return ssl.create_default_context()
    return ssl.create_default_context(cafile=certifi.where())


class ArbeiterApi:
    def __init__(self, basis: str, schluessel: str):
        self.basis, self.schluessel = basis.rstrip("/"), schluessel
        self.tls = tls_kontext()

    def _post(self, pfad: str, daten=None, roh: bytes | None = None, typ="application/json"):
        koerper = roh if roh is not None else (json.dumps(daten).encode("utf-8") if daten is not None else b"")
        req = urllib.request.Request(self.basis + "/api/bilder/arbeiter" + pfad, data=koerper, method="POST",
                                     headers={"Content-Type": typ})
        req.add_unredirected_header("X-Bild-Key", self.schluessel)
        try:
            with urllib.request.urlopen(req, timeout=60, context=self.tls) as r:
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
    """Startet ComfyUI/Ollama ueber das Starter-Skript. Kein Warten hier: der
    naechste Takt prueft erneut (ComfyUI braucht ~60 s bis /system_stats)."""
    subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(STARTER)],
                   capture_output=True, timeout=240)


def _dienste_bereit(comfy, prompt, starten, uhr) -> bool:
    """ComfyUI und Ollama pruefen, BEVOR ein Auftrag genommen wird. Laufen sie
    nicht, hoechstens alle 10 min starten und nie in den ersten 120 s nach dem
    Arbeiterstart (dann startet marketing-dienste-starten.ps1 sie gerade selbst)."""
    jetzt = uhr()
    if START["arbeiter"] is None:
        START["arbeiter"] = jetzt
    if comfy.laeuft() and prompt.laeuft():
        return True
    if (jetzt - START["arbeiter"] >= START_SCHONFRIST_S
            and (START["dienste"] is None or jetzt - START["dienste"] >= START_ABSTAND_S)):
        START["dienste"] = jetzt
        starten()
    return False


def _erzeugen(api, aid, auftrag, ziele, comfy, prompt):
    """Erzeugt und liefert je Platz ab. Rueckgabe (ergebnis, befunde) oder
    "verworfen", wenn der Auftrag dem Arbeiter nicht mehr gehoert."""
    # Erst alle Prompts (Ollama, keep_alive 0), dann alle Bilder am Stueck:
    # ComfyUI laedt die Modelle (~11 GB, gemessen 30.09. ~118 s von der HDD
    # E:, Rechnen nur ~6 s) so EINMAL je Auftrag. Nach jedem Bild freigeben
    # nur, wenn danach das Sehmodell der Selbstpruefung den Grafikspeicher braucht.
    je_bild_freigeben = os.environ.get("BILD_SELBSTPRUEFUNG", "") == "1"
    texte = {platz.id: prompt.prompt_schreiben(platz.als_dict(), str(auftrag.get("titel") or ""),
                                               str(auftrag.get("hinweis") or ""))
             for platz in ziele}
    try:
        return _bilder(api, aid, ziele, texte, comfy, prompt, je_bild_freigeben)
    finally:
        comfy.freigeben()


def _bilder(api, aid, ziele, texte, comfy, prompt, je_bild_freigeben):
    ergebnis, befunde = {}, []
    for platz in ziele:
        text = texte[platz.id]
        letzter = ""
        for _ in range(VERSUCHE_JE_PLATZ):
            if not api.weiter(aid):   # Vergabe je Versuch verlaengern (Kaltstart ~300 s, Vergabe 10 min)
                return "verworfen"
            try:
                png = comfy.erzeugen(text, platz.erzeug_breite, platz.erzeug_hoehe, random.randrange(2**31),
                                     zeitlimit_s=ERZEUGUNG_ZEITLIMIT_S)
            finally:
                if je_bild_freigeben:
                    comfy.freigeben()
            ok, letzter = prompt.pruefen(png, text)
            if not ok:
                continue
            jpeg = verkleinern(png, platz.erzeug_breite, platz.erzeug_hoehe)
            if not api.weiter(aid):   # 540 s Erzeugung + 180 s Pruefung koennen die Vergabe ueberziehen
                return "verworfen"
            try:
                ergebnis[platz.id] = api.bild(aid, platz.id, jpeg)
            except ApiFehler as e:
                if e.code != 422:
                    raise
                if "in Arbeit" in e.grund:
                    return "verworfen"
                befunde.append(f"{platz.id}: {e.grund}")
                break
            if letzter:
                befunde.append(f"{platz.id}: {letzter}")
            break
        else:
            befunde.append(f"{platz.id}: {letzter}")
    return ergebnis, befunde


def ein_durchlauf(api, comfy=bild_comfy, prompt=bild_prompt, starten=dienste_starten,
                  uhr=time.monotonic) -> str:
    if not _dienste_bereit(comfy, prompt, starten, uhr):
        return "wartet"
    auftrag = api.naechster()
    if not auftrag:
        return "leer"
    aid = str(auftrag["id"])
    # Ab hier gehoert der Auftrag dem Arbeiter: jede Stoerung gibt ihn zurueck,
    # statt die Vergabe bis zum Ablauf zu blockieren.
    try:
        ziele = [p for p in bildplaetze.finde(auftrag.get("bloecke") or {})
                 if (auftrag.get("platz") in (None, p.id)) and (not auftrag.get("nur_leere") or p.leer)]
        if not ziele:
            api.zurueck(aid, "Keine passenden Bildplaetze", True)
            return "zurueck"
        erg = _erzeugen(api, aid, auftrag, ziele, comfy, prompt)
        if erg == "verworfen":
            return "verworfen"
        ergebnis, befunde = erg
        if not ergebnis:
            api.zurueck(aid, "; ".join(befunde) or "Kein Bild bestanden", True)
            return "zurueck"
        try:
            api.fertig(aid, ergebnis, "; ".join(befunde))
        except ApiFehler as e:
            if e.code == 422 and "in Arbeit" in e.grund:
                return "verworfen"
            raise
        return "fertig"
    except (bild_comfy.ComfyFehler, ApiFehler, ValueError, OSError, TimeoutError, KeyError, TypeError) as e:
        # ValueError deckt JSONDecodeError, OSError deckt URLError und PIL.UnidentifiedImageError.
        api.zurueck(aid, f"Erzeugung unterbrochen: {type(e).__name__}: {e}", False)
        return "zurueck"


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
    START["arbeiter"] = time.monotonic()
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
