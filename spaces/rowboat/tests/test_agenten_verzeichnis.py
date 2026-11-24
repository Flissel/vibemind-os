"""VibeMind-Spaces fuer Rowboat: je aktivem Space mit Agent genau eine Lese-Karte, kein Rowboat-Agent."""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest
import yaml

_ROWBOAT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROWBOAT / "scripts"))

import agenten_verzeichnis as av  # noqa: E402

REGISTRY = yaml.safe_load(av.REGISTRY.read_text(encoding="utf-8"))
GESCHRIEBEN = av._bestand()
FORK = _ROWBOAT / "rowboat" / "apps" / "x"


def _kopf(text: str) -> dict:
    treffer = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    assert treffer, "YAML-Kopf fehlt"
    return yaml.safe_load(treffer.group(1))


def test_jeder_aktive_space_mit_agent_hat_genau_eine_karte_und_keine_karte_ohne_space():
    erwartet = {f"{sid}.md" for sid, spec in REGISTRY["spaces"].items()
                if spec.get("enabled") is True and spec.get("agent") and spec.get("rights_carrier") is not True}
    assert erwartet, "Registry ohne aktive Spaces?"
    assert set(GESCHRIEBEN) == erwartet
    for name, text in GESCHRIEBEN.items():
        kopf = _kopf(text)
        assert kopf["space"] == name.removesuffix(".md")
        assert kopf["agent"] == REGISTRY["spaces"][kopf["space"]]["agent"]


def test_ordner_ist_aktuell():
    assert av.erzeugen() == GESCHRIEBEN, "python spaces/rowboat/scripts/agenten_verzeichnis.py neu laufen lassen"


def test_karte_traegt_kein_feld_des_agenten_schemas_und_keine_adressen():
    for name, text in GESCHRIEBEN.items():
        kopf = _kopf(text)
        assert not set(kopf) & av.AGENTEN_FELDER, name
        assert set(kopf) <= {"space", "agent", "titel", "rolle", "stand", "beispiele", "geplant"}, name
        assert not re.search(r"https?://|\b\d{1,3}(\.\d{1,3}){3}\b|localhost|sk-[a-z0-9-]{8,}|api[_-]?key",
                             text, re.I), name


def test_loadagent_findet_keine_karte():
    """Rowboat liest Agenten nur aus WorkDir/agents (FSAgentsRepo); die Karten liegen woanders und
    taugen dort auch nicht: `name` ist im Agenten-Schema Pflicht, die Pruefung brach ab."""
    repo = (FORK / "packages" / "core" / "src" / "runtime" / "assembly" / "repo.ts").read_text(encoding="utf-8")
    assert 'path.join(WorkDir, "agents")' in repo
    assert re.search(r"glob\(\"\*\*/\*\.md\", \{ cwd: this\.agentsDir \}\)", repo)
    assert av.ZIELORDNER != "agents" and "/" not in av.ZIELORDNER
    schema = (FORK / "packages" / "shared" / "src" / "agent.ts").read_text(encoding="utf-8")
    assert re.search(r"^\s*name: z\.string\(\),", schema, re.M), "name ist im Agenten-Schema nicht mehr Pflicht?"
    for name, text in GESCHRIEBEN.items():
        assert "name" not in _kopf(text), name


def test_stand_laeuft_nur_mit_festem_weg_und_beispielen_sonst_geplant():
    for name, text in GESCHRIEBEN.items():
        kopf = _kopf(text)
        if kopf["space"] in av.LAUFENDE_SPACES:
            assert kopf["stand"] == "läuft"
            assert av.feste_wege(REGISTRY["spaces"][kopf["space"]]), kopf["space"]
            assert av.MIN_BEISPIELE <= len(kopf["beispiele"]) <= av.MAX_BEISPIELE
            assert "geplant" not in kopf
        else:
            assert kopf["stand"] == "noch nicht angeschlossen"
            assert "beispiele" not in kopf and kopf["geplant"].strip()


def test_ohne_agent_oder_ausgeschaltet_keine_karte():
    registry = {"spaces": {"a": {"agent": "brain-x", "enabled": False}, "b": {"agent": None, "enabled": True},
                           "coding": {"agent": "brain-coder", "enabled": True}}}
    assert list(av.karten(registry, {})) == ["coding.md"]


def test_rechtetraeger_bekommt_keine_karte():
    # Rechtetraeger (Spec 8.3): Ausweis und Rechte fuer feste Aufrufe, kein Agent, der Nachrichten bearbeitet.
    registry = {"spaces": {"recherche": {"agent": "brain-recherche", "enabled": True, "rights_carrier": True},
                           "coding": {"agent": "brain-coder", "enabled": True}}}
    assert list(av.karten(registry, {})) == ["coding.md"]


def test_ohne_deutsche_rolle_oder_geplant_bricht_der_erzeuger_ab():
    with pytest.raises(KeyError, match="neuer"):
        av.karten({"spaces": {"neuer": {"agent": "brain-neu", "enabled": True}}}, {})
    with pytest.raises(ValueError, match="ideas"):  # laeuft, aber ohne Beispielsaetze
        av.karten({"spaces": {"ideas": {"agent": "brain-ideas", "enabled": True}}}, {})


def test_beispielsaetze_deutsch_zuerst_ohne_technisches_und_ohne_nicht_laufende_wege():
    faehigkeiten = [
        {"capability": "a", "anchor_phrases": ["open the editor", "öffne den Editor"]},
        {"capability": "b", "enabled": False, "anchor_phrases": ["geheim"]},
        {"capability": "c", "anchor_phrases": ["ohne space"]},
        {"capability": "a", "anchor_phrases": ["research.web battery", "lies https://example.org",
                                               "was braucht Karte_Eins", "Desktop-Aufgabe: Öffne den Editor"]},
    ]
    saetze = av.saetze_je_space(faehigkeiten, lambda name, ziel: {"a": "desktop", "b": "desktop"}.get(name))
    assert saetze == {"desktop": ["öffne den Editor", "open the editor"]}
