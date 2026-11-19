"""Jede Prüfregel, die eine Fähigkeit nennt, muss es auch geben.

Zweimal ist das schon schiefgegangen: `string_nonempty` und `dict_nonempty`
standen in `capabilities.yaml`, ohne registriert zu sein. Der Prüfer meldet
dann bei jedem gelungenen Schritt „unknown rule" und damit „ungültig".
"""

from pathlib import Path

import pytest
import yaml

from core.capability_validator import RULES, CapabilityValidator

KATALOG = Path(__file__).resolve().parents[1] / "data" / "capabilities.yaml"


def _genannte_regeln():
    eintraege = yaml.safe_load(KATALOG.read_text(encoding="utf-8"))
    if isinstance(eintraege, dict):
        eintraege = eintraege.get("capabilities", [])
    genannt = {}
    for eintrag in eintraege:
        if not isinstance(eintrag, dict):
            continue
        art = (eintrag.get("validator") or {}).get("kind") or ""
        if art.startswith("rule:"):
            genannt.setdefault(art.split(":", 1)[1], []).append(eintrag.get("capability"))
    return genannt


def test_katalog_nennt_regeln():
    assert _genannte_regeln(), "capabilities.yaml nennt keine einzige Regel: Lesefehler?"


def test_jede_genannte_regel_ist_registriert():
    fehlend = {
        regel: faehigkeiten
        for regel, faehigkeiten in _genannte_regeln().items()
        if regel not in RULES
    }
    assert fehlend == {}


RESEARCH_ERGEBNIS = {
    "operation": "web",
    "content": "Feststoffakkus erreichen 2026 die Serie.",
    "sources": ["https://example.org/akku"],
}


def test_dict_nonempty_nimmt_gefuelltes_dict_an():
    urteil = CapabilityValidator().validate(
        {"kind": "rule:dict_nonempty", "on_fail": "report"},
        intent="research.web Akku", arg="Akku", raw_result=RESEARCH_ERGEBNIS,
    )
    assert urteil["valid"] is True
    assert urteil["error"] is None


@pytest.mark.parametrize("ergebnis", [{}, None, "", "Text statt Dict", ["a"], 0])
def test_dict_nonempty_weist_alles_andere_ab(ergebnis):
    urteil = CapabilityValidator().validate(
        {"kind": "rule:dict_nonempty", "on_fail": "report"},
        intent="research.web Akku", arg="Akku", raw_result=ergebnis,
    )
    assert urteil["valid"] is False
    assert urteil["error"] is None, "abgewiesen wird von der Regel, nicht wegen eines Fehlers"
