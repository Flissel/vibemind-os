"""Agenten-MCP (:8130): Werkzeuge in Threads, /health, Protokoll nur Warnungen (22.10.2026, wie :8137)."""
import inspect

import anyio
from starlette.testclient import TestClient

from spaces.marketing.claw import server


def test_alle_werkzeuge_laufen_in_threads_und_behalten_ihr_schema():
    mcp = server.bauen()
    tools = {t.name: t for t in anyio.run(mcp.list_tools)}
    assert set(tools) == {fn.__name__ for fn in server.WERKZEUGE}
    for fn in server.WERKZEUGE:
        assert set(tools[fn.__name__].inputSchema.get("properties", {})) == set(inspect.signature(fn).parameters)
        assert inspect.iscoroutinefunction(mcp._tool_manager.get_tool(fn.__name__).fn)


def test_health_antwortet_und_rebinding_schutz_bleibt():
    mcp = server.bauen()
    assert mcp.settings.transport_security is not None
    with TestClient(mcp.streamable_http_app(), base_url="http://127.0.0.1:8130") as c:
        r = c.get("/health")
    assert r.status_code == 200 and r.json() == {"status": "ok"}


def test_protokolliert_nur_warnungen():
    assert server.bauen().settings.log_level == "WARNING"
