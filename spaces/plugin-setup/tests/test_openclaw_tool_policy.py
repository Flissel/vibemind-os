"""`config/openclaw.json`s Tool-Policy IST der Schreibweg, den `server.py`s
Moduldoku zitiert -- "der Schreibweg ist durch KONFIGURATION verschlossen,
nicht durch STRUKTUR". Genau darum braucht diese Konfiguration einen Test:
ohne einen, aendert sich `tools.deny` (oder `browser.enabled`) unbemerkt,
und der Satz in `server.py` bleibt stehen, obwohl er nicht mehr stimmt --
dieselbe Klasse von Luecke wie die, die dieser Test schliesst (Review
Runde 3, Fix-Runde 3, Kritisch #1: "die Tuer hat ein Schloss, aber
niemand prueft, ob der Schluessel noch drin steckt").
"""
from __future__ import annotations

import json
from pathlib import Path

_KONFIG_PFAD = Path(__file__).resolve().parents[1] / "config" / "openclaw.json"

# Deckt sich mit server.py's Moduldoku UND dem geharteten Baseline-Beispiel
# in openclaw's eigener Doku (docs/gateway/security/index.md,
# "Hardened baseline in 60 seconds": deny group:automation/group:runtime/
# group:fs + einzelne sessions_*-Tools). Wir denyen group:sessions
# vollstaendig statt einzelner Tools darin -- dieser Agent braucht KEIN
# Session-/Subagent-Werkzeug, ein Teildeny waere hier nur groesser als
# noetig, nicht sicherer.
_ERFORDERLICHE_DENY_GRUPPEN = {
    "group:runtime",     # exec/process/code_execution -- die Shell selbst.
    "group:fs",          # read/write/edit/apply_patch.
    "group:web",         # web_search/x_search/web_fetch.
    "group:ui",          # browser/canvas.
    "group:automation",  # heartbeat_respond/cron/gateway. Denied wegen
                          # `cron` (zeitgesteuerte Turns) und `gateway`s
                          # Neustart-/`update.run`-Flaeche -- NICHT weil
                          # `gateway` `tools.deny` per `config.patch`
                          # umschreiben koennte: Fix-Runde 3 hatte genau das
                          # behauptet und dabei openclaw's Prosa-Doku als
                          # kurze Denyliste gelesen; gegen den kompilierten
                          # Code (2026.7.1) ist `ALLOWED_GATEWAY_CONFIG_PATHS`
                          # tatsaechlich eine 18-Muster-ALLOWLIST und nichts
                          # unter `tools.` passt darauf -- diese Eskalation
                          # existiert in diesem Image nicht. Korrigiert in
                          # Fix-Runde 4; die Allowlist ist eine Eigenschaft
                          # dieser Image-Version, kein Vertrag, den wir
                          # kontrollieren, darum bleibt die Gruppe trotzdem
                          # denied. S. server.py fuer die volle Herleitung.
    "group:sessions",     # sessions_spawn/subagents u.a. -- kein Bedarf.
}


def _konfig() -> dict:
    return json.loads(_KONFIG_PFAD.read_text(encoding="utf-8"))


def test_tools_deny_enthaelt_alle_erforderlichen_gruppen():
    deny = set(_konfig().get("tools", {}).get("deny", []))
    fehlend = _ERFORDERLICHE_DENY_GRUPPEN - deny
    assert not fehlend, f"config/openclaw.json: tools.deny fehlen: {sorted(fehlend)}"


def test_browser_bleibt_ueberall_deaktiviert():
    """Der zweite, unabhaengige Teil derselben Zusicherung (Review Runde 3,
    Fix-Runde 1): der Agent oeffnet nichts, weil er kein Browser-Werkzeug
    hat -- an BEIDEN Stellen, an denen dieses Image den Browser schaltet."""
    konfig = _konfig()
    assert konfig.get("browser", {}).get("enabled") is False
    assert konfig.get("plugins", {}).get("entries", {}).get("browser", {}).get("enabled") is False
