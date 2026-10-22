"""Video-Claw MCP-Server - Host-Sidecar 127.0.0.1:8137 (17.10.2026).

Nur fuer die eigene Shim-Instanz (:8118, video-mcp.json); kein Container braucht ihn, darum nur
loopback (anders als der Marketing-Sidecar :8130). Schluessel (Laura-Token, Arbeiter-Schluessel) aus
Vibemind_V1/.env nachladen, nie ueberschreiben - sie bleiben im Host-Prozess.
"""
import os
import sys
from pathlib import Path

PKG_ROOT = next(p.parent for p in Path(__file__).resolve().parents if p.name == "spaces")
if str(PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(PKG_ROOT))

from spaces.marketing.claw.mcp_huelle import gesundheit_anmelden, im_thread  # noqa: E402,F401
from spaces.marketing.video_claw import werkzeuge  # noqa: E402

REPO_ROOT = next((p for p in (PKG_ROOT, *PKG_ROOT.parents) if (p / "vibemind-os").is_dir()), PKG_ROOT)
_ENV_KEYS = ("LAURA_API_URL", "LAURA_TOKEN", "MARKETING_BILD_URL", "MARKETING_BILD_KEY")
HOST = "127.0.0.1"
PORT = int(os.environ.get("VIDEO_CLAW_MCP_PORT", "8137"))


def _load_env_fallback() -> None:
    datei = REPO_ROOT / ".env"
    fehlend = [k for k in _ENV_KEYS if not os.environ.get(k)]
    if not fehlend or not datei.exists():
        return
    for zeile in datei.read_text(encoding="utf-8", errors="replace").splitlines():
        for k in fehlend:
            if zeile.strip().startswith(k + "=") and not os.environ.get(k):
                os.environ[k] = zeile.split("=", 1)[1].strip().strip('"').strip("'")


def bauen():
    """Der Server mit allen Werkzeugen (in Threads), /health fuer den Waechter, Protokoll nur Warnungen."""
    from mcp.server.fastmcp import FastMCP

    # log_level WARNING: kein rich-Protokoll je Anfrage mehr (dort hing 21.10. der Hauptthread).
    server = FastMCP("video-claw", host=HOST, port=PORT, log_level="WARNING")
    for fn in werkzeuge.WERKZEUGE:
        server.tool()(im_thread(fn))
    gesundheit_anmelden(server)
    return server


def main() -> None:
    _load_env_fallback()
    bauen().run(transport="streamable-http")


if __name__ == "__main__":
    main()
