"""Desktop frei, ein Hauptschalter (core/desktop_freigabe.py), Vorgabe des Betreibers 24.11.2026."""
from __future__ import annotations

from pathlib import Path

import pytest

from core.desktop_freigabe import (
    FREI, GETIPPT, RUECKFRAGE, ZUGELASSEN, bestaetigung_noetig, desktop_herkunft, rueckfrage_an,
)

ROOT = Path(__file__).resolve().parents[3]


def test_standard_ist_frei():
    assert rueckfrage_an({}) is False


@pytest.mark.parametrize("wert", ["", "0", "nein", "aus", "true", "an", " 2 "])
def test_nur_1_schaltet_den_hauptschalter_ein(wert):
    assert rueckfrage_an({RUECKFRAGE: wert}) is False
    assert rueckfrage_an({RUECKFRAGE: " 1 "}) is True


@pytest.mark.parametrize("herkunft", [None, "", "autonom", "gesprochen", "telegram", "Getippt"])
def test_frei_laeuft_jede_herkunft_als_frei(herkunft):
    assert desktop_herkunft(herkunft, {}) == FREI
    assert desktop_herkunft(herkunft, {}) in ZUGELASSEN


def test_getippt_bleibt_getippt_auch_mit_hauptschalter():
    assert desktop_herkunft("getippt", {}) == GETIPPT
    assert desktop_herkunft("getippt", {RUECKFRAGE: "1"}) == GETIPPT


@pytest.mark.parametrize("herkunft", [None, "autonom", "telegram"])
def test_mit_hauptschalter_braucht_die_aufgabe_wieder_getippt(herkunft):
    assert desktop_herkunft(herkunft, {RUECKFRAGE: "1"}) is None


@pytest.mark.parametrize("herkunft", [None, "getippt", "autonom"])
def test_frei_braucht_keine_bestaetigung(herkunft):
    assert bestaetigung_noetig(True, False, herkunft, {}) is False


@pytest.mark.parametrize("herkunft", [None, "getippt", "autonom"])
def test_hauptschalter_holt_die_bestaetigungspflicht_zurueck(herkunft):
    umgebung = {RUECKFRAGE: "1"}
    assert bestaetigung_noetig(True, False, herkunft, umgebung) is True
    assert bestaetigung_noetig(True, True, herkunft, umgebung) is False


@pytest.mark.parametrize("kanal", [{"art": "telegram"}, {"art": " Telegram "}])
@pytest.mark.parametrize("herkunft", [None, "getippt", "autonom"])
def test_von_aussen_startet_nie_eine_desktop_aufgabe(kanal, herkunft):
    assert desktop_herkunft(herkunft, {}, kanal=kanal) is None
    assert desktop_herkunft(herkunft, {RUECKFRAGE: "1"}, kanal=kanal) is None


@pytest.mark.parametrize("kanal", [None, {}, {"art": "api"}, {"art": "dashboard"}, "telegram", {"art": 5}])
def test_eigene_kanaele_bleiben_frei(kanal):
    assert desktop_herkunft(None, {}, kanal=kanal) == FREI


def test_von_aussen_braucht_bestaetigung_auch_ohne_hauptschalter():
    aussen = {"art": "telegram"}
    assert bestaetigung_noetig(True, False, None, {}, kanal=aussen) is True
    assert bestaetigung_noetig(True, True, None, {}, kanal=aussen) is False
    assert bestaetigung_noetig(False, False, None, {}, kanal=aussen) is False
    assert bestaetigung_noetig(True, False, None, {}, kanal={"art": "api"}) is False


def test_von_aussen_haelt_der_plan_ausfuehrer_den_desktop_hop_an(monkeypatch):
    monkeypatch.delenv(RUECKFRAGE, raising=False)
    from core.plan_executor import PlanExecutor
    hop = PlanExecutor().execute(_klick_hop(), antwortkanal={"art": "telegram"})["executed"]["click"]
    assert hop["error"] == "confirmation required for mutating desktop event 'desktop.task'"


def test_ohne_pflicht_nie_warten():
    assert bestaetigung_noetig(False, False, None, {RUECKFRAGE: "1"}) is False


def test_es_gibt_nur_noch_einen_schalter():
    quelle = (ROOT / "brain/the_brain/core/desktop_freigabe.py").read_text(encoding="utf-8")
    assert "VIBEMIND_DESKTOP_HERKUNFT" not in quelle


def test_der_plan_ausfuehrer_nutzt_den_hauptschalter():
    quelle = (ROOT / "brain/the_brain/core/plan_executor.py").read_text(encoding="utf-8")
    assert "bestaetigung_noetig(desktop_route.requires_confirmation, confirmed," in quelle
    assert quelle.count('kanal=plan_ctx.get("antwortkanal")') == 2
    assert 'kanal=(plan_ctx or {}).get("antwortkanal"))' in quelle
    assert 'desktop_herkunft(plan_ctx.get("eingabe_herkunft"),' in quelle
    assert 'herkunft=desktop_herkunft((plan_ctx or {}).get("eingabe_herkunft"),' in quelle
    assert "spur=self._desktop_spur(plan_ctx)" in quelle
    assert "if desktop_route.requires_confirmation and not confirmed:" not in quelle


def _klick_hop():
    from core.plan_schema import HopSpec, Plan
    return Plan(plan_id="desktop-freigabe-test", intent="click the OK button", rationale="test",
                hops=[HopSpec(step_id="click", description="click the OK button", capability="desktop_skill",
                              execution_target="openfang:skill-coordinator")])


def test_mit_hauptschalter_haelt_der_plan_ausfuehrer_den_desktop_hop_an(monkeypatch):
    monkeypatch.setenv(RUECKFRAGE, "1")
    from core.plan_executor import PlanExecutor
    hop = PlanExecutor().execute(_klick_hop())["executed"]["click"]
    assert hop["error"] == "confirmation required for mutating desktop event 'desktop.task'"


@pytest.mark.parametrize(("satz", "auftrag"), [
    ("Öffne Chrome", "Öffne Chrome"),
    ("  öffne   chrome  ", "öffne chrome"),
    ("Desktop-Aufgabe: Öffne Chrome", "Öffne Chrome"),
    ("", None),
    ("x" * 501, None),
    (None, None),
])
def test_ueber_desktop_skill_ist_der_ganze_satz_der_auftrag(satz, auftrag):
    from core.desktop_schleife_contract import auftrag_aus_satz
    assert auftrag_aus_satz(satz) == auftrag


@pytest.mark.parametrize(("schritt", "name", "ziel"), [
    ({"action": "click", "x": 10, "y": 20}, "Adressleiste", "click 'Adressleiste' @10,20"),
    ({"action": "type", "text": "geheim123"}, None, "type (9 Zeichen)"),
    ({"action": "key", "key": "enter"}, None, "key enter"),
    ({"action": "hotkey", "keys": ["ctrl", "s"]}, None, "hotkey ctrl+s"),
    ({"action": "scroll", "direction": "down", "amount": 3}, None, "scroll down 3"),
])
def test_das_protokoll_nennt_das_ziel_ohne_getippten_text(schritt, name, ziel):
    from core.desktop_schleife_contract import protokoll_ziel
    assert protokoll_ziel(schritt, name) == ziel
    assert "geheim" not in protokoll_ziel(schritt, name)


def test_jede_aktion_landet_in_log_und_spur(caplog):
    from core import desktop_schleife
    zeilen = []
    with caplog.at_level("INFO", logger="core.desktop_schleife"):
        zeile = desktop_schleife._protokolliere(
            "dt-1", {"action": "click", "x": 5, "y": 6}, [{"name": "Speichern", "x": 5, "y": 6}], True,
            zeilen.append, "frei")
    assert zeilen == [zeile]
    for teil in ("agent=brain-desktop", "werkzeug=desktop_task_step", "aufgabe=dt-1",
                 "ziel=click 'Speichern' @5,6", "ok=ja", "herkunft=frei"):
        assert teil in zeile
    assert zeile[:4].isdigit() and zeile[10] == "T"
    assert any("[desktop] aktion" in r.getMessage() for r in caplog.records)


def test_eine_kaputte_spur_stoert_die_aufgabe_nicht():
    from core import desktop_schleife

    def kaputt(_zeile):
        raise RuntimeError("weg")
    desktop_schleife._protokolliere("dt-1", {"action": "key", "key": "tab"}, [], None, kaputt, "getippt")


def test_die_spur_des_plan_ausfuehrers_haengt_an_der_trace_id():
    from core.plan_executor import PlanExecutor

    class Recorder:
        def __init__(self):
            self.stufen = []

        def append_stage(self, *args):
            self.stufen.append(args)

    pe = PlanExecutor(recorder=Recorder())
    assert pe._desktop_spur({}) is None
    pe._desktop_spur({"trace_id": "t-7"})("zeile")
    assert pe.recorder.stufen == [("t-7", "desktop", "brain-desktop/desktop_task_step", "zeile")]
