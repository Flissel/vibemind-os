"""Start the Rowboat desktop app of this space in dev mode on the VibeMind vault (~/.rowboat).

    python spaces/rowboat/scripts/start_desktop.py --install   # first time and after every sync: build harbor, install the app, schema venv
    python spaces/rowboat/scripts/start_desktop.py --build-embed   # Einbettung bauen (VibeMind-Multiverse), nach jedem Sync
    python spaces/rowboat/scripts/start_desktop.py --embed         # gebaute App versteckt im Einbettungsmodus + vault map
    python spaces/rowboat/scripts/start_desktop.py             # Rowboat + vault-map refresh; Ctrl+C stops both

The vault map (config/vault-map.json) places VibeMind's notes in the schema hierarchy for the 3D brain graph.
It runs no `build` and no `migrate`, and never touches notes it did not generate: it regenerates the schema's
structure notes (missing ones, and existing ones marked `managed_by: schema`) and config/vault-map.json. Spec: Flissel/ehl-munich-2026
docs/superpowers/specs/2026-10-13-vibemind-desktop-update-design.md
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import signal
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

log = logging.getLogger("start_desktop")

SPACE = Path(__file__).resolve().parents[1]
IS_WINDOWS = os.name == "nt"
PROFILE = "vibemind"
MAP_SECONDS = 60
POLL_SECONDS = 0.5   # wait in slices: on Windows an untimed wait() does not let Ctrl+C through
# Einbettung unter Windows: die Jobs bekommen kein Konsolenfenster (Schliessen wuerde den Kern beenden).
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)

DEFAULT_ENV_FILE = SPACE.parents[2] / ".env"   # Vibemind_V1/.env (Space liegt unter vibemind-os/spaces/rowboat)
OPENFANG_KEY = "OPENFANG_API_KEY"
REMOTE_SERVER, REMOTE_TOKEN = "ROWBOAT_REMOTE_SERVER", "ROWBOAT_REMOTE_TOKEN"
FERN_ENV = "ROWBOAT_FERN_DATEI"   # Test-/Sonderpfad fuer die Fern-Datei
GIT_BASH_FALLBACK = Path(r"C:\Program Files\Git\bin\bash.exe")


def read_env_value(path: Path, name: str) -> str | None:
    """Liest genau einen Wert aus einer .env-Datei. Gibt sonst nichts weiter."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in lines:
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        key, _, value = s.partition("=")
        if key.strip() == name:
            value = value.strip().strip('"').strip("'")
            return value or None
    return None


class FernDateiFehler(ValueError):
    """Fern-Datei unbrauchbar. Die Meldung enthaelt nie den Token."""


def fern_datei_pfad() -> Path:
    override = os.environ.get(FERN_ENV)
    if override:
        return Path(override)
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "VibeMind" / "rowboat-fern.json"


def fern_ziel_laden(datei: Path) -> tuple[str, str] | None:
    """(url, token) des Mini-PC-Kerns aus der Fern-Datei; None, wenn sie fehlt (dann laeuft der lokale Kern).

    Unbrauchbarer Inhalt -> FernDateiFehler mit Hinweis ohne Token-Wert.
    """
    try:
        text = datei.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise FernDateiFehler(f"Fern-Datei {datei} nicht lesbar ({type(exc).__name__})") from None
    try:
        data = json.loads(text)
    except ValueError:
        raise FernDateiFehler(f"Fern-Datei {datei} ist kein gueltiges JSON") from None
    url = data.get("url") if isinstance(data, dict) else None
    token = data.get("token") if isinstance(data, dict) else None
    if not isinstance(url, str) or not isinstance(token, str) or not url.strip() or not token.strip():
        raise FernDateiFehler(f'Fern-Datei {datei} braucht die Schluessel "url" und "token" (Text)')
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        raise FernDateiFehler(f"Fern-Datei {datei}: url muss mit http:// oder https:// beginnen")
    return url, token.strip()


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


def electron_bin(lay: Layout, windows: bool = IS_WINDOWS) -> Path:
    return lay.app / "apps" / "main" / "node_modules" / ".bin" / ("electron.cmd" if windows else "electron")


def embed_build_steps(lay: Layout) -> list[tuple[list[str], Path]]:
    """Einbettung (Spec vibemind-os 2026-10-17-rowboat-einbettung): gebaute Oberflaeche statt Vite."""
    return [(["npm", "run", "deps"], lay.app),
            (["npm", "run", "build"], lay.app / "apps" / "renderer"),
            (["npm", "run", "build"], lay.app / "apps" / "main")]


def missing_embed(lay: Layout, windows: bool = IS_WINDOWS) -> list[str]:
    out = missing(lay, windows)
    for path, what in ((lay.app / "apps" / "renderer" / "dist" / "index.html", "Rowboat renderer build"),
                       (lay.app / "apps" / "main" / ".package" / "dist" / "main.cjs", "Rowboat main build"),
                       (lay.app / "apps" / "preload" / "dist" / "preload.js", "Rowboat preload build"),
                       (electron_bin(lay, windows), "Electron")):
        if not path.is_file():
            out.append(f"{path} ({what})")
    return out


def _is_wsl_bash(path: Path) -> bool:
    lowered = str(path).lower()
    return "\\windows\\system32\\" in lowered or "\\windowsapps\\" in lowered


def git_bash(windows: bool = IS_WINDOWS) -> Path | None:
    """Git Bash fuer Rowboats Build-Skripte (rm -rf). Nie das WSL-bash aus System32/WindowsApps."""
    if not windows:
        return None
    git = shutil.which("git")
    if git:
        candidate = Path(git).resolve().parent.parent / "bin" / "bash.exe"   # <Git>\cmd\git.exe oder <Git>\bin\git.exe
        if candidate.is_file() and not _is_wsl_bash(candidate):
            return candidate
    return GIT_BASH_FALLBACK if GIT_BASH_FALLBACK.is_file() else None


def map_command(py: Path, workdir: Path, watch: int = 0) -> list[str]:
    cmd = [str(py), "-m", "ehl_schema.cli", "map", "--workdir", str(workdir), "--profile", PROFILE]
    return cmd + (["--watch", str(watch)] if watch else [])


def jobs(lay: Layout, workdir: Path, windows: bool = IS_WINDOWS,
         embed: bool = False) -> list[tuple[str, list[str], Path]]:
    py = venv_python(lay.venv, windows)
    if embed:
        rowboat = ("Rowboat", [str(electron_bin(lay, windows)), ".", "--hidden", "--embed"], lay.app / "apps" / "main")
    else:
        rowboat = ("Rowboat", ["npm", "run", "dev"], lay.app)
    return [rowboat, ("vault map", map_command(py, workdir, MAP_SECONDS), lay.schema)]


def _shell(cmd: list[str], windows: bool) -> bool:
    return windows and (cmd[0] in ("npm", "pnpm") or cmd[0].lower().endswith(".cmd"))  # .cmd launchers on Windows


def stop(proc: subprocess.Popen, windows: bool = IS_WINDOWS) -> None:
    if proc.poll() is not None:
        return
    if windows:
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
        return
    # POSIX: each job leads its own process group (start_new_session), so signal the group
    # to reach npm's children too, not just npm itself.
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except ProcessLookupError:
            pass


def _run_steps(steps: list[tuple[list[str], Path]], windows: bool, env: dict[str, str] | None = None) -> int:
    for cmd, cwd in steps:
        log.info("running in %s: %s", cwd, " ".join(cmd))
        try:
            code = subprocess.run(cmd, cwd=cwd, shell=_shell(cmd, windows), env=env).returncode
        except FileNotFoundError:
            tool, hint = (" ".join(cmd[:2]), " (install Python 3.12 with the py launcher)") if cmd[0] == "py" \
                else (cmd[0], "")
            log.error("install step failed: %s not found%s. Command: %s", tool, hint, " ".join(cmd))
            return 1
        if code != 0:
            log.error("install step failed: %s", " ".join(cmd))
            return 1
    return 0


def _script_shell_env(windows: bool) -> tuple[dict[str, str] | None, bool]:
    """(env, ok): unter Windows Git Bash als npm/pnpm-Script-Shell; ok=False wenn es fehlt (Fehler ist geloggt)."""
    if not windows:
        return None, True
    # Rowboats/Harbors Skripte nutzen rm -rf; unter Windows laeuft npm/pnpm ueber cmd.exe ohne rm.exe -> Git Bash als Script-Shell.
    bash = git_bash(windows)
    if bash is None:
        log.error("Git Bash nicht gefunden – die Build-Skripte brauchen rm -rf (Git for Windows installieren)")
        return None, False
    return {**os.environ, "npm_config_script_shell": str(bash)}, True


def install(lay: Layout, windows: bool = IS_WINDOWS) -> int:
    env, ok = _script_shell_env(windows)
    return _run_steps(install_steps(lay, windows), windows, env) if ok else 1


def build_embed(lay: Layout, windows: bool = IS_WINDOWS) -> int:
    env, ok = _script_shell_env(windows)
    return _run_steps(embed_build_steps(lay), windows, env) if ok else 1


LOG_MAX_BYTES = 10 * 1024 * 1024


def open_job_log(workdir: Path, name: str):
    """Logdatei <workdir>/logs/<name>.log im Anhaengemodus; ueber 10 MB wird einmal nach .log.1 rotiert.

    Ein Logproblem darf den Start nie blockieren: Rotation scheitert -> weiter anhaengen, open scheitert -> None.
    """
    logs = workdir / "logs"
    path = logs / f"{name}.log"
    try:
        logs.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.stat().st_size > LOG_MAX_BYTES:
            try:
                os.replace(path, logs / f"{name}.log.1")
            except OSError as exc:
                log.warning("Logrotation fuer %s fehlgeschlagen (%s); haenge weiter an", path, exc)
        fh = open(path, "ab")
    except OSError as exc:
        log.warning("Logdatei %s nicht nutzbar (%s); Ausgabe geht ins Leere", path, exc)
        return None
    try:
        fh.write(f"=== start {datetime.now().isoformat(timespec='seconds')} pid={os.getpid()} ==={chr(10)}".encode("utf-8"))
        fh.flush()
    except OSError as exc:
        log.warning("Logkopf in %s nicht schreibbar (%s)", path, exc)
    return fh


def start(lay: Layout, workdir: Path, windows: bool = IS_WINDOWS, env_file: Path | None = None, embed: bool = False) -> int:
    env_file = env_file or DEFAULT_ENV_FILE
    gaps = missing_embed(lay, windows) if embed else missing(lay, windows)
    if gaps:
        log.error("not installed yet - run start_desktop.py %s first. Missing:\n- %s",
                  "--install and --build-embed" if embed else "--install", "\n- ".join(gaps))
        return 2
    try:
        fern = fern_ziel_laden(fern_datei_pfad())
    except FernDateiFehler as exc:
        log.error("%s - Start abgebrochen", exc)
        return 2
    # Der Schluessel gehoert nur dem Rowboat-Job (Spec 3.3): alle anderen Prozesse bekommen ihn nie, auch nicht geerbt.
    # Im Fern-Modus gilt dasselbe fuer Ziel und Token.
    hidden = {OPENFANG_KEY, REMOTE_SERVER, REMOTE_TOKEN} if fern else {OPENFANG_KEY}
    base_env = {k: v for k, v in os.environ.items() if k not in hidden}
    key = read_env_value(env_file, OPENFANG_KEY) or os.environ.get(OPENFANG_KEY)
    if fern:
        # Das Wissen liegt auf dem Mini-PC: kein lokaler Vault-Map-Lauf.
        log.info("Fern-Modus: %s", fern[0])
    elif subprocess.run(map_command(venv_python(lay.venv, windows), workdir), cwd=lay.schema,
                        env=base_env).returncode != 0:
        log.warning("vault map failed once; Rowboat starts anyway, the refresh retries every %d s", MAP_SECONDS)
    # Single-Instance auch ungebaut (Fork, Spec 2026-10-17): ein zweiter Start holt die laufende Instanz nach vorn.
    # Telemetrie aus: Upstream (ab 10/2026) sendet sonst standardmaessig an PostHog.
    env = {**base_env, "ROWBOAT_WORKDIR": str(workdir), "ROWBOAT_SINGLE_INSTANCE": "1",
           "ROWBOAT_TELEMETRY_DISABLED": "1"}
    if embed:
        env.pop("ELECTRON_RUN_AS_NODE", None)
        env["ROWBOAT_EMBED"] = "1"
    rowboat_env = {**env, OPENFANG_KEY: key} if key else dict(env)
    if fern:
        rowboat_env[REMOTE_SERVER], rowboat_env[REMOTE_TOKEN] = fern
    if not key:
        log.warning("%s nicht gefunden – Integrationen-Seite zeigt Hinweis", OPENFANG_KEY)
    if not windows:
        # Closing the terminal (SIGHUP) or a plain kill (SIGTERM) must reach the finally below like Ctrl+C does:
        # the jobs run in their own sessions, so they would otherwise keep running.
        def _interrupt(signum: int, frame: object) -> None:
            raise KeyboardInterrupt

        for name in ("SIGHUP", "SIGTERM"):
            signal.signal(getattr(signal, name), _interrupt)
    procs: list[subprocess.Popen] = []
    log_files: list = []
    # Nur im Einbettungsmodus versteckt: im Dev-Modus soll die Ausgabe von npm run dev sichtbar bleiben.
    extra = {"creationflags": CREATE_NO_WINDOW} if windows and embed else {}
    try:
        for name, cmd, cwd in jobs(lay, workdir, windows, embed=embed)[:1 if fern else None]:
            log.info("starting %s: %s", name, " ".join(cmd))
            io: dict = {}
            if embed:
                # Ohne Umleitung erbt Rowboat eine versteckte Konsole, die niemand liest (Haenger bei vollem Puffer).
                fh = open_job_log(workdir, "embed-main" if name == "Rowboat" else "vault-map")
                if fh is not None:
                    log_files.append(fh)
                io = {"stdout": fh if fh is not None else subprocess.DEVNULL, "stderr": subprocess.STDOUT,
                      "stdin": subprocess.DEVNULL}
            procs.append(subprocess.Popen(cmd, cwd=cwd, env=rowboat_env if name == "Rowboat" else env, shell=_shell(cmd, windows),
                                          start_new_session=not windows, **extra, **io))
        if embed:
            log.info("Rowboat laeuft versteckt im Einbettungsmodus; die Multiverse-Ansicht verbindet sich ueber %s. Ctrl+C stoppt alles.",
                     workdir / "embed.json")
        else:
            log.info("Rowboat opens after its first build (a few minutes). Ctrl+C stops everything.")
        code = _wait(procs[0])
    except KeyboardInterrupt:
        return 0
    finally:
        # Runs on Ctrl+C and on a failed spawn too, so no started job is left behind.
        for proc in procs:
            stop(proc, windows)
        for fh in log_files:
            fh.close()
    if code != 0:
        log.error("Rowboat stopped with exit code %s", code)
    return code


def _wait(proc: subprocess.Popen) -> int:
    while True:
        try:
            return proc.wait(timeout=POLL_SECONDS)
        except subprocess.TimeoutExpired:
            continue


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--install", action="store_true", help="build harbor, install Rowboat and the schema venv")
    ap.add_argument("--build-embed", action="store_true", help="Einbettung bauen (gebaute Oberflaeche fuer die Multiverse-Ansicht)")
    ap.add_argument("--embed", action="store_true", help="gebaute App versteckt im Einbettungsmodus starten")
    ap.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE, help="Datei, aus der nur OPENFANG_API_KEY gelesen wird")
    ap.add_argument("--workdir", type=Path, default=Path.home() / ".rowboat", help="Rowboat data folder")
    args = ap.parse_args(argv)
    lay = layout()
    if args.install:
        return install(lay)
    if args.build_embed:
        return build_embed(lay)
    return start(lay, args.workdir.expanduser().resolve(), env_file=args.env_file, embed=args.embed)


if __name__ == "__main__":
    sys.exit(main())
