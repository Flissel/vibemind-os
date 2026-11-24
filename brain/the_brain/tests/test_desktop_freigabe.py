"""Desktop ohne Rueckfrage, Herkunftspruefung getrennt schaltbar (core/desktop_freigabe.py)."""
from __future__ import annotations

from pathlib import Path

import pytest

from core.desktop_freigabe import (
    HERKUNFT, RUECKFRAGE, bestaetigung_noetig, desktop_herkunft, herkunft_pruefung_an, rueckfrage_an,
)

ROOT = Path(__file__).resolve().parents[3]


def test_standard_keine_rueckfrage_und_herkunftspruefung_an():
    assert rueckfrage_an({}) is False
    assert herkunft_pruefung_an({}) is True


@pytest.mark.parametrize("wert", ["aus", "AUS", " aus "])
def test_nur_aus_schaltet_die_herkunftspruefung_ab(wert):
    assert herkunft_pruefung_an({HERKUNFT: wert}) is False


@pytest.mark.parametrize("wert", ["", "0", "nein", "off", "false", "an"])
def test_andere_werte_lassen_die_herkunftspruefung_an(wert):
    assert herkunft_pruefung_an({HERKUNFT: wert}) is True


@pytest.mark.parametrize("herkunft", [None, "", "autonom", "gesprochen", "Getippt", "telegram"])
def test_fremde_herkunft_bleibt_draussen_solange_die_pruefung_an_ist(herkunft):
    assert desktop_herkunft(herkunft, {}) is None


def test_getippt_bleibt_getippt():
    assert desktop_herkunft("getippt", {}) == "getippt"


def test_ohne_pruefung_zaehlt_jede_herkunft():
    assert desktop_herkunft(None, {HERKUNFT: "aus"}) == "getippt"
    assert desktop_herkunft("telegram", {HERKUNFT: "aus"}) == "getippt"


def test_getippt_laeuft_ohne_bestaetigung():
    assert bestaetigung_noetig(True, False, "getippt", {}) is False


def test_fremde_herkunft_bekommt_durch_den_wegfall_der_rueckfrage_nichts_frei():
    assert bestaetigung_noetig(True, False, None, {}) is True
    assert bestaetigung_noetig(True, False, "autonom", {}) is True


def test_rueckfrage_1_holt_die_alte_pflicht_zurueck():
    umgebung = {RUECKFRAGE: "1"}
    assert bestaetigung_noetig(True, False, "getippt", umgebung) is True
    assert bestaetigung_noetig(True, True, "getippt", umgebung) is False


def test_ohne_pflicht_nie_warten():
    assert bestaetigung_noetig(False, False, None, {RUECKFRAGE: "1"}) is False


def test_der_plan_ausfuehrer_nutzt_beide_schalter():
    quelle = (ROOT / "brain/the_brain/core/plan_executor.py").read_text(encoding="utf-8")
    assert "bestaetigung_noetig(desktop_route.requires_confirmation, confirmed," in quelle
    assert 'desktop_herkunft(plan_ctx.get("eingabe_herkunft")) != "getippt"' in quelle
    assert 'herkunft=desktop_herkunft((plan_ctx or {}).get("eingabe_herkunft"))' in quelle
    # Die alte, starre Pruefung gibt es nicht mehr.
    assert "if desktop_route.requires_confirmation and not confirmed:" not in quelle


def test_plan_ausfuehrer_haelt_desktop_hop_ohne_herkunft_weiter_an(monkeypatch):
    """Ohne getippte Herkunft wartet der alte desktop_skill-Weg weiter auf Bestaetigung."""
    monkeypatch.delenv(RUECKFRAGE, raising=False)
    monkeypatch.delenv(HERKUNFT, raising=False)
    from core.plan_executor import PlanExecutor
    from core.plan_schema import HopSpec, Plan
    plan = Plan(plan_id="desktop-freigabe-test", intent="click the OK button", rationale="test",
                hops=[HopSpec(step_id="click", description="click the OK button", capability="desktop_skill",
                              execution_target="openfang:skill-coordinator")])
    hop = PlanExecutor().execute(plan)["executed"]["click"]
    assert hop["error"] == "confirmation required for mutating desktop event 'desktop.task'"
