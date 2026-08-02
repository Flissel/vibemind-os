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


class _SharedOpenFangUnavailable(RuntimeError):
    pass


class _ResponseCompletions:
    def __init__(self, text: str, calls: list[dict[str, Any]]) -> None:
        self.text = text
        self.calls = calls

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return types.SimpleNamespace(
            choices=[
                types.SimpleNamespace(
                    message=types.SimpleNamespace(content=self.text)
                )
            ]
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


def _response_client(text: str, calls: list[dict[str, Any]]) -> Any:
    return types.SimpleNamespace(
        chat=types.SimpleNamespace(
            completions=_ResponseCompletions(text, calls)
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


def _install_non_provider_import_stubs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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


@pytest.fixture
def pitch_deck_module(monkeypatch: pytest.MonkeyPatch) -> Any:
    _install_non_provider_import_stubs(monkeypatch)

    shared = types.ModuleType("vibemind_shared")
    shared.OpenFangUnavailable = _SharedOpenFangUnavailable
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


def test_import_and_explicit_image_preflight_do_not_touch_shared_or_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shared_imports: list[str] = []
    env_events: list[str] = []
    original_import = builtins.__import__

    class ForbiddenSharedImport(RuntimeError):
        pass

    def import_guard(
        name: str,
        globals_: Any = None,
        locals_: Any = None,
        fromlist: Any = (),
        level: int = 0,
    ) -> Any:
        if name == "vibemind_shared" or name.startswith("vibemind_shared."):
            shared_imports.append(name)
            raise ForbiddenSharedImport(name)
        return original_import(name, globals_, locals_, fromlist, level)

    class ExistingEnvPath:
        def __init__(self, value: Any) -> None:
            self.value = value

        @property
        def parent(self) -> ExistingEnvPath:
            return self

        def __truediv__(self, name: str) -> ExistingEnvPath:
            assert name == ".env"
            return self

        def exists(self) -> bool:
            env_events.append("exists")
            return True

    class ExistingEnvFile:
        def __enter__(self) -> ExistingEnvFile:
            return self

        def __exit__(self, *_: Any) -> None:
            pass

        def __iter__(self) -> Any:
            env_events.append("read")
            return iter(["OPENFANG_API_KEY=from-env\n"])

    def open_env(*_: Any, **__: Any) -> ExistingEnvFile:
        env_events.append("open")
        return ExistingEnvFile()

    _install_non_provider_import_stubs(monkeypatch)
    monkeypatch.delitem(sys.modules, "vibemind_shared", raising=False)
    monkeypatch.delitem(sys.modules, "vibemind_shared.llm_client", raising=False)
    monkeypatch.setattr(builtins, "__import__", import_guard)
    if str(OPS_ROOT) not in sys.path:
        sys.path.insert(0, str(OPS_ROOT))
    sys.modules.pop("pitch_deck_agent", None)

    try:
        module = importlib.import_module("pitch_deck_agent")
    except ForbiddenSharedImport:
        module = None

    assert shared_imports == []
    assert module is not None

    monkeypatch.setattr(module, "Path", ExistingEnvPath)
    monkeypatch.setattr(builtins, "open", open_env)
    monkeypatch.setattr(
        sys,
        "argv",
        ["pitch_deck_agent.py", "VibeMind", "AI platform", "--images"],
    )
    monkeypatch.delenv("OPENFANG_API_KEY", raising=False)

    with pytest.raises(module.OpenFangCapabilityUnavailable) as error:
        asyncio.run(module.main())

    assert str(error.value) == IMAGE_UNAVAILABLE_MESSAGE
    assert shared_imports == []
    assert env_events == []
    assert "OPENFANG_API_KEY" not in module.os.environ


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
    unavailable = _SharedOpenFangUnavailable("OpenFang unavailable")

    def raise_unavailable(role: str) -> Any:
        assert role == PITCH_DECK_ROLE
        raise unavailable

    monkeypatch.setattr(pitch_deck_module, "get_client_sync", raise_unavailable)

    with pytest.raises(_SharedOpenFangUnavailable) as error:
        pitch_deck_module.BriefingAgent()

    assert error.value is unavailable


def test_research_request_reraises_generic_provider_failure_without_retry(
    pitch_deck_module: Any,
) -> None:
    calls: list[dict[str, Any]] = []
    failure = ValueError("research request rejected with 400")
    agent = pitch_deck_module.ResearcherAgent()
    agent.client = _failing_client(failure, calls)

    with pytest.raises(ValueError) as error:
        agent._call("research prompt")

    assert error.value is failure
    assert len(calls) == 1


def test_shared_openfang_unavailable_request_is_not_converted(
    pitch_deck_module: Any,
) -> None:
    calls: list[dict[str, Any]] = []
    unavailable = _SharedOpenFangUnavailable("OpenFang unavailable")
    agent = pitch_deck_module.ResearcherAgent()
    agent.client = _failing_client(unavailable, calls)

    with pytest.raises(_SharedOpenFangUnavailable) as error:
        agent._call("research prompt")

    assert error.value is unavailable
    assert not isinstance(
        error.value,
        pitch_deck_module.OpenFangCapabilityUnavailable,
    )
    assert len(calls) == 1


def test_content_request_reraises_generic_provider_failure(
    pitch_deck_module: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []
    failure = ValueError("content request rejected with 422")
    agent = pitch_deck_module.ContentGeneratorAgent()
    agent.client = _failing_client(failure, calls)
    monkeypatch.setattr(
        pitch_deck_module,
        "get_rag",
        lambda: types.SimpleNamespace(
            get_product_info=lambda: "",
            get_team_info=lambda: "",
            get_vision_info=lambda: "",
        ),
    )

    with pytest.raises(ValueError) as error:
        asyncio.run(agent.handle(_research_result(pitch_deck_module), None))

    assert error.value is failure
    assert len(calls) == 1


def test_design_request_reraises_generic_provider_failure(
    pitch_deck_module: Any,
) -> None:
    calls: list[dict[str, Any]] = []
    failure = ValueError("design request rejected with 401")
    agent = pitch_deck_module.DesignDirectorAgent()
    agent.client = _failing_client(failure, calls)

    with pytest.raises(ValueError) as error:
        asyncio.run(
            agent.direct(_content_result(pitch_deck_module, images=False), None)
        )

    assert error.value is failure
    assert len(calls) == 1


def test_email_request_reraises_generic_provider_failure(
    pitch_deck_module: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[dict[str, Any]] = []
    failure = ValueError("email request rejected with 403")
    monkeypatch.setattr(
        pitch_deck_module,
        "get_client_sync",
        lambda role: _failing_client(failure, calls),
    )

    with pytest.raises(ValueError) as error:
        pitch_deck_module.generate_investor_emails(
            "VibeMind", "{}", tmp_path / "deck.pptx"
        )

    assert error.value is failure
    assert len(calls) == 1


def test_feedback_request_reraises_generic_provider_failure(
    pitch_deck_module: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []
    failure = ValueError("feedback request rejected with 429")
    monkeypatch.setattr(
        pitch_deck_module,
        "get_client_sync",
        lambda role: _failing_client(failure, calls),
    )
    answers = iter(["revise slide 1", ""])
    monkeypatch.setattr(builtins, "input", lambda _: next(answers))

    with pytest.raises(ValueError) as error:
        asyncio.run(
            pitch_deck_module.feedback_loop([], "VibeMind", {}, object())
        )

    assert error.value is failure
    assert len(calls) == 1


def test_research_parse_failure_degrades_after_successful_response(
    pitch_deck_module: Any,
) -> None:
    calls: list[dict[str, Any]] = []
    agent = pitch_deck_module.ResearcherAgent()
    agent.client = _response_client("not-json", calls)

    assert agent._call("research prompt") == {}
    assert len(calls) == 1


def test_design_parse_failure_keeps_existing_slides(
    pitch_deck_module: Any,
) -> None:
    calls: list[dict[str, Any]] = []
    message = _content_result(pitch_deck_module, images=False)
    agent = pitch_deck_module.DesignDirectorAgent()
    agent.client = _response_client("not-json", calls)

    result = asyncio.run(agent.direct(message, None))

    assert result.slides is message.slides
    assert len(calls) == 1


def test_feedback_parse_failure_keeps_existing_slides(
    pitch_deck_module: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []
    slides = [{"slide_type": "intro", "title": "Original"}]
    answers = iter(["revise slide 1", ""])
    monkeypatch.setattr(
        pitch_deck_module,
        "get_client_sync",
        lambda role: _response_client("not-json", calls),
    )
    monkeypatch.setattr(builtins, "input", lambda _: next(answers))

    result = asyncio.run(
        pitch_deck_module.feedback_loop(slides, "VibeMind", {}, object())
    )

    assert result is slides
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

    with pytest.raises(pitch_deck_module.OpenFangCapabilityUnavailable) as error:
        asyncio.run(
            agent.generate(_content_result(pitch_deck_module, images=True), None)
        )

    assert str(error.value) == IMAGE_UNAVAILABLE_MESSAGE
    assert type(error.value) is pitch_deck_module.OpenFangCapabilityUnavailable
    assert not isinstance(error.value, _SharedOpenFangUnavailable)
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
    env_events: list[str] = []

    class ExistingEnvPath:
        def __init__(self, value: Any) -> None:
            self.value = value

        @property
        def parent(self) -> ExistingEnvPath:
            return self

        def __truediv__(self, name: str) -> ExistingEnvPath:
            assert name == ".env"
            return self

        def exists(self) -> bool:
            env_events.append("exists")
            return True

    class ExistingEnvFile:
        def __enter__(self) -> ExistingEnvFile:
            return self

        def __exit__(self, *_: Any) -> None:
            pass

        def __iter__(self) -> Any:
            env_events.append("read")
            return iter(["OPENFANG_API_KEY=from-env\n"])

    def open_env(*_: Any, **__: Any) -> ExistingEnvFile:
        env_events.append("open")
        return ExistingEnvFile()

    class Runtime:
        def start(self) -> None:
            pass

        async def send_message(self, message: Any, agent_id: Any) -> Any:
            pipeline_requests.append(message)
            raise pitch_deck_module.OpenFangCapabilityUnavailable(
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
    monkeypatch.setattr(pitch_deck_module, "Path", ExistingEnvPath)
    monkeypatch.setattr(builtins, "open", open_env)
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

    with pytest.raises(pitch_deck_module.OpenFangCapabilityUnavailable) as error:
        asyncio.run(pitch_deck_module.main())

    assert str(error.value) == IMAGE_UNAVAILABLE_MESSAGE
    assert runtime_instances == []
    assert registrations == []
    assert client_roles == []
    assert pipeline_requests == []
    assert env_events == []
    assert "OPENFANG_API_KEY" not in pitch_deck_module.os.environ


def test_source_has_no_direct_provider_or_legacy_role_boundary() -> None:
    source_path = OPS_ROOT / "pitch_deck_agent.py"
    source = source_path.read_text(encoding="utf-8")
    tree = ast.parse(source)

    assert "from vibemind_shared import" in source
    top_level_shared_imports = [
        node
        for node in tree.body
        if (
            isinstance(node, ast.ImportFrom)
            and node.module is not None
            and node.module.startswith("vibemind_shared")
        )
        or (
            isinstance(node, ast.Import)
            and any(
                alias.name.startswith("vibemind_shared")
                for alias in node.names
            )
        )
    ]
    assert top_level_shared_imports == []
    assert "OpenFangUnavailable =" not in source
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
