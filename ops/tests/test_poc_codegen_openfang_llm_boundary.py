"""Hermetic boundary tests for the code-generation PoC OpenFang migration."""

from __future__ import annotations

import ast
import asyncio
import importlib
import sys
import types
from pathlib import Path
from typing import Any

import pytest


POC_ROOT = Path(__file__).resolve().parents[1] / "poc_codegen"
EXECUTOR_ROLE = "coding_executor"
SECURITY_AUDIT_ROLE = "coding_security_audit"
SHARED_PIN = "609dda92e0f4370c03085a7e2ff6a6f492693d0d"


class _OpenFangUnavailable(RuntimeError):
    pass


class _AsyncCompletions:
    def __init__(self, text: str = "print('ok')", error: Exception | None = None) -> None:
        self.text = text
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return types.SimpleNamespace(
            choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=self.text))]
        )


def _client(completions: _AsyncCompletions) -> Any:
    return types.SimpleNamespace(chat=types.SimpleNamespace(completions=completions))


def _install_import_stubs(monkeypatch: pytest.MonkeyPatch) -> None:
    autogen_core = types.ModuleType("autogen_core")

    class AgentId:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self.args = args
            self.kwargs = kwargs

    class RoutedAgent:
        def __init__(self, *_: Any) -> None:
            pass

    class TopicId:
        def __init__(self, *_: Any, **__: Any) -> None:
            pass

    autogen_core.AgentId = AgentId
    autogen_core.RoutedAgent = RoutedAgent
    autogen_core.MessageContext = object
    autogen_core.TypeSubscription = object
    autogen_core.TopicId = TopicId
    autogen_core.message_handler = lambda function: function
    monkeypatch.setitem(sys.modules, "autogen_core", autogen_core)

    serialization = types.ModuleType("autogen_core._serialization")
    serialization.try_get_known_serializers_for_type = lambda _: []
    monkeypatch.setitem(sys.modules, "autogen_core._serialization", serialization)

    runtimes = types.ModuleType("autogen_ext.runtimes.grpc")
    runtimes.GrpcWorkerAgentRuntime = object
    monkeypatch.setitem(sys.modules, "autogen_ext", types.ModuleType("autogen_ext"))
    monkeypatch.setitem(sys.modules, "autogen_ext.runtimes", types.ModuleType("autogen_ext.runtimes"))
    monkeypatch.setitem(sys.modules, "autogen_ext.runtimes.grpc", runtimes)

    messages = types.ModuleType("messages")

    class CodeRequest:
        def __init__(self, task: str) -> None:
            self.task = task

    class GeneratedCode:
        def __init__(self, code: str) -> None:
            self.code = code

    class ApprovedCode:
        def __init__(self, code: str) -> None:
            self.code = code

    class CodeResult:
        def __init__(self, output: str, success: bool) -> None:
            self.output = output
            self.success = success

    class TeamEvent:
        def __init__(self, **_: Any) -> None:
            pass

    messages.CodeRequest = CodeRequest
    messages.GeneratedCode = GeneratedCode
    messages.ApprovedCode = ApprovedCode
    messages.CodeResult = CodeResult
    messages.TeamEvent = TeamEvent
    monkeypatch.setitem(sys.modules, "messages", messages)


@pytest.fixture
def worker_module(monkeypatch: pytest.MonkeyPatch) -> Any:
    _install_import_stubs(monkeypatch)
    shared = types.ModuleType("vibemind_shared")
    shared.OpenFangUnavailable = _OpenFangUnavailable
    shared.get_client = lambda _: _client(_AsyncCompletions())
    shared.get_model = lambda role: f"openfang:{role}"
    monkeypatch.setitem(sys.modules, "vibemind_shared", shared)
    if str(POC_ROOT) not in sys.path:
        sys.path.insert(0, str(POC_ROOT))
    sys.modules.pop("legitimate_worker", None)
    return importlib.import_module("legitimate_worker")


def test_agents_acquire_their_designated_shared_clients_and_models(
    worker_module: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    roles: list[str] = []
    models: list[str] = []
    codegen_completions = _AsyncCompletions()
    review_completions = _AsyncCompletions("APPROVED")

    def get_client(role: str) -> Any:
        roles.append(role)
        return _client(codegen_completions if role == EXECUTOR_ROLE else review_completions)

    monkeypatch.setattr(worker_module, "get_client", get_client)
    monkeypatch.setattr(
        worker_module, "get_model", lambda role: models.append(role) or f"openfang:{role}"
    )

    codegen = worker_module.CodeGenAgent()
    review = worker_module.ReviewAgent()
    asyncio.run(codegen.handle(worker_module.CodeRequest("print hello"), None))
    asyncio.run(review.handle(worker_module.GeneratedCode("print('ok')"), None))

    assert roles == [EXECUTOR_ROLE, SECURITY_AUDIT_ROLE]
    assert models == [EXECUTOR_ROLE, SECURITY_AUDIT_ROLE]
    assert codegen_completions.calls[0]["model"] == f"openfang:{EXECUTOR_ROLE}"
    assert review_completions.calls[0]["model"] == f"openfang:{SECURITY_AUDIT_ROLE}"


def test_shared_acquisition_unavailability_propagates_unchanged(
    worker_module: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    unavailable = _OpenFangUnavailable("gateway unavailable")

    def get_client(role: str) -> Any:
        assert role == EXECUTOR_ROLE
        raise unavailable

    monkeypatch.setattr(worker_module, "get_client", get_client)

    with pytest.raises(_OpenFangUnavailable) as error:
        worker_module.CodeGenAgent()

    assert error.value is unavailable


@pytest.mark.parametrize(
    ("agent_name", "message", "role"),
    [
        ("CodeGenAgent", "CodeRequest", EXECUTOR_ROLE),
        ("ReviewAgent", "GeneratedCode", SECURITY_AUDIT_ROLE),
    ],
)
def test_request_failure_propagates_after_one_request_without_retry(
    worker_module: Any,
    monkeypatch: pytest.MonkeyPatch,
    agent_name: str,
    message: str,
    role: str,
) -> None:
    failure = _OpenFangUnavailable("request unavailable")
    completions = _AsyncCompletions(error=failure)
    monkeypatch.setattr(worker_module, "get_client", lambda actual_role: _client(completions))
    monkeypatch.setattr(worker_module, "get_model", lambda actual_role: f"openfang:{actual_role}")
    agent = getattr(worker_module, agent_name)()
    payload = getattr(worker_module, message)("print('ok')")

    with pytest.raises(_OpenFangUnavailable) as error:
        asyncio.run(agent.handle(payload, None))

    assert error.value is failure
    assert len(completions.calls) == 1
    assert completions.calls[0]["model"] == f"openfang:{role}"


def test_source_has_only_shared_async_llm_boundary() -> None:
    source = (POC_ROOT / "legitimate_worker.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    assert "OPENAI_API_KEY" not in source
    assert "gpt-4o" not in source.lower()
    assert "AsyncOpenAI" not in source
    assert not [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        and any(alias.name == "openai" for alias in node.names)
    ]

    client_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "get_client"
    ]
    assert len(client_calls) == 2
    assert [call.args[0].value for call in client_calls] == [
        EXECUTOR_ROLE,
        SECURITY_AUDIT_ROLE,
    ]

    model_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "get_model"
    ]
    assert len(model_calls) == 2
    assert [call.args[0].value for call in model_calls] == [
        EXECUTOR_ROLE,
        SECURITY_AUDIT_ROLE,
    ]


def test_legit_packaging_has_exact_shared_pin_and_config_only_wiring() -> None:
    compose = (POC_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    dockerfile = (POC_ROOT / "Dockerfile.legit").read_text(encoding="utf-8")
    requirements = (POC_ROOT / "requirements.legit.txt").read_text(encoding="utf-8")

    assert "dockerfile: Dockerfile.legit" in compose
    assert "../../llm_config.yml:/config/llm_config.yml:ro" in compose
    legit_block = compose.split("  legit:\n", 1)[1].split("  malicious:\n", 1)[0]
    assert "env_file:" not in legit_block
    assert "OPENAI_API_KEY" not in legit_block
    assert "Dockerfile.worker" in compose.split("  malicious:\n", 1)[1]
    assert "Dockerfile.host" in compose.split("  host:\n", 1)[1].split("  legit:\n", 1)[0]
    assert "50052:50051" in compose

    assert "ENV VIBEMIND_CONFIG_DIR=/config" in dockerfile
    assert "COPY requirements.legit.txt" in dockerfile
    assert "COPY legitimate_worker.py" in dockerfile
    assert "COPY messages.py" in dockerfile
    assert requirements.count("vibemind-shared @ git+https://github.com/Flissel/vibemind-shared.git@") == 1
    assert SHARED_PIN in requirements
    assert "openai" not in requirements.lower()
