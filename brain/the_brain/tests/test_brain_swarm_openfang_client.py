"""Contract tests for the Brain Swarm OpenFang model-client transport."""

from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path
from typing import Any

import pytest
from autogen_ext.models.openai import OpenAIChatCompletionClient


BRAIN_ROOT = Path(__file__).resolve().parents[1]
MODULE_NAME = "production.brain_swarm_orchestrator"


def _module_with(**attributes: Any) -> types.ModuleType:
    module = types.ModuleType("test_stub")
    for name, value in attributes.items():
        setattr(module, name, value)
    return module


def _load_orchestrator(
    monkeypatch: pytest.MonkeyPatch,
    client_class: type[Any],
    provider_info: dict[str, Any],
) -> tuple[Any, list[str], list[str]]:
    """Load the module with no real provider, AutoGen, or Brain dependencies."""
    if str(BRAIN_ROOT) not in sys.path:
        sys.path.insert(0, str(BRAIN_ROOT))

    provider_roles: list[str] = []
    model_roles: list[str] = []
    shared = _module_with(
        get_model=lambda role: model_roles.append(role) or provider_info["model"],
        get_provider_info=lambda role: provider_roles.append(role) or provider_info,
    )
    monkeypatch.setitem(sys.modules, "vibemind_shared", shared)
    monkeypatch.setitem(
        sys.modules,
        "vibemind_shared.llm_client",
        _module_with(_get_api_key=lambda provider: "test-openfang-key"),
    )

    monkeypatch.setitem(sys.modules, "production.production_planner", _module_with(ProductionPlanner=object))
    monkeypatch.setitem(
        sys.modules,
        "production.cognitive_feature_agents",
        _module_with(CognitiveFeatureAgentFactory=object),
    )
    monkeypatch.setitem(sys.modules, "production.unified_brain_client", _module_with(UnifiedBrainClient=object))

    monkeypatch.setitem(sys.modules, "autogen_agentchat.teams", _module_with(Swarm=object))
    monkeypatch.setitem(sys.modules, "autogen_agentchat.agents", _module_with(AssistantAgent=object))
    monkeypatch.setitem(
        sys.modules,
        "autogen_agentchat.messages",
        _module_with(HandoffMessage=object, TextMessage=object),
    )
    monkeypatch.setitem(
        sys.modules,
        "autogen_agentchat.conditions",
        _module_with(HandoffTermination=object, TextMentionTermination=object),
    )
    monkeypatch.setitem(
        sys.modules,
        "autogen_ext.models.openai",
        _module_with(OpenAIChatCompletionClient=client_class),
    )

    sys.modules.pop(MODULE_NAME, None)
    return importlib.import_module(MODULE_NAME), provider_roles, model_roles


class _CapturingOpenAIClient:
    calls: list[dict[str, Any]] = []

    def __init__(self, **kwargs: Any) -> None:
        self.calls.append(kwargs)


@pytest.mark.parametrize(
    ("max_retries", "timeout_seconds"),
    [(3, 30.0), (0, 0.1)],
)
def test_model_client_forwards_planning_openfang_transport_config(
    monkeypatch: pytest.MonkeyPatch,
    max_retries: int,
    timeout_seconds: float,
) -> None:
    _CapturingOpenAIClient.calls = []
    provider_info = {
        "provider": "openfang",
        "model": "openfang:brain-planner",
        "base_url": "http://openfang.test/v1",
        "max_retries": max_retries,
        "timeout_seconds": timeout_seconds,
    }
    module, provider_roles, model_roles = _load_orchestrator(
        monkeypatch, _CapturingOpenAIClient, provider_info
    )

    client = module.BrainSwarmOrchestrator.__new__(module.BrainSwarmOrchestrator)._create_model_client()

    assert isinstance(client, _CapturingOpenAIClient)
    assert provider_roles == ["planning"]
    assert model_roles == ["planning"]
    assert _CapturingOpenAIClient.calls == [{
        "model": "openfang:brain-planner",
        "api_key": "test-openfang-key",
        "base_url": "http://openfang.test/v1",
        "max_retries": max_retries,
        "timeout": timeout_seconds,
        "parallel_tool_calls": False,
        "default_headers": {
            "HTTP-Referer": "https://github.com/Flissel/the_brain",
            "X-Title": "Tahlamus Brain Swarm",
        },
    }]


class _LegacyOpenAIClient:
    calls: list[dict[str, Any]] = []

    def __init__(
        self,
        model: str,
        api_key: str,
        base_url: str,
        parallel_tool_calls: bool,
        default_headers: dict[str, str],
    ) -> None:
        self.calls.append({
            "model": model,
            "api_key": api_key,
            "base_url": base_url,
            "parallel_tool_calls": parallel_tool_calls,
            "default_headers": default_headers,
        })


def test_model_client_skips_transport_options_for_a_legacy_constructor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _LegacyOpenAIClient.calls = []
    provider_info = {
        "provider": "openfang",
        "model": "openfang:brain-planner",
        "base_url": "http://openfang.test/v1",
        "max_retries": 3,
        "timeout_seconds": 30.0,
    }
    module, _, _ = _load_orchestrator(monkeypatch, _LegacyOpenAIClient, provider_info)

    client = module.BrainSwarmOrchestrator.__new__(module.BrainSwarmOrchestrator)._create_model_client()

    assert isinstance(client, _LegacyOpenAIClient)
    assert _LegacyOpenAIClient.calls[0]["base_url"] == "http://openfang.test/v1"
    assert _LegacyOpenAIClient.calls[0]["parallel_tool_calls"] is False
    assert _LegacyOpenAIClient.calls[0]["default_headers"] == {
        "HTTP-Referer": "https://github.com/Flissel/the_brain",
        "X-Title": "Tahlamus Brain Swarm",
    }


def test_model_client_projects_flat_openai_args_on_autogen_075(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider_info = {
        "provider": "openfang",
        "model": "gpt-4o",
        "base_url": "http://openfang.test/v1",
        "max_retries": 3,
        "timeout_seconds": 30.0,
    }
    module, _, _ = _load_orchestrator(monkeypatch, OpenAIChatCompletionClient, provider_info)

    client = module.BrainSwarmOrchestrator.__new__(module.BrainSwarmOrchestrator)._create_model_client()

    assert client._create_args["parallel_tool_calls"] is False
    assert "model_kwargs" not in client._create_args
    assert client._client.default_headers["HTTP-Referer"] == "https://github.com/Flissel/the_brain"
    assert client._client.default_headers["X-Title"] == "Tahlamus Brain Swarm"


def test_model_client_fails_before_constructing_without_an_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _CapturingOpenAIClient.calls = []
    provider_info = {
        "provider": "openfang",
        "model": "openfang:brain-planner",
        "base_url": "http://openfang.test/v1",
        "max_retries": 3,
        "timeout_seconds": 30.0,
    }
    module, _, _ = _load_orchestrator(monkeypatch, _CapturingOpenAIClient, provider_info)
    monkeypatch.setattr(sys.modules["vibemind_shared.llm_client"], "_get_api_key", lambda provider: None)

    with pytest.raises(ValueError, match="No API key found for provider 'openfang'"):
        module.BrainSwarmOrchestrator.__new__(module.BrainSwarmOrchestrator)._create_model_client()

    assert _CapturingOpenAIClient.calls == []
