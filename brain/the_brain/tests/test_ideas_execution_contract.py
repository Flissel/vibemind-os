"""Truthful execution contracts for the Brain-owned Ideas path."""

import asyncio
import builtins
from pathlib import Path

import yaml

from core.capability_targets import SupabaseExecutor, result_indicates_failure
from core.capability_validator import CapabilityValidator
from core.space_routing_head import EVENT_SPACE_MAP
from core.supabase_ideas_ops import idea_to_project_op


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
            raise ImportError("world observer disabled")
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


def test_supabase_executor_registers_idea_to_project():
    executor = SupabaseExecutor("supabase:idea.to_project")
    assert executor.operation == "idea.to_project"


def test_idea_to_project_remains_owned_by_ideas_space():
    assert EVENT_SPACE_MAP["idea.to_project"] == "ideas"


class _ProjectClient:
    def __init__(self, *, readable=True):
        self.readable = readable
        self.patched = []

    async def find_bubble_by_title(self, title):
        return {"id": "idea-1", "title": title, "description": "Details"}

    async def get_idea(self, idea_id):
        if idea_id == "idea-1":
            return {"id": "idea-1", "title": "Launch", "description": "Details"}
        return None

    async def create_project_from_idea(self, idea):
        return {"id": "project-1", "name": idea["title"]}

    async def get_project(self, project_id):
        if self.readable and project_id == "project-1":
            return {"id": "project-1", "name": "Launch", "from_idea_id": "idea-1"}
        return None

    async def mark_idea_promoted(self, idea_id, project_id):
        self.patched.append((idea_id, project_id))
        return True


def test_to_project_returns_verified_result_and_links_source_idea():
    client = _ProjectClient()
    result = asyncio.run(idea_to_project_op(client, {"name": "Launch"}))
    assert result == {
        "ok": True,
        "project_id": "project-1",
        "idea_id": "idea-1",
        "name": "Launch",
        "verified": True,
    }
    assert client.patched == [("idea-1", "project-1")]


def test_to_project_fails_closed_when_project_cannot_be_read_back():
    client = _ProjectClient(readable=False)
    result = asyncio.run(idea_to_project_op(client, {"name": "Launch"}))
    assert result["ok"] is False
    assert result["verified"] is False
    assert client.patched == []


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
        "idea_update", "idea_delete", "idea_to_project",
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
