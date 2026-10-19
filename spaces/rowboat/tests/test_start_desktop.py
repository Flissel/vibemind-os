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


@pytest.fixture(autouse=True)
def _no_real_env_file(tmp_path, monkeypatch):
    """start() must never read the real Vibemind_V1/.env in tests."""
    monkeypatch.setattr(start_desktop, "DEFAULT_ENV_FILE", tmp_path / "nie-vorhanden.env")


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
    monkeypatch.setattr(start_desktop, "start", lambda lay, workdir, windows=False, env_file=None, embed=False: seen.append(workdir) or 0)
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

    monkeypatch.setattr(start_desktop, "git_bash", lambda windows=True: Path(r"C:\Gitinash.exe"))
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
    seen = {}
    monkeypatch.setattr(start_desktop.subprocess, "run", lambda *a, **k: type("R", (), {"returncode": 0})())

    def fake_popen(cmd, cwd=None, env=None, **k):
        seen["vault map" if "--watch" in cmd or cwd == layout(tmp_path).schema else "Rowboat"] = env
        return FakeProc(exit_code=0)

    monkeypatch.setattr(start_desktop.subprocess, "Popen", fake_popen)
    monkeypatch.delenv("ANDERES", raising=False)
    monkeypatch.delenv("OPENFANG_API_KEY", raising=False)
    caplog.set_level("INFO")
    start_desktop.start(layout(tmp_path), tmp_path, windows=True, env_file=env)
    assert set(seen) == {"Rowboat", "vault map"}
    assert seen["Rowboat"]["OPENFANG_API_KEY"] == "sek-ret"
    assert "OPENFANG_API_KEY" not in seen["vault map"]
    assert all("ANDERES" not in e for e in seen.values())
    out = capsys.readouterr()
    assert "sek-ret" not in caplog.text + out.out + out.err


def test_inherited_key_never_reaches_vault_map(tmp_path, monkeypatch):
    _prereqs(tmp_path, windows=True)
    envs = {}

    def fake_run(*a, **k):
        envs.setdefault("map-once", k.get("env"))
        return type("R", (), {"returncode": 0})()

    def fake_popen(cmd, cwd=None, env=None, **k):
        envs["vault map" if cwd == layout(tmp_path).schema else "Rowboat"] = env
        return FakeProc(exit_code=0)

    monkeypatch.setattr(start_desktop.subprocess, "run", fake_run)
    monkeypatch.setattr(start_desktop.subprocess, "Popen", fake_popen)
    monkeypatch.setenv("OPENFANG_API_KEY", "geerbt")
    start_desktop.start(layout(tmp_path), tmp_path, windows=True, env_file=tmp_path / "fehlt.env")
    assert envs["Rowboat"]["OPENFANG_API_KEY"] == "geerbt"   # Fallback auf die Eltern-Umgebung bleibt
    assert "OPENFANG_API_KEY" not in envs["vault map"]
    assert "OPENFANG_API_KEY" not in envs["map-once"]


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


# --- Einbettung (Spec 2026-10-17-rowboat-einbettung) ---
from start_desktop import electron_bin, embed_build_steps, missing_embed  # noqa: E402


def _embed_prereqs(tmp_path, windows):
    lay = _prereqs(tmp_path, windows)
    for p in (lay.app / "apps" / "renderer" / "dist" / "index.html",
              lay.app / "apps" / "main" / ".package" / "dist" / "main.cjs",
              lay.app / "apps" / "preload" / "dist" / "preload.js",
              electron_bin(lay, windows)):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("")
    return lay


def test_embed_build_builds_deps_then_renderer_then_main(tmp_path):
    lay = layout(tmp_path)
    assert embed_build_steps(lay) == [
        (["npm", "run", "deps"], lay.app),
        (["npm", "run", "build"], lay.app / "apps" / "renderer"),
        (["npm", "run", "build"], lay.app / "apps" / "main")]


def test_embed_job_starts_the_built_electron_hidden_in_apps_main(tmp_path):
    lay = layout(tmp_path)
    (name, cmd, cwd), (map_name, _, _) = jobs(lay, tmp_path, windows=True, embed=True)
    assert name == "Rowboat" and map_name == "vault map"
    assert cmd == [str(lay.app / "apps" / "main" / "node_modules" / ".bin" / "electron.cmd"), ".", "--hidden", "--embed"]
    assert cwd == lay.app / "apps" / "main"


def test_missing_embed_lists_the_builds_and_electron(tmp_path):
    lay = _prereqs(tmp_path, windows=True)
    assert len(missing_embed(lay, windows=True)) == 4
    assert missing_embed(_embed_prereqs(tmp_path / "b", windows=True), windows=True) == []


def test_embed_start_sets_embed_and_single_instance_and_drops_run_as_node(tmp_path, monkeypatch):
    lay = _embed_prereqs(tmp_path, windows=True)
    seen = {}
    monkeypatch.setattr(start_desktop.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0))

    def fake_popen(cmd, cwd=None, env=None, shell=False, **k):
        seen["map" if cwd == lay.schema else "rowboat"] = (cmd, env, shell)
        return FakeProc(exit_code=0)

    monkeypatch.setattr(start_desktop.subprocess, "Popen", fake_popen)
    monkeypatch.setenv("ELECTRON_RUN_AS_NODE", "1")
    assert start_desktop.start(lay, tmp_path, windows=True, embed=True) == 0
    cmd, env, shell = seen["rowboat"]
    assert cmd[-2:] == ["--hidden", "--embed"]
    assert shell is True   # electron.cmd braucht unter Windows die Shell
    assert env["ROWBOAT_EMBED"] == "1" and env["ROWBOAT_SINGLE_INSTANCE"] == "1"
    assert "ELECTRON_RUN_AS_NODE" not in env


def test_embed_start_refuses_without_builds(tmp_path, monkeypatch):
    lay = _prereqs(tmp_path, windows=True)
    started = []
    monkeypatch.setattr(start_desktop.subprocess, "Popen", lambda *a, **k: started.append(a))
    monkeypatch.setattr(start_desktop.subprocess, "run", lambda *a, **k: started.append(a))
    assert start_desktop.start(lay, tmp_path, windows=True, embed=True) == 2
    assert started == []


def test_dev_start_also_asks_for_single_instance(tmp_path, monkeypatch):
    lay = _prereqs(tmp_path, windows=True)
    seen = {}
    monkeypatch.setattr(start_desktop.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0))

    def fake_popen(cmd, cwd=None, env=None, **k):
        seen[str(cwd)] = env
        return FakeProc(exit_code=0)

    monkeypatch.setattr(start_desktop.subprocess, "Popen", fake_popen)
    start_desktop.start(lay, tmp_path, windows=True)
    assert seen[str(lay.app)]["ROWBOAT_SINGLE_INSTANCE"] == "1"
    assert "ROWBOAT_EMBED" not in seen[str(lay.app)]


def _popen_kwargs(tmp_path, monkeypatch, embed):
    lay = _embed_prereqs(tmp_path, windows=True) if embed else _prereqs(tmp_path, windows=True)
    kwargs_seen = []
    monkeypatch.setattr(start_desktop.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0))

    def fake_popen(cmd, **kwargs):
        kwargs_seen.append(kwargs)
        return FakeProc(exit_code=0)

    monkeypatch.setattr(start_desktop.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(start_desktop, "stop", lambda proc, windows=False: None)
    assert start_desktop.start(lay, tmp_path, windows=True, embed=embed) == 0
    return kwargs_seen


def test_embed_jobs_open_no_console_window_on_windows(tmp_path, monkeypatch):
    # Ein sichtbares Konsolenfenster (Autostart) wuerde beim Schliessen den Kern mitnehmen.
    kwargs_seen = _popen_kwargs(tmp_path, monkeypatch, embed=True)
    assert len(kwargs_seen) == 2
    assert all(k.get("creationflags") == start_desktop.CREATE_NO_WINDOW for k in kwargs_seen)
    assert start_desktop.CREATE_NO_WINDOW == 0x08000000


def test_dev_jobs_keep_their_console_output_visible(tmp_path, monkeypatch):
    kwargs_seen = _popen_kwargs(tmp_path, monkeypatch, embed=False)
    assert len(kwargs_seen) == 2
    assert all(k.get("creationflags", 0) == 0 for k in kwargs_seen)


GIT_BASH = Path(r"C:\Gitinash.exe")


def test_build_embed_runs_the_steps_in_order_and_stops_at_the_first_failure(tmp_path, monkeypatch):
    lay = layout(tmp_path)
    runs = []
    monkeypatch.setattr(start_desktop, "git_bash", lambda windows=True: GIT_BASH)

    def fake_run(cmd, cwd=None, shell=False, **k):
        runs.append((cmd, cwd))
        return SimpleNamespace(returncode=1 if cwd == lay.app / "apps" / "renderer" else 0)

    monkeypatch.setattr(start_desktop.subprocess, "run", fake_run)
    assert start_desktop.build_embed(lay, windows=True) == 1
    assert [c for c, _ in runs] == [["npm", "run", "deps"], ["npm", "run", "build"]]


def test_build_embed_points_npm_at_git_bash_on_windows(tmp_path, monkeypatch):
    lay = layout(tmp_path)
    envs = []
    monkeypatch.setattr(start_desktop, "git_bash", lambda windows=True: GIT_BASH)
    monkeypatch.setattr(start_desktop.subprocess, "run",
                        lambda cmd, cwd=None, shell=False, env=None, **k: envs.append(env) or SimpleNamespace(returncode=0))
    assert start_desktop.build_embed(lay, windows=True) == 0
    assert len(envs) == 3
    assert all(e["npm_config_script_shell"] == str(GIT_BASH) for e in envs)


def test_build_embed_without_git_bash_on_windows_runs_nothing(tmp_path, monkeypatch, caplog):
    runs = []
    monkeypatch.setattr(start_desktop, "git_bash", lambda windows=True: None)
    monkeypatch.setattr(start_desktop.subprocess, "run", lambda *a, **k: runs.append(a))
    with caplog.at_level("ERROR", logger="start_desktop"):
        assert start_desktop.build_embed(layout(tmp_path), windows=True) == 1
    assert runs == []
    assert "Git Bash nicht gefunden" in caplog.text and "rm -rf" in caplog.text


def test_git_bash_is_derived_from_git_exe_in_cmd_dir(tmp_path, monkeypatch):
    (tmp_path / "Git" / "cmd").mkdir(parents=True)
    (tmp_path / "Git" / "bin").mkdir(parents=True)
    git = tmp_path / "Git" / "cmd" / "git.exe"
    bash = tmp_path / "Git" / "bin" / "bash.exe"
    git.write_text("")
    bash.write_text("")
    monkeypatch.setattr(start_desktop.shutil, "which", lambda name: str(git))
    assert start_desktop.git_bash(windows=True) == bash


def test_git_bash_never_returns_a_wsl_bash(tmp_path, monkeypatch):
    sys32 = tmp_path / "Windows" / "System32"
    sys32.mkdir(parents=True)
    (sys32 / "git.exe").write_text("")
    (sys32 / "bash.exe").write_text("")   # WSL-Launcher neben einem (hier erfundenen) git.exe
    monkeypatch.setattr(start_desktop.shutil, "which", lambda name: str(sys32 / "git.exe"))
    monkeypatch.setattr(start_desktop, "GIT_BASH_FALLBACK", tmp_path / "keine" / "bash.exe")
    assert start_desktop.git_bash(windows=True) is None


def test_git_bash_falls_back_and_is_none_off_windows(tmp_path, monkeypatch):
    fb = tmp_path / "bash.exe"
    fb.write_text("")
    monkeypatch.setattr(start_desktop.shutil, "which", lambda name: None)
    monkeypatch.setattr(start_desktop, "GIT_BASH_FALLBACK", fb)
    assert start_desktop.git_bash(windows=True) == fb
    assert start_desktop.git_bash(windows=False) is None


def test_install_points_pnpm_at_git_bash_on_windows(tmp_path, monkeypatch):
    envs = []
    monkeypatch.setattr(start_desktop, "git_bash", lambda windows=True: GIT_BASH)
    monkeypatch.setattr(start_desktop.subprocess, "run",
                        lambda cmd, cwd=None, shell=False, env=None, **k: envs.append(env) or SimpleNamespace(returncode=0))
    assert start_desktop.install(layout(tmp_path), windows=True) == 0
    assert envs and all(e["npm_config_script_shell"] == str(GIT_BASH) for e in envs)


def test_install_without_git_bash_on_windows_runs_nothing(tmp_path, monkeypatch, caplog):
    runs = []
    monkeypatch.setattr(start_desktop, "git_bash", lambda windows=True: None)
    monkeypatch.setattr(start_desktop.subprocess, "run", lambda *a, **k: runs.append(a))
    with caplog.at_level("ERROR", logger="start_desktop"):
        assert start_desktop.install(layout(tmp_path), windows=True) == 1
    assert runs == []
    assert "Git Bash nicht gefunden" in caplog.text


def _log_start(tmp_path, monkeypatch, embed, big=False):
    lay = _embed_prereqs(tmp_path, windows=True) if embed else _prereqs(tmp_path, windows=True)
    if big:
        (tmp_path / "logs").mkdir(exist_ok=True)
        with open(tmp_path / "logs" / "embed-main.log", "wb") as f:
            f.seek(10 * 1024 * 1024 + 5)
            f.write(b"x")
    seen = []
    monkeypatch.setattr(start_desktop.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0))

    def fake_popen(cmd, **kw):
        out = kw.get("stdout")
        seen.append({"kw": kw, "name": getattr(out, "name", None), "mode": getattr(out, "mode", None)})
        return FakeProc(exit_code=0)

    monkeypatch.setattr(start_desktop.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(start_desktop, "stop", lambda proc, windows=False: None)
    assert start_desktop.start(lay, tmp_path, windows=True, embed=embed) == 0
    return seen


def test_embed_start_logs_both_jobs_to_files(tmp_path, monkeypatch):
    seen = _log_start(tmp_path, monkeypatch, embed=True)
    assert [Path(s["name"]).name for s in seen] == ["embed-main.log", "vault-map.log"]
    for s in seen:
        assert s["mode"] == "ab"
        assert s["kw"]["stderr"] == subprocess.STDOUT
        assert s["kw"]["stdin"] == subprocess.DEVNULL
        assert s["kw"]["creationflags"] == start_desktop.CREATE_NO_WINDOW
    text = (tmp_path / "logs" / "embed-main.log").read_text()
    assert text.startswith("=== start ") and f"pid={os.getpid()} ===" in text


def test_embed_log_handles_are_closed_after_start(tmp_path, monkeypatch):
    kept = []
    real_open = start_desktop.open_job_log
    monkeypatch.setattr(start_desktop, "open_job_log", lambda w, n: kept.append(real_open(w, n)) or kept[-1])
    _log_start(tmp_path, monkeypatch, embed=True)
    assert len(kept) == 2 and all(f.closed for f in kept)


def test_embed_log_over_10mb_is_rotated_once(tmp_path, monkeypatch):
    (tmp_path / "logs").mkdir(exist_ok=True)
    (tmp_path / "logs" / "embed-main.log.1").write_bytes(b"old")
    _log_start(tmp_path, monkeypatch, embed=True, big=True)
    rotated = tmp_path / "logs" / "embed-main.log.1"
    assert rotated.stat().st_size > 10 * 1024 * 1024
    assert (tmp_path / "logs" / "embed-main.log").stat().st_size < 1000


def test_dev_start_does_not_redirect_output(tmp_path, monkeypatch):
    seen = _log_start(tmp_path, monkeypatch, embed=False)
    assert all("stdout" not in s["kw"] and "stderr" not in s["kw"] and "stdin" not in s["kw"] for s in seen)
    assert not (tmp_path / "logs").exists()
