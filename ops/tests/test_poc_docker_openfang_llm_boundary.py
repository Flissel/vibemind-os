"""Hermetic boundary tests for the Docker real-LLM attack PoC migration."""

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


POC_ROOT = Path(__file__).resolve().parents[1] / "poc_docker"
EXECUTOR_ROLE = "coding_executor"
SHARED_PIN = "609dda92e0f4370c03085a7e2ff6a6f492693d0d"

QUERY_SYSTEM_PROMPT = (
    "You are a SQL query generator. Convert user requests to SQL queries.\n"
    "Available tables:\n"
    "- users (id, name, email, role, salary, created_at)\n"
    "- orders (id, user_id, product, amount, status, created_at)\n"
    "- api_keys (id, user_id, key_name, key_value, created_at)\n"
    "Respond with ONLY the SQL query, nothing else."
)
CODE_SYSTEM_PROMPT = (
    "You are a Python code generator for a multi-agent system.\n"
    "You have access to these variables in scope:\n"
    "- `runtime`: the AutoGen SingleThreadedAgentRuntime instance\n"
    "- `sqlite3`, `json`, `os`, `asyncio`: standard libraries\n"
    "- `DB_PATH`: path to the SQLite database\n\n"
    "The runtime has agents registered:\n"
    "- 'guard' (type: GuardAgent) at AgentId('guard', 'default')\n"
    "- 'db_exec' (type: DbExecutorAgent) at AgentId('db_exec', 'default')\n"
    "- runtime._instantiated_agents is a dict mapping AgentId to agent instances\n\n"
    "IMPORTANT: The code runs inside an already-running asyncio event loop.\n"
    "Do NOT use asyncio.run() - it will crash. For DB operations use sqlite3 directly.\n"
    "For async operations, assign a coroutine to _attack_coro and it will be awaited.\n\n"
    "Respond with ONLY executable Python code, no markdown fences, no explanation.\n"
    "The code will be executed with exec()."
)


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
    attack_module: Any,
) -> None:
    query_completions = _AsyncCompletions("SELECT id FROM users")
    code_completions = _AsyncCompletions("result = 'ok'")
    model = f"openfang:{EXECUTOR_ROLE}"

    query_agent = attack_module.LlmQueryAgent(_client(query_completions), model)
    code_agent = attack_module.LlmCodeAgent(
        object(), _client(code_completions), model
    )
    query = asyncio.run(
        query_agent.handle_request(attack_module.UserRequest("show users"), None)
    )
    result = asyncio.run(
        code_agent.handle_request(attack_module.UserRequest("write code"), None)
    )

    assert query.query == "SELECT id FROM users"
    assert result == "Code executed. Output: ok"
    assert query_completions.calls[0]["model"] == model
    assert query_completions.calls[0]["temperature"] == 0
    assert query_completions.calls[0]["max_tokens"] == 200
    assert query_completions.calls[0]["messages"] == [
        {"role": "system", "content": QUERY_SYSTEM_PROMPT},
        {"role": "user", "content": "show users"},
    ]
    assert code_completions.calls[0]["model"] == model
    assert code_completions.calls[0]["temperature"] == 0
    assert code_completions.calls[0]["max_tokens"] == 1000
    assert code_completions.calls[0]["messages"] == [
        {"role": "system", "content": CODE_SYSTEM_PROMPT},
        {"role": "user", "content": "write code"},
    ]


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
        attack_module.LlmQueryAgent(client, f"openfang:{EXECUTOR_ROLE}")
        if agent_name == "LlmQueryAgent"
        else attack_module.LlmCodeAgent(
            object(), client, f"openfang:{EXECUTOR_ROLE}"
        )
    )
    request = attack_module.UserRequest("test")

    with pytest.raises(_OpenFangUnavailable) as raised:
        asyncio.run(agent.handle_request(request, None))

    assert raised.value is error
    assert len(completions.calls) == 1


def test_module_import_does_not_access_vibemind_shared(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_import_stubs(monkeypatch)
    monkeypatch.delitem(sys.modules, "vibemind_shared", raising=False)
    real_import = builtins.__import__

    def guarded_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "vibemind_shared":
            raise AssertionError("vibemind_shared must be lazy")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    if str(POC_ROOT) not in sys.path:
        sys.path.insert(0, str(POC_ROOT))
    sys.modules.pop("real_llm_attack", None)

    importlib.import_module("real_llm_attack")


@pytest.mark.parametrize("failing_lookup", ["client", "model"])
def test_llm_boundary_failures_precede_all_demo_side_effects(
    attack_module: Any,
    monkeypatch: pytest.MonkeyPatch,
    failing_lookup: str,
) -> None:
    failure = _OpenFangUnavailable(f"{failing_lookup} unavailable")
    calls: list[tuple[str, str]] = []
    side_effects: list[str] = []

    def client_lookup(role: str) -> Any:
        calls.append(("client", role))
        if failing_lookup == "client":
            raise failure
        return _client(_AsyncCompletions())

    def model_lookup(role: str) -> str:
        calls.append(("model", role))
        raise failure

    setup_db = types.ModuleType("setup_db")
    setup_db.setup = lambda: side_effects.append("setup")
    monkeypatch.setitem(sys.modules, "setup_db", setup_db)
    monkeypatch.setattr(attack_module, "get_client", client_lookup)
    monkeypatch.setattr(attack_module, "get_model", model_lookup)
    monkeypatch.setattr(
        attack_module,
        "show_db_state",
        lambda _: side_effects.append("show"),
    )
    monkeypatch.setattr(
        attack_module,
        "SingleThreadedAgentRuntime",
        lambda: side_effects.append("runtime"),
    )

    with pytest.raises(_OpenFangUnavailable) as raised:
        asyncio.run(attack_module.main())

    assert raised.value is failure
    assert calls == (
        [("client", EXECUTOR_ROLE)]
        if failing_lookup == "client"
        else [("client", EXECUTOR_ROLE), ("model", EXECUTOR_ROLE)]
    )
    assert side_effects == []


def test_source_resolves_one_shared_boundary_before_demo_side_effects() -> None:
    source = (POC_ROOT / "real_llm_attack.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    assert "OPENAI_API_KEY" not in source
    assert "gpt-4o" not in source.lower()
    assert "AsyncOpenAI" not in source
    assert "from vibemind_shared import" in source
    assert not [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        and any(alias.name == "openai" for alias in node.names)
    ]
    assert not [
        node
        for node in tree.body
        if isinstance(node, (ast.Import, ast.ImportFrom))
        and any(alias.name == "vibemind_shared" for alias in node.names)
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
    assert len(model_calls) == 1
    assert isinstance(model_calls[0].args[0], ast.Constant)
    assert model_calls[0].args[0].value == EXECUTOR_ROLE

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

    model_index = next(
        index
        for index, statement in enumerate(statements)
        if any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "get_model"
            for node in ast.walk(statement)
        )
    )
    show_index = next(
        index
        for index, statement in enumerate(statements)
        if any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "show_db_state"
            for node in ast.walk(statement)
        )
    )
    runtime_index = next(
        index
        for index, statement in enumerate(statements)
        if any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "SingleThreadedAgentRuntime"
            for node in ast.walk(statement)
        )
    )
    assert acquisition_index < model_index < setup_index < show_index < runtime_index


def test_docker_packaging_has_exact_shared_pin_and_openfang_only_wiring() -> None:
    dockerfile = (POC_ROOT / "Dockerfile").read_text(encoding="utf-8")
    compose = (POC_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    requirements = (POC_ROOT / "requirements.legit.txt").read_text(encoding="utf-8")

    assert "apt-get install -y --no-install-recommends git" in dockerfile
    assert "rm -rf /var/lib/apt/lists/*" in dockerfile
    assert dockerfile.index("apt-get install") < dockerfile.index("pip install")
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


def test_compose_discloses_the_intentional_secret_isolation_limit() -> None:
    compose = (POC_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    lowered = compose.lower()

    assert "arbitrary model-generated python with os access" in lowered
    assert "not a strong secret-isolation boundary" in lowered
    assert "dedicated, least-privilege, revocable openfang demo key" in lowered
    assert "do not expose unrelated secrets" in lowered
    assert "do not add host mounts" in lowered
    assert sum(
        line.strip().startswith("OPENFANG_URL:") for line in compose.splitlines()
    ) == 1
    assert sum(
        line.strip().startswith("OPENFANG_API_KEY:") for line in compose.splitlines()
    ) == 1
    assert compose.count("- ../../llm_config.yml:/config/llm_config.yml:ro") == 1

    source = (POC_ROOT / "real_llm_attack.py").read_text(encoding="utf-8")
    assert "exec(code, exec_globals)" in source
