from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest


DEPLOYMENT_DIR = Path(__file__).resolve().parents[2] / "deployment"
COMPOSE_PATH = DEPLOYMENT_DIR / "compose.yml"


@pytest.mark.skipif(
    os.environ.get("LEARNING_RUNTIME_SMOKE") != "1",
    reason="starts and restarts the complete local Docker profile",
)
def test_runtime_survives_stateful_restart() -> None:
    result = subprocess.run(
        [
            "python",
            "-m",
            "spaces.learning.deployment.runtime_smoke",
            "--compose-file",
            str(COMPOSE_PATH),
            "--existing-project",
            os.environ.get("LEARNING_RUNTIME_PROJECT", "vibemind-learning"),
        ],
        cwd=Path(__file__).resolve().parents[4],
        check=False,
        capture_output=True,
        text=True,
        timeout=900,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "restart_receipt=verified" in result.stdout
