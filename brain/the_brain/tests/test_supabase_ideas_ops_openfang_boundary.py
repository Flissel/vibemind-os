"""Contract tests for Ideas LLM calls through the shared OpenFang boundary."""

from __future__ import annotations

import asyncio
import importlib
import sys
import types
from pathlib import Path
from typing import Any

import pytest


BRAIN_ROOT = Path(__file__).resolve().parents[1]
MODULE_NAME = "core.supabase_ideas_ops"
ROLE = "brain_fast_reasoning"
MODEL = "openfang:brain-fallback"


class _SharedOpenFangUnavailable(RuntimeError):
    """Hermetic stand-in for the shared availability boundary."""


class _AsyncCompletions:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return types.SimpleNamespace(
            choices=[types.SimpleNamespace(
                message=types.SimpleNamespace(content="OpenFang response")
            )]
        )


def _load_ops(monkeypatch: pytest.MonkeyPatch, get_client: Any) -> Any:
    if str(BRAIN_ROOT) not in sys.path:
        sys.path.insert(0, str(BRAIN_ROOT))
    shared = types.ModuleType("vibemind_shared")
    shared.OpenFangUnavailable = _SharedOpenFangUnavailable
    shared.get_client = get_client
    shared.get_model = lambda role: MODEL if role == ROLE else None
    monkeypatch.setitem(sys.modules, "vibemind_shared", shared)
    sys.modules.pop(MODULE_NAME, None)
    return importlib.import_module(MODULE_NAME)


def test_llm_call_uses_configured_openfang_role_without_direct_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    completions = _AsyncCompletions()
    roles: list[str] = []
    client = types.SimpleNamespace(chat=types.SimpleNamespace(completions=completions))
    module = _load_ops(
        monkeypatch,
        lambda role: roles.append(role) or client,
    )

    result = asyncio.run(module._llm_call("route only through OpenFang", max_tokens=37))

    assert result == {"ok": True, "text": "OpenFang response"}
    assert roles == [ROLE]
    assert completions.calls == [{
        "model": MODEL,
        "messages": [{"role": "user", "content": "route only through OpenFang"}],
        "max_tokens": 37,
    }]


def test_llm_call_propagates_shared_openfang_unavailability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unavailable = _SharedOpenFangUnavailable("OpenFang unavailable")
    module = _load_ops(monkeypatch, lambda role: (_ for _ in ()).throw(unavailable))

    with pytest.raises(_SharedOpenFangUnavailable) as caught:
        asyncio.run(module._llm_call("must fail closed"))

    assert caught.value is unavailable
