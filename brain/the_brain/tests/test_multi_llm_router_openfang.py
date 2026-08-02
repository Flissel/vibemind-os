"""Contract tests for the fail-closed OpenFang MultiLLMRouter boundary."""

from __future__ import annotations

import asyncio
import importlib
import inspect
import sys
import types
from pathlib import Path
from typing import Any

import pytest


BRAIN_ROOT = Path(__file__).resolve().parents[1]
if str(BRAIN_ROOT) not in sys.path:
    sys.path.insert(0, str(BRAIN_ROOT))


MODELS = {
    "brain_fast_reasoning": "openfang:brain-fallback",
    "brain_planning": "openfang:brain-planner",
    "brain_context_tracking": "openfang:brain-knowledge",
    "brain_communication": "openfang:brain-writer",
    "brain_long_term_memory": "openfang:brain-knowledge",
}


class _SyncCompletions:
    def __init__(self, text: str) -> None:
        self.text = text
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return types.SimpleNamespace(
            choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=self.text))]
        )


class _AsyncCompletions:
    def __init__(self, text: str) -> None:
        self.text = text
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return types.SimpleNamespace(
            choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=self.text))]
        )


def _client(completions: Any) -> Any:
    return types.SimpleNamespace(chat=types.SimpleNamespace(completions=completions))


@pytest.fixture
def router_module(monkeypatch: pytest.MonkeyPatch) -> Any:
    shared = types.ModuleType("vibemind_shared")

    class SharedOpenFangUnavailable(RuntimeError):
        pass

    shared.OpenFangUnavailable = SharedOpenFangUnavailable
    shared.get_client = lambda role: None
    shared.get_client_sync = lambda role: None
    shared.get_model = lambda role: MODELS[role]
    monkeypatch.setitem(sys.modules, "vibemind_shared", shared)
    sys.modules.pop("core.multi_llm_router", None)
    return importlib.import_module("core.multi_llm_router")


def test_route_uses_openfang_role_model_and_ignores_legacy_override(
    router_module: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    completions = _SyncCompletions("sync response")
    roles: list[str] = []

    def get_client_sync(role: str) -> Any:
        roles.append(role)
        return _client(completions)

    monkeypatch.setattr(router_module, "get_client_sync", get_client_sync)
    router = router_module.MultiLLMRouter(
        openrouter_api_key="legacy-key", enable_infinite_chat=False
    )

    result = router.route(
        "question_generation", "write a response", model="legacy-direct-model"
    )

    assert result == "sync response"
    assert roles == ["brain_communication"]
    assert completions.calls == [{
        "model": "openfang:brain-writer",
        "messages": [{"role": "user", "content": "write a response"}],
        "max_tokens": 800,
        "temperature": 0.8,
    }]


def test_aroute_uses_async_openfang_factory(router_module: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    completions = _AsyncCompletions("async response")
    roles: list[str] = []

    def get_client(role: str) -> Any:
        roles.append(role)
        return _client(completions)

    monkeypatch.setattr(router_module, "get_client", get_client)
    router = router_module.MultiLLMRouter(enable_infinite_chat=False)

    result = asyncio.run(router.aroute("decision_making", "decide", provider="legacy"))

    assert result == "async response"
    assert roles == ["brain_fast_reasoning"]
    assert completions.calls[0]["model"] == "openfang:brain-fallback"


def test_route_logs_and_reraises_openfang_unavailability(
    router_module: Any,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    unavailable = router_module.OpenFangUnavailable("OpenFang unavailable")

    def unavailable_client(role: str) -> Any:
        raise unavailable

    monkeypatch.setattr(router_module, "get_client_sync", unavailable_client)
    router = router_module.MultiLLMRouter(enable_infinite_chat=False)

    with pytest.raises(router_module.OpenFangUnavailable, match="OpenFang unavailable"):
        router.route("question_generation", "must not fall back")

    assert "OpenFang unavailable" in caplog.text


def test_router_contains_no_direct_provider_execution(router_module: Any) -> None:
    source = inspect.getsource(router_module)

    for forbidden in ("requests.post", "openrouter.ai", "api.groq.com", "OPENROUTER_API_KEY", "GROQ_API_KEY"):
        assert forbidden not in source
