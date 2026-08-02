"""Hermetic boundary tests for the Docker real-LLM attack PoC migration."""

from __future__ import annotations

import ast
import asyncio
import importlib
import sys
import types
from pathlib import Path
from typing import Any

import pytest


POC_ROOT = Path(__file__).resolve().parents[1] / "poc_docker"
EXECUTOR_ROLE = "coding_executor"
SHARED_PIN = "609dda92e0f4370c03085a7e2ff6a6f492693d0d"


class _OpenFangUnavailable(RuntimeError):
    pass


class _AsyncCompletions:
    def __init__(self, text: str = "SELECT 1", error: Exception | None = None) -> None:
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

    autogen_core.AgentId = AgentId
    autogen_core.MessageContext = object
    autogen_core.SingleThreadedAgentRuntime = object
    autogen_core.RoutedAgent = RoutedAgent
    autogen_core.message_handler = lambda function: function
    monkeypatch.setitem(sys.modules, "autogen_core", autogen_core)


@pytest.fixture
def attack_module(monkeypatch: pytest.MonkeyPatch) -> Any:
    _install_import_stubs(monkeypatch)
    shared = types.ModuleType("vibemind_shared")
    shared.OpenFangUnavailable = _OpenFangUnavailable
    shared.get_client = lambda _: _client(_AsyncCompletions())
    shared.get_model = lambda role: f"openfang:{role}"
    monkeypatch.setitem(sys.modules, "vibemind_shared", shared)
    if str(POC_ROOT) not in sys.path:
        sys.path.insert(0, str(POC_ROOT))
    sys.modules.pop("real_llm_attack", None)
    return importlib.import_module("real_llm_attack")


def test_agents_use_the_injected_shared_client_and_executor_model(
    attack_module: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    models: list[str] = []
    query_completions = _AsyncCompletions("SELECT id FROM users")
    code_completions = _AsyncCompletions("result = 'ok'")
    monkeypatch.setattr(
        attack_module,
        "get_model",
        lambda role: models.append(role) or f"openfang:{role}",
    )

    query_agent = attack_module.LlmQueryAgent(_client(query_completions))
    code_agent = attack_module.LlmCodeAgent(object(), _client(code_completions))
    query = asyncio.run(
        query_agent.handle_request(attack_module.UserRequest("show users"), None)
    )
    result = asyncio.run(
        code_agent.handle_request(attack_module.UserRequest("write code"), None)
    )

    assert query.query == "SELECT id FROM users"
    assert result == "Code executed. Output: ok"
    assert models == [EXECUTOR_ROLE, EXECUTOR_ROLE]
    assert query_completions.calls[0]["model"] == f"openfang:{EXECUTOR_ROLE}"
    assert query_completions.calls[0]["temperature"] == 0
    assert query_completions.calls[0]["max_tokens"] == 200
    assert [item["role"] for item in query_completions.calls[0]["messages"]] == [
        "system",
        "user",
    ]
    assert query_completions.calls[0]["messages"][1] == {
        "role": "user",
        "content": "show users",
    }
    assert code_completions.calls[0]["model"] == f"openfang:{EXECUTOR_ROLE}"
    assert code_completions.calls[0]["temperature"] == 0
    assert code_completions.calls[0]["max_tokens"] == 1000
    assert [item["role"] for item in code_completions.calls[0]["messages"]] == [
        "system",
        "user",
    ]
    assert code_completions.calls[0]["messages"][1] == {
        "role": "user",
        "content": "write code",
    }


@pytest.mark.parametrize(
    ("agent_name", "message"),
    [("LlmQueryAgent", "query"), ("LlmCodeAgent", "code")],
)
def test_request_errors_propagate_after_one_request_without_retry(
    attack_module: Any, agent_name: str, message: str
) -> None:
    error = _OpenFangUnavailable("request unavailable")
    completions = _AsyncCompletions(error=error)
    client = _client(completions)
    agent = (
        attack_module.LlmQueryAgent(client)
        if agent_name == "LlmQueryAgent"
        else attack_module.LlmCodeAgent(object(), client)
    )
    request = attack_module.UserRequest("test")

    with pytest.raises(_OpenFangUnavailable) as raised:
        asyncio.run(agent.handle_request(request, None))

    assert raised.value is error
    assert len(completions.calls) == 1


def test_source_has_one_shared_acquisition_before_db_setup_and_no_direct_sdk() -> None:
    source = (POC_ROOT / "real_llm_attack.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    assert "OPENAI_API_KEY" not in source
    assert "gpt-4o" not in source.lower()
    assert "AsyncOpenAI" not in source
    assert "from vibemind_shared import get_client, get_model" in source
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
    assert len(client_calls) == 1
    assert isinstance(client_calls[0].args[0], ast.Constant)
    assert client_calls[0].args[0].value == EXECUTOR_ROLE

    model_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "get_model"
    ]
    assert len(model_calls) == 2
    assert all(
        len(call.args) == 1
        and isinstance(call.args[0], ast.Constant)
        and call.args[0].value == EXECUTOR_ROLE
        for call in model_calls
    )

    main = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "main")
    statements = main.body
    acquisition_index = next(
        index
        for index, statement in enumerate(statements)
        if any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "get_client"
            for node in ast.walk(statement)
        )
    )
    setup_index = next(
        index
        for index, statement in enumerate(statements)
        if any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "setup"
            for node in ast.walk(statement)
        )
    )
    assert acquisition_index < setup_index


def test_docker_packaging_has_exact_shared_pin_and_openfang_only_wiring() -> None:
    dockerfile = (POC_ROOT / "Dockerfile").read_text(encoding="utf-8")
    compose = (POC_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    requirements = (POC_ROOT / "requirements.legit.txt").read_text(encoding="utf-8")

    assert "apt-get install -y --no-install-recommends git" in dockerfile
    assert "rm -rf /var/lib/apt/lists/*" in dockerfile
    assert "ENV VIBEMIND_CONFIG_DIR=/config" in dockerfile
    assert "COPY requirements.legit.txt" in dockerfile
    for script in ("setup_db.py", "attack_demo.py", "silent_attack_demo.py", "real_llm_attack.py"):
        assert f"COPY {script}" in dockerfile
    assert 'CMD ["python", "-u", "real_llm_attack.py"]' in dockerfile

    assert "../../llm_config.yml:/config/llm_config.yml:ro" in compose
    assert "env_file:" not in compose
    assert "OPENAI_API_KEY" not in compose
    assert "OPENFANG_URL: ${OPENFANG_URL:?" in compose
    assert "OPENFANG_API_KEY: ${OPENFANG_API_KEY:?" in compose
    assert requirements.count("vibemind-shared @ git+https://github.com/Flissel/vibemind-shared.git@") == 1
    assert SHARED_PIN in requirements
    assert "openai" not in requirements.lower()
