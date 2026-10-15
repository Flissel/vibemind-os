"""Checks for spaces/rowboat/scripts/sync_desktop.py (temporary git repos only)."""
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import sync_desktop  # noqa: E402
from sync_desktop import SyncError, read_pins, sync  # noqa: E402

PIN = "apps/x/apps/renderer/src/lib/rowboat-plugin-api.ts"


def commit_files(repo: Path, files: dict[str, str], message: str = "c") -> str:
    for rel, text in files.items():
        f = repo / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text, encoding="utf-8", newline="\n")
    run = lambda *a: subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True, text=True)
    run("add", "-A")
    run("-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null",
        "commit", "-q", "-m", message)
    return run("rev-parse", "HEAD").stdout.strip()


def new_repo(path: Path, files: dict[str, str]) -> tuple[Path, str]:
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    return path, commit_files(path, files)


@pytest.fixture
def world(tmp_path):
    fork, fork_sha = new_repo(tmp_path / "fork", {
        "apps/x/package.json": "x1\n", PIN: "pin\n", "apps/harbor/package.json": "h1\n", "apps/x/cache/blob.db": "db1\n", "README.md": "fork\n"})
    ehl, ehl_sha = new_repo(tmp_path / "ehl", {
        "schema/pyproject.toml": "s1\n", "schema/profiles/vibemind.yaml": "company: VibeMind\n", "market/x.py": "bmw\n"})
    vos, _ = new_repo(tmp_path / "vos", {
        ".gitignore": "node_modules/\n.venv/\n*.db\n",
        "spaces/rowboat/rowboat/apps/x/apps/renderer/old.ts": "old\n",
        "spaces/rowboat/rowboat/apps/rowboat/web.ts": "web\n",
        "README.md": "vos\n"})
    keep = vos / "spaces/rowboat/rowboat/apps/x/node_modules/keep.txt"
    keep.parent.mkdir(parents=True)
    keep.write_text("installed", encoding="utf-8")
    return {"fork": fork, "fork_sha": fork_sha, "ehl": ehl, "ehl_sha": ehl_sha, "vos": vos,
            "space": vos / "spaces/rowboat", "keep": keep}


def run_sync(w, fork_ref=None, ehl_ref=None, first_sync=False):
    return sync(w["space"], w["vos"], w["fork"], fork_ref or w["fork_sha"], w["ehl"], ehl_ref or w["ehl_sha"],
                first_sync=first_sync)


def test_first_sync_replaces_only_the_targets(world):
    shas = run_sync(world, first_sync=True)
    app = world["space"] / "rowboat/apps/x"
    assert (app / "package.json").read_text(encoding="utf-8") == "x1\n"
    assert not (app / "apps/renderer/old.ts").exists()
    assert (world["space"] / "rowboat/apps/harbor/package.json").is_file()
    assert (world["space"] / "schema/profiles/vibemind.yaml").is_file()
    assert not (world["space"] / "schema/market").exists() and not (world["space"] / "market").exists()
    assert (world["space"] / "rowboat/apps/rowboat/web.ts").read_text(encoding="utf-8") == "web\n"
    assert world["keep"].read_text(encoding="utf-8") == "installed"
    pins = read_pins(world["space"] / "rowboat/UPSTREAM.md")
    assert pins == {"Fork": shas["fork"], "EHL": shas["ehl"]} == {"Fork": world["fork_sha"], "EHL": world["ehl_sha"]}


def test_unchanged_copy_syncs_to_a_new_commit(world):
    run_sync(world, first_sync=True)
    new_sha = commit_files(world["fork"], {"apps/x/package.json": "x2\n"})
    run_sync(world, fork_ref=new_sha)
    assert (world["space"] / "rowboat/apps/x/package.json").read_text(encoding="utf-8") == "x2\n"
    assert read_pins(world["space"] / "rowboat/UPSTREAM.md")["Fork"] == new_sha


def test_file_tracked_in_fork_but_git_ignored_here_is_not_a_hand_edit(world):
    run_sync(world, first_sync=True)
    blob = world["space"] / "rowboat/apps/x/cache/blob.db"
    assert blob.is_file()
    blob.unlink()   # a fresh clone of this repo does not have it: *.db is ignored
    new_sha = commit_files(world["fork"], {"apps/x/package.json": "x4\n"})
    run_sync(world, fork_ref=new_sha)
    assert (world["space"] / "rowboat/apps/x/package.json").read_text(encoding="utf-8") == "x4\n"


def test_crlf_checkout_is_not_a_hand_edit(world):
    run_sync(world, first_sync=True)
    f = world["space"] / "rowboat/apps/x/package.json"
    f.write_bytes(b"x1\r\n")
    new_sha = commit_files(world["fork"], {"apps/x/package.json": "x3\n"})
    run_sync(world, fork_ref=new_sha)
    assert f.read_text(encoding="utf-8") == "x3\n"


def test_hand_edit_stops_the_next_sync(world):
    run_sync(world, first_sync=True)
    edited = world["space"] / "rowboat/apps/x/package.json"
    edited.write_text("edited here\n", encoding="utf-8", newline="\n")
    new_sha = commit_files(world["fork"], {"apps/x/package.json": "x2\n"})
    with pytest.raises(SyncError, match="rowboat/apps/x/package.json"):
        run_sync(world, fork_ref=new_sha)
    assert edited.read_text(encoding="utf-8") == "edited here\n"


def test_missing_pin_file_stops_before_replacing(world):
    subprocess.run(["git", "-C", str(world["fork"]), "rm", "-q", PIN], check=True)
    sha = commit_files(world["fork"], {}, "drop pin")
    with pytest.raises(SyncError, match="rowboat-plugin-api.ts"):
        run_sync(world, fork_ref=sha, first_sync=True)
    assert (world["space"] / "rowboat/apps/x/apps/renderer/old.ts").exists()


def test_unknown_ref_stops_before_touching(world):
    with pytest.raises(SyncError, match="no-such-ref is not a commit in .*fork"):
        run_sync(world, fork_ref="no-such-ref", first_sync=True)
    assert (world["space"] / "rowboat/apps/x/apps/renderer/old.ts").exists()
    assert not (world["space"] / "rowboat/UPSTREAM.md").exists()


def test_changes_outside_the_targets_stop_the_sync(world, monkeypatch):
    real_replace = sync_desktop.replace

    def leaky(dest, source, *rest):
        real_replace(dest, source, *rest)
        (world["vos"] / "README.md").write_text("collateral\n", encoding="utf-8")

    monkeypatch.setattr(sync_desktop, "replace", leaky)
    with pytest.raises(SyncError, match="README.md") as info:
        run_sync(world, first_sync=True)
    assert "unknown state" in str(info.value)
    assert "git restore --source=HEAD --staged --worktree -- " in str(info.value)
    assert "spaces/rowboat/rowboat/apps/x" in str(info.value)
    assert not (world["space"] / "rowboat/UPSTREAM.md").exists()


def test_failed_replace_reports_unknown_state_and_keeps_upstream(world, monkeypatch):
    real_replace = sync_desktop.replace
    calls: list[Path] = []

    def locked(dest, source, *rest):
        calls.append(dest)
        if len(calls) == 2:
            raise PermissionError("locked by another process")
        real_replace(dest, source, *rest)

    monkeypatch.setattr(sync_desktop, "replace", locked)
    with pytest.raises(SyncError, match="unknown state") as info:
        run_sync(world, first_sync=True)
    message = str(info.value)
    assert "git restore --source=HEAD --staged --worktree -- " in message
    assert "git clean -fd -- " in message
    assert "spaces/rowboat/rowboat/apps/harbor" in message
    assert "locked by another process" in message
    assert not (world["space"] / "rowboat/UPSTREAM.md").exists()


def test_patch_listing_fails_before_any_replace(world, monkeypatch):
    def broken(fork, sha, base):
        raise SyncError("git log failed")

    monkeypatch.setattr(sync_desktop, "patches", broken)
    with pytest.raises(SyncError, match="git log failed"):
        run_sync(world, first_sync=True)
    assert (world["space"] / "rowboat/apps/x/apps/renderer/old.ts").exists()
    assert not (world["space"] / "rowboat/UPSTREAM.md").exists()


def test_upstream_md_tells_how_to_repin_the_kernel_catalog(world):
    run_sync(world, first_sync=True)
    text = (world["space"] / "rowboat/UPSTREAM.md").read_text(encoding="utf-8")
    assert "DESKTOP_PLUGIN_CATALOG_PIN" in text
    assert "branch `vibemind`" in text and "Flissel/rowboat" in text


def test_successful_sync_tells_to_reinstall_dependencies(world, monkeypatch, caplog):
    monkeypatch.setattr(sync_desktop, "SPACE", world["space"])
    args = ["--fork", str(world["fork"]), "--fork-ref", world["fork_sha"],
            "--ehl", str(world["ehl"]), "--ehl-ref", world["ehl_sha"], "--first-sync"]
    with caplog.at_level("INFO", logger="sync_desktop"):
        assert sync_desktop.main(args) == 0
    assert "start_desktop.py --install" in caplog.text


# Review PR #151, Befund 1: ignorierte Dateien, die es nur hier gibt (.npmrc, .env, *.env.local),
# sind Nutzerdaten - der Sync darf sie nicht loeschen.
def _lokale_ignorierte_dateien(world):
    app = world["space"] / "rowboat/apps/x"
    dateien = {app / ".npmrc": "script-shell=pwsh\n", app / ".env": "KEY=lokal\n",
               app / "apps/main/.env.local": "X=1\n"}
    for f, text in dateien.items():
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text, encoding="utf-8", newline="\n")
    return dateien


def _fork_mit_gitignore(world):
    return commit_files(world["fork"], {"apps/x/.gitignore": ".npmrc\n*.env.local\n"})


def test_lokale_ignorierte_dateien_ueberleben_den_sync(world):
    commit_files(world["vos"], {".gitignore": "node_modules/\n.venv/\n*.db\n.env\n"})
    run_sync(world, fork_ref=_fork_mit_gitignore(world), first_sync=True)
    dateien = _lokale_ignorierte_dateien(world)
    new_sha = commit_files(world["fork"], {"apps/x/package.json": "x5\n"})
    run_sync(world, fork_ref=new_sha)
    assert (world["space"] / "rowboat/apps/x/package.json").read_text(encoding="utf-8") == "x5\n"
    for f, text in dateien.items():
        assert f.read_text(encoding="utf-8") == text, f


def test_lokale_ignorierte_datei_ueberlebt_den_ersten_sync(world):
    commit_files(world["vos"], {".gitignore": "node_modules/\n.venv/\n*.db\n.env\n"})
    env = world["space"] / "rowboat/apps/x/.env"
    env.parent.mkdir(parents=True, exist_ok=True)
    env.write_text("KEY=lokal\n", encoding="utf-8", newline="\n")
    run_sync(world, first_sync=True)
    assert env.read_text(encoding="utf-8") == "KEY=lokal\n"
    assert not (world["space"] / "rowboat/apps/x/apps/renderer/old.ts").exists()


def test_ignorierte_fork_datei_verschwindet_wenn_der_fork_sie_loescht(world):
    run_sync(world, first_sync=True)
    blob = world["space"] / "rowboat/apps/x/cache/blob.db"
    assert blob.is_file()
    (world["fork"] / "apps/x/cache/blob.db").unlink()
    new_sha = commit_files(world["fork"], {"apps/x/package.json": "x6\n"})
    run_sync(world, fork_ref=new_sha)
    assert not blob.exists()


# Review PR #151, findings 3, 4, 5 and 11: the riskier sync paths.
def git_out(repo: Path, *args: str, stdin: str | None = None) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True,
                          input=stdin).stdout


def test_tracked_dist_folder_loses_files_the_fork_deletes(world):
    sha = commit_files(world["fork"], {"apps/harbor/pkg/dist/old.js": "old\n", "apps/harbor/pkg/dist/kept.js": "k\n"})
    run_sync(world, fork_ref=sha, first_sync=True)
    dist = world["space"] / "rowboat/apps/harbor/pkg/dist"
    assert (dist / "old.js").is_file()
    (world["fork"] / "apps/harbor/pkg/dist/old.js").unlink()
    run_sync(world, fork_ref=commit_files(world["fork"], {}, "drop old.js"))
    assert not (dist / "old.js").exists()
    assert (dist / "kept.js").read_text(encoding="utf-8") == "k\n"


def test_hand_edit_in_a_tracked_dist_folder_stops_the_sync(world):
    sha = commit_files(world["fork"], {"apps/harbor/pkg/dist/index.js": "built\n"})
    run_sync(world, fork_ref=sha, first_sync=True)
    edited = world["space"] / "rowboat/apps/harbor/pkg/dist/index.js"
    edited.write_text("edited here\n", encoding="utf-8", newline="\n")
    with pytest.raises(SyncError, match="apps/harbor/pkg/dist/index.js"):
        run_sync(world, fork_ref=commit_files(world["fork"], {"apps/x/package.json": "x7\n"}))
    assert edited.read_text(encoding="utf-8") == "edited here\n"


def test_untracked_dist_folder_is_still_kept(world):
    run_sync(world, first_sync=True)
    built = world["space"] / "rowboat/apps/harbor/pkg/dist/index.js"
    built.parent.mkdir(parents=True)
    built.write_text("local build\n", encoding="utf-8")
    run_sync(world, fork_ref=commit_files(world["fork"], {"apps/x/package.json": "x8\n"}))
    assert built.read_text(encoding="utf-8") == "local build\n"


def test_symlink_in_the_fork_stops_before_replacing(world):
    blob = git_out(world["fork"], "hash-object", "-w", "--stdin", stdin="C:/Users/someone/secret.txt").strip()
    git_out(world["fork"], "update-index", "--add", "--cacheinfo", f"120000,{blob},apps/x/link.txt")
    git_out(world["fork"], "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false",
            "-c", "core.hooksPath=/dev/null", "commit", "-q", "-m", "link")
    sha = git_out(world["fork"], "rev-parse", "HEAD").strip()
    with pytest.raises(SyncError, match="link.txt"):
        run_sync(world, fork_ref=sha, first_sync=True)
    assert (world["space"] / "rowboat/apps/x/apps/renderer/old.ts").exists()
    assert not (world["space"] / "rowboat/apps/x/link.txt").exists()


def test_missing_upstream_md_after_a_sync_stops(world):
    run_sync(world, first_sync=True)
    upstream = world["space"] / "rowboat/UPSTREAM.md"
    upstream.unlink()
    edited = world["space"] / "rowboat/apps/x/package.json"
    edited.write_text("edited here\n", encoding="utf-8", newline="\n")
    with pytest.raises(SyncError, match="UPSTREAM.md is missing"):
        run_sync(world, fork_ref=commit_files(world["fork"], {"apps/x/package.json": "x9\n"}))
    assert edited.read_text(encoding="utf-8") == "edited here\n"
    assert not upstream.exists()


def test_first_sync_into_empty_targets_needs_no_flag(world):
    git_out(world["vos"], "rm", "-q", "-r", "spaces/rowboat/rowboat/apps/x/apps")
    commit_files(world["vos"], {}, "no copy yet")
    run_sync(world)   # only the ignored node_modules is there: nothing to protect
    assert (world["space"] / "rowboat/apps/x/package.json").is_file()
    assert world["keep"].read_text(encoding="utf-8") == "installed"


def test_hand_added_file_stops_the_sync(world):
    run_sync(world, first_sync=True)
    added = world["space"] / "rowboat/apps/x/apps/renderer/extra.ts"
    added.parent.mkdir(parents=True, exist_ok=True)
    added.write_text("mine\n", encoding="utf-8")
    with pytest.raises(SyncError, match="rowboat/apps/x/apps/renderer/extra.ts"):
        run_sync(world, fork_ref=commit_files(world["fork"], {"apps/x/package.json": "x10\n"}))
    assert added.read_text(encoding="utf-8") == "mine\n"


def test_hand_deleted_file_stops_the_sync(world):
    run_sync(world, first_sync=True)
    (world["space"] / "rowboat/apps/harbor/package.json").unlink()
    with pytest.raises(SyncError, match="rowboat/apps/harbor/package.json"):
        run_sync(world, fork_ref=commit_files(world["fork"], {"apps/x/package.json": "x11\n"}))
    assert not (world["space"] / "rowboat/apps/harbor/package.json").exists()


def test_resync_to_the_same_commit_changes_nothing(world):
    run_sync(world, first_sync=True)
    commit_files(world["vos"], {}, "synced")
    upstream = (world["space"] / "rowboat/UPSTREAM.md").read_bytes()
    run_sync(world)
    assert git_out(world["vos"], "status", "--porcelain", "-uall") == ""
    assert (world["space"] / "rowboat/UPSTREAM.md").read_bytes() == upstream
    assert world["keep"].read_text(encoding="utf-8") == "installed"


def test_old_pin_missing_from_the_clone_says_so(world):
    run_sync(world, first_sync=True)
    upstream = world["space"] / "rowboat/UPSTREAM.md"
    upstream.write_text(upstream.read_text(encoding="utf-8").replace(world["fork_sha"], "a" * 40), encoding="utf-8")
    with pytest.raises(SyncError, match="pinned in UPSTREAM.md") as info:
        run_sync(world)
    assert "a" * 40 in str(info.value) and "fetch" in str(info.value)
