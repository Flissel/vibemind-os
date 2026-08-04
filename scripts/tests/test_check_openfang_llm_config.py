"""Focused coverage for the Fungus embedding gateway contract."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
import yaml


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
CHECKER = REPOSITORY_ROOT / "scripts" / "check_openfang_llm_config.py"
ROOT_CONFIG = REPOSITORY_ROOT / "llm_config.yml.example"
BRAIN_CONFIG = REPOSITORY_ROOT / "brain" / "the_brain" / "llm_config.yml"
SPACE_REGISTRY = REPOSITORY_ROOT / "config" / "space_agent_registry.yml"


def _run_checker(
    root_config: Path, registry: Path = SPACE_REGISTRY
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(CHECKER),
            "--root-config",
            str(root_config),
            "--brain-config",
            str(BRAIN_CONFIG),
            "--registry",
            str(registry),
        ],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def _write_config_with_fungus_embedding(
    tmp_path: Path, **changes: object
) -> Path:
    config = yaml.safe_load(ROOT_CONFIG.read_text(encoding="utf-8"))
    config["embeddings"]["fungus_search"].update(changes)
    path = tmp_path / "llm_config.yml"
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    return path


def _write_registry_with_agentfarm_changes(tmp_path: Path, **changes: object) -> Path:
    registry = yaml.safe_load(SPACE_REGISTRY.read_text(encoding="utf-8"))
    agentfarm = registry["spaces"]["agentfarm"]
    for field, value in changes.items():
        if value is None:
            agentfarm.pop(field, None)
        else:
            agentfarm[field] = value
    path = tmp_path / "space_agent_registry.yml"
    path.write_text(yaml.safe_dump(registry, sort_keys=False), encoding="utf-8")
    return path


def test_fungus_embedding_contract_is_the_openfang_large_model() -> None:
    """Fungus retrieval must select OpenFang's canonical large embedding model."""
    config = yaml.safe_load(ROOT_CONFIG.read_text(encoding="utf-8"))

    assert config["embeddings"]["fungus_search"] == {
        "driver": "openai",
        "provider": "openfang",
        "model": "text-embedding-3-large",
        "dim": 3072,
    }


def test_checker_rejects_a_local_fungus_embedding_driver(tmp_path: Path) -> None:
    """The gateway contract must not permit a local embedding bypass."""
    root_config = _write_config_with_fungus_embedding(
        tmp_path,
        driver="sentence_transformers",
    )

    result = _run_checker(root_config)

    assert result.returncode == 1
    assert "embeddings.fungus_search.driver" in result.stderr


def test_checker_rejects_a_direct_openai_fungus_embedding_provider(
    tmp_path: Path,
) -> None:
    """The Fungus role must target OpenFang rather than OpenAI directly."""
    root_config = _write_config_with_fungus_embedding(tmp_path, provider="openai")

    result = _run_checker(root_config)

    assert result.returncode == 1
    assert "embeddings.fungus_search.provider" in result.stderr


def test_checker_rejects_an_ollama_fungus_embedding_provider(tmp_path: Path) -> None:
    """A local Ollama endpoint is not a permitted Fungus embedding executor."""
    root_config = _write_config_with_fungus_embedding(tmp_path, provider="ollama")

    result = _run_checker(root_config)

    assert result.returncode == 1
    assert "embeddings.fungus_search.provider" in result.stderr


@pytest.mark.parametrize(
    ("field", "value", "expected_error"),
    [
        (
            "model",
            "text-embedding-3-small",
            "embeddings.fungus_search.model",
        ),
        ("dim", 1536, "embeddings.fungus_search.dim"),
        ("endpoint", "http://localhost:11434", "unsupported fields: endpoint"),
    ],
)
def test_checker_rejects_fungus_embedding_contract_drift(
    tmp_path: Path,
    field: str,
    value: object,
    expected_error: str,
) -> None:
    """Model, dimension, and extra fields may not weaken the exact contract."""
    root_config = _write_config_with_fungus_embedding(tmp_path, **{field: value})

    result = _run_checker(root_config)

    assert result.returncode == 1
    assert expected_error in result.stderr


def test_agentfarm_disabled_space_role_reserves_its_chat_agent() -> None:
    """A disabled Space may name its future chat identity without dispatching."""
    config = yaml.safe_load(ROOT_CONFIG.read_text(encoding="utf-8"))
    registry = yaml.safe_load(SPACE_REGISTRY.read_text(encoding="utf-8"))
    agentfarm = registry["spaces"]["agentfarm"]

    assert agentfarm["enabled"] is False
    assert config["roles"]["space_agentfarm"] == {
        "provider": "openfang",
        "model": f"openfang:{agentfarm['reserved_chat_agent']}",
        "temperature": 0.2,
    }


def test_checker_rejects_agentfarm_legacy_dispatch_role(tmp_path: Path) -> None:
    """The inactive AgentFarm role must not retain the legacy dispatch agent."""
    config = yaml.safe_load(ROOT_CONFIG.read_text(encoding="utf-8"))
    config["roles"]["space_agentfarm"]["model"] = "openfang:vibemind"
    root_config = tmp_path / "llm_config.yml"
    root_config.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")

    result = _run_checker(root_config)

    assert result.returncode == 1
    assert "roles.space_agentfarm.model must follow reserved chat agent" in result.stderr


def test_checker_rejects_enabled_space_with_reserved_chat_agent(tmp_path: Path) -> None:
    """An enabled Space cannot treat a reservation as an execution target."""
    registry = _write_registry_with_agentfarm_changes(tmp_path, enabled=True)

    result = _run_checker(ROOT_CONFIG, registry)

    assert result.returncode == 1
    assert "spaces.agentfarm.reserved_chat_agent is only valid when enabled is false" in result.stderr


def test_checker_rejects_disabled_space_without_reserved_chat_agent(tmp_path: Path) -> None:
    """Disabled Space role reservations must stay explicitly registry-derived."""
    registry = _write_registry_with_agentfarm_changes(
        tmp_path, reserved_chat_agent=None
    )

    result = _run_checker(ROOT_CONFIG, registry)

    assert result.returncode == 1
    assert "spaces.agentfarm.reserved_chat_agent is required when enabled is false" in result.stderr


def test_checker_rejects_blank_disabled_space_reservation(tmp_path: Path) -> None:
    """A reservation must be a concrete agent identity, not whitespace."""
    registry = _write_registry_with_agentfarm_changes(
        tmp_path, reserved_chat_agent=" "
    )

    result = _run_checker(ROOT_CONFIG, registry)

    assert result.returncode == 1
    assert "spaces.agentfarm.reserved_chat_agent must be a non-empty string" in result.stderr


def test_checker_rejects_unrelated_role_using_reserved_chat_agent(
    tmp_path: Path,
) -> None:
    """A reserved Space identity must not become a global LLM target."""
    config = yaml.safe_load(ROOT_CONFIG.read_text(encoding="utf-8"))
    config["roles"]["local_fast"]["model"] = "openfang:brain-agentfarm"
    root_config = tmp_path / "llm_config.yml"
    root_config.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")

    result = _run_checker(root_config)

    assert result.returncode == 1
    assert "roles.local_fast.model references unknown agent" in result.stderr


def test_checker_rejects_reserved_space_role_extra_field(tmp_path: Path) -> None:
    """The reservation is declarative and cannot carry caller overrides."""
    config = yaml.safe_load(ROOT_CONFIG.read_text(encoding="utf-8"))
    config["roles"]["space_agentfarm"]["base_url"] = "http://bypass.invalid/v1"
    root_config = tmp_path / "llm_config.yml"
    root_config.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")

    result = _run_checker(root_config)

    assert result.returncode == 1
    assert "roles.space_agentfarm must contain only provider, model, temperature" in result.stderr


def test_checker_rejects_reserved_space_role_temperature_drift(tmp_path: Path) -> None:
    """The reservation retains the canonical deterministic chat temperature."""
    config = yaml.safe_load(ROOT_CONFIG.read_text(encoding="utf-8"))
    config["roles"]["space_agentfarm"]["temperature"] = 0.3
    root_config = tmp_path / "llm_config.yml"
    root_config.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")

    result = _run_checker(root_config)

    assert result.returncode == 1
    assert "roles.space_agentfarm.temperature must be 0.2" in result.stderr


def test_checker_rejects_direct_openai_reserved_space_role(tmp_path: Path) -> None:
    """The reserved chat identity still executes only through OpenFang."""
    config = yaml.safe_load(ROOT_CONFIG.read_text(encoding="utf-8"))
    config["roles"]["space_agentfarm"]["provider"] = "openai"
    root_config = tmp_path / "llm_config.yml"
    root_config.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")

    result = _run_checker(root_config)

    assert result.returncode == 1
    assert "roles.space_agentfarm.provider must be 'openfang'" in result.stderr


def test_checker_does_not_require_reservation_for_another_disabled_space(
    tmp_path: Path,
) -> None:
    """AgentFarm is the only Space governed by this reservation contract."""
    registry = yaml.safe_load(SPACE_REGISTRY.read_text(encoding="utf-8"))
    registry["spaces"]["ideas"]["enabled"] = False
    path = tmp_path / "space_agent_registry.yml"
    path.write_text(yaml.safe_dump(registry, sort_keys=False), encoding="utf-8")

    result = _run_checker(ROOT_CONFIG, path)

    assert result.returncode == 0, result.stderr
