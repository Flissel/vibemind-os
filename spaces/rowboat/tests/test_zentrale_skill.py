"""Disk-Skill zentrale-werkzeuge (deploy/headless/skills/zentrale-werkzeuge/SKILL.md) fuer copilot."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import yaml

HEADLESS = Path(__file__).resolve().parents[1] / "deploy" / "headless"
SKILL = HEADLESS / "skills" / "zentrale-werkzeuge" / "SKILL.md"
ORDNER = HEADLESS / "werkzeuge"
sys.path.insert(0, str(ORDNER))

_spec = importlib.util.spec_from_file_location("werkzeuge_server_skill", ORDNER / "server.py")
server = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(server)

from pc_suche_relay import PC_AUS_MELDUNG  # noqa: E402


def _text() -> str:
    return SKILL.read_text(encoding="utf-8")


def _frontmatter() -> dict:
    return yaml.safe_load(_text().split("---")[1])


def test_frontmatter_und_tools():
    fm = _frontmatter()
    assert fm["name"] == "zentrale-werkzeuge"
    assert set(fm["tools"]) == {"listMcpTools", "executeMcpTool", "fetch-url", "todo-add", "file-readText",
                                "file-grep", "file-list", "file-writeText", "file-mkdir"}
    assert not ({"executeCommand", "addMcpServer"} & set(fm["tools"]))


def test_alle_werkzeugnamen_genannt():
    t = _text()
    for fn in server.WERKZEUGE:
        assert fn.__name__ in t, fn.__name__
    assert 'serverName: "vibemind-werkzeuge"' in t
    assert "web_search_exa" in t and "web_fetch_exa" in t


def test_skill_regel_ausserwirkung():
    t = _text()
    assert "Am Desktop:" in t and "POST" in t
    assert "todo-add" in t
    assert "addMcpServer" in t and "executeCommand" in t     # ausdruecklich verboten


def test_skill_nennt_pc_aus_woertlich():
    assert PC_AUS_MELDUNG in _text()


def test_skill_nennt_abgeschnitten():
    t = _text()
    assert "abgeschnitten" in t and "unvollständig" in t


def test_skill_docs_dashboard_telegram():
    t = _text()
    assert "knowledge/Kanal/Docs/" in t
    assert "höchstens 5 Seiten" in t
    # D4 (26.10.): Dashboards als Markdown-Notiz, telegramtauglich, Spiegel bringt sie an den PC
    assert "knowledge/Kanal/Dashboards/" in t and "Markdown-Tabellen" in t
    assert "loadSkill('apps')" not in t
    assert "3500 Zeichen" in t and "10 Einträge" in t
    assert "config/" in t


def test_description_triggerwoerter():
    beschreibung = _frontmatter()["description"]
    for wort in ("GitHub", "Repos", "Issues", "PRs", "Commits", "Docs", "Doku", "Dashboard",
                 "auf meinem PC suchen"):
        assert wort in beschreibung, wort


def test_bauen_kopiert_skill():
    skript = (HEADLESS / "bauen.ps1").read_text(encoding="utf-8")
    assert "skills/zentrale-werkzeuge" in skript
