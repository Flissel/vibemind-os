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
IMAGE_UNAVAILABLE_MESSAGE = (
    "OpenFang gateway does not support AI image generation for pitch decks"
)


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


class _FailingCompletions:
    def __init__(self, error: Exception, calls: list[dict[str, Any]]) -> None:
        self.error = error
        self.calls = calls

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        raise self.error


class _PipelineObserved(RuntimeError):
    pass


def _failing_client(error: Exception, calls: list[dict[str, Any]]) -> Any:
    return types.SimpleNamespace(
        chat=types.SimpleNamespace(
            completions=_FailingCompletions(error, calls)
        )
    )


def _theme_fields(*, images: bool) -> dict[str, Any]:
    return {
        "theme_name": "midnight",
        "primary": "#111111",
        "secondary": "#222222",
        "accent": "#333333",
        "bg_dark": "#000000",
        "bg_light": "#FFFFFF",
        "text_light": "#FFFFFF",
        "text_dark": "#000000",
        "industry": "AI",
        "tone": "technical",
        "images": images,
    }


def _content_result(module: Any, *, images: bool) -> Any:
    return module.ContentResult(
        company_name="VibeMind",
        slides=[],
        **_theme_fields(images=images),
    )


def _research_result(module: Any) -> Any:
    return module.ResearchResult(
        company_name="VibeMind",
        description="AI platform",
        research="{}",
        **_theme_fields(images=False),
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

    class AgentId:
        def __init__(self, agent_type: str, key: str) -> None:
            self.agent_type = agent_type
            self.key = key

    class RoutedAgent:
        def __init__(self, *_: Any) -> None:
            pass

    autogen.AgentId = AgentId
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
    pitch_deck_module.generate_investor_emails("VibeMind", "{}", tmp_path / "deck.pptx")
    monkeypatch.setattr(builtins, "input", lambda _: "")
    asyncio.run(pitch_deck_module.feedback_loop([], "VibeMind", {}, object()))

    assert roles == [PITCH_DECK_ROLE] * 7


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


def test_research_request_reraises_openfang_unavailable_without_retry(
    pitch_deck_module: Any,
) -> None:
    calls: list[dict[str, Any]] = []
    unavailable = pitch_deck_module.OpenFangUnavailable("research unavailable")
    agent = pitch_deck_module.ResearcherAgent()
    agent.client = _failing_client(unavailable, calls)

    with pytest.raises(
        pitch_deck_module.OpenFangUnavailable,
        match="research unavailable",
    ):
        agent._call("research prompt")

    assert len(calls) == 1


def test_content_request_reraises_openfang_unavailable(
    pitch_deck_module: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []
    unavailable = pitch_deck_module.OpenFangUnavailable("content unavailable")
    agent = pitch_deck_module.ContentGeneratorAgent()
    agent.client = _failing_client(unavailable, calls)
    monkeypatch.setattr(
        pitch_deck_module,
        "get_rag",
        lambda: types.SimpleNamespace(
            get_product_info=lambda: "",
            get_team_info=lambda: "",
            get_vision_info=lambda: "",
        ),
    )

    with pytest.raises(
        pitch_deck_module.OpenFangUnavailable,
        match="content unavailable",
    ):
        asyncio.run(agent.handle(_research_result(pitch_deck_module), None))

    assert len(calls) == 1


def test_design_request_reraises_openfang_unavailable(
    pitch_deck_module: Any,
) -> None:
    calls: list[dict[str, Any]] = []
    unavailable = pitch_deck_module.OpenFangUnavailable("design unavailable")
    agent = pitch_deck_module.DesignDirectorAgent()
    agent.client = _failing_client(unavailable, calls)

    with pytest.raises(
        pitch_deck_module.OpenFangUnavailable,
        match="design unavailable",
    ):
        asyncio.run(
            agent.direct(_content_result(pitch_deck_module, images=False), None)
        )

    assert len(calls) == 1


def test_email_request_reraises_openfang_unavailable(
    pitch_deck_module: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[dict[str, Any]] = []
    unavailable = pitch_deck_module.OpenFangUnavailable("email unavailable")
    monkeypatch.setattr(
        pitch_deck_module,
        "get_client_sync",
        lambda role: _failing_client(unavailable, calls),
    )

    with pytest.raises(
        pitch_deck_module.OpenFangUnavailable,
        match="email unavailable",
    ):
        pitch_deck_module.generate_investor_emails(
            "VibeMind", "{}", tmp_path / "deck.pptx"
        )

    assert len(calls) == 1


def test_feedback_request_reraises_openfang_unavailable(
    pitch_deck_module: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []
    unavailable = pitch_deck_module.OpenFangUnavailable("feedback unavailable")
    monkeypatch.setattr(
        pitch_deck_module,
        "get_client_sync",
        lambda role: _failing_client(unavailable, calls),
    )
    monkeypatch.setattr(builtins, "input", lambda _: "revise slide 1")

    with pytest.raises(
        pitch_deck_module.OpenFangUnavailable,
        match="feedback unavailable",
    ):
        asyncio.run(
            pitch_deck_module.feedback_loop([], "VibeMind", {}, object())
        )

    assert len(calls) == 1


def test_image_request_fails_before_unsupported_gateway_call(
    pitch_deck_module: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    client_roles: list[str] = []
    monkeypatch.setattr(
        pitch_deck_module,
        "get_client_sync",
        lambda role: client_roles.append(role) or _client(),
    )
    agent = pitch_deck_module.ChartGeneratorAgent()
    agent.chart_dir = tmp_path / "charts"

    with pytest.raises(pitch_deck_module.OpenFangUnavailable) as error:
        asyncio.run(
            agent.generate(_content_result(pitch_deck_module, images=True), None)
        )

    assert str(error.value) == IMAGE_UNAVAILABLE_MESSAGE
    assert client_roles == []


def test_image_disabled_flow_does_not_require_image_capability(
    pitch_deck_module: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        pitch_deck_module,
        "get_client_sync",
        lambda role: pytest.fail(f"unexpected LLM client acquisition for {role}"),
    )
    agent = pitch_deck_module.ChartGeneratorAgent()
    agent.chart_dir = tmp_path / "charts"

    result = asyncio.run(
        agent.generate(_content_result(pitch_deck_module, images=False), None)
    )

    assert result.slides == []


def test_quick_cli_defaults_to_images_disabled_and_starts_pipeline(
    pitch_deck_module: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_instances: list[Any] = []
    registrations: list[str] = []
    pipeline_requests: list[Any] = []

    class Runtime:
        def start(self) -> None:
            pass

        async def send_message(self, message: Any, agent_id: Any) -> Any:
            pipeline_requests.append(message)
            raise _PipelineObserved

    def runtime_factory() -> Runtime:
        runtime = Runtime()
        runtime_instances.append(runtime)
        return runtime

    async def register(runtime: Any, name: str, factory: Any) -> None:
        registrations.append(name)

    monkeypatch.setattr(
        pitch_deck_module,
        "SingleThreadedAgentRuntime",
        runtime_factory,
    )
    for agent in (
        pitch_deck_module.BriefingAgent,
        pitch_deck_module.SemanticAnalyzerAgent,
        pitch_deck_module.ResearcherAgent,
        pitch_deck_module.ContentGeneratorAgent,
        pitch_deck_module.DesignDirectorAgent,
        pitch_deck_module.ChartGeneratorAgent,
        pitch_deck_module.SlideBuilderAgent,
    ):
        monkeypatch.setattr(agent, "register", staticmethod(register), raising=False)
    monkeypatch.setattr(
        sys,
        "argv",
        ["pitch_deck_agent.py", "VibeMind", "AI platform"],
    )
    monkeypatch.setenv("OPENFANG_API_KEY", "test-key")

    with pytest.raises(_PipelineObserved):
        asyncio.run(pitch_deck_module.main())

    assert len(runtime_instances) == 1
    assert len(registrations) == 7
    assert len(pipeline_requests) == 1
    assert pipeline_requests[0].images is False


def test_explicit_images_cli_fails_before_runtime_or_llm_work(
    pitch_deck_module: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_instances: list[Any] = []
    registrations: list[str] = []
    client_roles: list[str] = []
    pipeline_requests: list[Any] = []

    class Runtime:
        def start(self) -> None:
            pass

        async def send_message(self, message: Any, agent_id: Any) -> Any:
            pipeline_requests.append(message)
            raise pitch_deck_module.OpenFangUnavailable(
                IMAGE_UNAVAILABLE_MESSAGE
            )

    def runtime_factory() -> Runtime:
        runtime = Runtime()
        runtime_instances.append(runtime)
        return runtime

    async def register(runtime: Any, name: str, factory: Any) -> None:
        registrations.append(name)

    monkeypatch.setattr(
        pitch_deck_module,
        "SingleThreadedAgentRuntime",
        runtime_factory,
    )
    monkeypatch.setattr(
        pitch_deck_module,
        "get_client_sync",
        lambda role: client_roles.append(role) or _client(),
    )
    for agent in (
        pitch_deck_module.BriefingAgent,
        pitch_deck_module.SemanticAnalyzerAgent,
        pitch_deck_module.ResearcherAgent,
        pitch_deck_module.ContentGeneratorAgent,
        pitch_deck_module.DesignDirectorAgent,
        pitch_deck_module.ChartGeneratorAgent,
        pitch_deck_module.SlideBuilderAgent,
    ):
        monkeypatch.setattr(agent, "register", staticmethod(register), raising=False)
    monkeypatch.setattr(
        sys,
        "argv",
        ["pitch_deck_agent.py", "VibeMind", "AI platform", "--images"],
    )
    monkeypatch.delenv("OPENFANG_API_KEY", raising=False)

    with pytest.raises(pitch_deck_module.OpenFangUnavailable) as error:
        asyncio.run(pitch_deck_module.main())

    assert str(error.value) == IMAGE_UNAVAILABLE_MESSAGE
    assert runtime_instances == []
    assert registrations == []
    assert client_roles == []
    assert pipeline_requests == []


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
    assert "images.generate" not in source
    assert "dall-e-3" not in source

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
    assert len(client_acquisitions) == 7
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
    assert len(model_resolutions) == 7
    assert all(
        len(node.args) == 1
        and isinstance(node.args[0], ast.Name)
        and node.args[0].id == "PITCH_DECK_ROLE"
        for node in model_resolutions
    )
