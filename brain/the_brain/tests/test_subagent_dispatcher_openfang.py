"""Contract tests for the OpenFang-only subagent dispatcher."""

from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path
from typing import Any

import pytest


BRAIN_ROOT = Path(__file__).resolve().parents[1]
if str(BRAIN_ROOT) not in sys.path:
    sys.path.insert(0, str(BRAIN_ROOT))


ROUTES = {
    "claude_subagent": ("brain_planning", "openfang:brain-planner"),
    "groq_subagent": ("brain_fast_reasoning", "openfang:brain-fallback"),
    "openai_subagent": ("brain_communication", "openfang:brain-writer"),
    "ollama_subagent": ("local_fast", "openfang:assistant"),
}
MODELS_BY_ROLE = {role: agent for role, agent in ROUTES.values()}


class _SyncCompletions:
    def __init__(self, text: str = "completed") -> None:
        self.text = text
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return types.SimpleNamespace(
            choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=self.text))]
        )


class _AsyncCompletions:
    def __init__(self, text: str = "completed") -> None:
        self.text = text
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return types.SimpleNamespace(
            choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=self.text))]
        )


def _client(completions: Any) -> Any:
    return types.SimpleNamespace(
        chat=types.SimpleNamespace(completions=completions)
    )


@pytest.fixture
def dispatcher_module(monkeypatch: pytest.MonkeyPatch) -> Any:
    shared = types.ModuleType("vibemind_shared")
    class SharedOpenFangUnavailable(RuntimeError):
        pass

    shared.OpenFangUnavailable = SharedOpenFangUnavailable
    shared.get_client = lambda role: None
    shared.get_client_sync = lambda role: None
    shared.get_model = lambda role: ROUTES[role][1]
    monkeypatch.setitem(sys.modules, "vibemind_shared", shared)
    sys.modules.pop("core.subagent_dispatcher", None)
    return importlib.import_module("core.subagent_dispatcher")


@pytest.mark.parametrize(("tool_name", "role", "agent"), [
    (tool_name, role, agent) for tool_name, (role, agent) in ROUTES.items()
])
def test_dispatch_routes_legacy_aliases_through_openfang_factory(
    dispatcher_module: Any,
    monkeypatch: pytest.MonkeyPatch,
    tool_name: str,
    role: str,
    agent: str,
) -> None:
    completions = _SyncCompletions()
    factory_roles: list[str] = []

    def get_client_sync(actual_role: str) -> Any:
        factory_roles.append(actual_role)
        return _client(completions)

    monkeypatch.setattr(dispatcher_module, "get_client_sync", get_client_sync)
    monkeypatch.setattr(dispatcher_module, "get_model", lambda actual_role: MODELS_BY_ROLE[actual_role])
    dispatcher = dispatcher_module.SubagentDispatcher(llm_router=object())

    result = dispatcher.dispatch(
        tool_name,
        prompt="route this request",
        system="system context",
        max_tokens=73,
        temperature=0.25,
    )

    assert result["ok"] is True
    assert result["tool"] == tool_name
    assert result["model"] == agent
    assert isinstance(result["latency_ms"], float)
    assert result["error"] is None
    assert factory_roles == [role]
    assert completions.calls == [{
        "model": agent,
        "messages": [
            {"role": "system", "content": "system context"},
            {"role": "user", "content": "route this request"},
        ],
        "max_tokens": 73,
        "temperature": 0.25,
    }]


@pytest.mark.asyncio
async def test_adispatch_uses_async_openfang_factory(
    dispatcher_module: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    completions = _AsyncCompletions()
    factory_roles: list[str] = []

    def get_client(actual_role: str) -> Any:
        factory_roles.append(actual_role)
        return _client(completions)

    monkeypatch.setattr(dispatcher_module, "get_client", get_client)
    monkeypatch.setattr(dispatcher_module, "get_model", lambda role: MODELS_BY_ROLE[role])
    dispatcher = dispatcher_module.SubagentDispatcher(llm_router=object())

    result = await dispatcher.adispatch(
        "openai_subagent",
        prompt="write this",
        model="legacy-openai-model",
        provider="legacy-openai-provider",
    )

    assert result["ok"] is True
    assert result["model"] == "openfang:brain-writer"
    assert isinstance(result["latency_ms"], float)
    assert result["error"] is None
    assert factory_roles == ["brain_communication"]
    assert completions.calls[0]["model"] == "openfang:brain-writer"


@pytest.mark.parametrize("override", [
    {"model": "direct-provider-model"},
    {"provider": "direct-provider"},
])
def test_dispatch_ignores_direct_provider_or_model_overrides(
    dispatcher_module: Any,
    monkeypatch: pytest.MonkeyPatch,
    override: dict[str, str],
) -> None:
    completions = _SyncCompletions()
    factory_roles: list[str] = []

    def get_client_sync(role: str) -> Any:
        factory_roles.append(role)
        return _client(completions)

    monkeypatch.setattr(dispatcher_module, "get_client_sync", get_client_sync)
    monkeypatch.setattr(dispatcher_module, "get_model", lambda role: MODELS_BY_ROLE[role])
    dispatcher = dispatcher_module.SubagentDispatcher(llm_router=object())

    result = dispatcher.dispatch("claude_subagent", prompt="do not bypass", **override)

    assert result["ok"] is True
    assert result["model"] == "openfang:brain-planner"
    assert factory_roles == ["brain_planning"]
    assert completions.calls[0]["model"] == "openfang:brain-planner"


def test_dispatch_provider_failure_keeps_measured_latency(
    dispatcher_module: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    time_values = iter([1.0, 2.0, 2.125])

    def failing_create(**kwargs: Any) -> Any:
        raise RuntimeError("OpenFang completion failed")

    client = _client(types.SimpleNamespace(create=failing_create))
    monkeypatch.setattr(dispatcher_module.time, "time", lambda: next(time_values))
    monkeypatch.setattr(dispatcher_module, "get_client_sync", lambda role: client)
    monkeypatch.setattr(dispatcher_module, "get_model", lambda role: MODELS_BY_ROLE[role])
    dispatcher = dispatcher_module.SubagentDispatcher(llm_router=object())

    result = dispatcher.dispatch("claude_subagent", prompt="measure failure")

    assert result["ok"] is False
    assert result["error"] == "RuntimeError: OpenFang completion failed"
    assert result["latency_ms"] == 125.0


def test_dispatch_logs_and_reraises_openfang_unavailability(
    dispatcher_module: Any,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    shared_unavailable = sys.modules["vibemind_shared"].OpenFangUnavailable
    unavailable = shared_unavailable("OpenFang unavailable")

    def unavailable_client(role: str) -> Any:
        raise unavailable

    monkeypatch.setattr(dispatcher_module, "get_client_sync", unavailable_client)
    monkeypatch.setattr(dispatcher_module, "get_model", lambda role: MODELS_BY_ROLE[role])
    dispatcher = dispatcher_module.SubagentDispatcher(llm_router=object())

    with pytest.raises(shared_unavailable, match="OpenFang unavailable"):
        dispatcher.dispatch("claude_subagent", prompt="must not fall back")

    assert "OpenFang unavailable" in caplog.text
