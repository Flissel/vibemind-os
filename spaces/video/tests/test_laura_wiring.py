"""Laura-Anbindung: Submodul-Pin und MCP-Entry-Point.

Billige Struktur-Tests, die Drift fangen: wandert der Pin oder verschwindet
der Entry-Point, laeuft der MCP-Server nicht mehr an und die 28 Tools sind weg.
"""
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
LAURA_DIR = REPO_ROOT / "spaces" / "video" / "laura"


def test_gitmodules_declares_laura():
    text = (REPO_ROOT / ".gitmodules").read_text(encoding="utf-8")
    assert 'path = spaces/video/laura' in text
    assert "Lauras_star" in text


def test_laura_mcp_entrypoint_declared():
    pyproject = LAURA_DIR / "services" / "mcp" / "pyproject.toml"
    assert pyproject.exists(), f"Laura-Submodul nicht ausgecheckt: {pyproject}"
    text = pyproject.read_text(encoding="utf-8")
    assert 'laura-mcp = "laura_mcp.server:main"' in text
