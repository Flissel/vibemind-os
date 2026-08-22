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


def test_openfang_template_declares_laura_mcp():
    """Die versionierte Vorlage muss den laura-Eintrag tragen.

    Wirksam ist ~/.openfang/config.toml (channel_bridge.rs:1814 loest
    home_dir/config.toml auf) — die Vorlage haelt ihn reproduzierbar.
    """
    import tomllib
    toml_path = REPO_ROOT / "openfang" / "openfang.vibemind.toml"
    data = tomllib.loads(toml_path.read_text(encoding="utf-8"))
    servers = {s.get("name"): s for s in data.get("mcp_servers", [])}
    assert "laura" in servers, f"laura fehlt; vorhanden: {sorted(servers)}"
    laura = servers["laura"]
    assert laura["transport"]["type"] == "stdio"
    # Nur der NAME der Variable gehoert in die Config, nie der Wert
    assert laura.get("env") == ["LAURA_TOKEN"]
