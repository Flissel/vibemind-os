"""Regression coverage for the Brain runtime OpenFang configuration boundary."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
CHECKER = REPOSITORY_ROOT / "scripts" / "check_openfang_llm_config.py"
ROOT_CONFIG = REPOSITORY_ROOT / "llm_config.yml.example"
BRAIN_CONFIG = REPOSITORY_ROOT / "brain" / "the_brain" / "llm_config.yml"


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
