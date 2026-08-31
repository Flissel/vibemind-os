"""Hermetic boundary tests for the security-scanner OpenFang migration."""

from __future__ import annotations

import ast
import asyncio
import importlib
import sys
import types
from pathlib import Path
from typing import Any

import pytest


POC_ROOT = Path(__file__).resolve().parents[1] / "poc_security_scanner"
ROLE = "security_analyzer"
SHARED_PIN = "609dda92e0f4370c03085a7e2ff6a6f492693d0d"


class _OpenFangUnavailable(RuntimeError):
    pass


class _AsyncCompletions:
    def __init__(self, text: str = "REASONING:\nok\nFINDINGS:\n```json\n[]\n```\nOVERALL_SEVERITY: INFO", error: Exception | None = None) -> None:
        self.text = text
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return types.SimpleNamespace(
            choices=[types.SimpleNamespace(
                finish_reason="stop",
                message=types.SimpleNamespace(content=self.text, tool_calls=None),
            )]
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
    autogen_core.RoutedAgent = RoutedAgent
    autogen_core.MessageContext = object
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

    class AnalysisRequest:
        def __init__(self, target_host: str, all_results_json: str) -> None:
            self.target_host = target_host
            self.all_results_json = all_results_json

    class SecurityAnalysis:
        def __init__(self, **kwargs: Any) -> None:
            self.__dict__.update(kwargs)

    for name in (
        "ScanTarget", "ScanPlan", "ScanTask", "ScanResult", "ReportRequest",
        "SecurityReport", "ScanEvent",
    ):
        setattr(messages, name, type(name, (), {"__init__": lambda self, **kwargs: self.__dict__.update(kwargs)}))
    messages.AnalysisRequest = AnalysisRequest
    messages.SecurityAnalysis = SecurityAnalysis
    monkeypatch.setitem(sys.modules, "messages", messages)


@pytest.fixture
def scanner_modules(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    _install_import_stubs(monkeypatch)
    shared = types.ModuleType("vibemind_shared")
    shared.OpenFangUnavailable = _OpenFangUnavailable
    shared.get_client = lambda _: _client(_AsyncCompletions())
    shared.get_model = lambda role: f"openfang:{role}"
    monkeypatch.setitem(sys.modules, "vibemind_shared", shared)
    if str(POC_ROOT) not in sys.path:
        sys.path.insert(0, str(POC_ROOT))
    for name in ("tools", "analyzer", "orchestrator", "worker"):
        sys.modules.pop(name, None)
    return {name: importlib.import_module(name) for name in ("tools", "analyzer", "orchestrator")}


def test_all_reachable_llm_calls_resolve_the_security_analyzer_role(
    scanner_modules: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    models: list[str] = []
    completions = _AsyncCompletions()
    client = _client(completions)
    for module in scanner_modules.values():
        monkeypatch.setattr(module, "get_model", lambda role: models.append(role) or f"openfang:{role}")

    analyzer = scanner_modules["analyzer"].AnalyzerAgent(client)
    asyncio.run(analyzer.handle_analysis(scanner_modules["analyzer"].AnalysisRequest("host", "[]"), None))
    asyncio.run(scanner_modules["tools"].think("reason", client))

    assert models == [ROLE, ROLE]
    assert [call["model"] for call in completions.calls] == [f"openfang:{ROLE}"] * 2


@pytest.mark.parametrize(
    "module_name, invoke",
    [
        (
            "orchestrator",
            lambda module, client: module.OrchestratorAgent(client).handle_scan_target(
                module.ScanTarget(host="host", port_range="80", scan_types="ports"), None
            ),
        ),
        ("analyzer", lambda module, client: module.AnalyzerAgent(client).handle_analysis(module.AnalysisRequest("host", "[]"), None)),
        ("tools", lambda module, client: module.think("reason", client)),
    ],
)
def test_request_failures_propagate_unchanged_after_one_request(
    scanner_modules: dict[str, Any], monkeypatch: pytest.MonkeyPatch, module_name: str, invoke: Any
) -> None:
    failure = _OpenFangUnavailable("request unavailable")
    completions = _AsyncCompletions(error=failure)
    module = scanner_modules[module_name]
    monkeypatch.setattr(module, "get_model", lambda role: f"openfang:{role}")

    with pytest.raises(_OpenFangUnavailable) as error:
        asyncio.run(invoke(module, _client(completions)))

    assert error.value is failure
    assert len(completions.calls) == 1
    assert completions.calls[0]["model"] == f"openfang:{ROLE}"


@pytest.mark.parametrize(
    "failure",
    [
        _OpenFangUnavailable("gateway unavailable"),
        FileNotFoundError("/config/llm_config.yml is missing"),
        ValueError("llm_config.yml is invalid"),
    ],
)
def test_worker_acquisition_failure_propagates_unchanged(
    monkeypatch: pytest.MonkeyPatch, failure: Exception
) -> None:
    _install_import_stubs(monkeypatch)
    roles: list[str] = []
    shared = types.ModuleType("vibemind_shared")
    def get_client(role: str) -> Any:
        roles.append(role)
        raise failure
    shared.get_client = get_client
    shared.get_model = lambda role: f"openfang:{role}"
    monkeypatch.setitem(sys.modules, "vibemind_shared", shared)
    if str(POC_ROOT) not in sys.path:
        sys.path.insert(0, str(POC_ROOT))
    for name in ("tools", "analyzer", "orchestrator", "worker"):
        sys.modules.pop(name, None)
    worker = importlib.import_module("worker")
    monkeypatch.setattr(worker, "connect_to_host", lambda _: (_ for _ in ()).throw(AssertionError("must not connect")))

    with pytest.raises(type(failure)) as error:
        asyncio.run(worker.main())

    assert error.value is failure
    assert roles == [ROLE]


def test_sources_use_only_the_shared_security_analyzer_boundary() -> None:
    sources = {name: (POC_ROOT / name).read_text(encoding="utf-8") for name in ("worker.py", "orchestrator.py", "analyzer.py", "tools.py")}
    combined = "\n".join(sources.values()).lower()
    assert "openai_api_key" not in combined
    assert "asyncopenai" not in combined
    assert "gpt-4o" not in combined
    for source in sources.values():
        tree = ast.parse(source)
        assert not [node for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom)) and any(alias.name == "openai" for alias in node.names)]
    model_roles = []
    for name in ("orchestrator.py", "analyzer.py", "tools.py"):
        tree = ast.parse(sources[name])
        constants = {
            node.targets[0].id: node.value.value
            for node in tree.body
            if isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and isinstance(node.value, ast.Constant)
        }
        model_roles.extend(
            constants[node.args[0].id] for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == "get_model" and len(node.args) == 1
            and isinstance(node.args[0], ast.Name)
        )
    assert model_roles == [ROLE, ROLE, ROLE]
    worker_tree = ast.parse(sources["worker.py"])
    worker_constants = {
        node.targets[0].id: node.value.value
        for node in worker_tree.body
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and isinstance(node.value, ast.Constant)
    }
    client_roles = [
        worker_constants[node.args[0].id] for node in ast.walk(worker_tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        and node.func.id == "get_client" and len(node.args) == 1
        and isinstance(node.args[0], ast.Name)
    ]
    assert client_roles == [ROLE]


def test_packaging_is_pinned_and_isolates_openfang_from_host() -> None:
    compose = (POC_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    dockerfile = (POC_ROOT / "Dockerfile.worker").read_text(encoding="utf-8")
    requirements = (POC_ROOT / "requirements.legit.txt").read_text(encoding="utf-8")

    scanner = compose.split("  scanner:\n", 1)[1].split("\nnetworks:", 1)[0]
    host = compose.split("  host:\n", 1)[1].split("  scanner:\n", 1)[0]
    assert "env_file:" not in scanner
    assert "OPENAI_API_KEY" not in scanner
    assert "OPENFANG_URL: ${OPENFANG_URL:?" in scanner
    assert "OPENFANG_API_KEY: ${OPENFANG_API_KEY:?" in scanner
    assert "../../llm_config.yml:/config/llm_config.yml:ro" in scanner
    assert "OPENFANG_" not in host
    assert "ENV VIBEMIND_CONFIG_DIR=/config" in dockerfile
    assert "COPY requirements.legit.txt" in dockerfile
    assert "openai" not in requirements.lower()
    assert requirements.count("vibemind-shared @ git+https://github.com/Flissel/vibemind-shared.git@") == 1
    assert SHARED_PIN in requirements


def test_worker_image_installs_git_before_pip_and_removes_apt_lists() -> None:
    dockerfile = (POC_ROOT / "Dockerfile.worker").read_text(encoding="utf-8")

    apt_update = "apt-get update"
    git_install = "apt-get install -y --no-install-recommends git"
    apt_cleanup = "rm -rf /var/lib/apt/lists/*"
    pip_install = "pip install --no-cache-dir -r requirements.legit.txt"

    assert apt_update in dockerfile
    assert git_install in dockerfile
    assert apt_cleanup in dockerfile
    assert dockerfile.index(apt_update) < dockerfile.index(git_install)
    assert dockerfile.index(git_install) < dockerfile.index(apt_cleanup)
    assert dockerfile.index(apt_cleanup) < dockerfile.index(pip_install)


def test_non_scoped_host_service_block_is_byte_equivalent_to_base() -> None:
    current = (POC_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    base = __import__("subprocess").check_output(
        ["git", "show", "a50be0c106807eadbd8f53cc8a3242979b1c8380:ops/poc_security_scanner/docker-compose.yml"],
        text=True,
    )
    assert current.split("  host:\n", 1)[1].split("  scanner:\n", 1)[0] == base.split("  host:\n", 1)[1].split("  scanner:\n", 1)[0]
