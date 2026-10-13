"""Start the Rowboat desktop app of this space in dev mode on the VibeMind vault (~/.rowboat).

    python spaces/rowboat/scripts/start_desktop.py --install   # once: build harbor, install the app, schema venv
    python spaces/rowboat/scripts/start_desktop.py             # Rowboat + vault-map refresh; Ctrl+C stops both

The vault map (config/vault-map.json) places VibeMind's notes in the schema hierarchy for the 3D brain graph.
It never edits a note: no `build`, no `migrate`. Spec: Flissel/ehl-munich-2026
docs/superpowers/specs/2026-10-13-vibemind-desktop-update-design.md
"""
from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("start_desktop")

SPACE = Path(__file__).resolve().parents[1]
IS_WINDOWS = os.name == "nt"
PROFILE = "vibemind"
MAP_SECONDS = 60


@dataclass(frozen=True)
class Layout:
    app: Path
    harbor: Path
    schema: Path
    venv: Path


def layout(space: Path = SPACE) -> Layout:
    return Layout(space / "rowboat" / "apps" / "x", space / "rowboat" / "apps" / "harbor",
                  space / "schema", space / "schema" / ".venv")


def venv_python(venv: Path, windows: bool = IS_WINDOWS) -> Path:
    return venv / "Scripts" / "python.exe" if windows else venv / "bin" / "python"


def python312(windows: bool = IS_WINDOWS) -> list[str]:
    return ["py", "-3.12"] if windows else ["python3.12"]


def install_steps(lay: Layout, windows: bool = IS_WINDOWS) -> list[tuple[list[str], Path]]:
    return [(["pnpm", "install", "--frozen-lockfile"], lay.harbor),
            (["pnpm", "-r", "build"], lay.harbor),
            (["pnpm", "install", "--frozen-lockfile"], lay.app),
            ([*python312(windows), "-m", "venv", str(lay.venv)], lay.schema),
            ([str(venv_python(lay.venv, windows)), "-m", "pip", "install", "-q", "-e", str(lay.schema)], lay.schema)]


def missing(lay: Layout, windows: bool = IS_WINDOWS) -> list[str]:
    out = []
    if not (lay.harbor / "node_modules").is_dir():
        out.append(f"{lay.harbor / 'node_modules'} (harbor packages)")
    if not (lay.app / "node_modules").is_dir():
        out.append(f"{lay.app / 'node_modules'} (Rowboat packages)")
    if not venv_python(lay.venv, windows).is_file():
        out.append(f"{lay.venv} (vault schema)")
    return out


def map_command(py: Path, workdir: Path, watch: int = 0) -> list[str]:
    cmd = [str(py), "-m", "ehl_schema.cli", "map", "--workdir", str(workdir), "--profile", PROFILE]
    return cmd + (["--watch", str(watch)] if watch else [])


def jobs(lay: Layout, workdir: Path, windows: bool = IS_WINDOWS) -> list[tuple[str, list[str], Path]]:
    py = venv_python(lay.venv, windows)
    return [("Rowboat", ["npm", "run", "dev"], lay.app),
            ("vault map", map_command(py, workdir, MAP_SECONDS), lay.schema)]


def _shell(cmd: list[str], windows: bool) -> bool:
    return windows and cmd[0] in ("npm", "pnpm")  # .cmd launchers on Windows


def stop(proc: subprocess.Popen, windows: bool = IS_WINDOWS) -> None:
    if proc.poll() is not None:
        return
    if windows:
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
    else:
        proc.terminate()


def install(lay: Layout, windows: bool = IS_WINDOWS) -> int:
    for cmd, cwd in install_steps(lay, windows):
        log.info("running in %s: %s", cwd, " ".join(cmd))
        if subprocess.run(cmd, cwd=cwd, shell=_shell(cmd, windows)).returncode != 0:
            log.error("install step failed: %s", " ".join(cmd))
            return 1
    return 0


def start(lay: Layout, workdir: Path, windows: bool = IS_WINDOWS) -> int:
    gaps = missing(lay, windows)
    if gaps:
        log.error("not installed yet - run start_desktop.py --install first. Missing:\n- %s", "\n- ".join(gaps))
        return 2
    if subprocess.run(map_command(venv_python(lay.venv, windows), workdir), cwd=lay.schema).returncode != 0:
        log.warning("vault map failed once; Rowboat starts anyway, the refresh retries every %d s", MAP_SECONDS)
    env = {**os.environ, "ROWBOAT_WORKDIR": str(workdir)}
    procs = []
    for name, cmd, cwd in jobs(lay, workdir, windows):
        log.info("starting %s: %s", name, " ".join(cmd))
        procs.append(subprocess.Popen(cmd, cwd=cwd, env=env, shell=_shell(cmd, windows)))
    log.info("Rowboat opens after its first build (a few minutes). Ctrl+C stops everything.")
    try:
        procs[0].wait()
    except KeyboardInterrupt:
        pass
    finally:
        for proc in procs:
            stop(proc, windows)
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--install", action="store_true", help="build harbor, install Rowboat and the schema venv")
    ap.add_argument("--workdir", type=Path, default=Path.home() / ".rowboat", help="Rowboat data folder")
    args = ap.parse_args(argv)
    lay = layout()
    if args.install:
        return install(lay)
    return start(lay, args.workdir.expanduser())


if __name__ == "__main__":
    sys.exit(main())
