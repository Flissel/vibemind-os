"""Hermetic boundary tests for the OS Shield OpenFang migration."""

from __future__ import annotations

import ast
import asyncio
import builtins
import importlib
import subprocess
import sys
import types
from pathlib import Path
from typing import Any

import pytest


POC_ROOT = Path(__file__).resolve().parents[1] / "poc_os_shield"
ROLE = "security_blue_team"
MODEL = "openfang:brain-security"
PRODUCTION_FILES = (
    "main.py",
    "config.py",
    "orchestrator.py",
    "analyzer.py",
    "tools.py",
    "report_generator.py",
)


class _OpenFangUnavailable(RuntimeError):
    pass


class _AsyncCompletions:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        message = types.SimpleNamespace(content="REASONING:\nok\nFINDINGS:\n```json\n[]\n```\nOVERALL_SEVERITY: INFO", tool_calls=None)
        message.model_dump = lambda: {"role": "assistant", "content": message.content}
        return types.SimpleNamespace(
            choices=[types.SimpleNamespace(finish_reason="stop", message=message)]
        )


def _client(completions: _AsyncCompletions) -> Any:
    return types.SimpleNamespace(chat=types.SimpleNamespace(completions=completions))


def _message_type(name: str) -> type[Any]:
    return type(name, (), {"__init__": lambda self, **kwargs: self.__dict__.update(kwargs)})


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
    autogen_core.SingleThreadedAgentRuntime = object
    autogen_core.MessageContext = object
    autogen_core.message_handler = lambda function: function
    monkeypatch.setitem(sys.modules, "autogen_core", autogen_core)

    messages = types.ModuleType("messages")
    for name in (
        "ShieldRequest", "MonitorTask", "MonitorResult", "ThreatAnalysisRequest",
        "ThreatAnalysis", "EnforceRequest", "EnforceResult", "ReportRequest", "SecurityReport",
    ):
        setattr(messages, name, _message_type(name))
    monkeypatch.setitem(sys.modules, "messages", messages)

    config = types.ModuleType("config")
    config.WATCH_INTERVAL = 30
    config.AUTORUN_KEYS = []
    config.HIVE_NAMES = {}
    config.MAX_FILES_PER_DIR = 1
    config.SUSPICIOUS_OUTBOUND_PORTS = []
    config.SUSPICIOUS_PROCESS_NAMES = []
    monkeypatch.setitem(sys.modules, "config", config)

    shared = types.ModuleType("vibemind_shared")
    shared.get_client = lambda _role: _client(_AsyncCompletions())
    shared.get_model = lambda _role: MODEL
    monkeypatch.setitem(sys.modules, "vibemind_shared", shared)

    for name in ("main", "tools", "analyzer", "orchestrator", "report_generator"):
        sys.modules.pop(name, None)


def _install_main_dependencies(monkeypatch: pytest.MonkeyPatch) -> None:
    for module_name, class_name in (
        ("orchestrator", "OrchestratorAgent"),
        ("monitor_agent", "MonitorAgent"),
        ("analyzer", "ThreatAnalyzerAgent"),
        ("enforcer", "EnforcerAgent"),
        ("reporter", "ReporterAgent"),
    ):
        module = types.ModuleType(module_name)
        setattr(module, class_name, type(class_name, (), {}))
        monkeypatch.setitem(sys.modules, module_name, module)
    baselines = types.ModuleType("baselines")
    baselines.capture_baseline = None
    baselines.save_baseline = None
    baselines.load_baseline = None
    monkeypatch.setitem(sys.modules, "baselines", baselines)


@pytest.fixture
def shield_modules(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    _install_import_stubs(monkeypatch)
    monkeypatch.syspath_prepend(str(POC_ROOT))
    return {
        name: importlib.import_module(name)
        for name in ("tools", "analyzer", "orchestrator")
    }


def test_all_os_shield_modules_import_without_provider_or_env_file_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_import_stubs(monkeypatch)
    monkeypatch.syspath_prepend(str(POC_ROOT))
    sys.modules.pop("config", None)
    winreg = types.ModuleType("winreg")
    for name in ("HKEY_LOCAL_MACHINE", "HKEY_CURRENT_USER", "HKEY_CLASSES_ROOT"):
        setattr(winreg, name, name)
    monkeypatch.setitem(sys.modules, "winreg", winreg)
    vm_detection_tools = types.ModuleType("vm_detection_tools")

    async def scan_vm_threats(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {}

    vm_detection_tools.scan_vm_threats = scan_vm_threats
    monkeypatch.setitem(sys.modules, "vm_detection_tools", vm_detection_tools)

    for module_name, class_name in (
        ("monitor_agent", "MonitorAgent"),
        ("enforcer", "EnforcerAgent"),
        ("reporter", "ReporterAgent"),
    ):
        module = types.ModuleType(module_name)
        setattr(module, class_name, type(class_name, (), {}))
        monkeypatch.setitem(sys.modules, module_name, module)
    baselines = types.ModuleType("baselines")
    baselines.capture_baseline = None
    baselines.save_baseline = None
    baselines.load_baseline = None
    monkeypatch.setitem(sys.modules, "baselines", baselines)

    real_import = builtins.__import__

    def guarded_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "openai" or name.startswith("vibemind_shared") or name == "llm_client":
            raise AssertionError(f"provider access during import: {name}")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    for name in ("config", "tools", "analyzer", "orchestrator", "report_generator", "main"):
        sys.modules.pop(name, None)
        importlib.import_module(name)


def test_sources_use_only_lazy_shared_security_blue_team_boundary() -> None:
    sources = {name: (POC_ROOT / name).read_text(encoding="utf-8") for name in PRODUCTION_FILES}
    combined = "\n".join(sources.values())

    assert "OPENAI_API_KEY" not in combined
    assert "AsyncOpenAI" not in combined
    assert "from llm_client import" not in combined
    assert "load_dotenv" not in combined
    for source in sources.values():
        tree = ast.parse(source)
        assert not [
            node for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            and any(alias.name == "openai" for alias in node.names)
        ]


def test_all_llm_requests_preserve_their_existing_keyword_contracts() -> None:
    expected_keywords = {
        "orchestrator.py": {"model", "temperature", "messages", "tools", "tool_choice"},
        "analyzer.py": {"model", "temperature", "messages"},
        "tools.py": {"model", "temperature", "messages"},
        "report_generator.py": {"model", "temperature", "messages"},
    }

    for filename, expected in expected_keywords.items():
        tree = ast.parse((POC_ROOT / filename).read_text(encoding="utf-8"))
        calls = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "create"
            and isinstance(node.func.value, ast.Attribute)
            and node.func.value.attr == "completions"
        ]
        assert len(calls) == 1
        assert {keyword.arg for keyword in calls[0].keywords} == expected


def test_main_acquires_openfang_pair_before_admin_or_runtime_side_effects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_import_stubs(monkeypatch)
    _install_main_dependencies(monkeypatch)
    monkeypatch.syspath_prepend(str(POC_ROOT))
    main = importlib.import_module("main")
    events: list[str] = []
    stop = _OpenFangUnavailable("stop after ordering check")

    monkeypatch.setattr(main, "get_client", lambda role: events.append(f"client:{role}") or object())
    monkeypatch.setattr(main, "get_model", lambda role: events.append(f"model:{role}") or MODEL)
    monkeypatch.setattr(main, "is_admin", lambda: events.append("admin") or (_ for _ in ()).throw(stop))
    monkeypatch.setattr(sys, "argv", ["main.py", "--scan"])

    with pytest.raises(_OpenFangUnavailable) as error:
        asyncio.run(main.main())

    assert error.value is stop
    assert events == [f"client:{ROLE}", f"model:{ROLE}", "admin"]


def test_report_generator_acquires_openfang_pair_before_host_or_scan_side_effects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_import_stubs(monkeypatch)
    monkeypatch.syspath_prepend(str(POC_ROOT))
    report_generator = importlib.import_module("report_generator")
    events: list[str] = []
    stop = _OpenFangUnavailable("stop after ordering check")

    monkeypatch.setattr(report_generator, "get_client", lambda role: events.append(f"client:{role}") or object())
    monkeypatch.setattr(report_generator, "get_model", lambda role: events.append(f"model:{role}") or MODEL)
    monkeypatch.setattr(
        report_generator.socket,
        "gethostname",
        lambda: events.append("hostname") or (_ for _ in ()).throw(stop),
    )

    with pytest.raises(_OpenFangUnavailable) as error:
        asyncio.run(report_generator.generate_report("report.html"))

    assert error.value is stop
    assert events == [f"client:{ROLE}", f"model:{ROLE}", "hostname"]


def test_injected_pair_preserves_each_existing_request_contract(
    shield_modules: dict[str, Any],
) -> None:
    completions = _AsyncCompletions()
    client = _client(completions)
    analyzer = shield_modules["analyzer"].ThreatAnalyzerAgent(client, MODEL)
    request = shield_modules["analyzer"].ThreatAnalysisRequest(
        context="oneshot", all_results_json="[]"
    )

    asyncio.run(analyzer.handle_analysis(request, None))
    asyncio.run(shield_modules["tools"].think("reasoning", client, MODEL))

    assert [call["model"] for call in completions.calls] == [MODEL, MODEL]
    assert [call["temperature"] for call in completions.calls] == [0, 0]
    assert all(set(call) == {"model", "temperature", "messages"} for call in completions.calls)


@pytest.mark.parametrize("target", ("analyzer", "tools"))
def test_request_failure_propagates_unchanged_without_retry(
    shield_modules: dict[str, Any], target: str,
) -> None:
    failure = _OpenFangUnavailable("gateway unavailable")
    completions = _AsyncCompletions(error=failure)
    client = _client(completions)

    with pytest.raises(_OpenFangUnavailable) as error:
        if target == "analyzer":
            request = shield_modules["analyzer"].ThreatAnalysisRequest(
                context="oneshot", all_results_json="[]"
            )
            asyncio.run(shield_modules["analyzer"].ThreatAnalyzerAgent(client, MODEL).handle_analysis(request, None))
        else:
            asyncio.run(shield_modules["tools"].think("reasoning", client, MODEL))

    assert error.value is failure
    assert len(completions.calls) == 1


@pytest.mark.parametrize(
    "boundary,failure",
    (
        ("client", _OpenFangUnavailable("gateway unavailable")),
        ("client", FileNotFoundError("/config/llm_config.yml")),
        ("model", ValueError("llm_config.yml is invalid")),
    ),
)
def test_main_boundary_acquisition_failures_precede_admin_and_propagate_identity(
    monkeypatch: pytest.MonkeyPatch, boundary: str, failure: Exception,
) -> None:
    _install_import_stubs(monkeypatch)
    _install_main_dependencies(monkeypatch)
    monkeypatch.syspath_prepend(str(POC_ROOT))
    main = importlib.import_module("main")
    events: list[str] = []

    def get_client(role: str) -> Any:
        events.append(f"client:{role}")
        if boundary == "client":
            raise failure
        return object()

    def get_model(role: str) -> str:
        events.append(f"model:{role}")
        if boundary == "model":
            raise failure
        return MODEL

    monkeypatch.setattr(main, "get_client", get_client)
    monkeypatch.setattr(main, "get_model", get_model)
    monkeypatch.setattr(main, "is_admin", lambda: (_ for _ in ()).throw(AssertionError("admin must not run")))
    monkeypatch.setattr(sys, "argv", ["main.py", "--scan"])

    with pytest.raises(type(failure)) as error:
        asyncio.run(main.main())

    assert error.value is failure
    assert events == ([f"client:{ROLE}"] if boundary == "client" else [f"client:{ROLE}", f"model:{ROLE}"])


@pytest.mark.parametrize(
    "boundary,failure",
    (
        ("client", _OpenFangUnavailable("gateway unavailable")),
        ("client", FileNotFoundError("/config/llm_config.yml")),
        ("model", ValueError("llm_config.yml is invalid")),
    ),
)
def test_report_boundary_acquisition_failures_precede_host_probe_and_propagate_identity(
    monkeypatch: pytest.MonkeyPatch, boundary: str, failure: Exception,
) -> None:
    _install_import_stubs(monkeypatch)
    monkeypatch.syspath_prepend(str(POC_ROOT))
    report_generator = importlib.import_module("report_generator")
    events: list[str] = []

    def get_client(role: str) -> Any:
        events.append(f"client:{role}")
        if boundary == "client":
            raise failure
        return object()

    def get_model(role: str) -> str:
        events.append(f"model:{role}")
        if boundary == "model":
            raise failure
        return MODEL

    monkeypatch.setattr(report_generator, "get_client", get_client)
    monkeypatch.setattr(report_generator, "get_model", get_model)
    monkeypatch.setattr(report_generator.socket, "gethostname", lambda: (_ for _ in ()).throw(AssertionError("host probe must not run")))

    with pytest.raises(type(failure)) as error:
        asyncio.run(report_generator.generate_report("report.html"))

    assert error.value is failure
    assert events == ([f"client:{ROLE}"] if boundary == "client" else [f"client:{ROLE}", f"model:{ROLE}"])


def test_main_stops_runtime_without_masking_primary_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_import_stubs(monkeypatch)
    _install_main_dependencies(monkeypatch)
    monkeypatch.syspath_prepend(str(POC_ROOT))
    main = importlib.import_module("main")
    primary = _OpenFangUnavailable("request unavailable")
    cleanup = RuntimeError("stop failed")
    events: list[str] = []

    class Runtime:
        def start(self) -> None:
            events.append("start")

        async def stop(self) -> None:
            events.append("stop")
            raise cleanup

    class Registered:
        def __init__(self, *_: Any, **__: Any) -> None:
            pass

        @classmethod
        async def register(cls, _runtime: Any, _name: str, factory: Any) -> None:
            factory()

    monkeypatch.setattr(main, "get_client", lambda _role: object())
    monkeypatch.setattr(main, "get_model", lambda _role: MODEL)
    monkeypatch.setattr(main, "is_admin", lambda: False)
    monkeypatch.setattr(main, "SingleThreadedAgentRuntime", Runtime)
    for name in ("OrchestratorAgent", "MonitorAgent", "ThreatAnalyzerAgent", "EnforcerAgent", "ReporterAgent"):
        monkeypatch.setattr(main, name, Registered)

    async def load_baseline(_path: str) -> dict[str, Any]:
        return {}

    async def fail_scan(*_args: Any, **_kwargs: Any) -> Any:
        raise primary

    monkeypatch.setattr(main, "load_baseline", load_baseline)
    monkeypatch.setattr(main, "run_scan", fail_scan)
    monkeypatch.setattr(sys, "argv", ["main.py", "--scan"])

    with pytest.raises(_OpenFangUnavailable) as error:
        asyncio.run(main.main())

    assert error.value is primary
    assert events == ["start", "stop"]


def test_main_stops_runtime_without_masking_system_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_import_stubs(monkeypatch)
    _install_main_dependencies(monkeypatch)
    monkeypatch.syspath_prepend(str(POC_ROOT))
    main = importlib.import_module("main")
    sentinel = SystemExit(29)
    cleanup = RuntimeError("stop failed")
    stop_calls = 0

    class Runtime:
        def start(self) -> None:
            pass

        async def stop(self) -> None:
            nonlocal stop_calls
            stop_calls += 1
            raise cleanup

    class Registered:
        def __init__(self, *_: Any, **__: Any) -> None:
            pass

        @classmethod
        async def register(cls, _runtime: Any, _name: str, factory: Any) -> None:
            factory()

    async def load_baseline(_path: str) -> dict[str, Any]:
        return {}

    async def run_scan(*_args: Any, **_kwargs: Any) -> Any:
        return types.SimpleNamespace(overall_severity="CRITICAL")

    async def capture_baseline() -> dict[str, Any]:
        return {}

    async def save_baseline(_baseline: dict[str, Any], _path: str) -> None:
        pass

    monkeypatch.setattr(main, "get_client", lambda _role: object())
    monkeypatch.setattr(main, "get_model", lambda _role: MODEL)
    monkeypatch.setattr(main, "is_admin", lambda: False)
    monkeypatch.setattr(main, "SingleThreadedAgentRuntime", Runtime)
    for name in ("OrchestratorAgent", "MonitorAgent", "ThreatAnalyzerAgent", "EnforcerAgent", "ReporterAgent"):
        monkeypatch.setattr(main, name, Registered)
    monkeypatch.setattr(main, "load_baseline", load_baseline)
    monkeypatch.setattr(main, "run_scan", run_scan)
    monkeypatch.setattr(main, "capture_baseline", capture_baseline)
    monkeypatch.setattr(main, "save_baseline", save_baseline)
    monkeypatch.setattr(main.sys, "exit", lambda _code: (_ for _ in ()).throw(sentinel))
    monkeypatch.setattr(sys, "argv", ["main.py", "--scan"])

    with pytest.raises(SystemExit) as error:
        asyncio.run(main.main())

    assert error.value is sentinel
    assert stop_calls == 1


def test_orchestrator_uses_the_injected_model_and_preserves_tool_request_shape(
    shield_modules: dict[str, Any],
) -> None:
    orchestrator_module = shield_modules["orchestrator"]
    completions = _AsyncCompletions()
    agent = orchestrator_module.OrchestratorAgent(_client(completions), MODEL)
    analysis = orchestrator_module.ThreatAnalysis(
        reasoning="ok", severity="INFO", findings_json="[]", recommended_actions_json="[]"
    )
    report = orchestrator_module.SecurityReport(
        report_text="ok", overall_severity="INFO", finding_count=0, actions_taken=0
    )

    async def send_message(_message: Any, recipient: Any) -> Any:
        return analysis if recipient.args[0] == "analyzer_agent" else report

    agent.send_message = send_message
    request = orchestrator_module.ShieldRequest(
        scan_domains="process", mode="oneshot", baseline_json=""
    )

    assert asyncio.run(agent.handle_shield_request(request, None)) is report
    assert completions.calls == [{
        "model": MODEL,
        "temperature": 0,
        "messages": completions.calls[0]["messages"],
        "tools": orchestrator_module.TOOL_DEFINITIONS,
        "tool_choice": "auto",
    }]


def test_orchestrator_does_not_swallow_a_think_failure(
    shield_modules: dict[str, Any], monkeypatch: pytest.MonkeyPatch,
) -> None:
    orchestrator_module = shield_modules["orchestrator"]
    primary = _OpenFangUnavailable("think unavailable")
    tool_call = types.SimpleNamespace(
        id="call-1",
        function=types.SimpleNamespace(name="think", arguments='{"reasoning_prompt": "reason"}'),
    )
    message = types.SimpleNamespace(content=None, tool_calls=[tool_call])
    message.model_dump = lambda: {"role": "assistant", "tool_calls": []}
    response = types.SimpleNamespace(
        choices=[types.SimpleNamespace(finish_reason="tool_calls", message=message)]
    )

    class Completions:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        async def create(self, **kwargs: Any) -> Any:
            self.calls.append(kwargs)
            return response

    completions = Completions()

    async def fail_think(*_args: Any, **_kwargs: Any) -> Any:
        raise primary

    monkeypatch.setattr(orchestrator_module, "think", fail_think)
    agent = orchestrator_module.OrchestratorAgent(_client(completions), MODEL)
    request = orchestrator_module.ShieldRequest(
        scan_domains="process", mode="oneshot", baseline_json=""
    )

    with pytest.raises(_OpenFangUnavailable) as error:
        asyncio.run(agent.handle_shield_request(request, None))

    assert error.value is primary
    assert len(completions.calls) == 1


def test_report_request_failure_propagates_unchanged_after_one_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_import_stubs(monkeypatch)
    monkeypatch.syspath_prepend(str(POC_ROOT))
    report_generator = importlib.import_module("report_generator")
    failure = _OpenFangUnavailable("report unavailable")
    completions = _AsyncCompletions(error=failure)

    async def empty_check(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {}

    monkeypatch.setattr(report_generator, "get_client", lambda _role: _client(completions))
    monkeypatch.setattr(report_generator, "get_model", lambda _role: MODEL)
    monkeypatch.setattr(report_generator.socket, "gethostname", lambda: "host")
    for name in (
        "list_processes", "list_network_connections", "check_registry_autoruns",
        "detect_parent_child_anomalies", "detect_encoded_commands", "detect_suspicious_paths",
        "detect_lsass_access", "list_usb_devices", "detect_beaconing", "detect_data_exfiltration",
    ):
        monkeypatch.setattr(report_generator, name, empty_check)
    monkeypatch.setattr(report_generator.psutil, "virtual_memory", lambda: types.SimpleNamespace(total=1, percent=0))
    monkeypatch.setattr(report_generator.psutil, "cpu_count", lambda: 1)
    monkeypatch.setattr(report_generator.psutil, "boot_time", lambda: 0)

    with pytest.raises(_OpenFangUnavailable) as error:
        asyncio.run(report_generator.generate_report("report.html"))

    assert error.value is failure
    assert len(completions.calls) == 1
    assert set(completions.calls[0]) == {"model", "temperature", "messages"}


def test_prompts_and_non_model_request_kwargs_match_the_base_revision() -> None:
    for filename in ("orchestrator.py", "analyzer.py", "tools.py", "report_generator.py"):
        current = ast.parse((POC_ROOT / filename).read_text(encoding="utf-8"))
        base = ast.parse(subprocess.check_output(
            ["git", "show", f"2de18059ec605bf19a8932660e48a5e73a5655cc:ops/poc_os_shield/{filename}"],
            text=True,
            encoding="utf-8",
        ))

        def request_shape(tree: ast.AST) -> list[tuple[str | None, str]]:
            return sorted(
                (keyword.arg, ast.unparse(keyword.value))
                for node in ast.walk(tree)
                if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "create"
                and isinstance(node.func.value, ast.Attribute)
                and node.func.value.attr == "completions"
                for keyword in node.keywords
                if keyword.arg != "model"
            )

        assert request_shape(current) == request_shape(base), filename


def test_red_blue_external_blue_agent_calls_supply_the_existing_model() -> None:
    tree = ast.parse((POC_ROOT.parent / "poc_red_blue" / "main.py").read_text(encoding="utf-8"))
    expected = {
        "OrchestratorAgent": ["llm_client", "BLUE_TEAM_MODEL"],
        "ThreatAnalyzerAgent": ["llm_client", "BLUE_TEAM_MODEL"],
    }
    for constructor, argument_names in expected.items():
        calls = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == constructor
        ]
        assert len(calls) == 1
        assert [argument.id for argument in calls[0].args if isinstance(argument, ast.Name)] == argument_names
