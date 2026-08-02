"""Focused coverage for the Fungus embedding gateway contract."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import yaml


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
CHECKER = REPOSITORY_ROOT / "scripts" / "check_openfang_llm_config.py"
ROOT_CONFIG = REPOSITORY_ROOT / "llm_config.yml.example"
BRAIN_CONFIG = REPOSITORY_ROOT / "brain" / "the_brain" / "llm_config.yml"


def _run_checker(root_config: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(CHECKER),
            "--root-config",
            str(root_config),
            "--brain-config",
            str(BRAIN_CONFIG),
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
