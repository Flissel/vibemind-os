"""Stimme der Video-Werkbank am PC (11.11.2026): spricht eine Szene Satz fuer Satz ueber /satz des
Ton-Dienstes (claw/ton_dienst.py, 127.0.0.1:8138) und liefert die Dauer jedes Satzes.

Jeder Satz bekommt den vorigen und den naechsten als Kontext (Satzmelodie bei ElevenLabs). Die Saetze
werden mit PAUSE_S Stille zu EINER WAV zusammengefuegt; Start und Dauer jedes Satzes stammen aus den
tatsaechlich geschriebenen Daten, nicht aus Kopfzeilen des Dienstes.

Eine Szene hat nie zwei Stimmen: faellt ElevenLabs mitten in der Szene aus (Antwort 502 mit
X-Ton-Engine-Fehler) - oder melden die Saetze verschiedene Engines -, werden ALLE Saetze der Szene neu mit
engine="chatterbox" gesprochen und `rueckfall` ist True. Spaetere Schritte warnen damit vor Videos, die
Szenen mit verschiedenen Stimmen mischen. Fehlertexte: kurze Saetze oder Typnamen, nie der Sprechtext."""
from __future__ import annotations

import io
import json
import os
import subprocess
import tempfile
import urllib.error
import urllib.request
import wave
from dataclasses import dataclass
from pathlib import Path

from spaces.marketing.claw.video_saetze import saetze as saetze_trennen

DIENST = os.environ.get("TON_DIENST_URL", "http://127.0.0.1:8138")
PAUSE_S = 0.15                 # Stille zwischen zwei Saetzen
ZIEL_RATE = 48000              # gemeinsame Rate, wenn die Saetze verschieden ankommen
SATZ_ZEITLIMIT_S = 300         # ein Satz; der erste laedt das Modell


class StimmeFehler(Exception):
    """Die Szene konnte nicht gesprochen werden. `grund` ist ein kurzer deutscher Satz oder Typname."""

    def __init__(self, grund: str):
        super().__init__(grund)
        self.grund = grund


@dataclass
class Satz:
    text: str
    start_s: float
    dauer_s: float


@dataclass
class Gesprochen:
    saetze: list[Satz]
    engine: str            # 'elevenlabs' | 'chatterbox' - eine Engine fuer die ganze Szene
    rueckfall: bool        # True: ElevenLabs sollte sprechen, die Szene kam komplett lokal
    wav: Path
    dauer_s: float


def _post(nutzlast: dict, url: str | None = None) -> tuple[int, dict, bytes]:
    """POST /satz -> (status, Kopfzeilen, Rumpf). HTTP-Fehlerstatus kommen als Rueckgabe, nicht als Ausnahme."""
    req = urllib.request.Request((url or DIENST) + "/satz", data=json.dumps(nutzlast).encode("utf-8"),
                                 method="POST", headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=SATZ_ZEITLIMIT_S) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), b""


def _kopf(kopf: dict, name: str) -> str:
    return next((str(w) for k, w in kopf.items() if k.lower() == name.lower()), "")


def _lesen(wav: bytes) -> tuple[tuple[int, int, int], bytes]:
    """((Rate, Kanaele, Bytes je Sample), Rohdaten) aus einer WAV."""
    try:
        with wave.open(io.BytesIO(wav)) as w:
            return (w.getframerate(), w.getnchannels(), w.getsampwidth()), w.readframes(w.getnframes())
    except (wave.Error, EOFError):
        raise StimmeFehler("Der Sprech-Dienst lieferte kein lesbares Audio.") from None


def _auf_48k(quelle: Path, ziel: Path) -> None:
    """ffmpeg: mono, 16 Bit, ZIEL_RATE."""
    lauf = subprocess.run([os.environ.get("TON_FFMPEG") or "ffmpeg", "-hide_banner", "-nostdin", "-loglevel",
                           "error", "-y", "-i", str(quelle), "-ac", "1", "-ar", str(ZIEL_RATE),
                           "-c:a", "pcm_s16le", str(ziel)], capture_output=True, text=True, timeout=120)
    if lauf.returncode != 0 or not ziel.is_file():
        raise StimmeFehler("Die Saetze liessen sich nicht auf eine Rate bringen (ffmpeg).")


def _vereinheitlichen(wavs: list[bytes]) -> list[tuple[tuple[int, int, int], bytes]]:
    teile = [_lesen(w) for w in wavs]
    if len({fmt for fmt, _ in teile}) == 1 and teile[0][0][1:] == (1, 2):
        return teile
    neu = []
    with tempfile.TemporaryDirectory(prefix="video_stimme_") as tmp:
        for i, w in enumerate(wavs):
            quelle, ziel = Path(tmp) / f"in-{i:03d}.wav", Path(tmp) / f"aus-{i:03d}.wav"
            quelle.write_bytes(w)
            _auf_48k(quelle, ziel)
            neu.append(_lesen(ziel.read_bytes()))
    return neu


def _durchlauf(teile: list[str], stimme: str, sprache: str, engine: str, senden) -> tuple[str, list[bytes]] | None:
    """Alle Saetze mit einer Engine-Vorgabe sprechen. None = ElevenLabs fiel aus (nur bei engine='auto').
    Ergebnis (engine, wavs); melden die Saetze verschiedene Engines, ist die Szene gemischt -> None."""
    wavs, engines = [], set()
    for i, text in enumerate(teile):
        nutzlast = {"text": text, "vorher": teile[i - 1] if i > 0 else "",
                    "nachher": teile[i + 1] if i + 1 < len(teile) else "",
                    "sprache": sprache, "stimme": stimme, "engine": engine}
        try:
            status, kopf, rumpf = senden(nutzlast)
        except (OSError, ValueError) as e:     # Dienst weg, Zeitlimit, kaputte Antwort
            raise StimmeFehler(f"Der Sprech-Dienst ist nicht erreichbar ({type(e).__name__}).") from None
        if status == 502 and _kopf(kopf, "X-Ton-Engine-Fehler") and engine == "auto":
            return None
        if status != 200:
            raise StimmeFehler(f"Der Sprech-Dienst antwortete mit Status {status}.")
        engines.add(_kopf(kopf, "X-Ton-Engine") or "unbekannt")
        wavs.append(rumpf)
    if len(engines) != 1:
        return None
    return engines.pop(), wavs


def szene_sprechen(text: str, stimme: str, ordner: Path, senden=_post, sprache: str = "de") -> Gesprochen:
    """Szenentext -> eine WAV `ordner/stimme.wav` plus Start und Dauer je Satz. `stimme` nennt der Aufrufer
    (Stimmprobe im Ordner des Dienstes); hier steht kein Stimmname fest."""
    teile = saetze_trennen(text)
    if not teile:
        raise StimmeFehler("Kein Text zum Sprechen.")
    ergebnis = _durchlauf(teile, stimme, sprache, "auto", senden)
    rueckfall = ergebnis is None
    if rueckfall:     # ElevenLabs fiel aus oder die Engines waren gemischt: alles lokal, nichts gemischt
        ergebnis = _durchlauf(teile, stimme, sprache, "chatterbox", senden)
        if ergebnis is None:
            raise StimmeFehler("Der Sprech-Dienst lieferte keine einheitliche Stimme.")
    engine, wavs = ergebnis

    gelesen = _vereinheitlichen(wavs)
    (rate, kanaele, breite) = gelesen[0][0]
    pause = b"\x00" * (round(PAUSE_S * rate) * kanaele * breite)
    ordner = Path(ordner)
    ordner.mkdir(parents=True, exist_ok=True)
    ziel = ordner / "stimme.wav"
    liste, gesamt = [], 0
    with wave.open(str(ziel), "wb") as w:
        w.setnchannels(kanaele)
        w.setsampwidth(breite)
        w.setframerate(rate)
        for i, (satz_text, (_, daten)) in enumerate(zip(teile, gelesen)):
            if i > 0:
                w.writeframes(pause)
                gesamt += len(pause) // (kanaele * breite)
            frames = len(daten) // (kanaele * breite)
            liste.append(Satz(satz_text, gesamt / rate, frames / rate))
            w.writeframes(daten)
            gesamt += frames
    return Gesprochen(saetze=liste, engine=engine, rueckfall=rueckfall, wav=ziel, dauer_s=gesamt / rate)
