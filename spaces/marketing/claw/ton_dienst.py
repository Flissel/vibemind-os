"""Sprech-Dienst fuer Podcasts (17.10.2026): Chatterbox Multilingual lokal am PC, nur 127.0.0.1.

Laeuft in der eigenen Chatterbox-Umgebung (Torch + CUDA), NICHT im gemeinsamen .venv:
    C:\\Users\\User\\ClaudeWork\\chatterbox-venv\\Scripts\\python.exe -m spaces.marketing.claw.ton_dienst

    GET  /healthz   -> 200 ok (laedt das Modell nicht)
    POST /sprechen  {"text": "...", "sprache": "de", "stimme": "anna"}  -> audio/wav

Warum nicht Lauras tts-sidecar: der braucht immer eine Stimmprobe, ist auf Video-Spuren (Frames, Slots)
zugeschnitten und nutzt das rein englische Chatterbox-Modell. Hier spricht das mehrsprachige Modell
(language_id), ohne Stimmprobe mit seiner eingebauten Stimme. Eigene Stimmen sind WAV-Dateien
<name>.wav in TON_STIMMEN_ORDNER (nur mit Einverstaendnis der Person - das ist Stimmen-Klonen).
Modellgewichte: HF_HOME (E:/huggingface). Keine Modelle auf der VM, kein API-Budget.
"""
from __future__ import annotations

import io
import json
import logging
import os
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

LOG = logging.getLogger("ton_dienst")
PORT = int(os.environ.get("TON_DIENST_PORT", "8899"))
TEXT_MAX = 1000
SPRACHEN = {"ar", "da", "de", "el", "en", "es", "fi", "fr", "he", "hi", "it", "ja", "ko", "ms", "nl", "no",
            "pl", "pt", "ru", "sv", "sw", "tr", "zh"}
_STIMME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")


class Anfragefehler(ValueError):
    pass


def stimmen_ordner() -> Path | None:
    roh = os.environ.get("TON_STIMMEN_ORDNER", "").strip()
    return Path(roh) if roh else None


def anfrage_pruefen(nutzlast) -> tuple[str, str, Path | None]:
    """(text, sprache, stimmprobe|None) - oder Anfragefehler. Ohne Torch pruefbar."""
    if not isinstance(nutzlast, dict):
        raise Anfragefehler("Rumpf muss ein JSON-Objekt sein")
    text = nutzlast.get("text")
    if not isinstance(text, str) or not text.strip() or len(text) > TEXT_MAX:
        raise Anfragefehler(f"text: 1 bis {TEXT_MAX} Zeichen")
    sprache = nutzlast.get("sprache", "de")
    if sprache not in SPRACHEN:
        raise Anfragefehler("sprache wird vom Modell nicht unterstuetzt")
    stimme = nutzlast.get("stimme")
    if stimme in (None, "", "standard"):
        return text.strip(), sprache, None
    if not isinstance(stimme, str) or not _STIMME.fullmatch(stimme):
        raise Anfragefehler("stimme: Kleinbuchstaben, Ziffern, - und _")
    ordner = stimmen_ordner()
    datei = (ordner / f"{stimme}.wav") if ordner else None
    if datei is None or not datei.is_file():
        raise Anfragefehler(f"Keine Stimmprobe fuer '{stimme}' hinterlegt")
    return text.strip(), sprache, datei


_modell = None
_modell_lock = threading.Lock()
_sprech_lock = threading.Lock()


def _modell_laden():
    global _modell
    with _modell_lock:
        if _modell is None:
            from chatterbox.mtl_tts import ChatterboxMultilingualTTS
            geraet = os.environ.get("TON_GERAET", "cuda")
            LOG.info("lade Chatterbox Multilingual auf %s", geraet)
            _modell = ChatterboxMultilingualTTS.from_pretrained(device=geraet)
        return _modell


def sprechen(text: str, sprache: str, probe: Path | None) -> bytes:
    import torchaudio
    modell = _modell_laden()
    with _sprech_lock:   # ein Modell, eine GPU: nacheinander
        kw = {"language_id": sprache}
        if probe is not None:
            kw["audio_prompt_path"] = str(probe)
        wav = modell.generate(text, **kw)
    puffer = io.BytesIO()
    # 16-Bit-PCM statt torchaudios Vorgabe 32-Bit-Float: kleiner, und jedes Werkzeug liest es.
    torchaudio.save(puffer, wav.cpu(), modell.sr, format="wav", encoding="PCM_S", bits_per_sample=16)
    return puffer.getvalue()


class Handler(BaseHTTPRequestHandler):
    server_version = "TonDienst/1.0"

    def log_message(self, fmt, *args):
        LOG.info("%s - %s", self.address_string(), fmt % args)

    def _antwort(self, status: int, rumpf: bytes, typ: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", typ)
        self.send_header("Content-Length", str(len(rumpf)))
        self.end_headers()
        self.wfile.write(rumpf)

    def do_GET(self):  # noqa: N802
        if self.path == "/healthz":
            self._antwort(200, b"ok", "text/plain")
        else:
            self._antwort(404, b"nicht gefunden", "text/plain")

    def do_POST(self):  # noqa: N802
        if self.path != "/sprechen":
            self._antwort(404, b"nicht gefunden", "text/plain")
            return
        try:
            laenge = min(int(self.headers.get("Content-Length") or 0), 64 * 1024)
            text, sprache, probe = anfrage_pruefen(json.loads(self.rfile.read(laenge) or b"{}"))
        except (Anfragefehler, ValueError) as e:
            self._antwort(422, str(e).encode("utf-8"), "text/plain; charset=utf-8")
            return
        try:
            self._antwort(200, sprechen(text, sprache, probe), "audio/wav")
        except Exception as e:  # noqa: BLE001 - Modell/GPU-Fehler als 500 mit Grund
            LOG.exception("Sprechen fehlgeschlagen")
            self._antwort(500, f"{type(e).__name__}: {e}"[:300].encode("utf-8"), "text/plain; charset=utf-8")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    LOG.info("Sprech-Dienst auf 127.0.0.1:%s", PORT)
    server.serve_forever()


if __name__ == "__main__":
    main()
