"""Render-Huelle der Video-Werkbank (11.11.2026): HyperFrames (HTML -> MP4) und ffmpeg-Normalisierung.

Jede Szene wird zu einem MP4 mit identischen Parametern gerendert, damit sich die Szenen spaeter ohne
Neukodierung zusammenfuegen lassen. HyperFrames laeuft ueber das projektlokale `node_modules` (kein
Netz-Install, keine globale Installation). Ausgaben des Kindprozesses gehen in eine Datei, nie in eine
Pipe (ein Kindprozess, der die Pipe erbt, haengt sonst den Aufrufer). Fehlertexte sind kurze Saetze ohne Pfade.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import signal
import subprocess
import tempfile
import uuid
from pathlib import Path

from spaces.marketing.claw import video_slides

LOG = logging.getLogger("video_rendern")

WERKBANK = Path(__file__).resolve().parent.parent / "video_werkbank"
FPS = 30


class RenderFehler(Exception):
    """Render oder Normalisierung fehlgeschlagen; `grund` ist ein kurzer Satz ohne Pfade."""

    def __init__(self, grund: str):
        super().__init__(grund)
        self.grund = grund


def eingerichtet() -> bool:
    return (WERKBANK / "node_modules" / "hyperframes").is_dir()


def render_argumente(komposition: Path, ziel: Path) -> list[str]:
    # HyperFrames kennt --composition/--output/--fps (geprueft gegen `render --help`, v0.9.33).
    return ["npx", "--no-install", "hyperframes", "render",
            "--composition", str(komposition), "--output", str(ziel), "--fps", str(FPS)]


def normal_argumente(quelle: Path, ziel: Path, ton: Path | None = None) -> list[str]:
    a = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(quelle)]
    if ton is not None:
        a += ["-i", str(ton), "-map", "0:v:0", "-map", "1:a:0", "-shortest"]
    a += ["-vf", "scale=1920:1080,setsar=1", "-r", str(FPS),
          "-c:v", "libx264", "-pix_fmt", "yuv420p",
          "-c:a", "aac", "-ar", "48000", "-ac", "2",
          "-movflags", "+faststart", str(ziel)]
    return a


def _lauf(args: list[str], zeitlimit_s: float, cwd: Path | None, env: dict[str, str],
          protokoll: Path | None = None) -> int:
    """Startet den Kindprozess mit Ausgabe in einer Temp-Datei; liefert den Exit-Code oder wirft RenderFehler.
    Mit `protokoll` wird die Ausgabe nach dem Lauf dorthin kopiert (der Aufrufer wertet sie aus)."""
    exe = shutil.which(args[0])  # unter Windows ist npx ein .cmd
    if exe is None:
        raise RenderFehler(f"Programm {args[0]} nicht gefunden.")
    with tempfile.TemporaryFile() as log:
        try:
            if os.name == "nt":
                flags = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
            else:
                flags = {"start_new_session": True}
            proc = subprocess.Popen([exe, *args[1:]], stdout=log, stderr=subprocess.STDOUT,
                                    stdin=subprocess.DEVNULL, cwd=cwd, env=env, **flags)
        except OSError as e:
            raise RenderFehler(f"Prozess liess sich nicht starten ({type(e).__name__}).") from None
        try:
            code = proc.wait(timeout=zeitlimit_s)
        except subprocess.TimeoutExpired:
            _baum_beenden(proc.pid)
            raise RenderFehler("Zeitlimit beim Rendern ueberschritten.") from None
        except BaseException:
            _baum_beenden(proc.pid)  # jeder andere Abbruch (z. B. Strg+C) raeumt ebenfalls auf
            raise
        if code != 0:
            LOG.warning("%s endete mit Exit %s", args[0], code)
        if protokoll is not None:
            log.seek(0)
            Path(protokoll).write_bytes(log.read())
        return code


def _baum_beenden(pid: int) -> None:
    """Beendet den Kindprozess samt Unterprozessen, nur ueber die PID (nie per Namens-/Kommandozeilenmuster)."""
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, timeout=30)
        else:
            os.killpg(pid, signal.SIGKILL)  # eigene Sitzung (start_new_session): Gruppe = Kindbaum
    except (OSError, subprocess.SubprocessError) as e:
        LOG.warning("Prozessbaum liess sich nicht beenden (%s)", type(e).__name__)


def rendern(komposition: Path, ziel: Path, ton: Path | None = None, zeitlimit_s: float = 900) -> None:
    """Rendert `komposition` (HTML) nach `ziel` (MP4, 1920x1080, 30 fps).

    Mit `ton` wird die Tonspur nachtraeglich aus der WAV gesetzt (Rueckfall, falls HyperFrames keinen Ton schreibt).
    """
    komposition, ziel = Path(komposition).resolve(), Path(ziel).resolve()
    ziel.parent.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "DO_NOT_TRACK": "1"}
    if ton is None:
        roh = ziel
    else:
        roh = ziel.with_name(ziel.stem + ".roh.mp4")
    try:
        if _lauf(render_argumente(komposition, roh), zeitlimit_s, WERKBANK, env) != 0 or not roh.exists():
            raise RenderFehler("HyperFrames-Render ist fehlgeschlagen.")
        if ton is not None:
            if _lauf(normal_argumente(roh, ziel, Path(ton).resolve()), zeitlimit_s, None, env) != 0 or not ziel.exists():
                raise RenderFehler("Tonspur liess sich nicht einsetzen.")
    finally:
        if ton is not None:
            roh.unlink(missing_ok=True)


def pruefen(pfad: Path) -> dict:
    """ffprobe: {breite, hoehe, fps, dauer_s, ton}."""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height,r_frame_rate:format=duration",
             "-of", "json", str(pfad)], capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
        daten = json.loads(r.stdout)
    except (OSError, subprocess.TimeoutExpired, ValueError) as e:
        raise RenderFehler(f"Video liess sich nicht pruefen ({type(e).__name__}).") from None
    if r.returncode != 0 or not daten.get("streams"):
        raise RenderFehler("Video liess sich nicht pruefen.")
    video = next((s for s in daten["streams"] if s.get("codec_type") == "video"), None)
    if video is None:
        raise RenderFehler("Video ohne Bildspur.")
    zaehler, _, nenner = str(video.get("r_frame_rate", "0/1")).partition("/")
    fps = round(float(zaehler) / float(nenner or 1)) if float(nenner or 1) else 0
    return {"breite": int(video["width"]), "hoehe": int(video["height"]), "fps": fps,
            "dauer_s": float(daten.get("format", {}).get("duration", 0)),
            "ton": any(s.get("codec_type") == "audio" for s in daten["streams"])}


DAUER_TOLERANZ_S = 0.5


def szene_rendern(szene: dict, gesprochen, marke: dict, ordner: Path, ziel: Path) -> float:
    """Eine gesprochene Szene -> ein Clip im Einheitsformat von `normal_argumente` (H.264 yuv420p, 30 fps,
    1920x1080, AAC 48 kHz Stereo, faststart), damit sich Clips spaeter ohne Neukodierung verbinden lassen.

    Schreibt Komposition, Ton, Schriften und Logo nach `ordner`, rendert mit HyperFrames und normalisiert den
    Roh-Clip mit dem Ton aus `gesprochen.wav` (der eigene Ton des Renderers wird nicht verwendet: so haengt die
    Tonspur nie vom Mischer des Renderers ab). Gibt die Dauer des Clips zurueck; `RenderFehler`, wenn sie mehr als
    0,5 s von `gesprochen.dauer_s` abweicht.
    """
    ordner, ziel = Path(ordner), Path(ziel)
    bild = szene.get("bild") if isinstance(szene, dict) else None
    fehler = video_slides.bild_pruefen(bild, len(gesprochen.saetze))
    if fehler:
        raise RenderFehler("Der Bildplan der Szene ist ungueltig: " + fehler[0])
    ordner.mkdir(parents=True, exist_ok=True)
    plan = video_slides.zeitplan(bild, gesprochen.saetze, gesprochen.dauer_s)
    shutil.copyfile(gesprochen.wav, ordner / "ton.wav")
    (ordner / "schriften").mkdir(exist_ok=True)
    for name in video_slides.schrift_dateien(marke):
        shutil.copyfile(video_slides.SCHRIFTEN / name, ordner / "schriften" / name)
    logo = video_slides.logo_name(marke)
    if logo:
        shutil.copyfile(marke["logo"], ordner / logo)
    komposition, roh = ordner / "index.html", ordner / "roh.mp4"
    komposition.write_text(video_slides.komposition(szene, plan, marke, "ton.wav", gesprochen.dauer_s),
                           encoding="utf-8")
    env = {**os.environ, "DO_NOT_TRACK": "1"}
    spiegel = None
    try:
        # HyperFrames nimmt nur Kompositionen innerhalb des Projektordners an: liegt `ordner` anderswo,
        # wird er fuer den Render dorthin gespiegelt (renders/ ist git-ignoriert) und danach entfernt.
        try:
            ordner.resolve().relative_to(WERKBANK.resolve())
        except ValueError:
            spiegel = WERKBANK / "renders" / f"szene-{uuid.uuid4().hex[:8]}"
            shutil.copytree(ordner, spiegel)
            komposition, roh = spiegel / "index.html", spiegel / "roh.mp4"
        rendern(komposition, roh)
        ziel.parent.mkdir(parents=True, exist_ok=True)
        args = normal_argumente(roh.resolve(), ziel.resolve(), (komposition.parent / "ton.wav").resolve())
        if _lauf(args, 600, None, env) != 0 or not ziel.exists():
            raise RenderFehler("Der Clip liess sich nicht normalisieren.")
    finally:
        roh.unlink(missing_ok=True)
        if spiegel is not None:
            shutil.rmtree(spiegel, ignore_errors=True)
    dauer = pruefen(ziel)["dauer_s"]
    if abs(dauer - gesprochen.dauer_s) > DAUER_TOLERANZ_S:
        ziel.unlink(missing_ok=True)   # kein falscher Clip bleibt liegen
        raise RenderFehler("Der Clip ist nicht so lang wie die gesprochene Szene.")
    return dauer
