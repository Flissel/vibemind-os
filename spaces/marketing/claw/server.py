"""marketing-claw MCP-Server — Host-Sidecar auf 0.0.0.0:8130.

WARUM HOST-SIDECAR UND NICHT CONTAINER: Marketing-API (:5510) und
Claude-Shim (:8114) binden loopback; aus einem Container ist loopback
des Hosts nicht erreichbar. Der Sidecar sitzt daneben (wie die drei
Bubble-Sidecars) und bindet selbst 0.0.0.0, damit der openclaw-Container
ihn ueber host.docker.internal erreicht.

API-Notiz (gemessen 02.09.2026): Host-Python traegt die 1.x-Reihe des
mcp-Pakets (FastMCP, host/port im Konstruktor); der sales-Container hat
2.0 (MCPServer, host/port in run()). Beide Zweige stehen hier, der
passende gewinnt zur Laufzeit.
"""
import os
import sys
from pathlib import Path

# Startbar sowohl als Modul (python -m spaces.marketing.claw.server) als
# auch als Datei — der Launcher nutzt die Modulform, PKG_ROOT wie ueberall.
PKG_ROOT = next(p.parent for p in Path(__file__).resolve().parents if p.name == "spaces")
if str(PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(PKG_ROOT))

from spaces.marketing.claw import werkzeuge  # noqa: E402

REPO_ROOT = next((p for p in (PKG_ROOT, *PKG_ROOT.parents)
                  if (p / "vibemind-os").is_dir()), PKG_ROOT)

# Schluessel wie die Nachbar-Sidecars: aus der repo-.env nachladen, nie
# ueberschreiben, kein Key-Material im Launcher-Skript.
_ENV_KEYS = ("MARKETING_API_KEY", "MARKETING_PROPOSAL_API_KEY",
             "ROWBOAT_URL", "ROWBOAT_PROJECT_ID", "ROWBOAT_API_KEY")


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
    werkzeuge.statistik,
    werkzeuge.kampagne_entwerfen,
    werkzeuge.ad_texte_entwerfen,
    werkzeuge.layout_entwerfen,
    werkzeuge.publikum_vorschlagen,
    werkzeuge.posteingang_lesen,
    werkzeuge.kampagnen_auflisten,
    # Wissensbasis-Passthrough (Rowboat, nur lesend) — im Sidecar statt als
    # zweiter MCP-Server im Gateway: Container erreichen im Mirrored-Modus
    # kein LAN, und der Bearer-Schluessel bleibt so im Host-Prozess.
    werkzeuge.wissensquellen,
    werkzeuge.wissensquelle,
    werkzeuge.dokumente,
)

HOST = os.environ.get("MARKETING_CLAW_MCP_HOST", "0.0.0.0")
PORT = int(os.environ.get("MARKETING_CLAW_MCP_PORT", "8130"))


def main() -> None:
    _load_env_fallback()
    try:
        from mcp.server.mcpserver import MCPServer
        server = MCPServer("marketing-claw")
        for fn in WERKZEUGE:
            server.tool()(fn)
        server.run(transport="streamable-http", host=HOST, port=PORT)
    except ImportError:
        from mcp.server.fastmcp import FastMCP
        server = FastMCP("marketing-claw", host=HOST, port=PORT)
        for fn in WERKZEUGE:
            server.tool()(fn)
        server.run(transport="streamable-http")


if __name__ == "__main__":
    main()
