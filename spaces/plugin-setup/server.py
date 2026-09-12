"""plugin-setup MCP-Server -- Host-Sidecar auf 0.0.0.0:8131 (Aufgabe 6).

WARUM HOST-SIDECAR UND NICHT CONTAINER (Muster spaces/marketing/claw/
server.py): sowohl Rowboat als auch der ISOLIERTE OpenFang-Daemon dieses
Tasks (127.0.0.1:4273, eigener OPENFANG_HOME -- s. Global Constraints,
NIE :4200/~/.openfang/) binden Loopback; aus einem Container ist Loopback
des Hosts nicht ohne Weiteres erreichbar. Der Sidecar sitzt daneben und
bindet selbst 0.0.0.0, damit der openclaw-Container ihn ueber
host.docker.internal erreicht (s. config/openclaw.json).

`spaces/plugin-setup` traegt bewusst KEIN `__init__.py` und ist per
Bindestrich kein gueltiger Python-Modulname -- dieser Server (wie
werkzeuge.py, ablage.py, pruefung.py) wird darum als eigenstaendiges
Skript gestartet (`python server.py`), nicht als `-m spaces.plugin-setup...`.
"""
import os
import sys
from pathlib import Path

_HIER = Path(__file__).resolve().parent
if str(_HIER) not in sys.path:
    sys.path.insert(0, str(_HIER))

import anfragen  # noqa: E402
import fenster  # noqa: E402
import werkzeuge  # noqa: E402

REPO_ROOT = next((p for p in (_HIER, *_HIER.parents) if (p / "vibemind-os").is_dir()), _HIER)

# Schluessel wie die Nachbar-Sidecars: aus der repo-.env nachladen, nie
# ueberschreiben, kein Key-Material im Launcher-Skript. Siehe README.md
# in diesem Verzeichnis fuer, was jede Variable bedeutet.
_ENV_KEYS = ("ROWBOAT_URL", "ROWBOAT_API_KEY", "ROWBOAT_CATALOG_DIGEST",
             "PLUGIN_SETUP_OPENFANG_URL", "PLUGIN_SETUP_OPENFANG_API_KEY",
             "PLUGIN_SETUP_DB_CONTAINER")


def _load_env_fallback() -> None:
    env_file = REPO_ROOT / ".env"
    if not env_file.exists():
        return
    missing = [k for k in _ENV_KEYS if not os.environ.get(k)]
    if not missing:
        return
    for line in env_file.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        for k in missing:
            if line.startswith(k + "="):
                os.environ[k] = line.split("=", 1)[1].strip().strip('"').strip("'")


WERKZEUGE = (
    werkzeuge.plugin_bedarf,
    werkzeuge.eingabe_anfordern,
    werkzeuge.einrichtung_status,
    werkzeuge.plugin_installieren,
    werkzeuge.plugin_werkzeug_binden,
)

HOST = os.environ.get("PLUGIN_SETUP_MCP_HOST", "0.0.0.0")
PORT = int(os.environ.get("PLUGIN_SETUP_MCP_PORT", "8131"))


def main() -> None:
    _load_env_fallback()
    try:
        from mcp.server.mcpserver import MCPServer
        server = MCPServer("plugin-setup")
        for fn in WERKZEUGE:
            server.tool()(fn)
        server.run(transport="streamable-http", host=HOST, port=PORT)
    except ImportError:
        from mcp.server.fastmcp import FastMCP
        from starlette.responses import HTMLResponse
        server = FastMCP("plugin-setup", host=HOST, port=PORT)
        for fn in WERKZEUGE:
            server.tool()(fn)

        @server.custom_route("/fenster/{token}", methods=["GET"])
        async def _fenster_zeigen(request):
            a = anfragen.holen(request.path_params["token"])
            if a is None:
                return HTMLResponse("Link ungueltig.", status_code=404)
            return HTMLResponse(fenster.seite_fuer(a))

        @server.custom_route("/fenster/{token}", methods=["POST"])
        async def _fenster_annehmen(request):
            formular = await request.form()
            status, html = fenster.entgegennehmen(
                request.path_params["token"],
                str(formular.get("wert", "")),
                werkzeuge.schluessel_entgegennehmen,
            )
            return HTMLResponse(html, status_code=status)

        server.run(transport="streamable-http")


if __name__ == "__main__":
    main()
