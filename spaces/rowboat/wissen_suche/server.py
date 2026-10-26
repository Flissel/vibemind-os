"""MCP-Dienst ``rowboat-wissen``: Werkzeuge ``wissen_suchen`` und ``wissen_stand`` fuer Rowboat.

Laeuft auf dem Mini-PC im Host-Netz und bindet nur Loopback (127.0.0.1:3240, Pfad /mcp). Ein
Indexer-Faden haelt den Index aktuell; die Vektoren rechnet Ollama am PC (kein Modell hier).
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

import anyio
from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

from .einbetten import Einbetter, OllamaEinbetter
from .index import WissensIndex
from .indexer import Indexer
from .suche import wissen_stand, wissen_suchen

log = logging.getLogger(__name__)

HOST = "127.0.0.1"

WERKZEUG_BESCHREIBUNG = (
    "Durchsucht das Rowboat-Wissen (Notizen, Kontakt-Steckbriefe, Newsletter, Wochenbild) nach "
    "BEDEUTUNG, auch ohne exakte Wörter. Liefert Pfade unter knowledge/, die mit file-readText "
    "geöffnet werden können. Ist der PC aus, kommen Stichwort-Treffer mit Hinweis.")

STAND_BESCHREIBUNG = ("Stand der Bedeutungssuche: wie viele Notizen eingebettet, offen oder fehlerhaft "
                      "sind, welches Modell, ob Ollama am PC erreichbar ist und wann die letzte Runde lief.")


@dataclass(frozen=True)
class Einstellungen:
    wissen_ordner: str = "/wissen"
    index_datei: str = "/index/wissen.sqlite"
    ollama_url: str = "http://100.84.127.84:11435"
    modell: str = "qwen3-embedding:0.6b"
    dim: int = 1024
    num_ctx: int = 2048
    intervall_s: int = 600
    port: int = 3240


_ENV = {"wissen_ordner": "WISSEN_ORDNER", "index_datei": "WISSEN_INDEX", "ollama_url": "OLLAMA_URL",
        "modell": "EMBED_MODELL", "dim": "EMBED_DIM", "num_ctx": "EMBED_NUM_CTX",
        "intervall_s": "INDEX_INTERVALL_S", "port": "WISSEN_PORT"}


def einstellungen_laden(env: Mapping[str, str]) -> Einstellungen:
    """Liest die Env; jeder Wert wird gestrippt (CRLF aus Env-Dateien), leere Werte = Vorgabe."""
    vorgabe = Einstellungen()
    werte: dict[str, object] = {}
    for feld, name in _ENV.items():
        roh = (env.get(name) or "").strip()
        if not roh:
            continue
        werte[feld] = int(roh) if isinstance(getattr(vorgabe, feld), int) else roh
    return Einstellungen(**{**vorgabe.__dict__, **werte})


def _bauteile(e: Einstellungen, einbetter: Einbetter | None) -> tuple[WissensIndex, Einbetter]:
    einbetter = einbetter or OllamaEinbetter(e.ollama_url, e.modell, e.dim, num_ctx=e.num_ctx)
    return WissensIndex(Path(e.index_datei), einbetter.modell, einbetter.dim), einbetter


def baue_server(e: Einstellungen, einbetter: Einbetter | None = None,
                index: WissensIndex | None = None) -> FastMCP:
    if index is None:
        index, einbetter = _bauteile(e, einbetter)
    assert einbetter is not None
    wurzel = Path(e.wissen_ordner)
    mcp = FastMCP("wissen", host=HOST, port=e.port, stateless_http=True, json_response=True,
                  streamable_http_path="/mcp")

    @mcp.tool(name="wissen_suchen", description=WERKZEUG_BESCHREIBUNG)
    async def _wissen_suchen(frage: str, anzahl: int = 8) -> str:
        # Im Hilfsfaden: das Einbetten der Frage darf die Ereignisschleife nicht blockieren.
        ergebnis = await anyio.to_thread.run_sync(lambda: wissen_suchen(frage, anzahl, index, wurzel, einbetter))
        return json.dumps(ergebnis, ensure_ascii=False)

    @mcp.tool(name="wissen_stand", description=STAND_BESCHREIBUNG)
    async def _wissen_stand() -> str:
        ergebnis = await anyio.to_thread.run_sync(lambda: wissen_stand(index, e.ollama_url))
        return json.dumps(ergebnis, ensure_ascii=False)

    @mcp.custom_route("/gesund", methods=["GET"])
    async def _gesund(_: Request) -> JSONResponse:
        return JSONResponse({"ok": True})

    return mcp


def main() -> None:
    import os

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    e = einstellungen_laden(os.environ)
    index, einbetter = _bauteile(e, None)
    log.info("rowboat-wissen: Wissen %s, Index %s, Ollama %s (%s, %d Dim.), %s:%d/mcp",
             e.wissen_ordner, e.index_datei, e.ollama_url, e.modell, e.dim, HOST, e.port)
    mcp = baue_server(e, einbetter, index=index)
    Indexer(index, Path(e.wissen_ordner), einbetter, intervall_s=e.intervall_s).start()
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
