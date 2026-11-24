"""Musik-Dienst: MCP-Werkzeuge fuer Agenten und JSON-Routen fuer Video-Claw und Telegram-Bot.

Ein Prozess, ein Port (nur 127.0.0.1): `/mcp` (streamable-http) und die JSON-Routen teilen sich
dieselben `Werkzeuge`. Im Hintergrund arbeitet der Auftragslaeufer; fuer Laura laeuft auf Wunsch
der signierte Datei-Link (`djset.dienst.link`).

Start: `musik-dienst` (MUSIK_DATEN muss gesetzt sein).
"""
from __future__ import annotations

import argparse
import functools
import json
import logging
import os
import re
import sys
import threading
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import Context, MCPServer
from starlette.requests import Request
from starlette.responses import JSONResponse

from djset.dienst import laeufer as laeufer_modul
from djset.dienst import laura as laura_modul
from djset.dienst import link as datei_link
from djset.dienst.auftraege import _ID as _AUFTRAG_ID  # eine Quelle fuer die ID-Form
from djset.dienst.auftraege import Ablage
from djset.dienst.huelle import gesundheit_anmelden, im_thread
from djset.dienst.werkzeuge import Werkzeuge

LOG = logging.getLogger("djset.dienst.server")

HOST = "127.0.0.1"
STANDARD_PORT = 8148
STANDARD_LINK_PORT = 8146
KANAL_KOPF = "x-musik-kanal"
KANAELE = ("telegram", "app", "video-claw")
STANDARD_KANAL = "mcp"
BUDGET_AGENT = laeufer_modul.BUDGET_AGENT  # "musik-kette"
ENV_SCHLUESSEL = (
    "LAURA_API_URL", "LAURA_TOKEN",
    "MUSIK_LINK_HOST", "MUSIK_LINK_PORT", "MUSIK_LINK_SCHLUESSEL",
)
ABSCHALT_WARTE_S = 10.0


# ---------------------------------------------------------------- Kanal


def kanal_waehlen(wert: object) -> str:
    """Nur die bekannten Kanaele; alles andere (auch kein Wert) ist `mcp`."""
    return wert if isinstance(wert, str) and wert in KANAELE else STANDARD_KANAL


def kanal_aus_kopf(kopfzeilen: Mapping[str, str] | None) -> str:
    if not kopfzeilen:
        return STANDARD_KANAL
    for name, wert in kopfzeilen.items():
        if name.lower() == KANAL_KOPF:
            return kanal_waehlen(wert.strip() if isinstance(wert, str) else wert)
    return STANDARD_KANAL


def _kanal(ctx: Context | None) -> str:
    try:
        return kanal_aus_kopf(ctx.headers if ctx is not None else None)
    except Exception:  # noqa: BLE001 - kein Kontext heisst: kein Kopf, also mcp
        return STANDARD_KANAL


# ---------------------------------------------------------------- MCP-Werkzeuge


def bauen(werkzeuge: Werkzeuge) -> MCPServer:
    """Der Server mit den neun Werkzeugen (je in einem Thread) und /health.
    Der Kanal kommt aus dem HTTP-Kopf, nie aus einem Werkzeug-Parameter."""
    server = MCPServer("musik-dienst", log_level="WARNING")

    def bestand_zeigen() -> dict:
        """Songs im Bestand: ID, Titel, BPM, Tonart, Laenge, Herkunft, ob getrennt."""
        return werkzeuge.bestand_zeigen()

    def song_aufnehmen(datei: str, titel: str, herkunft: str, suno_id: str = "",
                       ctx: Context | None = None) -> dict:
        """Song aus dem Eingang in den Bestand aufnehmen (bestellt einen Auftrag). `datei` ist
        ein blosser Dateiname aus dem Eingang, `herkunft` die Quelle des Songs."""
        return werkzeuge.song_aufnehmen(datei, titel, herkunft, suno_id, kanal=_kanal(ctx))

    def set_bestellen(wunsch: str, minuten: float, songs: list[str] | None = None,
                      ohne_gesang: bool = False, ctx: Context | None = None) -> dict:
        """DJ-Set bestellen: Wunsch in Worten, Laenge in Minuten, optional Song-IDs."""
        return werkzeuge.set_bestellen(wunsch, minuten, songs, ohne_gesang, kanal=_kanal(ctx))

    def videomusik_bestellen(wunsch: str, sekunden: float | None = None, frames: int | None = None,
                             fps: str | None = None, laura_timeline_id: str | None = None,
                             songs: list[str] | None = None, gesang: bool | None = None,
                             ctx: Context | None = None) -> dict:
        """Videomusik bestellen. Genau eine Laengenangabe: sekunden, frames mit fps oder
        laura_timeline_id (dann liest der Dienst die Laenge aus der Laura-Zeitleiste)."""
        return werkzeuge.videomusik_bestellen(
            wunsch, sekunden, frames, fps, laura_timeline_id, songs, gesang, kanal=_kanal(ctx))

    def auftrag_zeigen(auftrag: str) -> dict:
        """Stand eines Auftrags: Zustand, Grund, Dateinamen, Bericht, Befunde."""
        return werkzeuge.auftrag_zeigen(auftrag)

    def auftrag_abbrechen(auftrag: str) -> dict:
        """Wartenden oder laufenden Auftrag abbrechen."""
        return werkzeuge.auftrag_abbrechen(auftrag)

    def in_laura_legen(auftrag: str, timeline_id: str, gain_percent: int = 100) -> dict:
        """Fertige Videomusik als Audio-Clip auf eine Laura-Zeitleiste legen."""
        return werkzeuge.in_laura_legen(auftrag, timeline_id, gain_percent)

    def laura_videos_suchen(suchwort: str) -> dict:
        """Videos und Zeitleisten in Laura nach einem Suchwort finden."""
        return werkzeuge.laura_videos_suchen(suchwort)

    def laura_rendern(timeline_id: str, musik_auftrag: str = "", ctx: Context | None = None) -> dict:
        """Laura-Zeitleiste rendern lassen (bestellt einen Auftrag)."""
        return werkzeuge.laura_rendern(timeline_id, kanal=_kanal(ctx), musik_auftrag=musik_auftrag)

    for fn in (bestand_zeigen, song_aufnehmen, set_bestellen, videomusik_bestellen, auftrag_zeigen,
               auftrag_abbrechen, in_laura_legen, laura_videos_suchen, laura_rendern):
        server.tool()(im_thread(fn))
    gesundheit_anmelden(server)
    routen_anmelden(server, werkzeuge)
    return server


# ---------------------------------------------------------------- JSON-Routen


def _antwort(daten: dict, status: int = 200) -> JSONResponse:
    return JSONResponse(daten, status_code=status)


def _schlecht(grund: str, status: int = 400) -> JSONResponse:
    return _antwort({"ok": False, "fehler": grund}, status)


async def _koerper(request: Request) -> dict | None:
    """JSON-Objekt aus dem Koerper; ein leerer Koerper ist `{}`; sonst None."""
    try:
        roh = await request.body()
        wert = json.loads(roh) if roh.strip() else {}
    except (ValueError, UnicodeDecodeError):
        return None
    return wert if isinstance(wert, dict) else None


def routen_anmelden(server: MCPServer, w: Werkzeuge) -> None:
    """Alle JSON-Routen an einer Stelle. Antwort ist das dict der Werkzeuge; eine boese
    Auftrags-ID kommt nie bis zu den Werkzeugen (404)."""
    import anyio

    async def thread(fn: Callable[..., dict], *args: Any, **kwargs: Any) -> dict:
        return await anyio.to_thread.run_sync(functools.partial(fn, *args, **kwargs))

    def id_ok(request: Request) -> str | None:
        wert = request.path_params.get("id", "")
        return wert if isinstance(wert, str) and _AUFTRAG_ID.fullmatch(wert) else None

    def nicht_gefunden() -> JSONResponse:
        return _schlecht("unbekannter Auftrag", 404)

    @server.custom_route("/auftraege/video", methods=["POST"])
    async def video_bestellen(request: Request) -> JSONResponse:
        b = await _koerper(request)
        if b is None:
            return _schlecht("Koerper muss ein JSON-Objekt sein")
        return _antwort(await thread(
            w.videomusik_bestellen, b.get("wunsch"), b.get("sekunden"), b.get("frames"),
            b.get("fps"), b.get("laura_timeline_id"), b.get("songs"), b.get("gesang"),
            kanal=kanal_waehlen(b.get("kanal"))))

    @server.custom_route("/auftraege/set", methods=["POST"])
    async def set_bestellen(request: Request) -> JSONResponse:
        b = await _koerper(request)
        if b is None:
            return _schlecht("Koerper muss ein JSON-Objekt sein")
        return _antwort(await thread(
            w.set_bestellen, b.get("wunsch"), b.get("minuten"), b.get("songs"),
            bool(b.get("ohne_gesang", False)), kanal=kanal_waehlen(b.get("kanal"))))

    @server.custom_route("/auftraege/{id}", methods=["GET"])
    async def auftrag(request: Request) -> JSONResponse:
        auftrag_id = id_ok(request)
        if auftrag_id is None:
            return nicht_gefunden()
        return _antwort(await thread(w.auftrag_zeigen, auftrag_id))

    @server.custom_route("/auftraege", methods=["GET"])
    async def offen(request: Request) -> JSONResponse:
        if request.query_params.get("offen") != "1":
            return _schlecht("nur offen=1 wird unterstuetzt")
        return _antwort(await thread(
            w.auftraege_offen, kanal_waehlen(request.query_params.get("kanal"))))

    @server.custom_route("/auftraege/{id}/gemeldet", methods=["POST"])
    async def gemeldet(request: Request) -> JSONResponse:
        auftrag_id = id_ok(request)
        if auftrag_id is None:
            return nicht_gefunden()
        return _antwort(await thread(w.gemeldet, auftrag_id))

    @server.custom_route("/auftraege/{id}/in-laura", methods=["POST"])
    async def in_laura(request: Request) -> JSONResponse:
        auftrag_id = id_ok(request)
        if auftrag_id is None:
            return nicht_gefunden()
        b = await _koerper(request)
        if b is None:
            return _schlecht("Koerper muss ein JSON-Objekt sein")
        return _antwort(await thread(
            w.in_laura_legen, auftrag_id, b.get("timeline_id"), b.get("gain_percent", 100)))

    @server.custom_route("/laura/rendern", methods=["POST"])
    async def rendern(request: Request) -> JSONResponse:
        b = await _koerper(request)
        if b is None:
            return _schlecht("Koerper muss ein JSON-Objekt sein")
        musik = b.get("musik_auftrag") or ""
        return _antwort(await thread(
            w.laura_rendern, b.get("timeline_id"), kanal=kanal_waehlen(b.get("kanal")),
            musik_auftrag=musik))

    @server.custom_route("/laura/suchen", methods=["GET"])
    async def suchen(request: Request) -> JSONResponse:
        return _antwort(await thread(w.laura_videos_suchen, request.query_params.get("q", "")))


# ---------------------------------------------------------------- Start


def env_nachladen(datei: Path, schluessel: tuple[str, ...] = ENV_SCHLUESSEL) -> None:
    """Fehlende Schluessel aus einer .env uebernehmen, nie etwas ueberschreiben, nie ausgeben."""
    fehlend = [k for k in schluessel if not os.environ.get(k)]
    if not fehlend or not datei.is_file():
        return
    for zeile in datei.read_text(encoding="utf-8", errors="replace").splitlines():
        zeile = zeile.strip()
        for k in fehlend:
            if zeile.startswith(k + "=") and not os.environ.get(k):
                os.environ[k] = zeile.split("=", 1)[1].strip().strip('"').strip("'")


def _aeusseres_env() -> Path | None:
    """`.env` des aeusseren Repos: der erste Vorfahr, der `vibemind-os` enthaelt."""
    for eltern in Path(__file__).resolve().parents:
        if (eltern / "vibemind-os").is_dir():
            return eltern / ".env"
    return None


def dj_set_befehl() -> list[str]:
    """Der `dj-set`-Befehl neben dem laufenden Interpreter, sonst `python -m djset.cli`."""
    exe = Path(sys.executable).parent / "dj-set.exe"
    return [str(exe)] if exe.is_file() else [sys.executable, "-m", "djset.cli"]


def _link_aus_umgebung(ablage: Ablage) -> tuple[Any, str | None, bytes | None]:
    """(Link-Server oder None, link_basis, link_schluessel). Fehlt Host oder Schluessel, oder
    laesst sich der Port nicht binden, ist der Link aus; die Werkzeuge melden das dann klar."""
    host = os.environ.get("MUSIK_LINK_HOST", "").strip()
    hex_schluessel = os.environ.get("MUSIK_LINK_SCHLUESSEL", "").strip()
    if not host or not hex_schluessel:
        LOG.warning("Datei-Link aus (MUSIK_LINK_HOST oder MUSIK_LINK_SCHLUESSEL fehlt)")
        return None, None, None
    try:
        schluessel = bytes.fromhex(hex_schluessel)
        port = int(os.environ.get("MUSIK_LINK_PORT", "").strip() or STANDARD_LINK_PORT)
    except ValueError:
        LOG.warning("Datei-Link aus (MUSIK_LINK_SCHLUESSEL oder MUSIK_LINK_PORT ungueltig)")
        return None, None, None
    try:
        link = datei_link.starten(host, port, ablage, schluessel)
    except OSError as problem:
        LOG.warning("Datei-Link aus: Port %s nicht bindbar (%s)", port, type(problem).__name__)
        return None, None, None
    return link, f"http://{host}:{port}", schluessel


def main(argv: list[str] | None = None) -> int:
    argparse.ArgumentParser(
        prog="musik-dienst",
        description="Musik-Dienst: MCP (/mcp) und JSON-Routen auf 127.0.0.1:MUSIK_STUDIO_PORT "
                    f"(Vorgabe {STANDARD_PORT}). Braucht MUSIK_DATEN.",
    ).parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    env = _aeusseres_env()
    if env is not None:
        env_nachladen(env)
    daten_text = os.environ.get("MUSIK_DATEN", "").strip()
    if not daten_text:
        print("Fehler: MUSIK_DATEN ist nicht gesetzt (Datenordner des Musik-Studios).", file=sys.stderr)
        return 2
    try:
        port = int(os.environ.get("MUSIK_STUDIO_PORT", "").strip() or STANDARD_PORT)
    except ValueError:
        print("Fehler: MUSIK_STUDIO_PORT ist keine Zahl.", file=sys.stderr)
        return 2

    daten = Path(daten_text)
    ablage = Ablage(daten)
    budget, buchen = laeufer_modul.budget_aus_umgebung()
    laeufer = laeufer_modul.Laeufer(
        ablage, dj_set_befehl(), laeufer_modul.speicher_frei_gb, budget, buchen,
        laura_render=lambda _auftrag: {"ok": False, "fehler": "Werkzeuge noch nicht gebunden"},
    )
    link, link_basis, link_schluessel = _link_aus_umgebung(ablage)
    werkzeuge = Werkzeuge(daten, ablage, laeufer, laura_modul, link_basis, link_schluessel)
    laeufer.laura_render = werkzeuge.render_ausfuehren  # spaet gebunden

    laeufer.beim_start()
    stopp = threading.Event()
    arbeiter = threading.Thread(target=laeufer.schleife, args=(stopp,), name="musik-laeufer", daemon=True)
    arbeiter.start()
    try:
        bauen(werkzeuge).run("streamable-http", host=HOST, port=port)
    except KeyboardInterrupt:
        pass
    finally:
        stopp.set()
        arbeiter.join(ABSCHALT_WARTE_S)
        if link is not None:
            link.shutdown()
            link.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
