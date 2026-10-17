"""Freigabeliste des Marketing-Shims (claw/shim/marketing-mcp.json, --strict-mcp-config): was darf der
Agent aufrufen? Ohne laufende Dienste pruefbar.

Befund 15.10.2026 (STATUS.md, Bekannte Luecken): die zwei Newsletter-Bild-Werkzeuge des Sidecars fehlten
in der Liste (der Agent konnte sie nie rufen), und Lauras schreibende Werkzeuge waren ohne Freigabe-
Schritt erlaubt. Seit 17.10. liest der Agent Laura nur; Schreiben/Rendern kommt gezielt ueber eigene
Werkzeuge mit Freigabe."""
import json
import pathlib
import re

from spaces.marketing.claw.shim import laura_probe

HIER = pathlib.Path(__file__).resolve().parents[1]
FREIGABE = json.loads((HIER / "shim" / "marketing-mcp.json").read_text(encoding="utf-8"))["allowedTools"]


def _sidecar_werkzeuge() -> set[str]:
    return set(re.findall(r"^\s+werkzeuge\.([a-z_]+),", (HIER / "server.py").read_text(encoding="utf-8"), re.M))


def test_jedes_sidecar_werkzeug_ist_freigegeben():
    fehlend = {w for w in _sidecar_werkzeuge() if f"mcp__marketing__{w}" not in FREIGABE}
    assert not fehlend, fehlend
    assert {"newsletter_bildplaetze", "newsletter_bild_beauftragen"} <= _sidecar_werkzeuge()


def test_keine_toten_marketing_eintraege():
    tot = {t for t in FREIGABE if t.startswith("mcp__marketing__")
           and t.removeprefix("mcp__marketing__") not in _sidecar_werkzeuge()}
    assert not tot, tot


def test_laura_nur_lesend():
    laura = {t.removeprefix("mcp__laura__") for t in FREIGABE if t.startswith("mcp__laura__")}
    assert laura == laura_probe.LESEND
    assert not laura & (laura_probe.SCHREIBEND | laura_probe.VERBOTEN)
