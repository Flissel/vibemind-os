"""
VibeMind PC Storage — MCP Server
=================================
Disk/storage inspection + safe cleanup for the local Windows machine.
Wraps the tosort/pc-cleaner scripts as structured MCP tools.

Tools (read-only):
- disk_usage            : Per-drive free/used/total (shutil.disk_usage)
- storage_scan          : Scan known cache/temp dirs, return sizes + safe flag
- dir_size              : Size of an arbitrary directory
- top_largest           : Top-N largest files under a path

Tools (mutating — require confirm=true):
- storage_clean         : Delete safe cache/temp dirs (run / nochrome variants)
"""
import os
import shutil
import string
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

mcp = FastMCP(
    "VibeMind PC Storage",
    instructions=(
        "Local PC storage inspection + safe cache cleanup. "
        "Always call storage_scan first to preview. "
        "storage_clean requires confirm=true and will permanently delete cache files."
    ),
)

LOCALAPPDATA = os.environ.get("LOCALAPPDATA", "")
APPDATA = os.environ.get("APPDATA", "")
TEMP = os.environ.get("TEMP", "")
HOME = os.path.expanduser("~")


def _fmt(b: int) -> str:
    if b > 1024**3:
        return f"{b / 1024**3:.2f} GB"
    if b > 1024**2:
        return f"{b / 1024**2:.1f} MB"
    if b > 1024:
        return f"{b / 1024:.0f} KB"
    return f"{b} B"


def _dir_size(path: str) -> int:
    total = 0
    try:
        for root, _dirs, files in os.walk(path):
            for f in files:
                try:
                    total += os.path.getsize(os.path.join(root, f))
                except (PermissionError, OSError):
                    pass
    except (PermissionError, OSError):
        pass
    return total


def _targets(include_chrome: bool = True) -> list[tuple[str, str, bool]]:
    t = [
        (TEMP, "Windows Temp", True),
        (os.path.join(LOCALAPPDATA, "Temp"), "User Temp", True),
        (os.path.join(LOCALAPPDATA, "Microsoft", "Windows", "INetCache"), "IE/Edge Cache", True),
        (os.path.join(LOCALAPPDATA, "pip", "cache"), "pip Cache", True),
        (os.path.join(LOCALAPPDATA, "npm-cache"), "npm Cache", True),
        (os.path.join(APPDATA, "npm-cache"), "npm Cache (Roaming)", True),
        (os.path.join(LOCALAPPDATA, "pnpm", "store"), "pnpm Store", True),
        (os.path.join(LOCALAPPDATA, "yarn", "Cache"), "Yarn Cache", True),
        (os.path.join(LOCALAPPDATA, "NuGet", "Cache"), "NuGet Cache", True),
        (os.path.join(HOME, ".cache"), ".cache", True),
        (os.path.join(HOME, ".pyenv", "pyenv-win", "install_cache"), "pyenv Install Cache", True),
        (os.path.join(LOCALAPPDATA, "CrashDumps"), "Crash Dumps", True),
        (os.path.join(LOCALAPPDATA, "Temp", "vscode-stable-user-x64"), "VSCode Update Cache", True),
        (os.path.join(LOCALAPPDATA, "Microsoft", "Windows", "Explorer"), "Thumbnail Cache", True),
        (os.path.join(LOCALAPPDATA, "Docker", "wsl"), "Docker WSL Data", False),
        (os.path.join(LOCALAPPDATA, "Packages"), "UWP App Packages", False),
        (os.path.join(HOME, "Downloads"), "Downloads", False),
    ]
    if include_chrome:
        t.extend([
            (os.path.join(LOCALAPPDATA, "Google", "Chrome", "User Data", "Default", "Cache"), "Chrome Cache", True),
            (os.path.join(LOCALAPPDATA, "Google", "Chrome", "User Data", "Default", "Code Cache"), "Chrome Code Cache", True),
        ])
    return t


def _clean_dir(path: str) -> tuple[int, int]:
    freed = 0
    errors = 0
    try:
        items = os.listdir(path)
    except (PermissionError, OSError):
        return 0, 1
    for item in items:
        fp = os.path.join(path, item)
        try:
            if os.path.isfile(fp) or os.path.islink(fp):
                size = os.path.getsize(fp)
                os.remove(fp)
                freed += size
            elif os.path.isdir(fp):
                size = _dir_size(fp)
                shutil.rmtree(fp, ignore_errors=True)
                freed += size
        except (PermissionError, OSError):
            errors += 1
    return freed, errors


@mcp.tool()
def disk_usage() -> dict[str, Any]:
    """Report free/used/total bytes for each mounted drive (Windows)."""
    drives = []
    for letter in string.ascii_uppercase:
        root = f"{letter}:\\"
        if os.path.exists(root):
            try:
                u = shutil.disk_usage(root)
                drives.append({
                    "drive": root,
                    "total": u.total,
                    "used": u.used,
                    "free": u.free,
                    "total_h": _fmt(u.total),
                    "used_h": _fmt(u.used),
                    "free_h": _fmt(u.free),
                    "percent_used": round(u.used / u.total * 100, 1) if u.total else 0,
                })
            except OSError:
                pass
    return {"drives": drives}


@mcp.tool()
def storage_scan(min_mb: float = 1.0, include_chrome: bool = True) -> dict[str, Any]:
    """Scan known cache/temp dirs. Read-only. Returns per-target size + safe flag."""
    results = []
    total_safe = 0
    total_risky = 0
    for path, name, safe in _targets(include_chrome):
        if not os.path.exists(path):
            continue
        size = _dir_size(path)
        if size < min_mb * 1024 * 1024:
            continue
        results.append({
            "name": name,
            "path": path,
            "size": size,
            "size_h": _fmt(size),
            "safe_to_delete": safe,
        })
        if safe:
            total_safe += size
        else:
            total_risky += size
    results.sort(key=lambda x: x["size"], reverse=True)
    return {
        "targets": results,
        "total_safe": total_safe,
        "total_safe_h": _fmt(total_safe),
        "total_risky": total_risky,
        "total_risky_h": _fmt(total_risky),
    }


@mcp.tool()
def dir_size(path: str) -> dict[str, Any]:
    """Compute recursive size of an arbitrary directory."""
    if not os.path.exists(path):
        return {"error": f"path not found: {path}"}
    size = _dir_size(path)
    return {"path": path, "size": size, "size_h": _fmt(size)}


@mcp.tool()
def top_largest(path: str, n: int = 20) -> dict[str, Any]:
    """Return the top-N largest files under a path."""
    if not os.path.exists(path):
        return {"error": f"path not found: {path}"}
    files: list[tuple[int, str]] = []
    for root, _dirs, fs in os.walk(path):
        for f in fs:
            fp = os.path.join(root, f)
            try:
                files.append((os.path.getsize(fp), fp))
            except (PermissionError, OSError):
                pass
    files.sort(reverse=True)
    return {
        "path": path,
        "files": [{"size": s, "size_h": _fmt(s), "path": p} for s, p in files[:n]],
    }


@mcp.tool()
def storage_clean(
    confirm: bool = False,
    include_chrome: bool = True,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Delete safe cache/temp dirs. Requires confirm=true to actually delete.
    Set include_chrome=false if Chrome is running. Defaults to dry_run=true.
    """
    if not confirm and not dry_run:
        return {"error": "refusing to delete without confirm=true; set dry_run=false AND confirm=true"}
    results = []
    total_freed = 0
    total_errors = 0
    for path, name, safe in _targets(include_chrome):
        if not safe or not os.path.exists(path):
            continue
        if dry_run or not confirm:
            size = _dir_size(path)
            results.append({"name": name, "path": path, "would_free": size, "would_free_h": _fmt(size)})
            total_freed += size
        else:
            freed, errors = _clean_dir(path)
            total_freed += freed
            total_errors += errors
            results.append({"name": name, "path": path, "freed": freed, "freed_h": _fmt(freed), "locked": errors})
    return {
        "dry_run": dry_run or not confirm,
        "targets": results,
        "total_freed": total_freed,
        "total_freed_h": _fmt(total_freed),
        "locked_errors": total_errors,
    }


if __name__ == "__main__":
    mcp.run()
