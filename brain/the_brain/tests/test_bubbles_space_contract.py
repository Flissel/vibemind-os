"""Canonical bubbles space and its legacy shuttles input alias."""

from pathlib import Path

import yaml

from core.capability_targets import SupabaseExecutor
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


def test_old_direct_promotion_is_retired():
    """24.11.2026: befoerdert wird nur ueber den festen Weg (Pruefung, Rueckfrage, Aenderungs-Kennung).

    Der direkte Supabase-Weg (bubble_promote_op / promote_bubble) ist weg; die Faehigkeit
    bubble_promote hat kein Ziel mehr und sagt warum.
    """
    # Als Text gelesen: supabase_ideas_ops braucht vibemind_shared (nur im Container).
    ops = (CAPS_PATH.parents[1] / "core" / "supabase_ideas_ops.py").read_text(encoding="utf-8")

    capability = _capability("bubble_promote")
    assert not capability.get("execution_target")
    assert "not implemented" in capability["description"].lower()
    assert "bubble.promote" not in SupabaseExecutor.OPERATIONS
    assert "async def bubble_promote_op" not in ops
    assert not hasattr(SupabaseIdeasClient, "promote_bubble")
