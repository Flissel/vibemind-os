"""bubble.evaluate laeuft nie als Agenten-Auftrag (Lauf 7 der Pruefung). Namen erfunden.

Befund: Eine Urteilsfrage fiel auf dem alten Stand ueber den Planer auf bubble.evaluate, wurde
ein Auftrag an brain-bubbles (Space "agentfarm"), lief 78 s, buchte und scheiterte am fehlenden
Werkzeug. "agentfarm" kam aus dem Rueckfall im Plan-Ausfuehrer fuer Faehigkeiten ohne Space.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import plan_executor as pe  # noqa: E402
from core.ideen_abkuerzung import loese, urteilsfrage  # noqa: E402

KONTEXT = {"space": "ideas", "bubble": {"id": "b0b1e001", "title": "Stadtfest"},
           "cards": [{"id": "a0000001", "title": "Sponsoren"}]}


@pytest.mark.parametrize("satz", ["Bewerte diese Bubble", "Bewerte die Bubble", "Ist diese Bubble reif?",
                                  "Ist die Bubble schon reif für die Umsetzung?", "Was fehlt noch?",
                                  "Welche Risiken siehst du hier?", "Was hältst du von dieser Bubble?"])
def test_urteilsfragen_laufen_als_einschaetzen(satz) -> None:
    assert urteilsfrage(satz)
    treffer = loese(satz, KONTEXT)
    assert treffer["ereignis"] == "idea.summarize" and treffer["argumente"] == {
        "bubble_id": "b0b1e001", "einschaetzen": True}


def test_bewerten_mit_zahl_bleibt_der_feste_satz() -> None:
    assert loese("Bewerte diese Bubble mit 70", KONTEXT)["ereignis"] == "bubble.rate"


@pytest.mark.parametrize("faehigkeit", ["bubble_evaluate", "bubble.evaluate"])
def test_plan_kann_bubble_evaluate_nicht_als_auftrag_stellen(monkeypatch, faehigkeit) -> None:
    from core.plan_schema import HopSpec

    gebaut, angelegt = [], []

    class _Router:
        def get_capability(self, _capability):
            return {"execution_target": "openfang:brain-bubbles"}

    monkeypatch.setattr("core.plan_executor.PlanExecutor._capture_kg_hits", lambda *args: [])
    monkeypatch.setattr("core.capability_targets.build_executor", lambda target: gebaut.append(target))
    monkeypatch.setattr("core.agent_auftraege.tabelle_aus_umgebung", lambda: angelegt.append(1))
    hop = HopSpec(step_id="s1", description="Urteil per Plan", capability=faehigkeit,
                  execution_target="openfang:brain-bubbles", arg_template=json.dumps({"bubble_name": "Stadtfest"}))
    ergebnis = pe.PlanExecutor(capability_router=_Router())._exec_hop(
        hop, {}, plan_ctx={"antwortkanal": {"art": "api"}, "plan_id": "p1", "trace_id": "t1"})
    assert ergebnis.ok is False and ergebnis.error.startswith("bubble.evaluate: kein Agentenweg")
    assert gebaut == [] and angelegt == []


def test_space_eines_auftrags_kommt_aus_dem_verzeichnis() -> None:
    assert pe._space_des_ereignisses("bubble.evaluate") == "bubbles"
    assert pe._space_des_ereignisses("idea.expand") == "ideas"
    assert pe._space_des_ereignisses("gibt.es_nicht") is None
    assert pe._ereignis_von("bubble_evaluate") == "bubble.evaluate"
