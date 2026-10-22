"""Gemeinsame Huelle fuer die MCP-Sidecars (:8130 Agent, :8137 Video).

WARUM (21./22.10.2026): FastMCP ruft synchrone Werkzeuge direkt in der einzigen Ereignisschleife auf.
Ein langes Werkzeug (reel_bauen bis 20 min) haelt den ganzen Server an; der Waechter durfte die
Sidecars deshalb nie auf Stille pruefen, und :8137 blieb am 21.10. eine Stunde eingefroren.
Werkzeuge laufen jetzt in Arbeiter-Threads, und /health antwortet auch waehrenddessen.
"""
import functools


def im_thread(fn):
    """Synchrones Werkzeug als async-Huelle, die in einem Arbeiter-Thread laeuft.
    Name, Doku und Parameter bleiben, damit das Werkzeug-Schema gleich ist."""
    import anyio

    @functools.wraps(fn)
    async def huelle(**kwargs):
        return await anyio.to_thread.run_sync(functools.partial(fn, **kwargs))

    return huelle


def gesundheit_anmelden(server) -> bool:
    """GET /health -> {"status": "ok"} fuer den Waechter. False, wenn die Server-Klasse keine eigenen
    Routen kennt (dann bleibt es beim Port-Check)."""
    route = getattr(server, "custom_route", None)
    if route is None:
        return False
    from starlette.responses import JSONResponse

    @route("/health", methods=["GET"])
    async def gesundheit(_request):
        return JSONResponse({"status": "ok"})

    return True
