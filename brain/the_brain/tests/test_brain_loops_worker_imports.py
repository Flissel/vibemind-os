"""Regression: brain_loops_worker.main() must not shadow module imports.

A local `import threading` inside main() made the name function-local; when
the retrainer branch did not run, `threading.Event()` at the shutdown setup
raised UnboundLocalError and the swarm service crash-looped (live 2026-08-30).
"""

from __future__ import annotations

import sys
from pathlib import Path

_BRAIN_ROOT = Path(__file__).resolve().parents[1]
if str(_BRAIN_ROOT) not in sys.path:
    sys.path.insert(0, str(_BRAIN_ROOT))

import brain_loops_worker  # noqa: E402


def test_main_does_not_shadow_module_level_imports():
    shadowed = {"threading", "signal", "time", "os", "sys"} & set(
        brain_loops_worker.main.__code__.co_varnames
    )
    assert not shadowed, f"main() shadows module-level imports: {shadowed}"
