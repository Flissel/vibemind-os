"""Hermetic contract tests for OpenFang-owned retry behavior."""

from __future__ import annotations

import asyncio
import importlib
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


def test_env_model_chain_cannot_bypass_openfang_retry_boundary(
    router_module: Any,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    calls: list[dict[str, Any]] = []
    unavailable = router_module.OpenFangUnavailable("gateway exhausted bounded retry")

    class AsyncCompletions:
        async def create(self, **kwargs: Any) -> Any:
            calls.append(kwargs)
            raise unavailable

    monkeypatch.setenv("BRAIN_LLM_FALLBACK_CHAIN", "direct-provider-model")
    monkeypatch.setenv("BRAIN_LLM_MAX_RETRIES", "9")
    monkeypatch.setattr(
        router_module,
        "get_client",
        lambda role: types.SimpleNamespace(
            chat=types.SimpleNamespace(completions=AsyncCompletions())
        ),
    )
    router = router_module.MultiLLMRouter(enable_infinite_chat=False)

    with pytest.raises(router_module.OpenFangUnavailable, match="bounded retry"):
        asyncio.run(router.aroute("path_planning", "must not route elsewhere"))

    assert [call["model"] for call in calls] == ["openfang:brain-planner"]
    assert "gateway exhausted bounded retry" in caplog.text
