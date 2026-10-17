"""Sprech-Dienst fuer Podcasts (17.10.2026): Chatterbox Multilingual lokal am PC, nur 127.0.0.1.

Laeuft in der eigenen Chatterbox-Umgebung (Torch + CUDA), NICHT im gemeinsamen .venv:
    C:\\Users\\User\\ClaudeWork\\chatterbox-venv\\Scripts\\python.exe -m spaces.marketing.claw.ton_dienst

    GET  /healthz   -> 200 ok (laedt das Modell nicht)
    POST /sprechen  {"text": "...", "sprache": "de", "stimme": "anna"}  -> audio/wav
    POST /voiceover Lauras Sidecar-Vertrag (Video-Claw, 17.10.2026): {text, duration_frames,
                    fps_num, fps_den, sample_rate, language?, voice_id?, fit_to_slot?} -> WAV mono
                    16 Bit in sample_rate; fit_to_slot fuellt mit Stille auf bzw. kuerzt auf den Slot
                    (wie Lauras eigener tts-sidecar). voice_id ist ein Stimmname, kein Pfad.

Lauscht immer auf 127.0.0.1, zusaetzlich auf TON_DIENST_ADRESSEN (Komma-Liste, z.B. die
Tailscale-Adresse des PCs, damit Laura auf der VM sprechen lassen kann - Betreiber-Entscheid
E5). Anfragen von ausserhalb des PCs nur von Absendern in TON_DIENST_ERLAUBT (die VM);
die Windows-Firewall beschraenkt zusaetzlich.

Warum nicht Lauras tts-sidecar: der braucht immer eine Stimmprobe, ist auf Video-Spuren (Frames, Slots)
zugeschnitten und nutzt das rein englische Chatterbox-Modell. Hier spricht das mehrsprachige Modell
(language_id), ohne Stimmprobe mit seiner eingebauten Stimme. Eigene Stimmen sind WAV-Dateien
<name>.wav in TON_STIMMEN_ORDNER (nur mit Einverstaendnis der Person - das ist Stimmen-Klonen).
Modellgewichte: HF_HOME (E:/huggingface). Keine Modelle auf der VM, kein API-Budget.
"""
from __future__ import annotations

import io
import ipaddress
import json
import logging
import os
import re
import subprocess
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

LOG = logging.getLogger("ton_dienst")
PORT = int(os.environ.get("TON_DIENST_PORT", "8899"))
TEXT_MAX = 1000
SPRACHEN = {"ar", "da", "de", "el", "en", "es", "fi", "fr", "he", "hi", "it", "ja", "ko", "ms", "nl", "no",
            "pl", "pt", "ru", "sv", "sw", "tr", "zh"}
_STIMME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
RATE_MIN, RATE_MAX = 8000, 96000
# Obergrenze der Slotlaenge: fit_to_slot fuellt per apad bis dahin mit Stille auf - ohne Grenze
# erzeugte EINE Anfrage (duration_frames=10**9) eine beliebig grosse WAV (Sicherheitspruefung 17.10.).
SLOT_MAX_S = 300


class Anfragefehler(ValueError):
    pass


def stimmen_ordner() -> Path | None:
    roh = os.environ.get("TON_STIMMEN_ORDNER", "").strip()
    return Path(roh) if roh else None


def lausch_adressen() -> list[str]:
    """127.0.0.1 plus TON_DIENST_ADRESSEN. Wildcard-Adressen sind verboten (nur gezielt oeffnen)."""
    adressen = ["127.0.0.1"]
    for roh in os.environ.get("TON_DIENST_ADRESSEN", "").split(","):
        a = roh.strip()
        if not a or a in adressen:
            continue
        if ipaddress.ip_address(a).is_unspecified:
            raise ValueError(f"TON_DIENST_ADRESSEN darf keine Wildcard enthalten: {a}")
        adressen.append(a)
    return adressen


def absender_erlaubt(absender: str) -> bool:
    """Lokal immer; sonst nur Adressen aus TON_DIENST_ERLAUBT."""
    try:
        if ipaddress.ip_address(absender).is_loopback:
            return True
    except ValueError:
        return False
    erlaubt = {a.strip() for a in os.environ.get("TON_DIENST_ERLAUBT", "").split(",") if a.strip()}
    return absender in erlaubt


def _stimmprobe(stimme) -> Path | None:
    if stimme in (None, "", "standard"):
        return None
    if not isinstance(stimme, str) or not _STIMME.fullmatch(stimme):
        raise Anfragefehler("stimme: Kleinbuchstaben, Ziffern, - und _")
    ordner = stimmen_ordner()
    datei = (ordner / f"{stimme}.wav") if ordner else None
    if datei is None or not datei.is_file():
        raise Anfragefehler(f"Keine Stimmprobe fuer '{stimme}' hinterlegt")
    return datei


def _positiv(nutzlast: dict, feld: str) -> int:
    wert = nutzlast.get(feld)
    if isinstance(wert, bool) or not isinstance(wert, int) or wert <= 0:
        raise Anfragefehler(f"{feld} muss eine positive Ganzzahl sein")
    return wert


def voiceover_pruefen(nutzlast) -> dict:
    """Lauras /voiceover-Vertrag pruefen - ohne Torch. Antwort: text, sprache, probe,
    sample_rate, fit, sekunden (Slotlaenge)."""
    if not isinstance(nutzlast, dict):
        raise Anfragefehler("Rumpf muss ein JSON-Objekt sein")
    text = nutzlast.get("text")
    if not isinstance(text, str) or not text.strip() or len(text) > TEXT_MAX:
        raise Anfragefehler(f"text: 1 bis {TEXT_MAX} Zeichen")
    frames = _positiv(nutzlast, "duration_frames")
    fps_num = _positiv(nutzlast, "fps_num")
    fps_den = _positiv(nutzlast, "fps_den")
    rate = _positiv(nutzlast, "sample_rate")
    if not RATE_MIN <= rate <= RATE_MAX:
        raise Anfragefehler(f"sample_rate: {RATE_MIN} bis {RATE_MAX}")
    sprache = nutzlast.get("language") or "de"
    if sprache not in SPRACHEN:
        raise Anfragefehler("sprache wird vom Modell nicht unterstuetzt")
    if frames * fps_den / fps_num > SLOT_MAX_S:
        raise Anfragefehler(f"duration_frames: Slot hoechstens {SLOT_MAX_S} s")
    return {"text": text.strip(), "sprache": sprache, "probe": _stimmprobe(nutzlast.get("voice_id")),
            "sample_rate": rate, "fit": bool(nutzlast.get("fit_to_slot", False)),
            "sekunden": frames * fps_den / fps_num}


def anpassen_argumente(quelle: str, ziel: str, *, sample_rate: int, fit: bool,
                       sekunden: float) -> list[str]:
    """ffmpeg-Argumente wie Lauras tts-sidecar: mono, sample_rate, pcm_s16le; fit -> apad + -t."""
    args = [os.environ.get("TON_FFMPEG") or "ffmpeg", "-hide_banner", "-nostdin", "-loglevel",
            "error", "-y", "-i", quelle, "-ac", "1", "-ar", str(sample_rate)]
    if fit:
        args += ["-af", "apad", "-t", f"{sekunden:.6f}"]
    return args + ["-c:a", "pcm_s16le", ziel]


def anpassen(wav: bytes, *, sample_rate: int, fit: bool, sekunden: float) -> bytes:
    with tempfile.TemporaryDirectory(prefix="ton_vo_") as tmp:
        roh, aus = Path(tmp) / "roh.wav", Path(tmp) / "aus.wav"
        roh.write_bytes(wav)
        args = anpassen_argumente(str(roh), str(aus), sample_rate=sample_rate, fit=fit,
                                  sekunden=sekunden)
        lauf = subprocess.run(args, capture_output=True, text=True)  # noqa: S603
        if lauf.returncode != 0 or not aus.is_file():
            fehler = (lauf.stderr or "").strip()[-300:]
            raise RuntimeError(f"ffmpeg: {fehler}")
        return aus.read_bytes()


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
    return text.strip(), sprache, _stimmprobe(nutzlast.get("stimme"))


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

    def _abgewiesen(self) -> bool:
        if absender_erlaubt(self.client_address[0]):
            return False
        self._antwort(403, b"Absender nicht erlaubt", "text/plain")
        return True

    def do_GET(self):  # noqa: N802
        if self._abgewiesen():
            return
        if self.path == "/healthz":
            self._antwort(200, b"ok", "text/plain")
        else:
            self._antwort(404, b"nicht gefunden", "text/plain")

    def do_POST(self):  # noqa: N802
        if self._abgewiesen():
            return
        pfad = self.path.split("?", 1)[0]
        if pfad not in ("/sprechen", "/voiceover"):
            self._antwort(404, b"nicht gefunden", "text/plain")
            return
        try:
            laenge = min(int(self.headers.get("Content-Length") or 0), 64 * 1024)
            nutzlast = json.loads(self.rfile.read(laenge) or b"{}")
            if pfad == "/voiceover":
                vo = voiceover_pruefen(nutzlast)
                text, sprache, probe = vo["text"], vo["sprache"], vo["probe"]
            else:
                vo = None
                text, sprache, probe = anfrage_pruefen(nutzlast)
        except (Anfragefehler, ValueError) as e:
            self._antwort(422, str(e).encode("utf-8"), "text/plain; charset=utf-8")
            return
        try:
            rumpf = sprechen(text, sprache, probe)
            if vo is not None:
                rumpf = anpassen(rumpf, sample_rate=vo["sample_rate"], fit=vo["fit"],
                                 sekunden=vo["sekunden"])
            self._antwort(200, rumpf, "audio/wav")
        except Exception as e:  # noqa: BLE001 - Modell/GPU-Fehler als 500 mit Grund
            LOG.exception("Sprechen fehlgeschlagen")
            self._antwort(500, f"{type(e).__name__}: {e}"[:300].encode("utf-8"), "text/plain; charset=utf-8")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    server = [ThreadingHTTPServer((adresse, PORT), Handler) for adresse in lausch_adressen()]
    for s in server[1:]:
        threading.Thread(target=s.serve_forever, daemon=True).start()
    LOG.info("Sprech-Dienst auf %s (Port %s)", ", ".join(s.server_address[0] for s in server), PORT)
    server[0].serve_forever()


if __name__ == "__main__":
    main()
