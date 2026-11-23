"""Punkt 33: "Halt fest ... in der Bubble X" aus einem fremden Reiter landete auf der Ideen-Uebersicht,
weil keine Nachricht die Bubble der Karte trug. Ein fester Schreibaufruf nennt jetzt ``ziel``
({space, bubble_id, node_id}) in der Antwort und in plan_completed. Ohne Netz; Namen erfunden.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.ideen_abkuerzung import ziel_fuer_schritt  # noqa: E402
from core.plan_executor import _abschluss_ereignis, _ziel_des_plans  # noqa: E402
from core.plan_schema import HopResult, HopSpec, Plan  # noqa: E402

BUBBLE = "b0b1e001"
KARTE = "a0000009"
OFFEN = {"space": "ideas", "bubble": {"id": BUBBLE, "title": "Stadtfest"}}


def _mcp(obj) -> dict:
    return {"content": [{"type": "text", "text": json.dumps(obj)}]}


@pytest.mark.parametrize("capability,argumente,ergebnis,kontext,erwartet", [
    # Anlegen: Bubble aus den Argumenten, Karte aus dem Ergebnis.
    ("idea.create", {"bubble_id": BUBBLE, "title": "Preise"}, _mcp({"id": KARTE}), None, KARTE),
    # Anlegen mit von der App vergebener Kennung.
    ("idea.create", {"bubble_id": BUBBLE, "title": "Preise", "id": KARTE}, None, None, KARTE),
    # Aendern: die Karte aus den Argumenten, die Bubble aus dem Kontext.
    ("idea.update", {"id": KARTE, "content": "x"}, _mcp({"id": KARTE}), OFFEN, KARTE),
    ("idea.rate", json.dumps({"id": KARTE, "score": 50}), None, OFFEN, KARTE),
    # Bubble-weite Aufrufe: keine Karte.
    ("idea.arrange", {"bubble_id": BUBBLE}, _mcp({"ok": True}), None, None),
    ("bubble.rate", {"id": BUBBLE, "score": 70}, None, None, None),
])
def test_ziel_eines_schreibaufrufs(capability, argumente, ergebnis, kontext, erwartet) -> None:
    assert ziel_fuer_schritt(capability, True, argumente, ergebnis, kontext) == {
        "space": "ideas", "bubble_id": BUBBLE, "node_id": erwartet}


@pytest.mark.parametrize("capability,ok,argumente,kontext", [
    ("idea.list", True, {"bubble_id": BUBBLE}, OFFEN),            # liest nur
    ("idea.create", False, {"bubble_id": BUBBLE, "title": "x"}, OFFEN),  # nicht geklappt
    ("idea.update", True, {"id": KARTE, "content": "x"}, {"space": "ideas", "bubble": None}),  # Bubble unbekannt
    ("bubble_list", True, {}, OFFEN),                              # kein fester Ideen-Aufruf
])
def test_kein_ziel(capability, ok, argumente, kontext) -> None:
    assert ziel_fuer_schritt(capability, ok, argumente, None, kontext) is None


def _plan(capability: str, argumente: dict, bubble: str = "") -> Plan:
    plan = Plan(plan_id="ideen_1", intent="x", rationale="",
                hops=[HopSpec(step_id="s1", description="x", capability=capability,
                              arg_template=json.dumps(argumente))])
    plan.ziel_bubble_id = bubble
    return plan


def test_plan_completed_traegt_das_ziel() -> None:
    plan = _plan("idea.create", {"bubble_id": BUBBLE, "title": "Preise"})
    executed = {"s1": HopResult(step_id="s1", ok=True, result=_mcp({"id": KARTE}), capability="idea.create")}
    ziel = _ziel_des_plans(plan, True, executed)
    nutzlast = _abschluss_ereignis(plan.plan_id, True, {"elapsed_s": 0.4}, executed, ziel)
    assert nutzlast["ziel"] == {"space": "ideas", "bubble_id": BUBBLE, "node_id": KARTE}


def test_plan_completed_nimmt_die_bubble_des_satzes() -> None:
    plan = _plan("idea.rate", {"id": KARTE, "score": 50}, bubble=BUBBLE)
    executed = {"s1": HopResult(step_id="s1", ok=True, result=_mcp({"ok": True}), capability="idea.rate")}
    assert _ziel_des_plans(plan, True, executed) == {"space": "ideas", "bubble_id": BUBBLE, "node_id": KARTE}


def test_plan_completed_ohne_ziel_bei_fehler_und_fremden_plaenen() -> None:
    plan = _plan("idea.create", {"bubble_id": BUBBLE, "title": "Preise"})
    fehler = {"s1": HopResult(step_id="s1", ok=False, error="boom", capability="idea.create")}
    assert _ziel_des_plans(plan, False, fehler) is None
    assert "ziel" not in _abschluss_ereignis(plan.plan_id, False, {"elapsed_s": 0.4}, fehler, None)
    fremd = _plan("desktop_screenshot", {})
    gut = {"s1": HopResult(step_id="s1", ok=True, result={"x": 1}, capability="desktop_screenshot")}
    assert _ziel_des_plans(fremd, True, gut) is None
