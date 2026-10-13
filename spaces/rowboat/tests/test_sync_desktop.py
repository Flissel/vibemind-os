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
        "apps/x/package.json": "x1\n", PIN: "pin\n", "apps/harbor/package.json": "h1\n", "README.md": "fork\n"})
    ehl, ehl_sha = new_repo(tmp_path / "ehl", {
        "schema/pyproject.toml": "s1\n", "schema/profiles/vibemind.yaml": "company: VibeMind\n", "market/x.py": "bmw\n"})
    vos, _ = new_repo(tmp_path / "vos", {
        ".gitignore": "node_modules/\n.venv/\n",
        "spaces/rowboat/rowboat/apps/x/apps/renderer/old.ts": "old\n",
        "spaces/rowboat/rowboat/apps/rowboat/web.ts": "web\n",
        "README.md": "vos\n"})
    keep = vos / "spaces/rowboat/rowboat/apps/x/node_modules/keep.txt"
    keep.parent.mkdir(parents=True)
    keep.write_text("installed", encoding="utf-8")
    return {"fork": fork, "fork_sha": fork_sha, "ehl": ehl, "ehl_sha": ehl_sha, "vos": vos,
            "space": vos / "spaces/rowboat", "keep": keep}


def run_sync(w, fork_ref=None, ehl_ref=None):
    return sync(w["space"], w["vos"], w["fork"], fork_ref or w["fork_sha"], w["ehl"], ehl_ref or w["ehl_sha"])


def test_first_sync_replaces_only_the_targets(world):
    shas = run_sync(world)
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
    run_sync(world)
    new_sha = commit_files(world["fork"], {"apps/x/package.json": "x2\n"})
    run_sync(world, fork_ref=new_sha)
    assert (world["space"] / "rowboat/apps/x/package.json").read_text(encoding="utf-8") == "x2\n"
    assert read_pins(world["space"] / "rowboat/UPSTREAM.md")["Fork"] == new_sha


def test_crlf_checkout_is_not_a_hand_edit(world):
    run_sync(world)
    f = world["space"] / "rowboat/apps/x/package.json"
    f.write_bytes(b"x1\r\n")
    new_sha = commit_files(world["fork"], {"apps/x/package.json": "x3\n"})
    run_sync(world, fork_ref=new_sha)
    assert f.read_text(encoding="utf-8") == "x3\n"


def test_hand_edit_stops_the_next_sync(world):
    run_sync(world)
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
        run_sync(world, fork_ref=sha)
    assert (world["space"] / "rowboat/apps/x/apps/renderer/old.ts").exists()


def test_unknown_ref_stops_before_touching(world):
    with pytest.raises(SyncError):
        run_sync(world, fork_ref="no-such-ref")
    assert (world["space"] / "rowboat/apps/x/apps/renderer/old.ts").exists()
    assert not (world["space"] / "rowboat/UPSTREAM.md").exists()


def test_changes_outside_the_targets_stop_the_sync(world, monkeypatch):
    real_replace = sync_desktop.replace

    def leaky(dest, source):
        real_replace(dest, source)
        (world["vos"] / "README.md").write_text("collateral\n", encoding="utf-8")

    monkeypatch.setattr(sync_desktop, "replace", leaky)
    with pytest.raises(SyncError, match="README.md") as info:
        run_sync(world)
    assert "unknown state" in str(info.value)
    assert "git restore --source=HEAD --staged --worktree -- " in str(info.value)
    assert "spaces/rowboat/rowboat/apps/x" in str(info.value)
    assert not (world["space"] / "rowboat/UPSTREAM.md").exists()


def test_failed_replace_reports_unknown_state_and_keeps_upstream(world, monkeypatch):
    real_replace = sync_desktop.replace
    calls: list[Path] = []

    def locked(dest, source):
        calls.append(dest)
        if len(calls) == 2:
            raise PermissionError("locked by another process")
        real_replace(dest, source)

    monkeypatch.setattr(sync_desktop, "replace", locked)
    with pytest.raises(SyncError, match="unknown state") as info:
        run_sync(world)
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
        run_sync(world)
    assert (world["space"] / "rowboat/apps/x/apps/renderer/old.ts").exists()
    assert not (world["space"] / "rowboat/UPSTREAM.md").exists()
