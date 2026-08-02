"""Hermetic boundary tests for the pitch-deck OpenFang LLM integration."""

from __future__ import annotations

import ast
import asyncio
import builtins
import importlib
import sys
import types
from pathlib import Path
from typing import Any

import pytest


OPS_ROOT = Path(__file__).resolve().parents[1]
PITCH_DECK_ROLE = "agent_pitch_deck"


class _Completions:
    def create(self, **_: Any) -> Any:
        return types.SimpleNamespace(
            choices=[types.SimpleNamespace(message=types.SimpleNamespace(content='{"theme": "midnight"}'))]
        )


def _client() -> Any:
    return types.SimpleNamespace(
        chat=types.SimpleNamespace(completions=_Completions()),
        images=types.SimpleNamespace(generate=lambda **_: None),
    )


@pytest.fixture
def pitch_deck_module(monkeypatch: pytest.MonkeyPatch) -> Any:
    matplotlib = types.ModuleType("matplotlib")
    matplotlib.use = lambda *_: None
    monkeypatch.setitem(sys.modules, "matplotlib", matplotlib)
    monkeypatch.setitem(sys.modules, "matplotlib.pyplot", types.ModuleType("matplotlib.pyplot"))
    monkeypatch.setitem(sys.modules, "numpy", types.ModuleType("numpy"))

    rag_module = types.ModuleType("pitchdeck_rag")
    rag_module.PitchdeckRAG = object
    monkeypatch.setitem(sys.modules, "pitchdeck_rag", rag_module)

    autogen = types.ModuleType("autogen_core")

    class RoutedAgent:
        def __init__(self, *_: Any) -> None:
            pass

    autogen.AgentId = object
    autogen.MessageContext = object
    autogen.RoutedAgent = RoutedAgent
    autogen.SingleThreadedAgentRuntime = object
    autogen.message_handler = lambda function: function
    monkeypatch.setitem(sys.modules, "autogen_core", autogen)

    shared = types.ModuleType("vibemind_shared")

    class SharedOpenFangUnavailable(RuntimeError):
        pass

    shared.OpenFangUnavailable = SharedOpenFangUnavailable
    shared.get_client_sync = lambda role: _client()
    shared.get_model = lambda role: f"openfang:{role}"
    monkeypatch.setitem(sys.modules, "vibemind_shared", shared)

    openai = types.ModuleType("openai")
    openai.OpenAI = _client
    monkeypatch.setitem(sys.modules, "openai", openai)
    legacy_llm_client = types.ModuleType("llm_client")
    legacy_llm_client.get_model = lambda *_: "legacy-model"
    monkeypatch.setitem(sys.modules, "llm_client", legacy_llm_client)

    if str(OPS_ROOT) not in sys.path:
        sys.path.insert(0, str(OPS_ROOT))
    sys.modules.pop("pitch_deck_agent", None)
    return importlib.import_module("pitch_deck_agent")


def test_all_llm_acquisitions_use_the_canonical_pitch_deck_role(
    pitch_deck_module: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    assert hasattr(pitch_deck_module, "get_client_sync")
    roles: list[str] = []
    monkeypatch.setattr(
        pitch_deck_module,
        "get_client_sync",
        lambda role: roles.append(role) or _client(),
    )

    for agent in (
        pitch_deck_module.BriefingAgent,
        pitch_deck_module.SemanticAnalyzerAgent,
        pitch_deck_module.ResearcherAgent,
        pitch_deck_module.ContentGeneratorAgent,
        pitch_deck_module.DesignDirectorAgent,
    ):
        agent()
    pitch_deck_module.ChartGeneratorAgent()._generate_bg_images(
        types.SimpleNamespace(slides=[])
    )
    pitch_deck_module.generate_investor_emails("VibeMind", "{}", tmp_path / "deck.pptx")
    monkeypatch.setattr(builtins, "input", lambda _: "")
    asyncio.run(pitch_deck_module.feedback_loop([], "VibeMind", {}, object()))

    assert roles == [PITCH_DECK_ROLE] * 8


def test_chat_uses_the_canonical_pitch_deck_model_role(
    pitch_deck_module: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert hasattr(pitch_deck_module, "get_client_sync")
    client_roles: list[str] = []
    model_roles: list[str] = []
    monkeypatch.setattr(
        pitch_deck_module,
        "get_client_sync",
        lambda role: client_roles.append(role) or _client(),
    )
    monkeypatch.setattr(
        pitch_deck_module,
        "get_model",
        lambda role: model_roles.append(role) or "openfang:writer",
    )
    monkeypatch.setattr(
        pitch_deck_module,
        "get_rag",
        lambda: types.SimpleNamespace(get_vision_info=lambda: ""),
    )

    agent = pitch_deck_module.SemanticAnalyzerAgent()
    asyncio.run(
        agent.analyze(
            pitch_deck_module.PitchDeckRequest("VibeMind", "AI platform"), None
        )
    )

    assert client_roles == [PITCH_DECK_ROLE]
    assert model_roles == [PITCH_DECK_ROLE]


def test_openfang_unavailable_from_client_acquisition_propagates(
    pitch_deck_module: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert hasattr(pitch_deck_module, "get_client_sync")
    assert hasattr(pitch_deck_module, "OpenFangUnavailable")
    unavailable = pitch_deck_module.OpenFangUnavailable("OpenFang unavailable")

    def raise_unavailable(role: str) -> Any:
        assert role == PITCH_DECK_ROLE
        raise unavailable

    monkeypatch.setattr(pitch_deck_module, "get_client_sync", raise_unavailable)

    with pytest.raises(pitch_deck_module.OpenFangUnavailable, match="OpenFang unavailable"):
        pitch_deck_module.BriefingAgent()


def test_source_has_no_direct_provider_or_legacy_role_boundary() -> None:
    source_path = OPS_ROOT / "pitch_deck_agent.py"
    source = source_path.read_text(encoding="utf-8")
    tree = ast.parse(source)

    assert "from vibemind_shared import" in source
    imports_openai = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        and any(alias.name == "openai" for alias in node.names)
    ]
    assert imports_openai == []

    forbidden_constructors = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "OpenAI"
    ]
    assert forbidden_constructors == []

    legacy_models = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "get_model"
        and len(node.args) >= 2
        and isinstance(node.args[0], ast.Constant)
        and node.args[0].value == "default"
        and isinstance(node.args[1], ast.Constant)
        and node.args[1].value == "pitch_deck_agent"
    ]
    assert legacy_models == []

    client_acquisitions = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "get_client_sync"
    ]
    assert len(client_acquisitions) == 8
    assert all(
        len(node.args) == 1
        and isinstance(node.args[0], ast.Name)
        and node.args[0].id == "PITCH_DECK_ROLE"
        for node in client_acquisitions
    )

    model_resolutions = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "get_model"
    ]
    assert len(model_resolutions) == 8
    assert all(
        len(node.args) == 1
        and isinstance(node.args[0], ast.Name)
        and node.args[0].id == "PITCH_DECK_ROLE"
        for node in model_resolutions
    )
