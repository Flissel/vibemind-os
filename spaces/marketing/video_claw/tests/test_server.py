"""Video-Claw MCP-Server (:8137): Werkzeuge laufen in Threads, /health antwortet waehrenddessen.

21.10.2026: Der Server fror eine Stunde ein. Synchrone Werkzeuge liefen direkt in der einzigen
Ereignisschleife (reel_bauen bis 20 min), darum durfte der Waechter ihn nie auf Stille pruefen.
"""
import inspect
import time

import anyio
from starlette.testclient import TestClient

from spaces.marketing.video_claw import server, werkzeuge


def test_werkzeug_im_thread_blockiert_die_schleife_nicht():
    def langsam(sekunden: float) -> dict:
        time.sleep(sekunden)
        return {"ok": True}

    eingepackt = server.im_thread(langsam)
    takte = []

    async def ablauf():
        async with anyio.create_task_group() as tg:
            async def ticken():
                for _ in range(20):
                    takte.append(time.monotonic())
                    await anyio.sleep(0.02)
            tg.start_soon(ticken)
            assert await eingepackt(sekunden=0.4) == {"ok": True}

    anyio.run(ablauf)
    assert len(takte) >= 10, "die Schleife stand still, waehrend das Werkzeug lief"


def test_eingepacktes_werkzeug_behaelt_namen_doku_und_parameter():
    for fn in werkzeuge.WERKZEUGE:
        e = server.im_thread(fn)
        assert inspect.iscoroutinefunction(e)
        assert e.__name__ == fn.__name__ and e.__doc__ == fn.__doc__
        assert inspect.signature(e) == inspect.signature(fn)


def test_alle_werkzeuge_mit_unveraenderten_parametern_registriert():
    mcp = server.bauen()
    tools = {t.name: t for t in anyio.run(mcp.list_tools)}
    assert set(tools) == {fn.__name__ for fn in werkzeuge.WERKZEUGE}
    for fn in werkzeuge.WERKZEUGE:
        erwartet = set(inspect.signature(fn).parameters)
        assert set(tools[fn.__name__].inputSchema.get("properties", {})) == erwartet


def test_health_antwortet():
    mcp = server.bauen()
    with TestClient(mcp.streamable_http_app()) as c:
        r = c.get("/health")
    assert r.status_code == 200 and r.json() == {"status": "ok"}


def test_protokolliert_nur_warnungen():
    assert server.bauen().settings.log_level == "WARNING"
