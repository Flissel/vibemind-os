"""F4 (2026-08-30): host-path mapping for containerised ground-truth checks.

The `truth:file_exists` validator runs inside brain-core (Linux container)
while coding agents write files on the Windows HOST. Without a mapping the
validator refutes every successful coding op ("file missing") and poisons
the reward signal. GROUND_TRUTH_PATH_MAP translates host path prefixes to
container mount points; unset -> exact legacy behaviour.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

_BRAIN_ROOT = Path(__file__).resolve().parents[1]
if str(_BRAIN_ROOT) not in sys.path:
    sys.path.insert(0, str(_BRAIN_ROOT))

world_observer = importlib.import_module("core.world_observer")


def _file_exists(path: str):
    return world_observer._check_file_exists({"path": path})


def test_without_map_behaviour_is_unchanged(tmp_path, monkeypatch):
    monkeypatch.delenv("GROUND_TRUTH_PATH_MAP", raising=False)
    real = tmp_path / "artifact.py"
    real.write_text("x = 1\n", encoding="utf-8")
    ok, signal, reason = _file_exists(str(real))
    assert ok is True and reason == "file exists"
    missing_ok, _, missing_reason = _file_exists(str(tmp_path / "nope.py"))
    assert missing_ok is False and missing_reason == "file missing"


def test_windows_host_prefix_maps_to_container_mount(tmp_path, monkeypatch):
    (tmp_path / "repo" / "tests").mkdir(parents=True)
    (tmp_path / "repo" / "tests" / "fixture.py").write_text("MARKER=1\n", encoding="utf-8")
    monkeypatch.setenv("GROUND_TRUTH_PATH_MAP", rf"C:\Users\User=>{tmp_path}")
    ok, signal, reason = _file_exists(r"C:\Users\User\repo\tests\fixture.py")
    assert ok is True, (signal, reason)
    assert signal["path"] == r"C:\Users\User\repo\tests\fixture.py"
    assert signal.get("mapped_path", "").replace("\\", "/").endswith("repo/tests/fixture.py")


def test_prefix_match_is_case_and_slash_insensitive(tmp_path, monkeypatch):
    (tmp_path / "x.txt").write_text("y", encoding="utf-8")
    monkeypatch.setenv("GROUND_TRUTH_PATH_MAP", rf"C:\Users\User=>{tmp_path}")
    ok, _, _ = _file_exists("c:/users/user/x.txt")
    assert ok is True


def test_non_matching_prefix_stays_unmapped(tmp_path, monkeypatch):
    monkeypatch.setenv("GROUND_TRUTH_PATH_MAP", rf"C:\Users\User=>{tmp_path}")
    ok, signal, reason = _file_exists(r"D:\elsewhere\file.txt")
    assert ok is False and reason == "file missing"
    assert "mapped_path" not in signal


def test_json_escaped_double_backslash_paths_still_map(tmp_path, monkeypatch):
    """The {result_path} extractor can hand over JSON-escaped paths with
    doubled backslashes (seen live 2026-08-30) — separator runs must not
    defeat the prefix match."""
    (tmp_path / "esc.txt").write_text("e", encoding="utf-8")
    monkeypatch.setenv("GROUND_TRUTH_PATH_MAP", rf"C:\Users\User=>{tmp_path}")
    ok, signal, reason = _file_exists("C:\\\\Users\\\\User\\\\esc.txt")
    assert ok is True, (signal, reason)


def test_multiple_and_malformed_entries_are_tolerated(tmp_path, monkeypatch):
    (tmp_path / "z.txt").write_text("z", encoding="utf-8")
    monkeypatch.setenv(
        "GROUND_TRUTH_PATH_MAP",
        rf"garbage-without-arrow;=>/nowhere;E:\data=>/data;C:\Users\User=>{tmp_path}",
    )
    ok, _, _ = _file_exists(r"C:\Users\User\z.txt")
    assert ok is True
