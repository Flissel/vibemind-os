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

    def __init__(self, pid=4242, wait_exc=None, exit_code=0, timeouts=0):
        self.pid = pid
        self.events = []
        self._wait_exc = wait_exc
        self._exit_code = exit_code
        self._timeouts = timeouts   # how many timed waits time out before the process "exits"

    def poll(self):
        return None

    def terminate(self):
        self.events.append("terminate")

    def wait(self, timeout=None):
        self.events.append(("wait", timeout))
        if self._wait_exc is not None:
            raise self._wait_exc
        if timeout is not None and self._timeouts > 0:
            self._timeouts -= 1
            raise subprocess.TimeoutExpired("npm", timeout)
        return self._exit_code


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
    monkeypatch.setattr(signal, "SIGHUP", 1, raising=False)
    monkeypatch.setattr(start_desktop.signal, "signal", lambda sig, handler: None)
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


def test_posix_hangup_and_terminate_stop_the_jobs(tmp_path, monkeypatch):
    lay = _prereqs(tmp_path, windows=False)
    registered = {}
    monkeypatch.setattr(signal, "SIGHUP", 1, raising=False)
    monkeypatch.setattr(start_desktop.signal, "signal", lambda sig, handler: registered.setdefault(sig, handler))
    monkeypatch.setattr(start_desktop.subprocess, "Popen", lambda cmd, **k: FakeProc())
    monkeypatch.setattr(start_desktop.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0))
    monkeypatch.setattr(start_desktop, "stop", lambda proc, windows=False: None)
    assert start_desktop.start(lay, tmp_path, windows=False) == 0
    assert set(registered) == {signal.SIGHUP, signal.SIGTERM}
    for handler in registered.values():
        with pytest.raises(KeyboardInterrupt):  # reaches the finally that stops the jobs
            handler(0, None)


def test_windows_installs_no_signal_handlers(tmp_path, monkeypatch):
    lay = _prereqs(tmp_path, windows=True)
    registered = []
    monkeypatch.setattr(start_desktop.signal, "signal", lambda sig, handler: registered.append(sig))
    monkeypatch.setattr(start_desktop.subprocess, "Popen", lambda cmd, **k: FakeProc())
    monkeypatch.setattr(start_desktop.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0))
    monkeypatch.setattr(start_desktop, "stop", lambda proc, windows=False: None)
    assert start_desktop.start(lay, tmp_path, windows=True) == 0
    assert registered == []


def test_relative_workdir_is_made_absolute(tmp_path, monkeypatch):
    seen = []
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(start_desktop, "start", lambda lay, workdir, windows=False, env_file=None: seen.append(workdir) or 0)
    assert start_desktop.main(["--workdir", "data"]) == 0
    assert seen == [(tmp_path / "data").resolve()]


# Review PR #151, finding 10 and the author's deferred list: Ctrl+C on Windows, exit codes, missing py launcher.
def _start_with(tmp_path, monkeypatch, rowboat, windows=True):
    lay = _prereqs(tmp_path, windows=windows)
    procs = iter([rowboat, FakeProc(pid=2)])
    stopped = []
    monkeypatch.setattr(start_desktop.subprocess, "Popen", lambda cmd, **k: next(procs))
    monkeypatch.setattr(start_desktop.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0))
    monkeypatch.setattr(start_desktop, "stop", lambda proc, windows=False: stopped.append(proc.pid))
    monkeypatch.setattr(start_desktop.signal, "signal", lambda sig, handler: None)
    monkeypatch.setattr(signal, "SIGHUP", 1, raising=False)
    return start_desktop.start(lay, tmp_path, windows=windows), stopped


def test_waiting_for_rowboat_never_blocks_without_a_timeout(tmp_path, monkeypatch):
    rowboat = FakeProc(pid=1, timeouts=3)
    code, stopped = _start_with(tmp_path, monkeypatch, rowboat)
    waits = [e for e in rowboat.events if isinstance(e, tuple) and e[0] == "wait"]
    assert len(waits) == 4 and all(timeout for _, timeout in waits)   # Ctrl+C gets through between waits
    assert code == 0 and stopped == [1, 2]


def test_ctrl_c_stops_both_jobs(tmp_path, monkeypatch):
    code, stopped = _start_with(tmp_path, monkeypatch, FakeProc(pid=1, wait_exc=KeyboardInterrupt()))
    assert code == 0 and stopped == [1, 2]


def test_a_rowboat_crash_gives_a_non_zero_exit_code(tmp_path, monkeypatch, caplog):
    with caplog.at_level("ERROR", logger="start_desktop"):
        code, stopped = _start_with(tmp_path, monkeypatch, FakeProc(pid=1, exit_code=3))
    assert code == 3 and stopped == [1, 2]
    assert "exit code 3" in caplog.text


def test_missing_py_launcher_gives_a_clear_message(tmp_path, monkeypatch, caplog):
    def fake_run(cmd, *args, **kwargs):
        if cmd[0] == "py":
            raise FileNotFoundError(2, "The system cannot find the file specified")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(start_desktop.subprocess, "run", fake_run)
    with caplog.at_level("ERROR", logger="start_desktop"):
        assert start_desktop.install(layout(tmp_path), windows=True) == 1
    assert "py -3.12" in caplog.text and "not found" in caplog.text


def test_read_env_value_takes_only_the_named_value(tmp_path):
    env = tmp_path / ".env"
    env.write_text('A=1\nOPENFANG_API_KEY = "sek-ret"\n# OPENFANG_API_KEY=alt\nOPENFANG_API_KEY_X=nein\n', encoding="utf-8")
    assert start_desktop.read_env_value(env, "OPENFANG_API_KEY") == "sek-ret"
    assert start_desktop.read_env_value(env, "FEHLT") is None
    assert start_desktop.read_env_value(tmp_path / "gibtsnicht", "OPENFANG_API_KEY") is None


def test_start_passes_only_the_openfang_key_and_never_prints_it(tmp_path, monkeypatch, caplog, capsys):
    _prereqs(tmp_path, windows=True)
    env = tmp_path / ".env"
    env.write_text("OPENFANG_API_KEY=sek-ret\nANDERES=geheim\n", encoding="utf-8")
    seen = []
    monkeypatch.setattr(start_desktop.subprocess, "run", lambda *a, **k: type("R", (), {"returncode": 0})())

    def fake_popen(cmd, cwd=None, env=None, **k):
        seen.append(env)
        return FakeProc(exit_code=0)

    monkeypatch.setattr(start_desktop.subprocess, "Popen", fake_popen)
    monkeypatch.delenv("ANDERES", raising=False)
    caplog.set_level("INFO")
    start_desktop.start(layout(tmp_path), tmp_path, windows=True, env_file=env)
    assert seen and all(e["OPENFANG_API_KEY"] == "sek-ret" for e in seen)
    assert all("ANDERES" not in e for e in seen)
    out = capsys.readouterr()
    assert "sek-ret" not in caplog.text + out.out + out.err


def test_start_warns_and_runs_without_key(tmp_path, monkeypatch, caplog):
    _prereqs(tmp_path, windows=True)
    monkeypatch.setattr(start_desktop.subprocess, "run", lambda *a, **k: type("R", (), {"returncode": 0})())
    started = []
    monkeypatch.setattr(start_desktop.subprocess, "Popen", lambda *a, **k: started.append(k.get("env")) or FakeProc(exit_code=0))
    monkeypatch.delenv("OPENFANG_API_KEY", raising=False)
    caplog.set_level("WARNING")
    start_desktop.start(layout(tmp_path), tmp_path, windows=True, env_file=tmp_path / "fehlt.env")
    assert started, "Rowboat muss trotzdem starten"
    assert "OPENFANG_API_KEY nicht gefunden" in caplog.text
