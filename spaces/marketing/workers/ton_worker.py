"""Ton-Arbeiter am PC (Podcast, 17.10.2026). Holt Auftraege von der Marketing-API der VM (Tailnet,
X-Bild-Key wie der Bild-Arbeiter), laesst jeden Abschnitt vom lokalen Sprech-Dienst sprechen
(claw/ton_dienst.py, Chatterbox Multilingual, 127.0.0.1:8899), fuegt die Teile mit ffmpeg zu einer MP3
zusammen und liefert sie ab. Keine Modelle auf der VM, kein API-Budget. Gestartet von
marketing-dienste-starten.ps1; Gesundheits-Port 8135.

Stimmen: hat ein Sprecher eine Stimmprobe <name>.wav in TON_STIMMEN_ORDNER (name = Sprecher klein,
ohne Umlaute), spricht er damit; sonst die eingebaute Stimme des Modells."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import unicodedata
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from spaces.marketing.workers.bild_worker import REPO_ROOT, ApiFehler, _grund, tls_kontext

PORT = 8135
TAKT_S = 20
DIENST = os.environ.get("TON_DIENST_URL", "http://127.0.0.1:8899")
PAUSE_S = 0.45                # Pause zwischen Abschnitten
SPRECHEN_ZEITLIMIT_S = 300    # ein Abschnitt; der erste laedt das Modell
STAND = {"letzter_lauf": None, "letztes_ergebnis": None}


def umgebung_laden() -> None:
    """MARKETING_BILD_URL/KEY und TON_STIMMEN_ORDNER aus Vibemind_V1/.env, wenn nicht gesetzt."""
    datei = REPO_ROOT / ".env"
    fehlend = [k for k in ("MARKETING_BILD_URL", "MARKETING_BILD_KEY", "TON_STIMMEN_ORDNER") if not os.environ.get(k)]
    if not fehlend or not datei.exists():
        return
    for zeile in datei.read_text(encoding="utf-8", errors="replace").splitlines():
        for k in fehlend:
            if zeile.strip().startswith(k + "="):
                os.environ[k] = zeile.split("=", 1)[1].strip().strip('"').strip("'")


class TonApi:
    def __init__(self, basis: str, schluessel: str):
        self.basis, self.schluessel = basis.rstrip("/"), schluessel
        self.tls = tls_kontext()

    def _post(self, pfad: str, daten=None, roh: bytes | None = None, typ="application/json"):
        koerper = roh if roh is not None else (json.dumps(daten).encode("utf-8") if daten is not None else b"")
        req = urllib.request.Request(self.basis + "/api/ton/arbeiter" + pfad, data=koerper, method="POST",
                                     headers={"Content-Type": typ})
        req.add_unredirected_header("X-Bild-Key", self.schluessel)
        try:
            with urllib.request.urlopen(req, timeout=120, context=self.tls) as r:
                return json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            raise ApiFehler(e.code, _grund(e)) from None

    def naechster(self):
        return self._post("/naechster").get("auftrag")

    def weiter(self, aid) -> bool:
        return bool(self._post(f"/{aid}/weiter").get("ok"))

    def datei(self, aid, mp3: bytes) -> str:
        return self._post(f"/{aid}/datei", roh=mp3, typ="audio/mpeg")["name"]

    def fertig(self, aid, datei: str, dauer_s: float) -> None:
        self._post(f"/{aid}/fertig", {"datei": datei, "dauer_s": round(dauer_s, 1)})

    def fehler(self, aid, befund: str) -> None:
        self._post(f"/{aid}/fertig", {"befund": befund[:500]})


def dienst_bereit(url: str = DIENST) -> bool:
    try:
        with urllib.request.urlopen(url + "/healthz", timeout=3) as r:
            return r.status == 200
    except (urllib.error.URLError, OSError):
        return False


def sprechen(text: str, sprache: str, stimme: str | None, url: str = DIENST) -> bytes:
    koerper = json.dumps({"text": text, "sprache": sprache, "stimme": stimme}).encode("utf-8")
    req = urllib.request.Request(url + "/sprechen", data=koerper, method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=SPRECHEN_ZEITLIMIT_S) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Sprech-Dienst {e.code}: {e.read()[:200].decode('utf-8', 'replace')}") from None


def stimme_fuer(sprecher: str) -> str | None:
    """Stimmprobe zum Sprechernamen, wenn hinterlegt - sonst None (eingebaute Stimme)."""
    for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("Ä", "Ae"), ("Ö", "Oe"), ("Ü", "Ue"), ("ß", "ss")):
        sprecher = sprecher.replace(a, b)
    name = unicodedata.normalize("NFKD", sprecher).encode("ascii", "ignore").decode().lower()
    name = re.sub(r"[^a-z0-9_-]+", "-", name).strip("-")[:40]
    ordner = os.environ.get("TON_STIMMEN_ORDNER", "").strip()
    return name if name and ordner and (Path(ordner) / f"{name}.wav").is_file() else None


def _wav_kopf(wav: bytes) -> tuple[int, int, int]:
    """(Abtastrate, Bytes je Frame, Datenlaenge) aus dem RIFF-Kopf - fuer jedes Format, auch 32-Bit-Float
    (Format 3), das torchaudio ohne Vorgabe schreibt und das wave-Modul nicht liest (gemessen 17.10.2026)."""
    if wav[:4] != b"RIFF" or wav[8:12] != b"WAVE":
        raise ValueError("keine WAV-Datei")
    pos, rate, block, laenge = 12, 0, 0, None
    while pos + 8 <= len(wav):
        kennung, groesse = wav[pos:pos + 4], int.from_bytes(wav[pos + 4:pos + 8], "little")
        if kennung == b"fmt ":
            rate = int.from_bytes(wav[pos + 12:pos + 16], "little")
            block = int.from_bytes(wav[pos + 20:pos + 22], "little")
        elif kennung == b"data":
            laenge = min(groesse, len(wav) - pos - 8)
            break
        pos += 8 + groesse + (groesse & 1)
    if not rate or not block or laenge is None:
        raise ValueError("WAV ohne fmt- oder data-Abschnitt")
    return rate, block, laenge


def _dauer(wav: bytes) -> float:
    rate, block, laenge = _wav_kopf(wav)
    return laenge / block / float(rate)


def zusammenfuegen(teile: list[bytes], pause_s: float = PAUSE_S, ffmpeg: str = "ffmpeg") -> tuple[bytes, float]:
    """WAV-Abschnitte mit kurzen Pausen zu einer MP3 (mono, 128 kbit/s). Gibt (mp3, dauer_s)."""
    if not teile:
        raise ValueError("keine Abschnitte")
    rate = _wav_kopf(teile[0])[0]
    dauer = sum(_dauer(t) for t in teile) + pause_s * (len(teile) - 1)
    with tempfile.TemporaryDirectory(prefix="ton_") as tmp:
        liste = []
        for i, t in enumerate(teile):
            p = Path(tmp) / f"teil-{i:03d}.wav"
            p.write_bytes(t)
            liste.append(p)
        eingaben, filter_teile = [], []
        for i, p in enumerate(liste):
            eingaben += ["-i", str(p)]
            filter_teile.append(f"[{i}:a]aresample={rate},aformat=channel_layouts=mono[a{i}]")
        ketten = []
        for i in range(len(liste)):
            ketten.append(f"[a{i}]")
            if i < len(liste) - 1:
                filter_teile.append(f"anullsrc=r={rate}:cl=mono,atrim=duration={pause_s}[p{i}]")
                ketten.append(f"[p{i}]")
        filter_teile.append("".join(ketten) + f"concat=n={len(ketten)}:v=0:a=1[aus]")
        ziel = Path(tmp) / "folge.mp3"
        befehl = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", *eingaben,
                  "-filter_complex", ";".join(filter_teile), "-map", "[aus]",
                  "-ac", "1", "-codec:a", "libmp3lame", "-b:a", "128k", str(ziel)]
        lauf = subprocess.run(befehl, capture_output=True, text=True, timeout=300)
        if lauf.returncode != 0 or not ziel.is_file():
            raise RuntimeError(f"ffmpeg: {lauf.stderr.strip()[:300]}")
        return ziel.read_bytes(), dauer


def ein_durchlauf(api, sprich=sprechen, bereit=dienst_bereit, ffmpeg: str = "ffmpeg") -> str:
    if not bereit():
        return "Sprech-Dienst nicht bereit"
    a = api.naechster()
    if not a:
        return "nichts zu tun"
    aid, sprache = a["id"], a.get("sprache") or "de"
    try:
        teile = []
        for i, abschnitt in enumerate(a.get("skript") or []):
            teile.append(sprich(abschnitt["text"], sprache, stimme_fuer(str(abschnitt.get("sprecher") or ""))))
            if i % 5 == 4:
                api.weiter(aid)
        mp3, dauer = zusammenfuegen(teile, ffmpeg=ffmpeg)
        name = api.datei(aid, mp3)
        api.fertig(aid, name, dauer)
        return f"fertig: {name} ({dauer:.0f} s)"
    except ApiFehler:
        raise
    except Exception as e:  # noqa: BLE001 - jeder andere Fehler wird zum Befund des Auftrags
        api.fehler(aid, f"{type(e).__name__}: {e}")
        return f"fehler: {type(e).__name__}"


class _Gesundheit(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        rumpf = json.dumps({"ok": True, **STAND, "dienst": dienst_bereit()}).encode("utf-8")
        self.send_response(200 if self.path == "/health" else 404)
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
    if not shutil.which("ffmpeg"):
        raise SystemExit("ffmpeg fehlt im PATH")
    api = TonApi(basis, schluessel)
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
