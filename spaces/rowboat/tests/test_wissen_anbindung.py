"""Tests fuer die Rowboat-Anbindung der Bedeutungssuche (mcp.json-Eintrag, Disk-Skill)."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import yaml

ROWBOAT = Path(__file__).resolve().parents[1]
SKRIPT = ROWBOAT / "scripts" / "wissen_mcp_eintragen.py"
SKILL = ROWBOAT / "deploy" / "headless" / "skills" / "wissen-suchen" / "SKILL.md"
sys.path.insert(0, str(ROWBOAT / "scripts"))

import wissen_mcp_eintragen as wme  # noqa: E402

HEUTE = {"mcpServers": {"exa": {"type": "http", "url": "https://mcp.exa.ai/mcp"}}}


def test_eintragen_behaelt_andere_server():
    neu = wme.eintragen(json.loads(json.dumps(HEUTE)))
    assert neu["mcpServers"]["exa"] == HEUTE["mcpServers"]["exa"]
    assert neu["mcpServers"]["wissen"] == {"type": "http", "url": "http://127.0.0.1:3240/mcp"}
    assert wme.EINTRAG == {"type": "http", "url": "http://127.0.0.1:3240/mcp"}


def test_eintragen_ist_idempotent():
    einmal = wme.eintragen(json.loads(json.dumps(HEUTE)))
    zweimal = wme.eintragen(json.loads(json.dumps(einmal)))
    assert einmal == zweimal
    assert list(zweimal["mcpServers"]) == ["exa", "wissen"]


def test_eintragen_ohne_mcpservers_legt_an():
    assert wme.eintragen({}) == {"mcpServers": {"wissen": wme.EINTRAG}}


def _per_stdin(pfad: Path) -> subprocess.CompletedProcess:
    # So laeuft es auf der VM: ssh offload-vm 'python3 - <pfad>' < wissen_mcp_eintragen.py
    return subprocess.run([sys.executable, "-", str(pfad)], input=SKRIPT.read_bytes(),
                          capture_output=True, timeout=30)


def test_cli_schreibt_backup_einmal_und_atomar(tmp_path):
    config = tmp_path / "config" / "mcp.json"
    config.parent.mkdir()
    original = json.dumps(HEUTE, indent=2)
    config.write_text(original, encoding="utf-8")

    erg = _per_stdin(config)
    assert erg.returncode == 0, erg.stderr
    assert erg.stdout.decode().split() == ["exa", "wissen"]
    backup = config.with_name("mcp.json.bak-vor-wissen")
    assert backup.read_text(encoding="utf-8") == original
    daten = json.loads(config.read_text(encoding="utf-8"))
    assert set(daten["mcpServers"]) == {"exa", "wissen"}

    # Zweiter Lauf: Sicherung bleibt die Fassung VOR dem ersten Eintrag, Ergebnis unveraendert.
    vorher = config.read_text(encoding="utf-8")
    assert _per_stdin(config).returncode == 0
    assert backup.read_text(encoding="utf-8") == original
    assert config.read_text(encoding="utf-8") == vorher
    assert sorted(p.name for p in config.parent.iterdir()) == ["mcp.json", "mcp.json.bak-vor-wissen"]


def test_cli_ohne_datei_schreibt_nichts(tmp_path):
    erg = _per_stdin(tmp_path / "fehlt.json")
    assert erg.returncode != 0
    assert list(tmp_path.iterdir()) == []


def test_cli_kaputtes_json_bleibt_unberuehrt(tmp_path):
    config = tmp_path / "mcp.json"
    config.write_text("{kaputt", encoding="utf-8")
    assert _per_stdin(config).returncode != 0
    assert config.read_text(encoding="utf-8") == "{kaputt"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["mcp.json"]


def test_skript_nur_stdlib():
    text = SKRIPT.read_text(encoding="utf-8")
    for fremd in ("import yaml", "import requests", "from wissen_suche"):
        assert fremd not in text
    assert "os.replace" in text


def test_skill_frontmatter():
    text = SKILL.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    kopf = yaml.safe_load(text.split("---\n", 2)[1])
    assert kopf["name"] == "wissen-suchen"
    assert kopf["description"] == (
        "Bedeutungssuche im Rowboat-Wissen – IMMER zuerst nutzen, wenn nach Kunden, Kontakten, Themen, "
        "Interessen, Notizen oder 'was wissen wir über …' gefragt wird, besonders wenn die genauen Wörter "
        "unklar sind.")
    assert kopf["tools"] == ["listMcpTools", "executeMcpTool"]
    assert "wissen_suchen" in text and "wörtlich" in text
    assert 'serverName: "wissen"' in text and "file-readText" in text and "_archiv/" in text
    assert "file-grep" in text


def test_bauen_kopiert_skill():
    skript = (ROWBOAT / "deploy" / "headless" / "bauen.ps1").read_text(encoding="utf-8")
    assert "skills/wissen-suchen" in skript
    assert "~/rowboat-data/skills/wissen-suchen" in skript
