"""Hermetic boundary tests for the default site-verifier OpenFang migration."""

from __future__ import annotations

import ast
import asyncio
import builtins
import importlib
import json
import subprocess
import sys
import types
from pathlib import Path
from typing import Any

import pytest


POC_ROOT = Path(__file__).resolve().parents[1] / "poc_site_verifier"
BASE_SHA = "64ab68b65a699481bddbf5b32b2c83ba7a9a4cfe"
ROLE = "security_analyzer"
MODEL = "openfang:brain-security"
PRODUCTION_FILES = (
    "verify.py", "orchestrator.py", "analyzer.py", "reporter.py", "tools.py", "report_generator.py",
)


class _OpenFangUnavailable(RuntimeError):
    pass


class _AsyncCompletions:
    def __init__(self, responses: list[Any] | None = None, error: Exception | None = None) -> None:
        self.responses = list(responses or [])
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        if self.responses:
            return self.responses.pop(0)
        return _response("ok")


def _response(content: str, *, tool_calls: list[Any] | None = None) -> Any:
    message = types.SimpleNamespace(content=content, tool_calls=tool_calls)
    message.model_dump = lambda: {"role": "assistant", "content": content, "tool_calls": tool_calls}
    return types.SimpleNamespace(
        choices=[types.SimpleNamespace(
            finish_reason="tool_calls" if tool_calls else "stop",
            message=message,
        )]
    )


def _client(completions: _AsyncCompletions) -> Any:
    return types.SimpleNamespace(chat=types.SimpleNamespace(completions=completions))


def _install_import_stubs(monkeypatch: pytest.MonkeyPatch) -> None:
    autogen_core = types.ModuleType("autogen_core")

    class AgentId:
        def __init__(self, *args: Any) -> None:
            self.args = args

    class RoutedAgent:
        def __init__(self, *_: Any) -> None:
            pass

        async def send_message(self, *_: Any, **__: Any) -> Any:
            raise AssertionError("send_message must be replaced by the test")

    class SingleThreadedAgentRuntime:
        pass

    autogen_core.AgentId = AgentId
    autogen_core.RoutedAgent = RoutedAgent
    autogen_core.SingleThreadedAgentRuntime = SingleThreadedAgentRuntime
    autogen_core.MessageContext = object
    autogen_core.message_handler = lambda function: function
    monkeypatch.setitem(sys.modules, "autogen_core", autogen_core)

    dotenv = types.ModuleType("dotenv")
    dotenv.load_dotenv = lambda *_args, **_kwargs: None
    monkeypatch.setitem(sys.modules, "dotenv", dotenv)

    openai = types.ModuleType("openai")
    openai.AsyncOpenAI = object
    monkeypatch.setitem(sys.modules, "openai", openai)

    shared = types.ModuleType("vibemind_shared")
    shared.__path__ = []
    shared.OpenFangUnavailable = _OpenFangUnavailable
    shared.get_client = lambda _role: _client(_AsyncCompletions())
    shared.get_model = lambda _role: MODEL
    monkeypatch.setitem(sys.modules, "vibemind_shared", shared)

    shared_llm_client = types.ModuleType("vibemind_shared.llm_client")
    shared_llm_client.get_client = shared.get_client
    shared_llm_client.get_model = lambda role, *_args: shared.get_model(role)
    shared_llm_client.get_config = lambda: {}
    shared_llm_client.get_temperature = lambda _role: 0
    monkeypatch.setitem(sys.modules, "vibemind_shared.llm_client", shared_llm_client)

    browser_verify = types.ModuleType("browser_verify")
    browser_verify.browser_verify = lambda *_args, **_kwargs: None
    monkeypatch.setitem(sys.modules, "browser_verify", browser_verify)


def _import_modules(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    _install_import_stubs(monkeypatch)
    monkeypatch.syspath_prepend(str(POC_ROOT))
    for name in (
        "llm_client", "messages", "tools", "checker", "analyzer", "reporter", "orchestrator", "verify",
        "report_generator",
    ):
        sys.modules.pop(name, None)
    return {
        name: importlib.import_module(name)
        for name in ("messages", "tools", "analyzer", "reporter", "orchestrator", "verify", "report_generator")
    }


@pytest.fixture
def site_modules(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    return _import_modules(monkeypatch)


def test_modules_import_without_shared_or_provider_access(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_import_stubs(monkeypatch)
    monkeypatch.syspath_prepend(str(POC_ROOT))
    for name in (
        "llm_client", "messages", "tools", "checker", "analyzer", "reporter", "orchestrator", "verify",
        "report_generator",
    ):
        sys.modules.pop(name, None)

    real_import = builtins.__import__

    def guarded_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "openai" or name.startswith("vibemind_shared") or name == "llm_client":
            raise AssertionError(f"provider access during import: {name}")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    for name in ("tools", "analyzer", "reporter", "orchestrator", "verify", "report_generator"):
        importlib.import_module(name)


class _RegisteredAgent:
    registrations: list[tuple[str, tuple[Any, ...]]] = []

    def __init__(self, *args: Any) -> None:
        self.args = args

    @classmethod
    async def register(cls, _runtime: Any, name: str, factory: Any) -> None:
        cls.registrations.append((name, factory().args))


class _FakeRuntime:
    def __init__(
        self,
        events: list[str],
        result: Any = None,
        failure: Exception | None = None,
        stop_failure: Exception | None = None,
    ) -> None:
        self.events = events
        self.result = result
        self.failure = failure
        self.stop_failure = stop_failure
        self.send_count = 0
        self.events.append("runtime")

    def start(self) -> None:
        self.events.append("start")

    async def send_message(self, *_args: Any, **_kwargs: Any) -> Any:
        self.send_count += 1
        self.events.append("send")
        if self.failure is not None:
            raise self.failure
        return self.result

    async def stop(self) -> None:
        self.events.append("stop")
        if self.stop_failure is not None:
            raise self.stop_failure


def _report(messages: Any) -> Any:
    return messages.AuthenticityReport(
        report_text="report",
        url="https://example.com",
        domain="example.com",
        verdict="AUTHENTIC",
        confidence="HIGH",
        finding_count=0,
        red_flag_count=0,
    )


def test_verify_site_resolves_boundary_once_before_runtime_and_injects_it(
    site_modules: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    verify = site_modules["verify"]
    events: list[str] = []
    completions = _AsyncCompletions()
    client = _client(completions)
    runtime_holder: dict[str, _FakeRuntime] = {}
    _RegisteredAgent.registrations = []

    def make_runtime() -> _FakeRuntime:
        runtime = _FakeRuntime(events, result=_report(site_modules["messages"]))
        runtime_holder["runtime"] = runtime
        return runtime

    monkeypatch.setattr(verify, "get_client", lambda role: events.append(f"client:{role}") or client)
    monkeypatch.setattr(verify, "get_model", lambda role: events.append(f"model:{role}") or MODEL)
    monkeypatch.setattr(verify, "SingleThreadedAgentRuntime", make_runtime)
    monkeypatch.setattr(verify, "OrchestratorAgent", _RegisteredAgent)
    monkeypatch.setattr(verify, "AnalyzerAgent", _RegisteredAgent)
    monkeypatch.setattr(verify, "ReporterAgent", _RegisteredAgent)
    monkeypatch.setattr(verify, "CheckerAgent", _RegisteredAgent)

    result = asyncio.run(verify.verify_site("example.com"))

    assert result.verdict == "AUTHENTIC"
    assert events[:3] == [f"client:{ROLE}", f"model:{ROLE}", "runtime"]
    assert events.count(f"client:{ROLE}") == 1
    assert events.count(f"model:{ROLE}") == 1
    assert runtime_holder["runtime"].send_count == 1
    registrations = dict(_RegisteredAgent.registrations)
    assert registrations == {
        "orchestrator": (client, MODEL),
        "checker_agent": (),
        "analyzer_agent": (client, MODEL),
        "reporter_agent": (client, MODEL),
    }
    assert events[-1] == "stop"


@pytest.mark.parametrize(
    "boundary,failure",
    [
        ("client", _OpenFangUnavailable("gateway unavailable")),
        ("client", FileNotFoundError("/config/llm_config.yml is missing")),
        ("model", ValueError("llm_config.yml is invalid")),
    ],
)
def test_acquisition_failures_precede_runtime_and_propagate_unchanged(
    site_modules: dict[str, Any], monkeypatch: pytest.MonkeyPatch, boundary: str, failure: Exception
) -> None:
    verify = site_modules["verify"]
    calls: list[str] = []

    def get_client(role: str) -> Any:
        calls.append(f"client:{role}")
        if boundary == "client":
            raise failure
        return _client(_AsyncCompletions())

    def get_model(role: str) -> str:
        calls.append(f"model:{role}")
        if boundary == "model":
            raise failure
        return MODEL

    monkeypatch.setattr(verify, "get_client", get_client)
    monkeypatch.setattr(verify, "get_model", get_model)
    monkeypatch.setattr(
        verify,
        "SingleThreadedAgentRuntime",
        lambda: (_ for _ in ()).throw(AssertionError("runtime must not be created")),
    )

    with pytest.raises(type(failure)) as error:
        asyncio.run(verify.verify_site("example.com"))

    assert error.value is failure
    assert calls == ([f"client:{ROLE}"] if boundary == "client" else [f"client:{ROLE}", f"model:{ROLE}"])


def test_runtime_stops_and_request_failure_propagates_without_retry(
    site_modules: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    verify = site_modules["verify"]
    failure = _OpenFangUnavailable("request unavailable")
    events: list[str] = []
    runtime = _FakeRuntime(events, failure=failure)
    _RegisteredAgent.registrations = []

    monkeypatch.setattr(verify, "get_client", lambda _role: _client(_AsyncCompletions()))
    monkeypatch.setattr(verify, "get_model", lambda _role: MODEL)
    monkeypatch.setattr(verify, "SingleThreadedAgentRuntime", lambda: runtime)
    monkeypatch.setattr(verify, "OrchestratorAgent", _RegisteredAgent)
    monkeypatch.setattr(verify, "AnalyzerAgent", _RegisteredAgent)
    monkeypatch.setattr(verify, "ReporterAgent", _RegisteredAgent)
    monkeypatch.setattr(verify, "CheckerAgent", _RegisteredAgent)

    with pytest.raises(_OpenFangUnavailable) as error:
        asyncio.run(verify.verify_site("example.com"))

    assert error.value is failure
    assert runtime.send_count == 1
    assert events[-2:] == ["send", "stop"]


def test_runtime_stop_failure_does_not_replace_primary_request_failure(
    site_modules: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    verify = site_modules["verify"]
    primary = _OpenFangUnavailable("request unavailable")
    cleanup = RuntimeError("runtime stop failed")
    events: list[str] = []
    runtime = _FakeRuntime(events, failure=primary, stop_failure=cleanup)
    _RegisteredAgent.registrations = []

    monkeypatch.setattr(verify, "get_client", lambda _role: _client(_AsyncCompletions()))
    monkeypatch.setattr(verify, "get_model", lambda _role: MODEL)
    monkeypatch.setattr(verify, "SingleThreadedAgentRuntime", lambda: runtime)
    monkeypatch.setattr(verify, "OrchestratorAgent", _RegisteredAgent)
    monkeypatch.setattr(verify, "AnalyzerAgent", _RegisteredAgent)
    monkeypatch.setattr(verify, "ReporterAgent", _RegisteredAgent)
    monkeypatch.setattr(verify, "CheckerAgent", _RegisteredAgent)

    with pytest.raises(_OpenFangUnavailable) as error:
        asyncio.run(verify.verify_site("example.com"))

    assert error.value is primary
    assert runtime.send_count == 1
    assert events[-2:] == ["send", "stop"]


@pytest.mark.parametrize(
    "boundary,failure",
    [
        ("client", _OpenFangUnavailable("gateway unavailable")),
        ("client", FileNotFoundError("/config/llm_config.yml is missing")),
        ("model", ValueError("llm_config.yml is invalid")),
    ],
)
def test_report_generator_acquires_boundary_before_any_checks(
    site_modules: dict[str, Any], monkeypatch: pytest.MonkeyPatch, boundary: str, failure: Exception
) -> None:
    report_generator = site_modules["report_generator"]
    calls: list[str] = []

    def get_client(role: str) -> Any:
        calls.append(f"client:{role}")
        if boundary == "client":
            raise failure
        return _client(_AsyncCompletions())

    def get_model(role: str) -> str:
        calls.append(f"model:{role}")
        if boundary == "model":
            raise failure
        return MODEL

    async def must_not_check(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("network/check side effects must not run")

    monkeypatch.setattr(report_generator, "get_client", get_client, raising=False)
    monkeypatch.setattr(report_generator, "get_model", get_model, raising=False)
    monkeypatch.setattr(report_generator.asyncio, "gather", must_not_check)

    with pytest.raises(type(failure)) as error:
        asyncio.run(report_generator.generate_report("https://example.com"))

    assert error.value is failure
    assert calls == ([f"client:{ROLE}"] if boundary == "client" else [f"client:{ROLE}", f"model:{ROLE}"])


@pytest.mark.parametrize(
    "failure",
    [
        _OpenFangUnavailable("request unavailable"),
        ValueError("provider response invalid"),
    ],
)
def test_report_llm_request_failure_propagates_unchanged_after_one_call(
    site_modules: dict[str, Any], failure: Exception
) -> None:
    report_generator = site_modules["report_generator"]
    completions = _AsyncCompletions(error=failure)

    with pytest.raises(type(failure)) as error:
        asyncio.run(report_generator._request_llm_report(_client(completions), MODEL, "system", "user"))

    assert error.value is failure
    assert completions.calls == [{
        "model": MODEL,
        "temperature": 0,
        "max_tokens": 8000,
        "messages": [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "user"},
        ],
    }]


def test_report_llm_json_decode_failure_propagates_unchanged_after_one_call(
    site_modules: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    report_generator = site_modules["report_generator"]
    completions = _AsyncCompletions([_response("not json")])
    failure = json.JSONDecodeError("invalid", "not json", 0)
    monkeypatch.setattr(report_generator.json, "loads", lambda _text: (_ for _ in ()).throw(failure))

    with pytest.raises(json.JSONDecodeError) as error:
        asyncio.run(report_generator._request_llm_report(_client(completions), MODEL, "system", "user"))

    assert error.value is failure
    assert len(completions.calls) == 1


def test_all_four_llm_calls_use_injected_model_and_preserve_request_contracts(
    site_modules: dict[str, Any]
) -> None:
    messages = site_modules["messages"]
    completions = _AsyncCompletions([
        _response("done"),
        _response("REASONING:\nok\nFINDINGS:\n```json\n[]\n```\nVERDICT: AUTHENTIC\nCONFIDENCE: HIGH"),
        _response("report"),
        _response("REASONING:\nok\nCONCLUSION: fine\nVERDICT: AUTHENTIC"),
    ])
    client = _client(completions)

    orchestrator = site_modules["orchestrator"].OrchestratorAgent(client, MODEL)

    async def send_message(message: Any, *_args: Any, **_kwargs: Any) -> Any:
        if isinstance(message, messages.AnalysisRequest):
            return messages.AuthenticityAnalysis("ok", "AUTHENTIC", "HIGH", "[]")
        return messages.AuthenticityReport("report", message.url, message.domain, "AUTHENTIC", "HIGH", 0, 0)

    orchestrator.send_message = send_message
    asyncio.run(orchestrator.handle_verify_target(messages.VerifyTarget("https://example.com", "example.com", "ssl"), None))
    analyzer = site_modules["analyzer"].AnalyzerAgent(client, MODEL)
    asyncio.run(analyzer.handle_analysis(messages.AnalysisRequest("https://example.com", "example.com", "[]"), None))
    reporter = site_modules["reporter"].ReporterAgent(client, MODEL)
    asyncio.run(reporter.handle_report(messages.ReportRequest(
        "https://example.com", "example.com", "[]", "ok", "AUTHENTIC", "HIGH", "[]"
    ), None))
    asyncio.run(site_modules["tools"].think("reason", client, MODEL))

    assert len(completions.calls) == 4
    assert [call["model"] for call in completions.calls] == [MODEL] * 4
    assert [call["temperature"] for call in completions.calls] == [0] * 4
    assert completions.calls[0]["tools"] is site_modules["orchestrator"].TOOL_DEFINITIONS
    assert completions.calls[0]["tool_choice"] == "auto"
    assert set(completions.calls[0]) == {"model", "temperature", "messages", "tools", "tool_choice"}
    assert all(set(call) == {"model", "temperature", "messages"} for call in completions.calls[1:])
    assert completions.calls[3]["messages"][-1] == {"role": "user", "content": "reason"}


def test_think_openfang_failure_is_not_swallowed_by_orchestrator(site_modules: dict[str, Any]) -> None:
    failure = _OpenFangUnavailable("think unavailable")
    tool_call = types.SimpleNamespace(
        id="call-1",
        function=types.SimpleNamespace(name="think", arguments='{"reasoning_prompt":"reason"}'),
    )
    completions = _AsyncCompletions([_response("", tool_calls=[tool_call])])
    client = _client(completions)
    orchestrator = site_modules["orchestrator"].OrchestratorAgent(client, MODEL)

    async def fail_think(_prompt: str, _client: Any, _model: str) -> Any:
        raise failure

    site_modules["orchestrator"].think = fail_think

    with pytest.raises(_OpenFangUnavailable) as error:
        asyncio.run(orchestrator.handle_verify_target(
            site_modules["messages"].VerifyTarget("https://example.com", "example.com", "ssl"), None
        ))

    assert error.value is failure
    assert len(completions.calls) == 1


def _request_contract_nodes(source: str, function_name: str) -> list[str]:
    tree = ast.parse(source)
    function = next(
        node for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function_name
    )
    contracts: list[ast.AST] = []
    for node in ast.walk(function):
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id in {"system_prompt", "user_prompt"}
        ):
            contracts.append(node.value)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "create":
            contracts.extend(
                keyword.value for keyword in node.keywords
                if keyword.arg in {"temperature", "messages", "tools", "tool_choice"}
            )
    return [ast.dump(node, include_attributes=False) for node in contracts]


def test_prompt_literals_are_byte_equivalent_to_base() -> None:
    functions = {
        "orchestrator.py": "handle_verify_target",
        "analyzer.py": "handle_analysis",
        "reporter.py": "handle_report",
        "tools.py": "think",
    }
    for path, function_name in functions.items():
        current = (POC_ROOT / path).read_text(encoding="utf-8")
        base = subprocess.check_output(
            ["git", "show", f"{BASE_SHA}:ops/poc_site_verifier/{path}"], text=True, encoding="utf-8"
        )
        assert _request_contract_nodes(current, function_name) == _request_contract_nodes(base, function_name)


def test_report_generator_prompt_literals_are_byte_equivalent_to_base() -> None:
    current = (POC_ROOT / "report_generator.py").read_text(encoding="utf-8")
    base = subprocess.check_output(
        ["git", "show", f"{BASE_SHA}:ops/poc_site_verifier/report_generator.py"],
        text=True,
        encoding="utf-8",
    )

    def prompt_assignments(source: str) -> list[str]:
        tree = ast.parse(source)
        function = next(
            node for node in ast.walk(tree)
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "generate_report"
        )
        return [
            ast.dump(node.value, include_attributes=False)
            for node in ast.walk(function)
            if isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id in {"system_prompt", "user_prompt"}
        ]

    assert prompt_assignments(current) == prompt_assignments(base)


def test_sources_use_only_lazy_shared_security_analyzer_boundary() -> None:
    sources = {name: (POC_ROOT / name).read_text(encoding="utf-8") for name in PRODUCTION_FILES}
    combined = "\n".join(sources.values())
    assert "OPENAI_API_KEY" not in combined
    assert "AsyncOpenAI" not in combined
    assert "from llm_client import" not in combined

    for source in sources.values():
        tree = ast.parse(source)
        assert not [
            node for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            and any(alias.name == "openai" for alias in node.names)
        ]
        assert not [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "get_model"
            and len(node.args) != 1
        ]

    verify_tree = ast.parse(sources["verify.py"])
    role_constants = {
        node.targets[0].id: node.value.value
        for node in verify_tree.body
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and isinstance(node.value, ast.Constant)
    }
    client_roles = [
        role_constants[node.args[0].id]
        for node in ast.walk(verify_tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "get_client"
        and len(node.args) == 1
        and isinstance(node.args[0], ast.Name)
    ]
    model_roles = [
        role_constants[node.args[0].id]
        for node in ast.walk(verify_tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "get_model"
        and len(node.args) == 1
        and isinstance(node.args[0], ast.Name)
    ]
    assert client_roles == [ROLE]
    assert model_roles == [ROLE]
