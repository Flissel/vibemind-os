"""Replace the pinned copies of the Rowboat desktop app and the vault schema in this space.

    python spaces/rowboat/scripts/sync_desktop.py --fork <clone of Flissel/rowboat> --fork-ref <commit> \\
        --ehl <clone of Flissel/ehl-munich-2026> --ehl-ref <commit>

apps/x and apps/harbor come from the fork, schema/ from the EHL repo. Never edit these copies by hand: change the
fork or the EHL repo, then run this script. It stops when a copy was edited since the last sync and when anything
outside the copies would change. The very first sync into folders that already hold files needs --first-sync. Spec: Flissel/ehl-munich-2026
docs/superpowers/specs/2026-10-13-vibemind-desktop-update-design.md
"""
from __future__ import annotations

import argparse
import io
import logging
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("sync_desktop")

SPACE = Path(__file__).resolve().parents[1]
FORK_URL = "https://github.com/Flissel/rowboat"
EHL_URL = "https://github.com/Flissel/ehl-munich-2026"
UPSTREAM_BASE = "72e9d8c"
IGNORED = ("node_modules", ".venv", "__pycache__", "dist", ".vite", ".pytest_cache", ".pnpm-store")
PIN_FILE = "apps/renderer/src/lib/rowboat-plugin-api.ts"   # inside apps/x; read by apps/rowboat's catalog-pin-guard test
PIN_LINE = re.compile(r"^- (Fork|EHL) commit: `([0-9a-f]{40})`$", re.M)


AFTER_SYNC_HINT = ("next: run `python spaces/rowboat/scripts/start_desktop.py --install` so the dependencies and "
                   "harbor build match the new copy (the sync keeps node_modules and dist)")


class SyncError(Exception):
    """The sync stopped; the step that failed says why."""


@dataclass(frozen=True)
class Target:
    source: str   # "fork" or "ehl"
    subdir: str   # folder inside the source repo
    dest: Path    # folder in this space


def targets(space: Path) -> list[Target]:
    return [Target("fork", "apps/x", space / "rowboat" / "apps" / "x"),
            Target("fork", "apps/harbor", space / "rowboat" / "apps" / "harbor"),
            Target("ehl", "schema", space / "schema")]


def git(repo: Path, *args: str) -> str:
    proc = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, encoding="utf-8")
    if proc.returncode != 0:
        raise SyncError(f"git {' '.join(args)} in {repo}: {proc.stderr.strip()}")
    return proc.stdout


def resolve(repo: Path, ref: str) -> str:
    try:
        return git(repo, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}").strip()
    except SyncError:
        raise SyncError(f"{ref} is not a commit in {repo} (fetch it, or check the clone and the ref)") from None


def _ignored(name: str) -> bool:
    return name in IGNORED or name.endswith(".egg-info")


def _walk(root: Path, tracked: frozenset[str] | None = frozenset()):
    """os.walk that skips the IGNORED folders (node_modules, dist, …) unless their path is in tracked, i.e. the
    source repo has files there. tracked=None walks everything (used for the exports)."""
    for dirpath, dirnames, filenames in os.walk(root):
        if tracked is not None:
            rel = Path(dirpath).relative_to(root)
            dirnames[:] = [d for d in dirnames if not _ignored(d) or (rel / d).as_posix() in tracked]
        yield Path(dirpath), dirnames, filenames


def dirs_of(paths) -> frozenset[str]:
    """Every folder (relative posix path) that holds one of the given relative file paths."""
    return frozenset(p.rsplit("/", i)[0] for p in paths for i in range(1, p.count("/") + 1))


def files_of(root: Path, tracked: frozenset[str] | None = frozenset()) -> dict[str, bytes]:
    """Every file under root (relative posix path -> content with LF newlines); IGNORED folders are left out
    unless their path is in tracked (tracked=None: nothing is left out)."""
    out: dict[str, bytes] = {}
    if not root.is_dir():
        return out
    for dirpath, _, filenames in _walk(root, tracked):
        for name in filenames:
            file = dirpath / name
            out[file.relative_to(root).as_posix()] = file.read_bytes().replace(b"\r\n", b"\n")
    return out


def ignored_paths(repo: Path, paths: list[str]) -> set[str]:
    """The given repo-relative posix paths that git ignores in repo (patterns only; tracked or not does not matter)."""
    if not paths:
        return set()
    proc = subprocess.run(["git", "-C", str(repo), "check-ignore", "--no-index", "-z", "--stdin"],
                          input="\0".join(paths) + "\0", capture_output=True, text=True, encoding="utf-8")
    if proc.returncode not in (0, 1):   # 1 = none of the paths is ignored
        raise SyncError(f"git check-ignore in {repo}: {proc.stderr.strip()}")
    return {p for p in proc.stdout.split("\0") if p}


def export(repo: Path, ref: str, subdir: str, dest: Path, what: str = "") -> Path:
    """subdir of repo at ref, unpacked under dest. Links are refused: on Python < 3.11.4 tarfile has no data
    filter, and a link would pull files from outside the repo into the copy."""
    proc = subprocess.run(["git", "-C", str(repo), "archive", "--format=tar", ref, subdir], capture_output=True)
    if proc.returncode != 0:
        raise SyncError(f"cannot export {subdir} at {ref}{what} from {repo}; fetch that commit into the clone, or "
                        f"check that {subdir} exists there. git archive said: "
                        f"{proc.stderr.decode(errors='replace').strip()}")
    dest.mkdir(parents=True, exist_ok=True)
    try:
        with tarfile.open(fileobj=io.BytesIO(proc.stdout)) as tar:
            members = tar.getmembers()
            links = [m.name for m in members if m.issym() or m.islnk()]
            if links:
                raise SyncError(f"{subdir} at {ref} in {repo} contains links, which the sync does not copy:\n  "
                                + "\n  ".join(links[:50]))
            bad = [m.name for m in members if not (m.isfile() or m.isdir())]
            if bad:
                raise SyncError(f"{subdir} at {ref} in {repo} contains special files:\n  " + "\n  ".join(bad[:50]))
            if hasattr(tarfile, "data_filter"):
                tar.extractall(dest, members=members, filter="data")
            else:  # Python < 3.11.4: only plain files and folders get here, and git paths have no ".."
                tar.extractall(dest, members=members)
    except tarfile.TarError as err:
        raise SyncError(f"cannot unpack {subdir} at {ref} from {repo}: {err}") from err
    return dest / subdir


def differences(expected: dict[str, bytes], actual: dict[str, bytes]) -> list[str]:
    return sorted(p for p in expected.keys() | actual.keys() if expected.get(p) != actual.get(p))


def replace(dest: Path, source: Path, keep: frozenset[str] = frozenset(),
            tracked: frozenset[str] = frozenset()) -> None:
    """dest becomes a copy of source; IGNORED folders inside dest (node_modules, .venv, …) are kept unless their
    path is in tracked (the source repo has files there), and so are the files in keep (paths relative to dest)."""
    if dest.is_dir():
        dirs: list[Path] = []
        for dirpath, _, filenames in _walk(dest, tracked):
            dirs.append(dirpath)
            for name in filenames:
                if (dirpath / name).relative_to(dest).as_posix() not in keep:
                    (dirpath / name).unlink()
        for d in sorted(dirs, key=lambda p: len(p.parts), reverse=True):
            if d != dest and not any(d.iterdir()):
                d.rmdir()
    shutil.copytree(source, dest, symlinks=True, dirs_exist_ok=True)


def local_ignored(repo: Path, dest: Path, from_source: set[str],
                  tracked: frozenset[str] = frozenset()) -> frozenset[str]:
    """Files in dest that git ignores here and that did not come from the source repo (.npmrc, .env, …):
    user data that only exists on this machine, so a sync must not delete it."""
    prefix = dest.relative_to(repo).as_posix()
    local = [p for p in files_of(dest, tracked) if p not in from_source] if dest.is_dir() else []
    ignored = ignored_paths(repo, [f"{prefix}/{p}" for p in local])
    return frozenset(p for p in local if f"{prefix}/{p}" in ignored)


def _unignored_files(repo: Path, dest: Path) -> list[str]:
    """Files in dest (repo-relative posix) outside the IGNORED folders that git does not ignore here."""
    prefix = dest.relative_to(repo).as_posix()
    paths = [f"{prefix}/{p}" for p in files_of(dest)]
    skip = ignored_paths(repo, paths)
    return [p for p in paths if p not in skip]


def changed_paths(repo: Path) -> set[str]:
    out = git(repo, "status", "--porcelain", "-z", "-uall", "--no-renames")
    return {entry[3:] for entry in out.split("\0") if entry}


def read_pins(upstream_md: Path) -> dict[str, str]:
    return dict(PIN_LINE.findall(upstream_md.read_text(encoding="utf-8")))


def patches(fork: Path, sha: str, base: str) -> list[str]:
    try:
        base_sha = resolve(fork, base)
    except SyncError:
        return [f"(upstream base {base} is not in this fork clone)"]
    return [line for line in git(fork, "log", "--format=%h %s", f"{base_sha}..{sha}").splitlines() if line]


def upstream_text(fork_sha: str, ehl_sha: str, base: str, patch_lines: list[str]) -> str:
    lines = ["# Pinned upstream code – do not edit by hand", "",
             "`apps/x` and `apps/harbor` (Rowboat desktop app) and `../schema` (vault schema) are copies made by",
             "`spaces/rowboat/scripts/sync_desktop.py`. Change the fork or the EHL repo, then run the script again;",
             "it refuses to run when these copies were edited here.", "",
             "To re-pin the kernel plugin catalog, change `DESKTOP_PLUGIN_CATALOG_PIN` on branch `vibemind` of",
             "Flissel/rowboat (in `apps/x/apps/renderer/src/lib/rowboat-plugin-api.ts`), then run the sync.", "",
             f"- Fork: {FORK_URL} (branch `vibemind`)",
             f"- Fork commit: `{fork_sha}`",
             f"- Upstream base: rowboatlabs/rowboat `{base}`",
             f"- EHL repo: {EHL_URL} (`schema/`)",
             f"- EHL commit: `{ehl_sha}`", "",
             "## Patches on top of upstream (EHL hierarchy graph, VibeMind)", ""]
    lines += [f"- {p}" for p in patch_lines] or ["- (none)"]
    return "\n".join(lines) + "\n"


def unknown_state(rel_targets: list[str], why: str) -> str:
    targets = " ".join(rel_targets)
    return ("the pinned copies are now in an unknown state (replaced in part or not at all) and UPSTREAM.md was not "
            "updated. Restore the copies before running the sync again:\n"
            f"  git restore --source=HEAD --staged --worktree -- {targets}\n"
            f"  git clean -fd -- {targets}\n"
            f"reason: {why}")


def sync(space: Path, repo: Path, fork: Path, fork_ref: str, ehl: Path, ehl_ref: str,
         upstream_base: str = UPSTREAM_BASE, first_sync: bool = False) -> dict[str, str]:
    sources = {"fork": fork, "ehl": ehl}
    shas = {"fork": resolve(fork, fork_ref), "ehl": resolve(ehl, ehl_ref)}
    upstream_md = space / "rowboat" / "UPSTREAM.md"
    tgts = targets(space)
    from_source: dict[Target, set[str]] = {}   # files of the old pin: ignored ones of these may go
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        if upstream_md.exists():
            pins = read_pins(upstream_md)
            old = {"fork": pins.get("Fork"), "ehl": pins.get("EHL")}
            if None in old.values():
                raise SyncError(f"{upstream_md} names no fork or EHL commit")
            edited: list[str] = []
            for i, t in enumerate(tgts):
                pinned = export(sources[t.source], old[t.source], t.subdir, tmpdir / f"old{i}",
                                what=" (pinned in UPSTREAM.md)")
                pinned_files = files_of(pinned, None)
                from_source[t] = set(pinned_files)
                prefix = t.dest.relative_to(repo).as_posix()
                diff = [f"{prefix}/{p}" for p in differences(pinned_files, files_of(t.dest, dirs_of(pinned_files)))]
                skip = ignored_paths(repo, diff)   # tracked in the source repo but git-ignored here: absent on a fresh clone
                edited += [p for p in diff if p not in skip]
            if edited:
                raise SyncError("edited by hand since the last sync (change the fork or the EHL repo instead):\n  "
                                + "\n  ".join(edited[:50]))
        elif not first_sync:
            filled = [p for t in tgts for p in _unignored_files(repo, t.dest)]
            if filled:
                raise SyncError(f"{upstream_md.relative_to(repo).as_posix()} is missing, so the sync cannot check the "
                                "copies for edits by hand, but they hold files:\n  " + "\n  ".join(filled[:20])
                                + "\nRestore UPSTREAM.md (git restore), or pass --first-sync if this really is the "
                                "first sync and these files may be overwritten.")
        new = {t: export(sources[t.source], shas[t.source], t.subdir, tmpdir / f"new{i}") for i, t in enumerate(tgts)}
        if not (new[tgts[0]] / PIN_FILE).is_file():
            raise SyncError(f"apps/x/{PIN_FILE} is missing at fork commit {shas['fork']}; the web app's pin test needs it")
        patch_lines = patches(fork, shas["fork"], upstream_base)   # before any replace: a failing git log must not leave half a sync
        before = changed_paths(repo)
        rel_targets = [t.dest.relative_to(repo).as_posix() for t in tgts]
        sourced = {t: from_source.get(t, set()) | set(files_of(new[t], None)) for t in tgts}
        tracked = {t: dirs_of(sourced[t]) for t in tgts}
        keep = {t: local_ignored(repo, t.dest, sourced[t], tracked[t]) for t in tgts}
        try:
            for t in tgts:
                replace(t.dest, new[t], keep[t], tracked[t])
        except OSError as err:
            raise SyncError(unknown_state(rel_targets, f"{type(err).__name__}: {err}")) from err
        allowed = [p + "/" for p in rel_targets]
        outside = sorted(p for p in changed_paths(repo) - before if not any(p.startswith(a) for a in allowed))
        if outside:
            raise SyncError(unknown_state(rel_targets, "changes outside the pinned copies:\n  " + "\n  ".join(outside)))
    try:
        upstream_md.write_text(upstream_text(shas["fork"], shas["ehl"], upstream_base, patch_lines),
                               encoding="utf-8", newline="\n")
    except OSError as err:
        raise SyncError(unknown_state(rel_targets, f"writing {upstream_md}: {type(err).__name__}: {err}")) from err
    return shas


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--fork", type=Path, required=True, help="local clone of Flissel/rowboat")
    ap.add_argument("--fork-ref", required=True, help="commit or branch of the fork, e.g. vibemind")
    ap.add_argument("--ehl", type=Path, required=True, help="local clone of Flissel/ehl-munich-2026")
    ap.add_argument("--ehl-ref", required=True, help="commit or branch of the EHL repo")
    ap.add_argument("--first-sync", action="store_true",
                    help="no UPSTREAM.md yet: overwrite the copies without checking them for edits by hand")
    args = ap.parse_args(argv)
    repo = Path(git(SPACE, "rev-parse", "--show-toplevel").strip())
    try:
        shas = sync(SPACE, repo, args.fork, args.fork_ref, args.ehl, args.ehl_ref, first_sync=args.first_sync)
    except SyncError as err:
        log.error("sync stopped: %s", err)
        return 1
    log.info("synced apps/x + apps/harbor from %s and schema from %s; see spaces/rowboat/rowboat/UPSTREAM.md",
             shas["fork"][:9], shas["ehl"][:9])
    log.info(AFTER_SYNC_HINT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
