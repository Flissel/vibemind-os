"""Feste Aufrufe des Ideen-Space im Plan-Ausfuehrer (laedt plan_executor, also torch)."""

from __future__ import annotations

from pathlib import Path
import copy
import json
import sys

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "brain" / "the_brain"))

from core.capability_targets import McpExecutor, resolve_registry_execution_target
from core.capability_validator import CapabilityValidator
from core.plan_executor import PlanExecutor
from core.plan_schema import HopSpec
from core import world_observer


def _quittung(inhalt):
    return {"content": [{"type": "text", "text": json.dumps(inhalt)}], "isError": False}


class _Executor:
    def __init__(self, result=None) -> None:
        self.calls = []
        self.result = {} if result is None else result

    def call_with_arg(self, arg, arg_kwarg=None, extra_params=None):
        self.calls.append((arg, arg_kwarg))
        return {"ok": True, "result": self.result}

    def is_resolvable(self) -> bool:
        return True


class _Validator:
    def __init__(self, *, verified=True, raises=False) -> None:
        self.calls = []
        self.verified = verified
        self.raises = raises

    def validate(self, config, **kwargs):
        self.calls.append((copy.deepcopy(config), kwargs.get("arg")))
        if self.raises:
            raise RuntimeError("sensitive validator failure")
        return {"valid": self.verified is True, "kind": config["kind"],
                "on_fail": "block", "verified": self.verified}


def _hop(event_id: str, argumente, **kwargs) -> HopSpec:
    return HopSpec(
        step_id="s1", description=event_id, capability=event_id,
        arg_template=argumente if isinstance(argumente, str) else json.dumps(argumente), **kwargs,
    )


def _lauf(monkeypatch, hop, *, executor=None, validator=None):
    built = []
    executor = executor or _Executor(_quittung([{"id": "n1"}]))
    monkeypatch.setattr("core.plan_executor.PlanExecutor._capture_kg_hits", lambda *args: [])
    monkeypatch.setattr("core.capability_targets.build_executor",
                        lambda target: built.append(target) or executor)
    result = PlanExecutor(validator=validator)._exec_hop(hop, {})
    return result, built, executor


@pytest.mark.parametrize("event_id, agent, werkzeug", [
    ("idea.create", "brain-ideas", "idea_create"),
    ("idea.update", "brain-ideas", "idea_update"),
    ("idea.list", "brain-ideas", "idea_list"),
    ("idea.format", "brain-ideas", "idea_format"),
    ("idea.arrange", "brain-ideas", "idea_arrange"),
    ("idea.auto_link", "brain-ideas", "idea_auto_link"),
    ("idea.rate", "brain-ideas", "idea_rate"),
    ("idea.rate_reason", "brain-ideas", "bewertung_begruenden"),
    ("bubble.rate", "brain-bubbles", "bubble_rate"),
    ("bubble.promote", "brain-bubbles", "bubble_promote"),
])
def test_verzeichnis_bindet_jedes_ereignis_an_den_ideen_server(event_id, agent, werkzeug) -> None:
    assert resolve_registry_execution_target(event_id) == f"mcp:{agent}:spaces-ideas:{werkzeug}"


def test_aelterer_weg_bleibt_unveraendert() -> None:
    assert resolve_registry_execution_target("idea.connect") == "mcp:brain-ideas:spaces-ideas:idea_connect"
    assert resolve_registry_execution_target("idea.aenderung_zurueck") == (
        "mcp:brain-ideas:spaces-ideas:aenderung_zurueck")
    assert resolve_registry_execution_target("bubble.create") == "mcp:brain-bubbles:spaces-ideas:bubble_create"


def test_schreibender_schritt_schickt_nur_kennungen_und_wird_nachgeprueft(monkeypatch) -> None:
    validator = _Validator()
    argumente = {"id": "n1", "score": 80}

    result, built, executor = _lauf(
        monkeypatch, _hop("idea.rate", argumente, execution_target="supabase:idea.rate", arg_kwarg="idea_name"),
        validator=validator)

    assert result.ok is True
    assert built == ["mcp:brain-ideas:spaces-ideas:idea_rate"]
    assert executor.calls == [(argumente, None)]
    assert validator.calls == [({
        "kind": "truth:supabase_ideen_fest", "on_fail": "block", "require_verified": True,
        "postcondition": {"check": "supabase_ideen_fest", "ereignis": "idea.rate"},
    }, argumente)]


def test_bubble_ereignis_laeuft_im_namen_des_bubble_agenten(monkeypatch) -> None:
    result, built, executor = _lauf(
        monkeypatch, _hop("bubble.promote", {"bubble_id": "bub1"}), validator=_Validator(),
        executor=_Executor(_quittung({"id": "p1", "from_idea_id": "bub1"})))

    assert result.ok is True
    assert built == ["mcp:brain-bubbles:spaces-ideas:bubble_promote"]
    assert executor.calls == [({"bubble_id": "bub1"}, None)]


@pytest.mark.parametrize("event_id, argumente", [
    ("idea.rate", "Preise"),                                   # Name statt Kennung
    ("idea.rate", {"id": "", "score": 80}),                    # leere Kennung
    ("idea.rate", {"idea_name": "Preise", "score": 80}),
    ("idea.format", {"id": "n1"}),
    ("bubble.promote", ""),
    ("idea.create", {"title": "Preise"}),
])
def test_ohne_kennung_gibt_es_eine_rueckfrage_und_keinen_aufruf(monkeypatch, event_id, argumente) -> None:
    result, built, executor = _lauf(monkeypatch, _hop(event_id, argumente), validator=_Validator())

    assert result.ok is False
    assert result.error.startswith("clarification required")
    assert built == [] and executor.calls == []


def test_schreiben_ohne_pruefer_wird_nicht_ausgefuehrt(monkeypatch) -> None:
    result, built, _ = _lauf(monkeypatch, _hop("idea.rate", {"id": "n1", "score": 80}), validator=None)

    assert result.ok is False
    assert result.error == "canonical idea.rate validator unavailable"
    assert built == []


def test_lesen_braucht_keinen_pruefer(monkeypatch) -> None:
    result, built, executor = _lauf(
        monkeypatch, _hop("idea.list", {"bubble_id": "bub1"}), validator=None,
        executor=_Executor(_quittung([{"id": "n1", "title": "Preise"}])))

    assert result.ok is True
    assert built == ["mcp:brain-ideas:spaces-ideas:idea_list"]
    assert executor.calls == [({"bubble_id": "bub1"}, None)]


@pytest.mark.parametrize("validator, meldung", [
    (_Validator(verified=False), "validator blocked: canonical idea.rate truth unverified"),
    (_Validator(verified=None), "validator blocked: canonical idea.rate truth unverified"),
    (_Validator(raises=True), "validator blocked: canonical idea.rate validator error"),
])
def test_nicht_bestaetigter_zustand_blockiert_den_schritt(monkeypatch, validator, meldung) -> None:
    result, _, executor = _lauf(monkeypatch, _hop("idea.rate", {"id": "n1", "score": 80}), validator=validator)

    assert len(executor.calls) == 1
    assert result.ok is False
    assert result.error == meldung
    assert "sensitive" not in str(result.error)


def test_pruefer_liest_den_zustand_mit_den_kennungen_der_quittung(monkeypatch) -> None:
    gesehen = []

    def beobachte(pc):
        gesehen.append(pc)
        return type("V", (), {"verified_ok": True, "verdict": "VERIFIED", "reason": "ok", "signal": {}})()

    monkeypatch.setattr(world_observer, "observe", beobachte)
    verdict = CapabilityValidator().validate(
        {"kind": "truth:supabase_ideen_fest", "on_fail": "block",
         "postcondition": {"check": "supabase_ideen_fest", "ereignis": "idea.update"}},
        intent="x", arg={"id": "n1", "title": "Neu {mit Klammer}"},
        raw_result=_quittung([{"id": "n1"}]),
    )

    assert verdict["valid"] is True and verdict["verified"] is True
    assert gesehen == [{"check": "supabase_ideen_fest", "erwartet": {
        "art": "zeile", "tabelle": "canvas_nodes", "id": "n1", "felder": {"title": "Neu {mit Klammer}"},
    }}]


def test_pruefer_bestaetigt_nichts_bei_fehlertext_in_der_quittung(monkeypatch) -> None:
    monkeypatch.setattr(world_observer, "observe", lambda pc: pytest.fail("darf nicht lesen"))
    verdict = CapabilityValidator().validate(
        {"kind": "truth:supabase_ideen_fest", "on_fail": "block",
         "postcondition": {"check": "supabase_ideen_fest", "ereignis": "idea.rate"}},
        intent="x", arg={"id": "n1", "score": 80}, raw_result=_quittung({"error": "idea_not_found"}),
    )

    assert verdict["valid"] is False and verdict["verified"] is None


def test_ausfuehrer_haengt_die_plan_kennung_an_die_neuen_schreibenden_werkzeuge() -> None:
    for werkzeug in ("idea_format", "idea_arrange", "idea_auto_link", "idea_rate", "bewertung_begruenden"):
        executor = McpExecutor(f"mcp:brain-ideas:spaces-ideas:{werkzeug}")
        assert executor._mit_aenderung_id({"id": "n1"}, "plan_7")["aenderung_id"] == "plan_7"
    assert "aenderung_id" not in McpExecutor("mcp:brain-ideas:spaces-ideas:idea_list")._mit_aenderung_id({}, "plan_7")
    assert "aenderung_id" not in McpExecutor("mcp:brain-ideas:spaces-ideas:idea_connect")._mit_aenderung_id(
        {"from_id": "a", "to_id": "b"}, "plan_7")
