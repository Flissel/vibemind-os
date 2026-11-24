"""Truthful execution contracts for the Brain-owned Ideas path."""

import builtins
from pathlib import Path

import yaml

from core.capability_targets import SupabaseExecutor, result_indicates_failure
from core.capability_validator import CapabilityValidator


class _UnverifiedTruth:
    verified_ok = None
    verdict = "UNVERIFIED"
    reason = "ground truth unavailable"
    signal = {"source": "test"}


def _truth_validator(postcondition=None):
    validator = {"kind": "truth:supabase_row", "on_fail": "report"}
    if postcondition is not None:
        validator["postcondition"] = postcondition
    return validator


def test_truth_without_postcondition_is_not_valid(monkeypatch):
    observed = []
    monkeypatch.setattr(
        "core.world_observer.observe",
        lambda postcondition: observed.append(postcondition) or _UnverifiedTruth(),
    )

    verdict = CapabilityValidator().validate(
        _truth_validator(), intent="promote bubble", arg="Launch", raw_result="done"
    )

    assert observed == [{"check": "supabase_row"}]
    assert verdict["verified"] is None
    assert verdict["valid"] is False


def test_truth_with_incomplete_postcondition_is_not_valid(monkeypatch):
    monkeypatch.setattr("core.world_observer.observe", lambda _pc: _UnverifiedTruth())

    verdict = CapabilityValidator().validate(
        _truth_validator({"check": "supabase_row"}),
        intent="promote bubble",
        arg="Launch",
        raw_result="done",
    )

    assert verdict["verified"] is None
    assert verdict["valid"] is False


def test_truth_with_ground_truth_disabled_is_not_valid(monkeypatch):
    monkeypatch.setattr("core.world_observer.GROUND_TRUTH_ENABLED", False)

    verdict = CapabilityValidator().validate(
        _truth_validator(
            {
                "check": "supabase_row",
                "table": "projects",
                "match": "id=eq.project-1",
                "expect": "present",
            }
        ),
        intent="promote bubble",
        arg="Launch",
        raw_result="done",
    )

    assert verdict["verified"] is None
    assert verdict["valid"] is False


def test_truth_with_world_observer_import_error_is_not_valid(monkeypatch):
    original_import = builtins.__import__

    def disabled_world_observer(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "core" and "world_observer" in fromlist:
            raise ImportError("world observer disabled: Bearer should-not-leak")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", disabled_world_observer)
    verdict = CapabilityValidator().validate(
        _truth_validator(
            {
                "check": "supabase_row",
                "table": "projects",
                "match": "id=eq.project-1",
                "expect": "present",
            }
        ),
        intent="promote bubble",
        arg="Launch",
        raw_result="done",
    )

    assert verdict["verified"] is None
    assert verdict["valid"] is False
    assert verdict["reason"] == "ground-truth UNVERIFIED: world_observer unavailable"
    assert verdict["verify_signal"] == {
        "status": "unverified",
        "reason": "world_observer unavailable",
    }
    assert verdict["kind"] == "truth:supabase_row"
    assert verdict["on_fail"] == "report"
    assert isinstance(verdict["elapsed_s"], float)
    assert "should-not-leak" not in str(verdict)


def test_truth_with_world_observer_runtime_error_is_unverified_envelope(monkeypatch):
    def observer_crash(_postcondition):
        raise RuntimeError("observer transport failed: Bearer should-not-leak")

    monkeypatch.setattr("core.world_observer.observe", observer_crash)

    verdict = CapabilityValidator().validate(
        _truth_validator(
            {
                "check": "supabase_row",
                "table": "projects",
                "match": "id=eq.project-1",
                "expect": "present",
            }
        ),
        intent="promote bubble",
        arg="Launch",
        raw_result="done",
    )

    assert verdict["valid"] is False
    assert verdict["verified"] is None
    assert verdict["reason"] == "ground-truth UNVERIFIED: world_observer observation failed"
    assert verdict["verify_signal"] == {
        "status": "unverified",
        "reason": "world_observer observation failed",
    }
    assert verdict["kind"] == "truth:supabase_row"
    assert verdict["on_fail"] == "report"
    assert isinstance(verdict["elapsed_s"], float)
    assert "should-not-leak" not in str(verdict)


def test_truth_verified_and_refuted_verdicts_remain_conclusive(monkeypatch):
    class _VerifiedTruth:
        verified_ok = True
        verdict = "VERIFIED"
        reason = "row present"
        signal = {"rows_found": 1}

    class _RefutedTruth:
        verified_ok = False
        verdict = "REFUTED"
        reason = "row missing"
        signal = {"rows_found": 0}

    config = _truth_validator(
        {
            "check": "supabase_row",
            "table": "projects",
            "match": "id=eq.project-1",
            "expect": "present",
        }
    )
    monkeypatch.setattr("core.world_observer.observe", lambda _pc: _VerifiedTruth())
    verified = CapabilityValidator().validate(
        config, intent="promote bubble", arg="Launch", raw_result="done"
    )
    monkeypatch.setattr("core.world_observer.observe", lambda _pc: _RefutedTruth())
    refuted = CapabilityValidator().validate(
        config, intent="promote bubble", arg="Launch", raw_result="done"
    )

    assert verified["verified"] is True
    assert verified["valid"] is True
    assert refuted["verified"] is False
    assert refuted["valid"] is False


def test_idea_error_strings_are_executor_failures():
    for result in (
        "Idea 'Missing' not found.",
        "'A' and 'B' are not connected.",
        "Nothing to update on 'A' (no content/new_title given).",
        "No previous format to revert 'A' to.",
        "'A' cannot be connected to itself.",
    ):
        assert result_indicates_failure(result), result


def test_idea_to_project_is_retired_everywhere():
    """24.11.2026: ein Projekt entsteht nur ueber bubble.promote mit Pruefung und Rueckfrage.

    Kein Supabase-Weg, kein Planer-Ziel im Verzeichnis, keine Faehigkeit mit Ziel.
    """
    import pytest

    from core.space_contract import load_space_contract
    from core.supabase_ideas_client import SupabaseIdeasClient

    # Als Text gelesen: supabase_ideas_ops braucht vibemind_shared (nur im Container).
    ops = (Path(__file__).resolve().parents[1] / "core" / "supabase_ideas_ops.py").read_text(encoding="utf-8")
    with pytest.raises(ValueError):
        SupabaseExecutor("supabase:idea.to_project")
    assert "async def idea_to_project_op" not in ops
    assert not hasattr(SupabaseIdeasClient, "create_project_from_idea")
    assert "idea.to_project" not in load_space_contract().event_space_map
    caps = yaml.safe_load((Path(__file__).resolve().parents[1] / "data" / "capabilities.yaml")
                          .read_text(encoding="utf-8"))
    assert not next(c for c in caps if c["capability"] == "idea_to_project").get("execution_target")


def test_required_truth_rejects_unverified_observation(monkeypatch):
    class _Verdict:
        verified_ok = None
        verdict = "UNVERIFIED"
        reason = "Supabase unavailable"
        signal = {"source": "supabase"}

    monkeypatch.setattr("core.world_observer.observe", lambda _pc: _Verdict())
    validator = CapabilityValidator()
    result = validator.validate(
        {
            "kind": "truth:supabase_row",
            "on_fail": "block",
            "require_verified": True,
            "postcondition": {
                "check": "supabase_row",
                "table": "projects",
                "match": "id=eq.project-1",
                "expect": "present",
            },
        },
        intent="promote idea",
        arg="Launch",
        raw_result={"project_id": "project-1"},
    )
    assert result["valid"] is False
    assert result["verified"] is None


def test_mutating_idea_truth_validators_are_fail_closed():
    path = Path(__file__).resolve().parents[1] / "data" / "capabilities.yaml"
    capabilities = yaml.safe_load(path.read_text(encoding="utf-8"))
    required = {
        "idea_add", "idea_connect", "idea_disconnect", "idea_auto_link",
        "idea_update", "idea_delete",
    }
    required.update(c["capability"] for c in capabilities
                    if c.get("execution_target") == "supabase:idea.format"
                    and str((c.get("validator") or {}).get("kind", "")).startswith("truth:"))
    mutations = {c["capability"]: c for c in capabilities
                 if c.get("capability") in required}
    assert mutations.keys() == required
    for capability in mutations.values():
        validator = capability["validator"]
        assert validator.get("on_fail") == "block", capability["capability"]
        assert validator.get("require_verified") is True, capability["capability"]
    assert mutations["idea_update"]["validator"]["postcondition"]["table"] == "canvas_nodes"
    assert mutations["idea_delete"]["validator"]["postcondition"]["table"] == "canvas_nodes"
