"""Regression coverage for Mirofish's fixed OpenFang LLM gateway."""

from __future__ import annotations

import concurrent.futures
import importlib.util
import logging
import sys
from pathlib import Path
from types import ModuleType


BACKEND = Path(__file__).resolve().parents[1]
APP = BACKEND / "app"
VIBEMIND_SHARED_REPOSITORY = "https://github.com/Flissel/vibemind-shared.git"
VIBEMIND_SHARED_COMMIT = "609dda92e0f4370c03085a7e2ff6a6f492693d0d"
VIBEMIND_SHARED_DEPENDENCY = (
    f"vibemind-shared @ git+{VIBEMIND_SHARED_REPOSITORY}@{VIBEMIND_SHARED_COMMIT}"
)


def _package(monkeypatch, name: str, path: Path) -> None:
    module = ModuleType(name)
    module.__path__ = [str(path)]
    monkeypatch.setitem(sys.modules, name, module)


def _load_module(monkeypatch, name: str, path: Path) -> ModuleType:
    sys.modules.pop(name, None)
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    return module


def _load_gateway_modules(monkeypatch):
    calls: list[tuple[str, str]] = []
    client = object()
    shared = ModuleType("vibemind_shared")

    def get_client_sync(role: str):
        calls.append(("client", role))
        return client

    def get_model(role: str):
        calls.append(("model", role))
        return "openfang:brain-forecaster"

    shared.get_client_sync = get_client_sync
    shared.get_model = get_model
    monkeypatch.setitem(sys.modules, "vibemind_shared", shared)

    _package(monkeypatch, "app", APP)
    _package(monkeypatch, "app.utils", APP / "utils")
    _package(monkeypatch, "app.services", APP / "services")
    config = ModuleType("app.config")
    config.Config = type("Config", (), {"LLM_API_KEY": "legacy", "LLM_BASE_URL": "http://legacy", "LLM_MODEL_NAME": "legacy"})
    monkeypatch.setitem(sys.modules, "app.config", config)
    logger = ModuleType("app.utils.logger")
    logger.get_logger = logging.getLogger
    monkeypatch.setitem(sys.modules, "app.utils.logger", logger)
    entity_reader = ModuleType("app.services.entity_reader")
    entity_reader.EntityNode = object
    monkeypatch.setitem(sys.modules, "app.services.entity_reader", entity_reader)
    storage = ModuleType("app.storage")
    storage.GraphStorage = object
    monkeypatch.setitem(sys.modules, "app.storage", storage)

    llm = _load_module(monkeypatch, "app.utils.llm_client", APP / "utils" / "llm_client.py")
    oasis = _load_module(monkeypatch, "app.services.oasis_profile_generator", APP / "services" / "oasis_profile_generator.py")
    simulation = _load_module(monkeypatch, "app.services.simulation_config_generator", APP / "services" / "simulation_config_generator.py")
    return llm, oasis, simulation, client, calls


def test_mirofish_llm_constructors_use_only_the_fixed_openfang_role(monkeypatch):
    sources = [
        APP / "utils" / "llm_client.py",
        APP / "services" / "oasis_profile_generator.py",
        APP / "services" / "simulation_config_generator.py",
    ]
    for source in sources:
        text = source.read_text(encoding="utf-8")
        assert "from openai import OpenAI" not in text
        assert "OpenAI(" not in text

    llm, oasis, simulation, client, calls = _load_gateway_modules(monkeypatch)

    instances = [
        llm.LLMClient(api_key="bypass", base_url="https://provider.invalid", model="override"),
        oasis.OasisProfileGenerator(api_key="bypass", base_url="https://provider.invalid", model_name="override"),
        simulation.SimulationConfigGenerator(api_key="bypass", base_url="https://provider.invalid", model_name="override"),
    ]

    assert all(instance.client is client for instance in instances)
    assert all(instance.model_name == "openfang:brain-forecaster" for instance in instances[1:])
    assert llm.LLMClient.__dict__.get("_is_ollama") is None
    assert calls == [
        ("model", "space_mirofish"), ("client", "space_mirofish"),
        ("model", "space_mirofish"), ("client", "space_mirofish"),
        ("model", "space_mirofish"), ("client", "space_mirofish"),
    ]


def test_mirofish_constructors_propagate_openfang_gateway_failures(monkeypatch):
    shared = ModuleType("vibemind_shared")
    shared.get_model = lambda _role: "unused"
    shared.get_client_sync = lambda _role: (_ for _ in ()).throw(RuntimeError("OpenFang unavailable"))
    monkeypatch.setitem(sys.modules, "vibemind_shared", shared)

    _package(monkeypatch, "app", APP)
    _package(monkeypatch, "app.utils", APP / "utils")
    _package(monkeypatch, "app.services", APP / "services")
    config = ModuleType("app.config")
    config.Config = type("Config", (), {"LLM_API_KEY": "legacy", "LLM_BASE_URL": "http://legacy", "LLM_MODEL_NAME": "legacy"})
    monkeypatch.setitem(sys.modules, "app.config", config)
    logger = ModuleType("app.utils.logger")
    logger.get_logger = logging.getLogger
    monkeypatch.setitem(sys.modules, "app.utils.logger", logger)
    entity_reader = ModuleType("app.services.entity_reader")
    entity_reader.EntityNode = object
    monkeypatch.setitem(sys.modules, "app.services.entity_reader", entity_reader)
    storage = ModuleType("app.storage")
    storage.GraphStorage = object
    monkeypatch.setitem(sys.modules, "app.storage", storage)

    llm = _load_module(monkeypatch, "app.utils.llm_client", APP / "utils" / "llm_client.py")
    oasis = _load_module(monkeypatch, "app.services.oasis_profile_generator", APP / "services" / "oasis_profile_generator.py")
    simulation = _load_module(monkeypatch, "app.services.simulation_config_generator", APP / "services" / "simulation_config_generator.py")

    for constructor in (llm.LLMClient, oasis.OasisProfileGenerator, simulation.SimulationConfigGenerator):
        try:
            constructor()
        except RuntimeError as exc:
            assert str(exc) == "OpenFang unavailable"
        else:
            raise AssertionError("OpenFang failure must not fall back to a direct provider")


def test_mirofish_request_failures_propagate_without_local_retries(monkeypatch):
    llm, oasis, simulation, _client, _calls = _load_gateway_modules(monkeypatch)

    class FailingCompletions:
        def create(self, **_kwargs):
            raise RuntimeError("OpenFang request failed")

    failing_client = type(
        "FailingClient",
        (),
        {"chat": type("Chat", (), {"completions": FailingCompletions()})()},
    )()

    llm_instance = llm.LLMClient()
    oasis_instance = oasis.OasisProfileGenerator()
    simulation_instance = simulation.SimulationConfigGenerator()
    for instance in (llm_instance, oasis_instance, simulation_instance):
        instance.client = failing_client

    calls = [
        lambda: llm_instance.chat([{"role": "user", "content": "test"}]),
        lambda: oasis_instance._generate_profile_with_llm(
            "Ada", "person", "summary", {}, "context"
        ),
        lambda: simulation_instance._call_llm("prompt", "system"),
    ]

    for call in calls:
        try:
            call()
        except RuntimeError as exc:
            assert str(exc) == "OpenFang request failed"
        else:
            raise AssertionError("OpenFang request failure must propagate")


def test_oasis_batch_failure_does_not_publish_partial_profiles(
    monkeypatch, tmp_path: Path
) -> None:
    _llm, oasis, _simulation, _client, _calls = _load_gateway_modules(monkeypatch)
    generator = oasis.OasisProfileGenerator()

    class Entity:
        def __init__(self, name: str) -> None:
            self.name = name
            self.summary = f"Summary for {name}"
            self.uuid = f"uuid-{name}"

        def get_entity_type(self) -> str:
            return "person"

    def generate_profile(*, entity, user_id: int, use_llm: bool):
        del use_llm
        if entity.name == "failure":
            raise RuntimeError("OpenFang request failed")
        return oasis.OasisAgentProfile(
            user_id=user_id,
            user_name=entity.name,
            name=entity.name,
            bio=entity.summary,
            persona=entity.summary,
        )

    monkeypatch.setattr(generator, "generate_profile_from_entity", generate_profile)
    monkeypatch.setattr(generator, "_print_generated_profile", lambda *_args: None)
    monkeypatch.setattr(
        concurrent.futures,
        "as_completed",
        lambda futures: iter(futures),
    )
    output_path = tmp_path / "profiles.json"

    try:
        generator.generate_profiles_from_entities(
            [Entity("success"), Entity("failure")],
            realtime_output_path=str(output_path),
            parallel_count=2,
        )
    except RuntimeError as exc:
        assert str(exc) == "OpenFang request failed"
    else:
        raise AssertionError("mixed batch must propagate the failed future")

    assert not output_path.exists()


def test_vibemind_shared_installation_pin_is_consistent() -> None:
    requirements = (BACKEND / "requirements.txt").read_text(encoding="utf-8")
    pyproject = (BACKEND / "pyproject.toml").read_text(encoding="utf-8")
    lock = (BACKEND / "uv.lock").read_text(encoding="utf-8")

    assert VIBEMIND_SHARED_DEPENDENCY in requirements.splitlines()
    assert f'"{VIBEMIND_SHARED_DEPENDENCY}"' in pyproject
    assert "[tool.hatch.metadata]" in pyproject
    assert "allow-direct-references = true" in pyproject
    assert 'name = "vibemind-shared"' in lock
    locked_source = (
        f'{VIBEMIND_SHARED_REPOSITORY}?rev={VIBEMIND_SHARED_COMMIT}'
        f'#{VIBEMIND_SHARED_COMMIT}'
    )
    assert f'source = {{ git = "{locked_source}" }}' in lock
    assert (
        f'git = "{VIBEMIND_SHARED_REPOSITORY}?rev={VIBEMIND_SHARED_COMMIT}"'
        in lock
    )
