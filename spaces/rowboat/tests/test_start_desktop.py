"""Checks for spaces/rowboat/scripts/start_desktop.py."""
import os
import signal
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import start_desktop  # noqa: E402
from start_desktop import install_steps, jobs, layout, map_command, missing, venv_python  # noqa: E402


class FakeProc:
    """Stands in for subprocess.Popen; records what stop() asks of it."""

    def __init__(self, pid=4242, wait_exc=None):
        self.pid = pid
        self.events = []
        self._wait_exc = wait_exc

    def poll(self):
        return None

    def terminate(self):
        self.events.append("terminate")

    def wait(self, timeout=None):
        self.events.append(("wait", timeout))
        if self._wait_exc is not None:
            raise self._wait_exc
        return 0


def _prereqs(tmp_path, windows):
    """Make every prerequisite that start() checks for appear to be installed."""
    lay = layout(tmp_path)
    (lay.harbor / "node_modules").mkdir(parents=True)
    (lay.app / "node_modules").mkdir(parents=True)
    py = venv_python(lay.venv, windows)
    py.parent.mkdir(parents=True)
    py.write_text("")
    return lay


def test_install_builds_harbor_first_then_app_then_schema(tmp_path):
    lay = layout(tmp_path)
    steps = install_steps(lay, windows=True)
    assert [(cmd[:2], cwd) for cmd, cwd in steps[:3]] == [
        (["pnpm", "install"], lay.harbor), (["pnpm", "-r"], lay.harbor), (["pnpm", "install"], lay.app)]
    assert steps[3][0][:4] == ["py", "-3.12", "-m", "venv"]
    assert steps[4][0][-2:] == ["-e", str(lay.schema)]


def test_map_never_builds_or_migrates_and_uses_the_profile(tmp_path):
    cmd = map_command(Path("py"), tmp_path, watch=60)
    assert cmd[1:4] == ["-m", "ehl_schema.cli", "map"]
    assert cmd[cmd.index("--profile") + 1] == "vibemind"
    assert cmd[-2:] == ["--watch", "60"]
    every = [c for _, c, _ in jobs(layout(tmp_path), tmp_path, windows=False)] + [cmd]
    assert not any(word in c for c in every for word in ("build", "migrate"))


def test_rowboat_gets_the_workdir_and_runs_in_apps_x(tmp_path):
    lay = layout(tmp_path)
    (name, cmd, cwd), (map_name, _, _) = jobs(lay, tmp_path / ".rowboat", windows=False)
    assert (name, cmd, cwd, map_name) == ("Rowboat", ["npm", "run", "dev"], lay.app, "vault map")


def test_missing_lists_everything_that_is_not_installed(tmp_path):
    assert len(missing(layout(tmp_path), windows=True)) == 3


def test_nothing_starts_when_something_is_missing(tmp_path, monkeypatch):
    started = []
    monkeypatch.setattr(start_desktop.subprocess, "Popen", lambda *a, **k: started.append(a))
    monkeypatch.setattr(start_desktop.subprocess, "run", lambda *a, **k: started.append(a))
    assert start_desktop.start(layout(tmp_path), tmp_path, windows=True) == 2
    assert started == []


def test_first_process_is_stopped_when_second_spawn_fails(tmp_path, monkeypatch):
    lay = _prereqs(tmp_path, windows=True)
    first = FakeProc(pid=111)
    spawned = []

    def fake_popen(cmd, **kwargs):
        spawned.append(cmd)
        if len(spawned) == 1:
            return first
        raise OSError("py launcher not found")

    runs = []

    def fake_run(cmd, *args, **kwargs):
        runs.append(cmd)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(start_desktop.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(start_desktop.subprocess, "run", fake_run)
    with pytest.raises(OSError):
        start_desktop.start(lay, tmp_path, windows=True)
    assert ["taskkill", "/PID", "111", "/T", "/F"] in runs


def test_jobs_get_their_own_process_group_on_posix(tmp_path, monkeypatch):
    lay = _prereqs(tmp_path, windows=False)
    kwargs_seen = []

    def fake_popen(cmd, **kwargs):
        kwargs_seen.append(kwargs)
        return FakeProc(pid=200 + len(kwargs_seen))

    monkeypatch.setattr(start_desktop.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(start_desktop.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0))
    monkeypatch.setattr(start_desktop, "stop", lambda proc, windows=False: None)
    assert start_desktop.start(lay, tmp_path, windows=False) == 0
    assert [k["start_new_session"] for k in kwargs_seen] == [True, True]


def _fake_posix_groups(monkeypatch):
    """Patch the POSIX-only os/signal names so the branch runs on Windows too."""
    killed = []
    monkeypatch.setattr(start_desktop.os, "getpgid", lambda pid: pid * 10, raising=False)
    monkeypatch.setattr(start_desktop.os, "killpg", lambda pgid, sig: killed.append((pgid, sig)), raising=False)
    monkeypatch.setattr(signal, "SIGKILL", 9, raising=False)
    return killed


def test_posix_stop_signals_the_whole_process_group(monkeypatch):
    killed = _fake_posix_groups(monkeypatch)
    proc = FakeProc(pid=7)
    start_desktop.stop(proc, windows=False)
    assert killed == [(70, signal.SIGTERM)]
    assert ("wait", 10) in proc.events
    assert "terminate" not in proc.events


def test_posix_stop_kills_the_group_when_sigterm_is_ignored(monkeypatch):
    killed = _fake_posix_groups(monkeypatch)
    proc = FakeProc(pid=7, wait_exc=subprocess.TimeoutExpired("npm", 10))
    start_desktop.stop(proc, windows=False)
    assert killed == [(70, signal.SIGTERM), (70, 9)]


def test_posix_stop_tolerates_a_group_that_is_already_gone(monkeypatch):
    killed = _fake_posix_groups(monkeypatch)

    def gone(pgid, sig):
        killed.append((pgid, sig))
        raise ProcessLookupError(pgid)

    monkeypatch.setattr(start_desktop.os, "killpg", gone, raising=False)
    proc = FakeProc(pid=7)
    start_desktop.stop(proc, windows=False)  # must not raise
    assert killed == [(70, signal.SIGTERM)]
