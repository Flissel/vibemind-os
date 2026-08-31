"""Regression coverage for the Brain runtime OpenFang configuration boundary."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import yaml


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
CHECKER = REPOSITORY_ROOT / "scripts" / "check_openfang_llm_config.py"
ROOT_CONFIG = REPOSITORY_ROOT / "llm_config.yml.example"
BRAIN_CONFIG = REPOSITORY_ROOT / "brain" / "the_brain" / "llm_config.yml"
SHARED_SOURCE = REPOSITORY_ROOT / "shared" / "src"
SPACE_REGISTRY = REPOSITORY_ROOT / "config" / "space_agent_registry.yml"


def test_central_agentfarm_role_reserves_the_disabled_chat_identity() -> None:
    """The central LLM config declares the disabled Space's future chat agent."""
    config = yaml.safe_load(ROOT_CONFIG.read_text(encoding="utf-8"))
    registry = yaml.safe_load(SPACE_REGISTRY.read_text(encoding="utf-8"))
    agentfarm = registry["spaces"]["agentfarm"]

    assert agentfarm["enabled"] is False
    assert config["roles"]["space_agentfarm"]["provider"] == "openfang"
    assert config["roles"]["space_agentfarm"]["model"] == (
        f"openfang:{agentfarm['reserved_chat_agent']}"
    )


def test_checker_rejects_direct_provider_in_brain_runtime_config(tmp_path: Path) -> None:
    """A Brain image config may not retain a direct-provider escape hatch."""
    runtime_config = tmp_path / "llm_config.yml"
    shutil.copy(BRAIN_CONFIG, runtime_config)
    runtime_config.write_text(
        runtime_config.read_text(encoding="utf-8").replace(
            "provider: openfang", "provider: openai", 1
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            str(CHECKER),
            "--root-config",
            str(ROOT_CONFIG),
            "--brain-config",
            str(runtime_config),
        ],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert "brain runtime config" in result.stderr


def test_brain_runtime_loader_resolves_openfang_url_and_fails_closed_without_it() -> None:
    """The pinned shared loader must consume the Brain image's real config."""
    environment = os.environ.copy()
    environment["VIBEMIND_CONFIG_DIR"] = str(BRAIN_CONFIG.parent)
    environment["OPENFANG_URL"] = "http://host.docker.internal:4200"
    environment["PYTHONPATH"] = os.pathsep.join(
        filter(None, (str(SHARED_SOURCE), environment.get("PYTHONPATH")))
    )
    loader_probe = (
        "from vibemind_shared import get_provider_info; "
        "info = get_provider_info('brain_planning'); "
        "assert info['provider'] == 'openfang'; "
        "assert info['base_url'] == 'http://host.docker.internal:4200/v1'"
    )

    resolved = subprocess.run(
        [sys.executable, "-c", loader_probe],
        cwd=REPOSITORY_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert resolved.returncode == 0, resolved.stderr

    environment.pop("OPENFANG_URL")
    missing = subprocess.run(
        [sys.executable, "-c", loader_probe],
        cwd=REPOSITORY_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    output = missing.stdout + missing.stderr
    assert missing.returncode != 0
    assert "OPENFANG_URL" in output
    assert "api.openai.com" not in output
