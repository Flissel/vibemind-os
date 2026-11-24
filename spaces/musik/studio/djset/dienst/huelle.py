"""Huelle fuer den MCP-Server des Musik-Dienstes (Kopie von spaces/marketing/claw/mcp_huelle.py,
angepasst an mcp 2.x).

WARUM: Der MCP-Server ruft synchrone Werkzeuge direkt in der einzigen Ereignisschleife auf. Ein
langes Werkzeug haelt dann den ganzen Server an. Werkzeuge laufen deshalb in Arbeiter-Threads,
und /health antwortet auch waehrenddessen.
"""
from __future__ import annotations

import functools
from collections.abc import Callable
from typing import Any


def im_thread(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Synchrones Werkzeug als async-Huelle, die in einem Arbeiter-Thread laeuft.
    Name, Doku und Parameter bleiben, damit das Werkzeug-Schema gleich ist."""
    import anyio

    @functools.wraps(fn)
    async def huelle(**kwargs: Any) -> Any:
        return await anyio.to_thread.run_sync(functools.partial(fn, **kwargs))

    return huelle


def gesundheit_anmelden(server: Any) -> bool:
    """GET /health -> {"status": "ok"} fuer den Waechter. False, wenn die Server-Klasse keine
    eigenen Routen kennt (dann bleibt es beim Port-Check)."""
    route = getattr(server, "custom_route", None)
    if route is None:
        return False
    from starlette.responses import JSONResponse

    @route("/health", methods=["GET"])
    async def gesundheit(_request: Any) -> Any:
        return JSONResponse({"status": "ok"})

    return True
