"""Checks for spaces/rowboat/scripts/start_desktop.py."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import start_desktop  # noqa: E402
from start_desktop import install_steps, jobs, layout, map_command, missing  # noqa: E402


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
