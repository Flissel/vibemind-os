"""Welche OpenFang-Agenten im Repo koennten die Werkzeuge `mcp_auftragsbuch_*` sehen?

OpenFang (openfang-kernel, kernel.rs, Werkzeugliste je Agent): Ein Agent mit leerem oder fehlendem
`mcp_servers` sieht alle verbundenen Werkzeug-Server, ausser er traegt `no_mcp = true`. Ist dazu
`capabilities.tools` leer (oder enthaelt `*`), sieht er auch alle ihre Werkzeuge. Die Sperre je
Auftragsart in OpenFang gilt also nur fuer die Identitaet `brain-sales`.

Dieser Test haelt die heutige Menge fest, damit ein neuer Agent mit offenem Zugang auffaellt
(Runbook Stufe 3 prueft dasselbe im Betrieb). Vorlagen (`agent.toml.tmpl`) zaehlen nicht.
Ohne Submodul openfang wird er uebersprungen.
"""
from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

AGENTEN = Path(__file__).resolve().parents[3] / "openfang" / "agents"

# Leeres/fehlendes mcp_servers ohne no_mcp = true (Stand 22.11.).
OFFENER_SERVERZUGANG = {
    "analyst", "approval-handler", "architect", "assistant", "capability-gap-filer", "code-reviewer",
    "coder", "customer-support", "data-scientist", "debugger", "devops-lead", "doc-writer",
    "email-assistant", "error-monitor", "health-tracker", "hello-world", "home-automation",
    "learning-course-factory", "legal-assistant", "marketing-sender", "meeting-assistant", "ops",
    "orchestrator", "personal-finance", "planner", "recruiter", "researcher", "sales-assistant",
    "security-auditor", "skill-curator", "social-media", "test-engineer", "translator",
    "travel-planner", "tutor", "writer",
}
# Davon mit leerem capabilities.tools (oder `*`): sehen alle vier mcp_auftragsbuch_* (Stand 22.11.).
SIEHT_ALLE_WERKZEUGE = {"capability-gap-filer", "learning-course-factory"}

pytestmark = pytest.mark.skipif(not AGENTEN.is_dir(), reason="Submodul openfang fehlt")


def _manifeste() -> dict[str, dict]:
    return {p.parent.name: tomllib.loads(p.read_text(encoding="utf-8"))
            for p in sorted(AGENTEN.glob("*/agent.toml"))}


def _offen(m: dict) -> bool:
    return not m.get("no_mcp", False) and not m.get("mcp_servers")


def _alle_werkzeuge(m: dict) -> bool:
    werkzeuge = m.get("capabilities", {}).get("tools") or []
    return not werkzeuge or "*" in werkzeuge


def test_menge_der_agenten_mit_offenem_mcp_zugang_ist_festgehalten() -> None:
    manifeste = _manifeste()
    assert len(manifeste) >= 50
    offen = {n for n, m in manifeste.items() if _offen(m)}
    assert offen == OFFENER_SERVERZUGANG, (
        f"neu offen: {sorted(offen - OFFENER_SERVERZUGANG)}, nicht mehr offen: {sorted(OFFENER_SERVERZUGANG - offen)}"
        " - ein neuer Agent mit offenem Zugang sieht auch das Auftragsbuch (Runbook Stufe 3 melden)")


def test_diese_agenten_saehen_alle_werkzeuge_des_auftragsbuchs() -> None:
    manifeste = _manifeste()
    sieht = {n for n, m in manifeste.items() if _offen(m) and _alle_werkzeuge(m)}
    assert sieht == SIEHT_ALLE_WERKZEUGE


def test_nur_brain_sales_nennt_werkzeuge_des_auftragsbuchs_ausdruecklich() -> None:
    nennen = {n: sorted(w for w in m.get("capabilities", {}).get("tools") or [] if w.startswith("mcp_auftragsbuch_"))
              for n, m in _manifeste().items()}
    nennen = {n: w for n, w in nennen.items() if w}
    assert nennen == {"brain-sales": ["mcp_auftragsbuch_auftrag_lesen", "mcp_auftragsbuch_auftrag_probe"]}
    assert _manifeste()["brain-sales"]["mcp_servers"] == ["auftragsbuch"]


# Traeger, die das Auftragsbuch sehen duerfen (Lieferung Block 3: `allowed_agents` am Server `auftragsbuch`).
ERLAUBTE_TRAEGER = {"brain-sales", "brain-rowboat"}


@pytest.mark.xfail(reason="Lieferung Block 3", strict=True)
def test_nur_erlaubte_traeger_sehen_alle_werkzeuge_des_auftragsbuchs() -> None:
    """Bleibt rot (xfail), bis die Koordination die harte Sperre geliefert hat und `capability-gap-filer` sowie
    `learning-course-factory` (leere Werkzeugliste, offener Zugang) nicht mehr durchrutschen; danach `xfail` entfernen."""
    manifeste = _manifeste()
    sieht = {n for n, m in manifeste.items() if _offen(m) and _alle_werkzeuge(m)}
    assert sieht <= ERLAUBTE_TRAEGER, f"sehen alle Werkzeuge, ohne Traeger zu sein: {sorted(sieht - ERLAUBTE_TRAEGER)}"
