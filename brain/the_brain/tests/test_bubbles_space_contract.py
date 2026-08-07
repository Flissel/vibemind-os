"""Canonical bubbles space and its legacy shuttles input alias."""

import asyncio
import types
from pathlib import Path

import yaml

from core.capability_targets import SupabaseExecutor
from core.capability_validator import CapabilityValidator
from core.intent_envelope import build_envelope
from core.supabase_ideas_client import SupabaseIdeasClient


ROOT = Path(__file__).resolve().parents[3]
CAPS_PATH = Path(__file__).resolve().parents[1] / "data" / "capabilities.yaml"


def _capability(name: str) -> dict:
    capabilities = yaml.safe_load(CAPS_PATH.read_text(encoding="utf-8"))
    return next(cap for cap in capabilities if cap["capability"] == name)


def test_registry_declares_shuttles_as_bubbles_input_alias_only():
    registry = yaml.safe_load(
        (ROOT / "config" / "space_agent_registry.yml").read_text(encoding="utf-8")
    )
    spaces = registry["spaces"]

    assert spaces["bubbles"]["aliases"] == ["shuttles"]
    assert "shuttles" not in spaces


def test_intent_envelope_normalizes_legacy_shuttles_override():
    envelope = build_envelope(
        event_id="bubble.list",
        params={},
        space_override="shuttles",
    )

    assert envelope["space"] == "bubbles"
    assert "shuttles" not in str(envelope)


class _PromoteClient:
    def __init__(self) -> None:
        self.promoted = []

    async def find_bubble_by_title(self, title):
        return {
            "id": "bubble-1",
            "title": title,
            "description": "Canonical bubble",
            "score": 82,
            "status": "scored",
        }

    async def get_idea(self, _idea_id):
        return None

    async def promote_bubble(self, bubble):
        self.promoted.append(bubble)
        return {"id": "project-1", "name": bubble["title"]}


def test_promote_uses_real_supabase_project_write_and_returns_queryable_id():
    from core.supabase_ideas_ops import bubble_promote_op

    client = _PromoteClient()

    result = asyncio.run(
        bubble_promote_op(client, {"bubble_name": "Launch Plan"})
    )

    assert client.promoted == [
        {
            "id": "bubble-1",
            "title": "Launch Plan",
            "description": "Canonical bubble",
            "score": 82,
            "status": "scored",
        }
    ]
    assert result == "Bubble 'Launch Plan' promoted to project (id=project-1)."


class _FailingPromoteClient(_PromoteClient):
    """promote_bubble rolls back and returns None — nothing was persisted."""

    async def promote_bubble(self, bubble):
        self.promoted.append(bubble)
        return None


def test_failed_promote_reports_failure_instead_of_success_shaped_string():
    """D1 (Zyklus-1-Befund 2026-08-01): a rolled-back promote must not read as
    success. The op returns a dict with ok=False so the executor can tell
    failure from success without parsing prose."""
    from core.supabase_ideas_ops import bubble_promote_op

    client = _FailingPromoteClient()

    result = asyncio.run(
        bubble_promote_op(client, {"bubble_name": "Launch Plan"})
    )

    assert isinstance(result, dict), (
        "failed promote must not return a bare string — the executor reads "
        "ok/error, and a string is treated as a successful result"
    )
    assert result["ok"] is False
    assert "Launch Plan" in result["error"]


def test_missing_bubble_promote_reports_failure():
    """The not-found branch uses the same contract as the failure branch."""
    from core.supabase_ideas_ops import bubble_promote_op

    class _NoBubbleClient(_PromoteClient):
        async def find_bubble_by_title(self, title):
            return None

    result = asyncio.run(
        bubble_promote_op(_NoBubbleClient(), {"bubble_name": "Ghost"})
    )

    assert isinstance(result, dict)
    assert result["ok"] is False


class _MissingPromoteClient:
    async def find_bubble_by_title(self, _title):
        return None

    async def get_idea(self, _idea_id):
        return None


def test_promote_not_found_returns_structured_failure_and_fails_the_hop():
    from core.supabase_ideas_ops import bubble_promote_op

    result = asyncio.run(
        bubble_promote_op(_MissingPromoteClient(), {"bubble_name": "Missing"})
    )

    assert result == {
        "ok": False,
        "error": "Bubble to promote not found.",
    }

    executor = SupabaseExecutor("supabase:bubble.promote")
    executor._call = lambda _payload: result
    hop = executor.call(bubble_name="Missing")

    assert hop["ok"] is False
    assert hop["result"] == result
    assert "op result indicates failure" in hop["error"]


def test_promote_is_wired_to_supabase_with_project_truth_validation():
    capability = _capability("bubble_promote")

    assert capability["execution_target"] == "supabase:bubble.promote"
    assert capability["validator"] == {
        "kind": "truth:supabase_row",
        "on_fail": "report",
        "postcondition": {
            "check": "supabase_row",
            "table": "projects",
            "match": "id=eq.{result_id}",
            "expect": "present",
        },
    }
    assert "bubble.promote" in SupabaseExecutor.OPERATIONS


def test_unresolved_promote_postcondition_is_unverified_but_not_valid(monkeypatch):
    import core

    monkeypatch.setattr(core, "world_observer", types.SimpleNamespace(), raising=False)
    verdict = CapabilityValidator().validate(
        _capability("bubble_promote")["validator"],
        intent="promote bubble Missing",
        arg="Missing",
        raw_result={"ok": False, "error": "Bubble to promote not found."},
    )

    assert verdict["verified"] is None
    assert verdict["valid"] is False
    assert verdict["reason"] == (
        "ground-truth UNVERIFIED: postcondition placeholder unresolved"
    )
    assert verdict["verify_signal"]["status"] == "unverified"


class _RecordingSupabaseClient(SupabaseIdeasClient):
    def __init__(self) -> None:
        super().__init__(url="http://supabase.invalid", anon_key="anon")
        self.requests = []

    async def _request(self, method, path, **kwargs):
        self.requests.append((method, path, kwargs))
        if method == "POST" and path == "/projects":
            return [{**kwargs["json"], "id": "project-1"}]
        if method == "PATCH" and path == "/ideas":
            return [{"id": "bubble-1", **kwargs["json"]}]
        raise AssertionError(f"unexpected request: {method} {path}")


def test_supabase_promotion_persists_project_then_links_canonical_bubble():
    client = _RecordingSupabaseClient()
    bubble = {
        "id": "bubble-1",
        "title": "Launch Plan",
        "description": "Canonical bubble",
        "score": 82,
    }

    project = asyncio.run(client.promote_bubble(bubble))

    assert project["id"] == "project-1"
    assert [(method, path) for method, path, _ in client.requests] == [
        ("POST", "/projects"),
        ("PATCH", "/ideas"),
    ]
    project_payload = client.requests[0][2]["json"]
    assert project_payload["from_idea_id"] == "bubble-1"
    assert project_payload["metadata"]["source_space"] == "bubbles"
    assert client.requests[1][2]["json"] == {
        "status": "promoted",
        "promoted_to_project_id": "project-1",
    }
