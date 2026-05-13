"""
MCP Server for Handoff Multi-Agent System

Exposes the handoff system to Claude Code for intelligent orchestration
of desktop automation tasks.

Usage:
    python mcp_server_handoff.py

Add to .claude/settings.json:
{
    "mcpServers": {
        "handoff": {
            "command": "python",
            "args": ["path/to/mcp_server_handoff.py"]
        }
    }
}

Production Deployment:
    See service/ directory for Windows service management and health monitoring.
"""

import asyncio
import json
import sys
import os
import signal
import logging
from typing import Any, Dict, List, Optional
from datetime import datetime

# Windows: opt into Per-Monitor DPI awareness *before* importing pyautogui/PIL
# or ImageGrab. Without this, GDI screen capture from a non-DPI-aware process
# only sees the bare desktop background — modern hardware-accelerated windows
# (browsers, VS Code, Electron apps) are composited by DWM and don't appear
# in the captured frame, so every screenshot is a uniform desktop-background
# color (e.g. Win 11 dark mode = (25, 26, 27)). Setting awareness here makes
# all child capture libs (pyautogui, mss, ImageGrab) see the real composited
# desktop.
if sys.platform == "win32":
    try:
        import ctypes
        # PROCESS_PER_MONITOR_DPI_AWARE = 2  (Win 8.1+)
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

# Add parent to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Add voice/python to path so transitive imports of llm_config resolve
# (core/openrouter_client.py uses `from llm_config import get_model`).
_VOICE_PYTHON = os.path.normpath(
    os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "..", "..", "..", "..", "..", "voice", "python",
    )
)
if os.path.isdir(_VOICE_PYTHON) and _VOICE_PYTHON not in sys.path:
    sys.path.insert(0, _VOICE_PYTHON)

# Load production config
try:
    from config import load_config, get_config
    _config = load_config()
except ImportError:
    _config = None

# Setup logging
logging.basicConfig(
    level=logging.INFO if not _config else getattr(logging, _config.log_level),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('HandoffMCP')

# MCP imports
try:
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import Tool, TextContent
except ImportError:
    print("MCP package not installed. Run: pip install mcp", file=sys.stderr)
    sys.exit(1)


# ── Adaptive-skill helpers ──────────────────────────────────────────────
# Loaded lazily so we don't slow down server startup when skills are unused.
_SKILL_LIB_ROOT = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", "..", "..", "..", "..", "skills",
))


def _ensure_skill_lib_on_path() -> bool:
    if os.path.isdir(_SKILL_LIB_ROOT) and _SKILL_LIB_ROOT not in sys.path:
        sys.path.insert(0, _SKILL_LIB_ROOT)
    return os.path.isdir(_SKILL_LIB_ROOT)


def _skill_search(query: str, agent: Optional[str] = None, limit: int = 5) -> Dict[str, Any]:
    if not _ensure_skill_lib_on_path():
        return {"success": False, "error": f"skill library not found at {_SKILL_LIB_ROOT}"}
    try:
        import _indexer  # type: ignore[import-not-found]
        results = _indexer.search(query=query, agent=agent, limit=limit)
        return {
            "success": True,
            "query": query,
            "results": [
                {
                    "name": r["payload"].get("name"),
                    "app": r["payload"].get("app"),
                    "description": r["payload"].get("description"),
                    "confidence": r["payload"].get("confidence", 0.0),
                    "file_path": r["payload"].get("file_path"),
                    "score": r.get("score"),
                }
                for r in results
            ],
        }
    except Exception as exc:
        return {"success": False, "error": f"{type(exc).__name__}: {exc}"}


# ── App lifecycle (launch/focus/list) ──────────────────────────────────
# Whitelist of allowed app names → command. Prevents arbitrary process spawn.
# Resolved via `where` on Windows so we don't hardcode install paths.
_APP_WHITELIST: Dict[str, list[str]] = {
    "notepad": ["notepad.exe"],
    "calc": ["calc.exe"],
    "explorer": ["explorer.exe"],
    "chrome": ["chrome.exe"],
    "msedge": ["msedge.exe"],
    "firefox": ["firefox.exe"],
    "code": ["code.cmd", "code.exe"],
    "vscode": ["code.cmd", "code.exe"],
    "winword": ["winword.exe"],
    "word": ["winword.exe"],
    "excel": ["excel.exe"],
    "powerpnt": ["powerpnt.exe"],
    "powerpoint": ["powerpnt.exe"],
    "outlook": ["outlook.exe"],
    "claude": ["claude.exe", "Claude.exe"],
    "claude-desktop": ["claude.exe", "Claude.exe"],
    "telegram": ["Telegram.exe", "telegram.exe"],
    "spotify": ["Spotify.exe"],
    "powershell": ["powershell.exe", "pwsh.exe"],
}


def _query_app_paths_registry(exe_name: str) -> Optional[str]:
    """Look up an executable in the Windows ``App Paths`` registry.

    Windows applications register themselves under
    ``HKLM\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\App Paths\\<exe>``
    (and the HKCU equivalent). The default value of that key is the full
    install path. This is the *authoritative* discovery source — nicer
    than scanning Program Files.
    """
    if sys.platform != "win32":
        return None
    try:
        import winreg
    except ImportError:
        return None
    sub = rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{exe_name}"
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for flag in (0, getattr(winreg, "KEY_WOW64_64KEY", 0), getattr(winreg, "KEY_WOW64_32KEY", 0)):
            try:
                with winreg.OpenKey(hive, sub, 0, winreg.KEY_READ | flag) as key:
                    value, _type = winreg.QueryValueEx(key, "")
                    value = os.path.expandvars(str(value).strip().strip('"'))
                    if value and os.path.isfile(value):
                        return value
            except OSError:
                continue
    return None


def _scan_start_menu(exe_name: str) -> Optional[str]:
    """Search Start Menu .lnk shortcuts for one whose target matches exe_name.

    Cheap, no dependencies — just walks the two Start Menu trees and reads
    .lnk files via the ``win32com.client`` Shell32 helper if available.
    Otherwise we read raw bytes and string-search for the exe name.
    """
    if sys.platform != "win32":
        return None
    roots = [
        os.path.expandvars(r"%ProgramData%\Microsoft\Windows\Start Menu\Programs"),
        os.path.expandvars(r"%APPDATA%\Microsoft\Windows\Start Menu\Programs"),
    ]
    target_lower = exe_name.lower()
    try:
        import win32com.client  # type: ignore[import-not-found]
        shell = win32com.client.Dispatch("WScript.Shell")
        for root in roots:
            if not os.path.isdir(root):
                continue
            for dirpath, _dirs, files in os.walk(root):
                for fn in files:
                    if not fn.lower().endswith(".lnk"):
                        continue
                    lnk_path = os.path.join(dirpath, fn)
                    try:
                        sc = shell.CreateShortcut(lnk_path)
                        target = (sc.TargetPath or "").strip()
                        if target.lower().endswith(target_lower) and os.path.isfile(target):
                            return target
                    except Exception:
                        continue
        return None
    except ImportError:
        # Fallback: byte-scan .lnk files for the exe name. Not perfect but
        # works for most apps because Windows stores the path in clear text
        # inside the shortcut binary.
        for root in roots:
            if not os.path.isdir(root):
                continue
            for dirpath, _dirs, files in os.walk(root):
                for fn in files:
                    if not fn.lower().endswith(".lnk"):
                        continue
                    lnk_path = os.path.join(dirpath, fn)
                    try:
                        raw = open(lnk_path, "rb").read()
                    except OSError:
                        continue
                    # Find UTF-16-LE encoded path that ends in our exe name
                    needle = ("\\" + exe_name).encode("utf-16-le").lower()
                    blob = raw.lower()
                    idx = blob.find(needle)
                    if idx == -1:
                        continue
                    # Walk backwards to find drive letter (A-Z + ':')
                    start = max(0, idx - 600)
                    seg = raw[start: idx + len(needle)]
                    # Try decode + extract last \X:\... path
                    try:
                        text = seg.decode("utf-16-le", errors="ignore")
                    except Exception:
                        continue
                    # Find rightmost drive letter
                    for i in range(len(text) - 2, 0, -1):
                        if text[i] == ":" and text[i + 1] == "\\" and text[i - 1].isalpha():
                            null_idx = text.find("\x00", i)
                            if null_idx == -1:
                                null_idx = len(text)
                            candidate = text[i - 1: null_idx]
                            candidate = candidate.split("\x00", 1)[0]
                            if os.path.isfile(candidate):
                                return candidate
                            break
        return None


def _scan_standard_dirs(exe_name: str) -> Optional[str]:
    """Last resort: walk ProgramFiles / LocalAppData two levels deep."""
    roots = [
        os.environ.get("ProgramFiles", r"C:\Program Files"),
        os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
        os.environ.get("LOCALAPPDATA", os.path.expandvars(r"%LOCALAPPDATA%")),
        os.environ.get("APPDATA", os.path.expandvars(r"%APPDATA%")),
    ]
    target_lower = exe_name.lower()
    for root in roots:
        if not root or not os.path.isdir(root):
            continue
        # Limit walk depth to 5 to avoid slow scans of huge trees
        for dirpath, _dirs, files in os.walk(root):
            depth = dirpath[len(root):].count(os.sep)
            if depth > 5:
                _dirs[:] = []
                continue
            for fn in files:
                if fn.lower() == target_lower:
                    candidate = os.path.join(dirpath, fn)
                    if os.path.isfile(candidate):
                        return candidate
    return None


# Cache resolved paths so the (potentially slow) start-menu walk runs once.
_RESOLVED_CACHE: Dict[str, str] = {}


def _resolve_app_command(app_name: str) -> Optional[str]:
    """Look up the executable for a whitelisted app name.

    Discovery cascade (first match wins, result cached):
      1. ``shutil.which()`` — PATH / current dir.
      2. Windows ``App Paths`` registry — apps register themselves here.
      3. Start Menu .lnk shortcuts — most installed apps have one.
      4. Standard install dirs (Program Files, LocalAppData) — last resort.
    """
    import shutil

    key = app_name.lower().strip()
    cached = _RESOLVED_CACHE.get(key)
    if cached and os.path.isfile(cached):
        return cached

    candidates = _APP_WHITELIST.get(key)
    if not candidates:
        return None

    for cand in candidates:
        # 1. PATH
        path = shutil.which(cand)
        if path:
            _RESOLVED_CACHE[key] = path
            return path
        # 2. App Paths registry
        path = _query_app_paths_registry(cand)
        if path:
            _RESOLVED_CACHE[key] = path
            return path
        # 3. Start Menu shortcut
        path = _scan_start_menu(cand)
        if path:
            _RESOLVED_CACHE[key] = path
            return path
        # 4. Standard dirs (slowest, only if everything else failed)
        path = _scan_standard_dirs(cand)
        if path:
            _RESOLVED_CACHE[key] = path
            return path
    return None


def _list_top_level_windows() -> list[Dict[str, Any]]:
    """Enumerate visible top-level windows on Windows (title, hwnd, pid)."""
    if sys.platform != "win32":
        return []
    try:
        import ctypes
        import ctypes.wintypes as wt

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        EnumWindows = user32.EnumWindows
        EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
        GetWindowTextW = user32.GetWindowTextW
        GetWindowTextLengthW = user32.GetWindowTextLengthW
        IsWindowVisible = user32.IsWindowVisible
        GetWindowThreadProcessId = user32.GetWindowThreadProcessId

        windows: list[Dict[str, Any]] = []

        def callback(hwnd, _lparam):
            if not IsWindowVisible(hwnd):
                return True
            length = GetWindowTextLengthW(hwnd)
            if length == 0:
                return True
            buf = ctypes.create_unicode_buffer(length + 1)
            GetWindowTextW(hwnd, buf, length + 1)
            pid = wt.DWORD()
            GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            windows.append({"hwnd": int(hwnd), "title": buf.value, "pid": int(pid.value)})
            return True

        EnumWindows(EnumWindowsProc(callback), 0)
        return windows
    except Exception as exc:
        logger.warning("EnumWindows failed: %s", exc)
        return []


def _focus_window(hwnd: int) -> bool:
    if sys.platform != "win32":
        return False
    try:
        import ctypes

        user32 = ctypes.windll.user32
        # SW_RESTORE = 9 → un-minimize if minimized
        user32.ShowWindow(hwnd, 9)
        # Windows blocks SetForegroundWindow unless our process is itself the
        # foreground process. The well-known workaround is to attach our input
        # queue to the queue of the current foreground window and bring our
        # target to top from inside that joint queue — then detach.
        kernel32 = ctypes.windll.kernel32
        cur_fg = user32.GetForegroundWindow()
        cur_thread = kernel32.GetCurrentThreadId()
        fg_thread = user32.GetWindowThreadProcessId(cur_fg, None) if cur_fg else 0
        attached = False
        if fg_thread and fg_thread != cur_thread:
            attached = bool(user32.AttachThreadInput(cur_thread, fg_thread, True))
        try:
            user32.BringWindowToTop(hwnd)
            ok = bool(user32.SetForegroundWindow(hwnd))
        finally:
            if attached:
                user32.AttachThreadInput(cur_thread, fg_thread, False)
        return ok
    except Exception as exc:
        logger.warning("SetForegroundWindow failed for hwnd=%s: %s", hwnd, exc)
        return False


def _app_launch(app_name: str, args: Optional[list[str]] = None) -> Dict[str, Any]:
    cmd = _resolve_app_command(app_name)
    if cmd is None:
        return {
            "success": False,
            "error": f"app '{app_name}' not whitelisted or executable not found on PATH",
            "whitelist": list(_APP_WHITELIST.keys()),
        }
    try:
        import subprocess
        full = [cmd, *(args or [])]
        # Use CREATE_NEW_CONSOLE on Windows so the child has its own window
        creationflags = 0x00000010 if sys.platform == "win32" else 0  # CREATE_NEW_CONSOLE
        proc = subprocess.Popen(full, creationflags=creationflags, close_fds=True)
        return {"success": True, "app": app_name, "pid": proc.pid, "command": cmd, "args": args or []}
    except Exception as exc:
        return {"success": False, "error": f"{type(exc).__name__}: {exc}"}


def _app_focus(title_substring: str) -> Dict[str, Any]:
    sub = title_substring.lower().strip()
    candidates = [w for w in _list_top_level_windows() if sub in w["title"].lower()]
    if not candidates:
        return {"success": False, "error": f"no visible window matched '{title_substring}'"}
    # Prefer the most recently used (last in EnumWindows order is roughly the front)
    target = candidates[0]
    ok = _focus_window(target["hwnd"])
    return {
        "success": ok,
        "matched": target,
        "candidates": candidates if len(candidates) > 1 else None,
    }


def _app_list_running() -> Dict[str, Any]:
    windows = _list_top_level_windows()
    return {"success": True, "count": len(windows), "windows": windows}


def _app_launch_or_focus(app_name: str, title_hint: Optional[str] = None,
                         args: Optional[list[str]] = None) -> Dict[str, Any]:
    """Convenience: focus the app if a window is already open, else launch it."""
    hint = (title_hint or app_name).lower().strip()
    candidates = [w for w in _list_top_level_windows() if hint in w["title"].lower()]
    if candidates:
        ok = _focus_window(candidates[0]["hwnd"])
        return {"success": ok, "action": "focused", "matched": candidates[0]}
    launched = _app_launch(app_name, args=args)
    if launched.get("success"):
        launched["action"] = "launched"
    return launched


def _window_maximize(title_substring: str) -> Dict[str, Any]:
    """Maximize a window matching the title substring (Win32 ShowWindow SW_MAXIMIZE)."""
    if sys.platform != "win32":
        return {"success": False, "error": "windows-only"}
    sub = title_substring.lower().strip()
    candidates = [w for w in _list_top_level_windows() if sub in w["title"].lower()]
    if not candidates:
        return {"success": False, "error": f"no window matches '{title_substring}'"}
    target = candidates[0]
    try:
        import ctypes
        user32 = ctypes.windll.user32
        # SW_MAXIMIZE = 3
        user32.ShowWindow(target["hwnd"], 3)
        # Plus bring to foreground
        _focus_window(target["hwnd"])
        return {"success": True, "matched": target, "action": "maximized"}
    except Exception as exc:
        return {"success": False, "error": f"{type(exc).__name__}: {exc}"}


def _populate_sheet(ws, rows: list[list[Any]], bold_rows: Optional[list[int]],
                    auto_width: bool, cell_styles: Optional[list[dict]] = None,
                    freeze_pane: Optional[str] = None) -> Optional[str]:
    """Populate one openpyxl worksheet from rows + styling. Returns error str on failure."""
    from openpyxl.styles import Font, PatternFill, Border, Side, Alignment
    from openpyxl.utils import get_column_letter

    bold_set = set(int(r) for r in (bold_rows or []))
    bold_font = Font(bold=True)

    for row_idx, row in enumerate(rows, start=1):
        if not isinstance(row, list):
            return f"row {row_idx} is not a list"
        for col_idx, cell in enumerate(row, start=1):
            value = "" if cell is None else cell
            # Formula detection: a string starting with "=" is written as a
            # formula by openpyxl (it does this automatically — we just don't
            # need to escape it). Numbers and bare strings stay as-is.
            target = ws.cell(row=row_idx, column=col_idx, value=value)
            if row_idx in bold_set:
                target.font = bold_font

    if cell_styles:
        thin = Side(border_style="thin", color="666666")
        full_border = Border(left=thin, right=thin, top=thin, bottom=thin)
        # Snapshot current sheet bounds — accessing ws[range] for a range
        # wider than max_column *materializes* empty cells and permanently
        # extends the sheet. We clamp the range to existing data instead.
        snap_max_col = ws.max_column or 0
        snap_max_row = ws.max_row or 0
        for style in cell_styles:
            range_str = style.get("range")
            if not range_str:
                continue
            # Clamp range like 'A1:Z1' to actual data: if user requested
            # cols past snap_max_col, truncate. Same for rows.
            try:
                from openpyxl.utils import range_boundaries, get_column_letter
                min_col, min_row, max_col, max_row = range_boundaries(range_str)
                if snap_max_col > 0:
                    max_col = min(max_col, snap_max_col)
                if snap_max_row > 0:
                    max_row = min(max_row, snap_max_row)
                if max_col < min_col or max_row < min_row:
                    # Range was entirely outside data — skip silently.
                    continue
                clamped = (
                    f"{get_column_letter(min_col)}{min_row}"
                    f":{get_column_letter(max_col)}{max_row}"
                )
                cell_block = ws[clamped]
            except Exception:
                # Fallback: try the original range. May still extend dims.
                try:
                    cell_block = ws[range_str]
                except Exception:
                    continue
            # ws[range] returns a tuple-of-tuples for ranges, single cell otherwise
            if not isinstance(cell_block, tuple):
                cell_block = ((cell_block,),)
            elif cell_block and not isinstance(cell_block[0], tuple):
                cell_block = (cell_block,)

            fill_color = style.get("fill_color")
            font_color = style.get("font_color")
            bold_flag = style.get("bold")
            italic_flag = style.get("italic")
            border_flag = style.get("border")
            align = style.get("align")  # left | center | right
            number_format = style.get("number_format")

            fill = PatternFill(start_color=fill_color, end_color=fill_color, fill_type="solid") if fill_color else None
            font_kwargs: dict[str, Any] = {}
            if font_color:
                font_kwargs["color"] = font_color
            if bold_flag is not None:
                font_kwargs["bold"] = bool(bold_flag)
            if italic_flag is not None:
                font_kwargs["italic"] = bool(italic_flag)
            font = Font(**font_kwargs) if font_kwargs else None
            alignment = Alignment(horizontal=align) if align in ("left", "center", "right") else None

            for cell_row in cell_block:
                for c in cell_row:
                    if fill is not None:
                        c.fill = fill
                    if font is not None:
                        # Merge with existing font traits we already applied (bold)
                        prev = c.font
                        merged = Font(
                            name=prev.name,
                            size=prev.size,
                            bold=font.bold if font.bold is not None else prev.bold,
                            italic=font.italic if font.italic is not None else prev.italic,
                            color=font.color if font.color else prev.color,
                        )
                        c.font = merged
                    if border_flag:
                        c.border = full_border
                    if alignment is not None:
                        c.alignment = alignment
                    if number_format:
                        c.number_format = number_format

    if freeze_pane:
        try:
            ws.freeze_panes = freeze_pane
        except Exception:
            pass  # invalid coord, silently ignore

    if auto_width:
        for col_idx in range(1, (ws.max_column or 0) + 1):
            max_len = 0
            for row_idx in range(1, (ws.max_row or 0) + 1):
                v = ws.cell(row=row_idx, column=col_idx).value
                if v is None:
                    continue
                ln = len(str(v))
                if ln > max_len:
                    max_len = ln
            if max_len > 0:
                ws.column_dimensions[get_column_letter(col_idx)].width = min(max_len + 4, 60)

    return None


def _xlsx_create_from_data(file_path: str, rows: Optional[list[list[Any]]] = None,
                           sheet_name: Optional[str] = None,
                           bold_rows: Optional[list[int]] = None,
                           auto_width: bool = True,
                           overwrite: bool = True,
                           sheets: Optional[list[dict]] = None,
                           cell_styles: Optional[list[dict]] = None,
                           freeze_pane: Optional[str] = None) -> Dict[str, Any]:
    """Create an .xlsx file directly via openpyxl — no Excel UI needed.

    Args (all optional except file_path):
      - ``rows``: 2-D list for a single-sheet workbook
      - ``sheet_name``: name for that sheet
      - ``bold_rows``: 1-based row numbers to bold
      - ``cell_styles``: list of {range, fill_color (hex without #), font_color,
        bold, italic, border, align, number_format}
      - ``freeze_pane``: cell coord (e.g. ``"A2"``) — rows above + cols left frozen
      - ``sheets``: alternative to ``rows`` — list of {name, rows, bold_rows,
        cell_styles, freeze_pane} for multi-sheet workbooks. First sheet
        becomes the active one.

    Strings starting with ``=`` are written as Excel formulas automatically.
    """
    if not file_path:
        return {"success": False, "error": "file_path required"}
    if not rows and not sheets:
        return {"success": False, "error": "either rows or sheets must be provided"}
    if rows and sheets:
        return {"success": False, "error": "rows and sheets are mutually exclusive — pick one"}

    file_path = os.path.abspath(os.path.expandvars(os.path.expanduser(file_path)))
    if os.path.isfile(file_path) and not overwrite:
        return {"success": False, "error": f"file exists and overwrite=False: {file_path}"}
    parent = os.path.dirname(file_path)
    if parent and not os.path.isdir(parent):
        try:
            os.makedirs(parent, exist_ok=True)
        except Exception as exc:
            return {"success": False, "error": f"cannot create parent dir: {exc}"}

    try:
        from openpyxl import Workbook
    except ImportError:
        return {"success": False, "error": "openpyxl not installed"}

    wb = Workbook()
    sheets_written: list[str] = []

    if sheets:
        # Remove the default sheet, replace with requested ones
        default = wb.active
        wb.remove(default)
        for i, sh in enumerate(sheets):
            name = (sh.get("name") or f"Sheet{i+1}")[:31]
            ws = wb.create_sheet(title=name, index=i)
            err = _populate_sheet(
                ws,
                sh.get("rows") or [],
                sh.get("bold_rows"),
                auto_width,
                sh.get("cell_styles"),
                sh.get("freeze_pane"),
            )
            if err:
                wb.close()
                return {"success": False, "error": f"sheet {name!r}: {err}"}
            sheets_written.append(name)
        # Ensure first sheet is active
        wb.active = 0
    else:
        ws = wb.active
        if sheet_name:
            ws.title = sheet_name[:31]
        err = _populate_sheet(ws, rows or [], bold_rows, auto_width, cell_styles, freeze_pane)
        if err:
            wb.close()
            return {"success": False, "error": err}
        sheets_written.append(ws.title)

    try:
        wb.save(file_path)
    except Exception as exc:
        wb.close()
        return {"success": False, "error": f"save failed: {type(exc).__name__}: {exc}"}
    wb.close()

    return {
        "success": True,
        "file_path": file_path,
        "sheets": sheets_written,
        "size_bytes": os.path.getsize(file_path),
    }


def _docx_create_from_data(file_path: str, blocks: list[dict],
                           overwrite: bool = True) -> Dict[str, Any]:
    """Create a .docx file via python-docx from a list of structured blocks.

    Block types:
      - {type: "heading", level: 1|2|3, text: "..."}
      - {type: "paragraph", text: "...", bold: false, italic: false}
      - {type: "table", rows: [[...]], header_row: true}
      - {type: "page_break"}
      - {type: "list", items: ["..."], style: "bullet"|"number"}
    """
    if not file_path:
        return {"success": False, "error": "file_path required"}
    file_path = os.path.abspath(os.path.expandvars(os.path.expanduser(file_path)))
    if os.path.isfile(file_path) and not overwrite:
        return {"success": False, "error": f"file exists and overwrite=False: {file_path}"}
    parent = os.path.dirname(file_path)
    if parent and not os.path.isdir(parent):
        try:
            os.makedirs(parent, exist_ok=True)
        except Exception as exc:
            return {"success": False, "error": f"cannot create parent dir: {exc}"}

    try:
        from docx import Document
        from docx.shared import Pt
    except ImportError:
        return {"success": False, "error": "python-docx not installed"}

    doc = Document()
    counts = {"headings": 0, "paragraphs": 0, "tables": 0, "lists": 0, "page_breaks": 0}

    for i, block in enumerate(blocks):
        if not isinstance(block, dict):
            return {"success": False, "error": f"block {i} is not a dict"}
        btype = block.get("type")
        try:
            if btype == "heading":
                doc.add_heading(str(block.get("text", "")), level=int(block.get("level", 1)))
                counts["headings"] += 1
            elif btype == "paragraph":
                p = doc.add_paragraph()
                run = p.add_run(str(block.get("text", "")))
                if block.get("bold"):
                    run.bold = True
                if block.get("italic"):
                    run.italic = True
                counts["paragraphs"] += 1
            elif btype == "table":
                rows = block.get("rows") or []
                if not rows:
                    continue
                tbl = doc.add_table(rows=len(rows), cols=max((len(r) for r in rows), default=1))
                tbl.style = "Light Grid Accent 1"
                header_row = block.get("header_row", False)
                for r_idx, row in enumerate(rows):
                    for c_idx, cell in enumerate(row):
                        c = tbl.rows[r_idx].cells[c_idx]
                        c.text = "" if cell is None else str(cell)
                        if header_row and r_idx == 0:
                            for run in c.paragraphs[0].runs:
                                run.bold = True
                counts["tables"] += 1
            elif btype == "page_break":
                doc.add_page_break()
                counts["page_breaks"] += 1
            elif btype == "list":
                style = "List Bullet" if block.get("style", "bullet") == "bullet" else "List Number"
                for item in block.get("items") or []:
                    doc.add_paragraph(str(item), style=style)
                counts["lists"] += 1
            else:
                return {"success": False, "error": f"block {i}: unknown type {btype!r}"}
        except Exception as exc:
            return {"success": False, "error": f"block {i} ({btype}): {type(exc).__name__}: {exc}"}

    try:
        doc.save(file_path)
    except Exception as exc:
        return {"success": False, "error": f"save failed: {type(exc).__name__}: {exc}"}

    return {
        "success": True,
        "file_path": file_path,
        "blocks_written": len(blocks),
        "counts": counts,
        "size_bytes": os.path.getsize(file_path),
    }


def _csv_create_from_data(file_path: str, rows: list[list[Any]],
                          delimiter: str = ",", encoding: str = "utf-8-sig",
                          overwrite: bool = True) -> Dict[str, Any]:
    """Write rows as CSV. utf-8-sig is Excel-friendly (preserves BOM for German chars)."""
    if not file_path:
        return {"success": False, "error": "file_path required"}
    file_path = os.path.abspath(os.path.expandvars(os.path.expanduser(file_path)))
    if os.path.isfile(file_path) and not overwrite:
        return {"success": False, "error": f"file exists and overwrite=False: {file_path}"}
    parent = os.path.dirname(file_path)
    if parent and not os.path.isdir(parent):
        try:
            os.makedirs(parent, exist_ok=True)
        except Exception as exc:
            return {"success": False, "error": f"cannot create parent dir: {exc}"}

    import csv

    try:
        with open(file_path, "w", encoding=encoding, newline="") as f:
            writer = csv.writer(f, delimiter=delimiter)
            for row in rows:
                writer.writerow(["" if c is None else c for c in row])
    except Exception as exc:
        return {"success": False, "error": f"write failed: {type(exc).__name__}: {exc}"}

    return {
        "success": True,
        "file_path": file_path,
        "rows_written": len(rows),
        "size_bytes": os.path.getsize(file_path),
        "encoding": encoding,
        "delimiter": delimiter,
    }


def _docx_verify_file(file_path: str,
                      expected_substrings: Optional[list[str]] = None,
                      min_paragraphs: Optional[int] = None,
                      min_tables: Optional[int] = None,
                      expected_table_cells: Optional[list[dict]] = None) -> Dict[str, Any]:
    """Read-only verification of a .docx file.

    expected_table_cells: list of {table_index, row, col, expected_substring}
    """
    if not file_path or not os.path.isfile(file_path):
        return {"success": False, "exists": False, "file_path": file_path,
                "error": f"file does not exist: {file_path}"}
    try:
        from docx import Document
    except ImportError:
        return {"success": False, "error": "python-docx not installed"}
    try:
        doc = Document(file_path)
    except Exception as exc:
        return {"success": False, "exists": True, "valid": False,
                "error": f"docx load failed: {type(exc).__name__}: {exc}"}

    paragraphs = [p.text for p in doc.paragraphs]
    tables = doc.tables
    full_text = "\n".join(paragraphs + [c.text for t in tables for r in t.rows for c in r.cells]).lower()

    failures: list[str] = []
    checks: Dict[str, Any] = {
        "paragraph_count": len(paragraphs),
        "table_count": len(tables),
    }

    if expected_substrings:
        sub_results: dict[str, bool] = {}
        for needle in expected_substrings:
            ok = needle.lower() in full_text
            sub_results[needle] = ok
            if not ok:
                failures.append(f"substring not found: {needle!r}")
        checks["expected_substrings"] = sub_results

    if min_paragraphs is not None:
        ok = len(paragraphs) >= int(min_paragraphs)
        checks["min_paragraphs"] = {"required": min_paragraphs, "actual": len(paragraphs), "ok": ok}
        if not ok:
            failures.append(f"only {len(paragraphs)} paragraphs, need {min_paragraphs}")

    if min_tables is not None:
        ok = len(tables) >= int(min_tables)
        checks["min_tables"] = {"required": min_tables, "actual": len(tables), "ok": ok}
        if not ok:
            failures.append(f"only {len(tables)} tables, need {min_tables}")

    if expected_table_cells:
        cell_results = []
        for spec in expected_table_cells:
            t_idx = int(spec.get("table_index", 0))
            r = int(spec.get("row", 0))
            c = int(spec.get("col", 0))
            expected = str(spec.get("expected_substring", ""))
            actual = None
            ok = False
            try:
                actual = tables[t_idx].rows[r].cells[c].text
                ok = expected.lower() in actual.lower()
            except Exception as exc:
                actual = f"<error: {exc}>"
            cell_results.append({
                "table_index": t_idx, "row": r, "col": c,
                "expected_substring": expected, "actual": actual, "ok": ok,
            })
            if not ok:
                failures.append(f"table[{t_idx}][{r}][{c}]: expected {expected!r}, got {actual!r}")
        checks["expected_table_cells"] = cell_results

    success = len(failures) == 0
    return {
        "success": success,
        "exists": True,
        "valid": True,
        "file_path": file_path,
        "checks": checks,
        "failures": failures,
        "summary": (
            f"OK ({len(paragraphs)} paragraphs, {len(tables)} tables)"
            if success else f"FAIL ({len(failures)} issues): {'; '.join(failures[:3])}"
        ),
    }


def _rowboat_upload(file_path: str, title: Optional[str] = None,
                    tags: Optional[list[str]] = None) -> Dict[str, Any]:
    """Upload a file to the Rowboat knowledge backend at :3000.

    Reads ROWBOAT_URL (default http://localhost:3000), ROWBOAT_API_KEY,
    ROWBOAT_PROJECT_ID from environment. If any of those are missing, returns
    a graceful failure (not an exception).
    """
    if not file_path or not os.path.isfile(file_path):
        return {"success": False, "error": f"file not found: {file_path}"}

    rowboat_url = os.environ.get("ROWBOAT_URL", "http://localhost:3000")
    api_key = os.environ.get("ROWBOAT_API_KEY", "")
    project_id = os.environ.get("ROWBOAT_PROJECT_ID", "")
    if not api_key or not project_id:
        return {
            "success": False,
            "skipped": True,
            "reason": "rowboat not configured",
            "missing": [k for k in ("ROWBOAT_API_KEY", "ROWBOAT_PROJECT_ID")
                        if not os.environ.get(k)],
            "hint": "Set ROWBOAT_API_KEY and ROWBOAT_PROJECT_ID in env to enable.",
        }

    try:
        import requests
    except ImportError:
        return {"success": False, "error": "requests not installed"}

    upload_url = f"{rowboat_url.rstrip('/')}/api/v1/{project_id}/upload"
    headers = {"Authorization": f"Bearer {api_key}"}
    filename = os.path.basename(file_path)
    data: dict[str, str] = {}
    if title:
        data["title"] = str(title)
    if tags:
        data["tags"] = ",".join(str(t) for t in tags)
    try:
        with open(file_path, "rb") as fh:
            files = {"file": (filename, fh)}
            resp = requests.post(upload_url, headers=headers, data=data, files=files, timeout=30)
        return {
            "success": 200 <= resp.status_code < 300,
            "status_code": resp.status_code,
            "url": upload_url,
            "filename": filename,
            "response_text": resp.text[:500] if resp.text else "",
        }
    except Exception as exc:
        return {"success": False, "error": f"{type(exc).__name__}: {exc}", "url": upload_url}


def _rowboat_search(query: str, folder: Optional[str] = None,
                    limit: int = 20) -> Dict[str, Any]:
    """Search the local Rowboat MongoDB for documents matching ``query``.

    Goes directly to the MongoDB collection ``rowboat.source_docs`` rather
    than via Rowboat's HTTP API (the HTTP /api/v1/<project>/chat path is
    auth-protected and wasn't usable in our local docker setup as of
    2026-05-07: returns "Invalid API key").

    The MongoDB schema (verified from a live local instance) is:
      sources(_id, projectId, name, description, data{type}, status, ...)
      source_docs(_id, sourceId, projectId, name, version, status, content,
                  data{type, content, ...}, createdAt, lastUpdatedAt)

    We do a case-insensitive substring search on the document content + name,
    skipping deleted docs. ``folder`` filters by source name (e.g. "Bewerbung"
    only returns docs whose source.name contains that substring).

    Returns ``{success, count, results: [{name, source_name, content_excerpt,
    created_at, _id}]}``.
    """
    if not query or not isinstance(query, str):
        return {"success": False, "error": "query must be a non-empty string"}
    try:
        from pymongo import MongoClient
    except ImportError:
        return {"success": False, "error": "pymongo not installed"}

    uri = os.environ.get("ROWBOAT_MONGODB_URI", "mongodb://localhost:27017")
    db_name = os.environ.get("ROWBOAT_MONGODB_DB", "rowboat")
    project_id = os.environ.get("ROWBOAT_PROJECT_ID", "")

    try:
        client = MongoClient(uri, serverSelectionTimeoutMS=5000)
        db = client[db_name]

        # Resolve folder filter to source ids (as STRINGS — source_docs.sourceId
        # is stored as a string while sources._id is an ObjectId).
        source_ids: Optional[list] = None
        if folder:
            cursor = db["sources"].find(
                {"name": {"$regex": folder, "$options": "i"}},
                {"_id": 1, "name": 1},
            )
            source_ids = [str(s["_id"]) for s in cursor]
            if not source_ids:
                return {
                    "success": True,
                    "count": 0,
                    "results": [],
                    "note": f"no sources matched folder filter {folder!r}",
                }

        # Note (2026-05-07): source_docs has 1673 entries with status='deleted'
        # that the UI still surfaces — so we don't filter by status. If you
        # need only live docs, post-filter the results.
        mongo_query: Dict[str, Any] = {
            "$or": [
                {"data.content": {"$regex": query, "$options": "i"}},
                {"name": {"$regex": query, "$options": "i"}},
            ],
        }
        if project_id:
            mongo_query["projectId"] = project_id
        if source_ids is not None:
            mongo_query["sourceId"] = {"$in": source_ids}

        # Build a sourceId(string) -> source.name map so the result has
        # human-readable bubble names.
        src_filter: Dict[str, Any] = {}
        if project_id:
            src_filter["projectId"] = project_id
        src_map = {str(s["_id"]): s.get("name", "") for s in
                   db["sources"].find(src_filter, {"_id": 1, "name": 1})}

        # Fetch more than `limit` so we have enough candidates AFTER dedup.
        # source_docs in this MongoDB store every version of a doc — same
        # `name` appears N times across edits. Without dedup, a search for
        # 'Brain' returns 9× the same overview-snapshot.
        # Dedup strategy: group by (sourceId, name), keep the entry with
        # the highest lastUpdatedAt (or createdAt fallback). Cap at `limit`
        # AFTER dedup.
        fetch_n = max(int(limit) * 5, 50)
        cursor = db["source_docs"].find(mongo_query).limit(fetch_n)
        bucket: Dict[tuple, dict] = {}
        for d in cursor:
            key = (d.get("sourceId", ""), d.get("name", ""))
            ts = str(d.get("lastUpdatedAt") or d.get("createdAt") or "")
            existing = bucket.get(key)
            if existing is None or ts > existing.get("_ts", ""):
                content = ""
                data = d.get("data") or {}
                if isinstance(data, dict):
                    content = data.get("content") or ""
                bucket[key] = {
                    "_id": str(d.get("_id", "")),
                    "name": d.get("name", ""),
                    "source_name": src_map.get(d.get("sourceId"), ""),
                    "content_excerpt": content[:500],
                    "content_full_length": len(content),
                    "created_at": str(d.get("createdAt", "")),
                    "last_updated_at": str(d.get("lastUpdatedAt", "")),
                    "_ts": ts,
                }
        # Sort by recency (newest first) and cap at limit.
        results = sorted(bucket.values(), key=lambda r: r.get("_ts", ""), reverse=True)[:int(limit)]
        for r in results:
            r.pop("_ts", None)
        return {"success": True, "count": len(results), "query": query,
                "folder": folder, "results": results,
                "dedup_note": f"deduped from {fetch_n} raw matches by (sourceId,name), kept newest"}
    except Exception as exc:
        return {"success": False, "error": f"{type(exc).__name__}: {exc}"}
    finally:
        try:
            client.close()
        except Exception:
            pass


_ROARBOOT_KNOWLEDGE_ROOT = os.path.expanduser("~/.rowboat/knowledge")


def _roarboot_list_folders(root: Optional[str] = None) -> Dict[str, Any]:
    """List the top-level folders in the Roarboot knowledge tree.

    These are the folders shown in the Roarboot Electron UI sidebar
    (Bewerbung, Investor Programs, Notes, Organizations, People, Projects,
    Topics, Videos, Voice Memos, vibemind-discourse). They live as actual
    directories under ``~/.rowboat/knowledge/`` (.md files in subfolders)
    and are git-versioned. Returns ``{success, root, folders: [{name,
    file_count, subdir_count}]}``.
    """
    base = os.path.abspath(root or _ROARBOOT_KNOWLEDGE_ROOT)
    if not os.path.isdir(base):
        return {"success": False, "error": f"knowledge root not found: {base}"}

    folders = []
    try:
        for entry in sorted(os.listdir(base)):
            if entry.startswith(".") or entry == "Welcome.md":
                continue
            full = os.path.join(base, entry)
            if not os.path.isdir(full):
                continue
            file_count = 0
            subdir_count = 0
            for dirpath, dirs, files in os.walk(full):
                if "/.git" in dirpath.replace("\\", "/") or os.path.basename(dirpath).startswith("."):
                    continue
                file_count += sum(1 for f in files if f.endswith(".md"))
                subdir_count += sum(1 for d in dirs if not d.startswith("."))
            folders.append({
                "name": entry,
                "file_count": file_count,
                "subdir_count": subdir_count,
            })
        return {"success": True, "root": base, "folders": folders}
    except Exception as exc:
        return {"success": False, "error": f"{type(exc).__name__}: {exc}"}


def _roarboot_read_knowledge(folder: str, query: Optional[str] = None,
                             limit: int = 20,
                             name_pattern: Optional[str] = None,
                             root: Optional[str] = None) -> Dict[str, Any]:
    """Read .md files from a Roarboot knowledge folder.

    The ``folder`` argument is a top-level folder name (e.g. 'Bewerbung',
    'People', 'Projects') OR a subpath ('Projects/VibeMind - Brain Capability
    Router'). When ``query`` is given, only files whose path or content
    matches the case-insensitive substring are returned. When ``name_pattern``
    is given (e.g. '_overview.md'), only files whose **filename** matches the
    case-insensitive substring are returned — useful to grab *only* the
    overview files of every subfolder without diving into sub-pages.

    Returns ``{success, folder, root, count, files: [{path, name,
    content_excerpt, content_full_length, modified_at}]}``.
    """
    if not folder:
        return {"success": False, "error": "folder required"}

    base = os.path.abspath(root or _ROARBOOT_KNOWLEDGE_ROOT)
    if not os.path.isdir(base):
        return {"success": False, "error": f"knowledge root not found: {base}"}

    target = os.path.normpath(os.path.join(base, folder))
    # Path-traversal guard: target must stay inside base.
    if not target.startswith(base):
        return {"success": False, "error": "folder must be inside knowledge root"}
    if not os.path.isdir(target):
        return {"success": False, "error": f"folder not found: {folder} (resolved: {target})"}

    q_lower = (query or "").lower().strip()
    np_lower = (name_pattern or "").lower().strip()
    try:
        results = []
        for dirpath, dirs, files in os.walk(target):
            # Skip hidden dirs (.git etc).
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for f in sorted(files):
                if not f.endswith(".md"):
                    continue
                # name_pattern filters by FILENAME only (case-insensitive
                # substring). Used to grab only e.g. '_overview.md' files.
                if np_lower and np_lower not in f.lower():
                    continue
                full = os.path.join(dirpath, f)
                rel = os.path.relpath(full, base).replace("\\", "/")
                # Read content (small md files, 100KB cap is plenty).
                try:
                    with open(full, "r", encoding="utf-8", errors="replace") as fh:
                        content = fh.read(100_000)
                except Exception as exc:
                    content = f"<read failed: {exc}>"

                if q_lower:
                    haystack = (rel + "\n" + content).lower()
                    if q_lower not in haystack:
                        continue

                stat = os.stat(full)
                results.append({
                    "path": rel,
                    "name": os.path.splitext(f)[0],
                    "content_excerpt": content[:600],
                    "content_full_length": len(content),
                    "modified_at": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
                })
                if len(results) >= int(limit):
                    return {"success": True, "folder": folder, "root": base,
                            "count": len(results), "results": results,
                            "truncated": True}
        return {"success": True, "folder": folder, "root": base,
                "count": len(results), "results": results, "truncated": False}
    except Exception as exc:
        return {"success": False, "error": f"{type(exc).__name__}: {exc}"}


def _xlsx_to_markdown(file_path: str, max_rows_per_sheet: int = 50,
                      max_chars_per_cell: int = 200) -> str:
    """Render an xlsx as a markdown report (one table per sheet) for LLM input.

    Uses ``data_only=False`` so that formulas are visible (e.g.
    ``=SUMME(F2:F6)`` instead of ``None``). When openpyxl-generated files
    have not been opened in Excel yet, the cached formula values are
    missing, and ``data_only=True`` would render those cells as empty —
    which would mislead a critic LLM into saying "values are missing".
    """
    try:
        from openpyxl import load_workbook
    except ImportError:
        return f"<openpyxl not installed>"
    if not os.path.isfile(file_path):
        return f"<file not found: {file_path}>"
    try:
        wb = load_workbook(file_path, read_only=True, data_only=False)
    except Exception as exc:
        return f"<load failed: {exc}>"

    parts: list[str] = []
    parts.append(f"## File: {os.path.basename(file_path)}\n")
    parts.append(
        "_Note: cells starting with `=` are formulas (will be evaluated by "
        "Excel on open). Don't flag them as 'empty' or 'missing values'._\n"
    )
    for sn in wb.sheetnames:
        ws = wb[sn]
        parts.append(f"\n### Sheet: {sn}  ({ws.max_row}x{ws.max_column})\n")
        rows = []
        for r_idx, row in enumerate(ws.iter_rows(values_only=True), 1):
            if r_idx > int(max_rows_per_sheet):
                rows.append(f"_(... {ws.max_row - max_rows_per_sheet} more rows omitted)_")
                break
            cells = []
            for v in row:
                if v is None:
                    s = ""
                else:
                    s = str(v)
                    if len(s) > max_chars_per_cell:
                        s = s[: max_chars_per_cell - 3] + "..."
                    s = s.replace("|", "\\|").replace("\n", " ")
                cells.append(s)
            rows.append("| " + " | ".join(cells) + " |")
        if rows:
            # Insert markdown header separator after first row
            sep = "| " + " | ".join(["---"] * (ws.max_column or 1)) + " |"
            parts.append(rows[0])
            parts.append(sep)
            parts.extend(rows[1:])
    wb.close()
    return "\n".join(parts)


def _docx_to_markdown(file_path: str, max_chars: int = 8000) -> str:
    """Render a .docx as markdown for LLM input."""
    try:
        from docx import Document
    except ImportError:
        return f"<python-docx not installed>"
    if not os.path.isfile(file_path):
        return f"<file not found: {file_path}>"
    try:
        d = Document(file_path)
    except Exception as exc:
        return f"<load failed: {exc}>"

    parts = [f"## File: {os.path.basename(file_path)}\n"]
    parts.append(f"_paragraphs: {len(d.paragraphs)}, tables: {len(d.tables)}_\n")
    for p in d.paragraphs:
        if not p.text.strip():
            continue
        # Style hints
        style = (p.style.name if p.style else "") or ""
        if style.startswith("Heading"):
            level = "".join(c for c in style if c.isdigit()) or "1"
            parts.append(f"{'#' * (int(level) + 2)} {p.text}")
        else:
            parts.append(p.text)
    for i, tbl in enumerate(d.tables):
        parts.append(f"\n### Table {i+1} ({len(tbl.rows)}x{len(tbl.columns)})\n")
        for r_idx, row in enumerate(tbl.rows):
            cells = [c.text.replace("|", "\\|").replace("\n", " ") for c in row.cells]
            parts.append("| " + " | ".join(cells) + " |")
            if r_idx == 0:
                parts.append("| " + " | ".join(["---"] * len(row.cells)) + " |")
    out = "\n".join(parts)
    if len(out) > int(max_chars):
        out = out[: int(max_chars) - 50] + "\n\n_(truncated)_"
    return out


def _file_evaluate(file_path: str, expected_intent: str,
                   source_data_description: Optional[str] = None,
                   criteria: Optional[list[str]] = None,
                   model: str = "gpt-4o-mini") -> Dict[str, Any]:
    """Critically evaluate an .xlsx or .docx file against an intent.

    Reads the file, renders it as markdown, sends to a critic LLM with
    instructions to find issues. Returns a structured report:
      {success, score (0-100), issues: [{severity, issue, fix}],
       completeness, recommendations, model, tokens}.

    The critic is told to be skeptical: prefer false-positives in issues
    over silent passes. Best paired with a clear `expected_intent` and
    `source_data_description` so the critic knows what to expect.
    """
    if not os.path.isfile(file_path):
        return {"success": False, "error": f"file not found: {file_path}"}
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        return {"success": False, "skipped": True,
                "reason": "OPENAI_API_KEY not set"}

    ext = os.path.splitext(file_path)[1].lower()
    if ext == ".xlsx":
        rendered = _xlsx_to_markdown(file_path)
    elif ext == ".docx":
        rendered = _docx_to_markdown(file_path)
    else:
        return {"success": False, "error": f"unsupported file type: {ext}"}

    default_criteria = [
        "Vollstaendigkeit: sind alle erwarteten Eintraege drin?",
        "Korrektheit: keine erfundenen oder dupliziert-fehl-extrahierten Felder?",
        "Datenqualitaet: konsistente Datentypen, sinnvolle Werte?",
        "Spalten-/Sektions-Wahl passt zum Intent?",
        "Praxistauglichkeit: koennte ein Fachanwender das so nutzen?",
    ]
    crit_list = criteria or default_criteria

    system_prompt = (
        "Du bist ein kritischer Reviewer fuer Office-Dateien (Excel/Word) die "
        "automatisiert generiert wurden. Deine Aufgabe ist Probleme zu finden, "
        "nicht zu loben. Sei skeptisch. Wenn etwas nicht passt — sag es klar. "
        "Antworte AUSSCHLIESSLICH als gueltiges JSON mit dem Schema:\n"
        '{"score": <0-100>, '
        '"completeness": "<kurze Bewertung>", '
        '"issues": [{"severity": "high|medium|low", "issue": "<beschreibung>", '
        '"fix_suggestion": "<optional konkreter fix>"}], '
        '"recommendations": ["<optional>"], '
        '"summary": "<1-2 saetze>"}\n'
        "Score-Heuristik: 90-100 = produktionsreif, 70-89 = brauchbar mit "
        "kleinen Maengeln, 50-69 = nur Konzept-Demo, <50 = nicht akzeptabel. "
        "KEIN Markdown, KEINE Erklaerungen ausserhalb des JSON."
    )

    user_msg_parts = [
        f"INTENT: {expected_intent}",
    ]
    if source_data_description:
        user_msg_parts.append(f"\nSOURCE-DATEN: {source_data_description}")
    user_msg_parts.append("\nKRITERIEN:")
    for c in crit_list:
        user_msg_parts.append(f"- {c}")
    user_msg_parts.append("\nDATEI-INHALT:\n")
    user_msg_parts.append(rendered)

    try:
        import requests
    except ImportError:
        return {"success": False, "error": "requests not installed"}

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": "\n".join(user_msg_parts)},
        ],
        "response_format": {"type": "json_object"},
    }
    if not model.startswith(("gpt-5", "o1", "o3", "o4")):
        payload["temperature"] = 0.2

    try:
        resp = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}",
                     "Content-Type": "application/json"},
            json=payload, timeout=120,
        )
    except Exception as exc:
        return {"success": False, "error": f"openai call: {type(exc).__name__}: {exc}"}

    if resp.status_code != 200:
        return {"success": False, "error": f"openai HTTP {resp.status_code}",
                "response_text": resp.text[:500]}
    data = resp.json()
    try:
        raw_answer = data["choices"][0]["message"]["content"]
        report = json.loads(raw_answer)
    except Exception as exc:
        return {"success": False, "error": f"could not parse LLM JSON: {exc}",
                "raw": data.get("choices", [{}])[0].get("message", {}).get("content", "")[:500]}

    usage = data.get("usage", {})
    return {
        "success": True,
        "file_path": file_path,
        "score": report.get("score"),
        "completeness": report.get("completeness"),
        "issues": report.get("issues", []),
        "recommendations": report.get("recommendations", []),
        "summary": report.get("summary"),
        "model": model,
        "tokens": {
            "input": usage.get("prompt_tokens"),
            "output": usage.get("completion_tokens"),
        },
    }


def _roarboot_ask(question: str, folder: Optional[str] = None,
                  max_files: int = 10, max_chars_per_file: int = 4000,
                  model: str = "gpt-4o-mini",
                  root: Optional[str] = None) -> Dict[str, Any]:
    """Ask a natural-language question over Roarboot knowledge.

    Reads ``.md`` files from the Roarboot knowledge tree (optionally filtered
    by ``folder``), concatenates them into a context, then calls an LLM with
    the user's question. Returns ``{success, answer, files_used: [...],
    model, total_input_chars}``.

    This is the alternative to ``rowboat_chat`` (which would need the
    ``rowboat_agents`` Python service that isn't in the repo). Here we read
    the same .md files the Roarboot Electron app uses and ask OpenAI directly
    — no container chat-service needed.

    Reads ``OPENAI_API_KEY`` from env. Graceful fail when missing.
    """
    if not question:
        return {"success": False, "error": "question required"}

    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        return {"success": False, "skipped": True,
                "reason": "OPENAI_API_KEY not set"}

    # Step 1: collect .md files. If folder is given, scope to that folder;
    # otherwise scan all top-level folders.
    base = os.path.abspath(root or _ROARBOOT_KNOWLEDGE_ROOT)
    if not os.path.isdir(base):
        return {"success": False, "error": f"knowledge root not found: {base}"}

    target = os.path.normpath(os.path.join(base, folder)) if folder else base
    if not target.startswith(base):
        return {"success": False, "error": "folder must be inside knowledge root"}
    if not os.path.isdir(target):
        return {"success": False, "error": f"folder not found: {folder}"}

    # Walk and pick most-recently-modified .md files first.
    candidates = []
    for dirpath, dirs, files in os.walk(target):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for f in files:
            if f.endswith(".md"):
                full = os.path.join(dirpath, f)
                try:
                    mtime = os.path.getmtime(full)
                except OSError:
                    continue
                candidates.append((mtime, full))
    candidates.sort(reverse=True)  # newest first
    chosen = candidates[: int(max_files)]

    if not chosen:
        return {"success": False, "error": f"no .md files in {folder or base}"}

    # Step 2: build context.
    parts: list[str] = []
    files_used: list[str] = []
    for _mtime, full in chosen:
        rel = os.path.relpath(full, base).replace("\\", "/")
        try:
            with open(full, "r", encoding="utf-8", errors="replace") as fh:
                content = fh.read(int(max_chars_per_file))
        except Exception:
            continue
        parts.append(f"### File: {rel}\n\n{content}\n")
        files_used.append(rel)

    context = "\n---\n\n".join(parts)

    # Step 3: call OpenAI Chat Completions.
    try:
        import requests
    except ImportError:
        return {"success": False, "error": "requests not installed"}

    system_prompt = (
        "Du bist ein Assistent der Fragen ueber die persoenliche Knowledge-Base "
        "des Users beantwortet. Antworte praezise auf Deutsch und beziehe dich "
        "konkret auf den Inhalt der Dateien. Wenn die Antwort nicht im Kontext "
        "steht, sag das ehrlich. Nenne die Datei-Pfade die du als Quelle nutzt."
    )
    user_message = (
        f"Frage: {question}\n\n"
        f"--- KNOWLEDGE-BASE ({len(files_used)} Dateien) ---\n\n{context}"
    )

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
    }
    # gpt-5.x doesn't support custom temperature, gpt-4o does.
    if not model.startswith(("gpt-5", "o1", "o3", "o4")):
        payload["temperature"] = 0.2

    try:
        resp = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}",
                     "Content-Type": "application/json"},
            json=payload,
            timeout=120,
        )
    except Exception as exc:
        return {"success": False, "error": f"openai call failed: {type(exc).__name__}: {exc}"}

    if resp.status_code != 200:
        return {"success": False, "error": f"openai HTTP {resp.status_code}",
                "response_text": resp.text[:500]}
    data = resp.json()
    answer = ""
    try:
        answer = data["choices"][0]["message"]["content"]
    except Exception:
        return {"success": False, "error": "could not extract answer", "raw": data}

    usage = data.get("usage", {})
    return {
        "success": True,
        "answer": answer,
        "files_used": files_used,
        "model": model,
        "total_input_chars": len(user_message),
        "tokens": {
            "input": usage.get("prompt_tokens"),
            "output": usage.get("completion_tokens"),
        },
    }


def _rowboat_list_folders(limit: int = 50) -> Dict[str, Any]:
    """List bubble/source names from Rowboat MongoDB.

    Each entry is one bubble (=Rowboat source). Returns ``{success, count,
    folders: [{name, doc_count, _id}]}``. Filtered by ``ROWBOAT_PROJECT_ID``
    when set.

    Note (2026-05-07): the source_docs.sourceId field is stored as a *string*
    while sources._id is an ObjectId — we match by string.
    Note: source_docs has 1673 'deleted' status entries that the UI still
    shows, so we count *all* statuses, not only non-deleted.
    """
    try:
        from pymongo import MongoClient
    except ImportError:
        return {"success": False, "error": "pymongo not installed"}

    uri = os.environ.get("ROWBOAT_MONGODB_URI", "mongodb://localhost:27017")
    db_name = os.environ.get("ROWBOAT_MONGODB_DB", "rowboat")
    project_id = os.environ.get("ROWBOAT_PROJECT_ID", "")

    try:
        client = MongoClient(uri, serverSelectionTimeoutMS=5000)
        db = client[db_name]
        src_filter: Dict[str, Any] = {}
        if project_id:
            src_filter["projectId"] = project_id
        sources = list(db["sources"].find(src_filter, {"_id": 1, "name": 1}).limit(int(limit)))

        # Count source_docs per source. Match sourceId-as-string because
        # that's how it's stored in source_docs (vs ObjectId in sources).
        folders = []
        for s in sources:
            sid_str = str(s["_id"])
            count = db["source_docs"].count_documents({"sourceId": sid_str})
            folders.append({
                "_id": sid_str,
                "name": s.get("name", ""),
                "doc_count": int(count),
            })
        folders.sort(key=lambda f: -f["doc_count"])
        return {"success": True, "count": len(folders), "folders": folders}
    except Exception as exc:
        return {"success": False, "error": f"{type(exc).__name__}: {exc}"}
    finally:
        try:
            client.close()
        except Exception:
            pass


def _rowboat_chat(message: str, context: str = "default",
                  conversation_id: Optional[str] = None,
                  timeout: int = 120) -> Dict[str, Any]:
    """Send a chat message to the Rowboat backend and return its answer.

    This is the read path counterpart to ``rowboat_upload``: ``upload`` pushes
    a file into the knowledge graph, ``chat`` asks Rowboat a natural-language
    question whose answer is RAG-augmented over the entire knowledge graph
    (Notes/Bewerbung/Investor Programs/People/Projects/Topics/etc.).

    Returns ``{success, response, conversation_id, context}``. Conversation IDs
    are *not* persisted across MCP calls — pass ``conversation_id`` back in if
    you want to continue a thread within the same skill run.

    Reads ``ROWBOAT_URL`` (default ``http://localhost:3000``), ``ROWBOAT_API_KEY``,
    ``ROWBOAT_PROJECT_ID`` from env. Graceful failure (not exception) on
    missing config.
    """
    if not message or not isinstance(message, str):
        return {"success": False, "error": "message must be a non-empty string"}

    rowboat_url = os.environ.get("ROWBOAT_URL", "http://localhost:3000")
    api_key = os.environ.get("ROWBOAT_API_KEY", "")
    project_id = os.environ.get("ROWBOAT_PROJECT_ID", "")
    if not api_key or not project_id:
        return {
            "success": False,
            "skipped": True,
            "reason": "rowboat not configured",
            "missing": [k for k in ("ROWBOAT_API_KEY", "ROWBOAT_PROJECT_ID")
                        if not os.environ.get(k)],
        }

    try:
        import requests
    except ImportError:
        return {"success": False, "error": "requests not installed"}

    chat_url = f"{rowboat_url.rstrip('/')}/api/v1/{project_id}/chat"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    payload: Dict[str, Any] = {"messages": [{"role": "user", "content": message}]}
    if conversation_id:
        payload["conversationId"] = conversation_id

    try:
        resp = requests.post(chat_url, json=payload, headers=headers, timeout=timeout)
    except requests.exceptions.ConnectionError:
        return {"success": False, "error": "connection refused — is Rowboat running on :3100?"}
    except requests.exceptions.Timeout:
        return {"success": False, "error": f"timeout after {timeout}s"}
    except Exception as exc:
        return {"success": False, "error": f"{type(exc).__name__}: {exc}"}

    if resp.status_code != 200:
        return {
            "success": False,
            "error": f"HTTP {resp.status_code}",
            "status_code": resp.status_code,
            "url": chat_url,
            "response_text": resp.text[:500] if resp.text else "",
        }

    try:
        data = resp.json()
    except Exception as exc:
        return {"success": False, "error": f"non-JSON response: {exc}", "raw": resp.text[:500]}

    # Walk the turn output backwards to find the assistant's last content.
    response_text = ""
    turn = data.get("turn") if isinstance(data, dict) else None
    if isinstance(turn, dict):
        for msg in reversed(turn.get("output") or []):
            if not isinstance(msg, dict):
                continue
            if msg.get("role") == "assistant" and msg.get("content"):
                response_text = str(msg["content"])
                break

    return {
        "success": bool(response_text),
        "response": response_text,
        "conversation_id": (data.get("conversationId") if isinstance(data, dict) else None),
        "context": context,
        "status_code": resp.status_code,
    }


def _excel_verify_file(file_path: str, expected_cells: Optional[Dict[str, str]] = None,
                       min_rows: Optional[int] = None,
                       must_contain_text: Optional[list[str]] = None) -> Dict[str, Any]:
    """Hard disk-level verification of an Excel file.

    Reads the .xlsx with openpyxl and checks:
      - file exists and parses as valid xlsx
      - optionally specific cells contain expected values (substring match)
      - optionally the active sheet has at least min_rows rows
      - optionally each string in must_contain_text appears somewhere in the sheet

    Returns ``{exists, valid, checks: {...}, summary}``.
    """
    if not file_path:
        return {"success": False, "exists": False, "error": "file_path required"}
    if not os.path.isfile(file_path):
        return {
            "success": False,
            "exists": False,
            "file_path": file_path,
            "error": f"file does not exist on disk: {file_path}",
        }
    try:
        from openpyxl import load_workbook
    except ImportError:
        return {"success": False, "error": "openpyxl not installed"}
    try:
        wb = load_workbook(file_path, read_only=True, data_only=False)
    except Exception as exc:
        return {
            "success": False,
            "exists": True,
            "valid": False,
            "file_path": file_path,
            "error": f"openpyxl load failed: {type(exc).__name__}: {exc}",
        }

    ws = wb.active
    max_row = ws.max_row or 0
    max_col = ws.max_column or 0
    checks: Dict[str, Any] = {}
    failures: list[str] = []

    if expected_cells:
        cell_results: Dict[str, Any] = {}
        for coord, expected in expected_cells.items():
            try:
                actual = ws[coord].value
            except Exception:
                actual = None
            actual_str = "" if actual is None else str(actual)
            ok = str(expected) in actual_str
            cell_results[coord] = {"expected_substring": expected, "actual": actual_str, "ok": ok}
            if not ok:
                failures.append(f"cell {coord}: expected substring {expected!r}, got {actual_str!r}")
        checks["expected_cells"] = cell_results

    if min_rows is not None:
        # Use the largest sheet, not just the active one — multi-sheet
        # workbooks often have a small summary on the active sheet and the
        # bulk data on a sibling sheet.
        max_rows_any = max(
            (wb[sn].max_row or 0) for sn in wb.sheetnames
        )
        ok = max_rows_any >= int(min_rows)
        checks["min_rows"] = {
            "required": min_rows,
            "actual_largest_sheet": max_rows_any,
            "actual_active_sheet": max_row,
            "ok": ok,
        }
        if not ok:
            failures.append(f"largest sheet has {max_rows_any} rows, need {min_rows}")

    if must_contain_text:
        # Build a lower-case haystack across ALL sheets — multi-sheet
        # workbooks would otherwise miss substrings on non-active sheets.
        # Sheet names are included in the haystack too, so substrings that
        # are tab-names (e.g. 'KW18', 'Resturlaub') match correctly.
        haystack: list[str] = []
        for sheet_name in wb.sheetnames:
            haystack.append(sheet_name.lower())
            ws_iter = wb[sheet_name]
            for row in ws_iter.iter_rows(values_only=True):
                for v in row:
                    if v is not None:
                        haystack.append(str(v).lower())
        joined = "\n".join(haystack)
        text_results: Dict[str, bool] = {}
        for needle in must_contain_text:
            ok = needle.lower() in joined
            text_results[needle] = ok
            if not ok:
                failures.append(f"text {needle!r} not found anywhere in workbook")
        checks["must_contain_text"] = text_results

    wb.close()

    success = len(failures) == 0
    return {
        "success": success,
        "exists": True,
        "valid": True,
        "file_path": file_path,
        "sheet_name": ws.title,
        "max_row": max_row,
        "max_col": max_col,
        "checks": checks,
        "failures": failures,
        "summary": (
            f"OK ({max_row} rows, {max_col} cols)" if success
            else f"FAIL ({len(failures)} issues): {'; '.join(failures[:3])}"
        ),
    }


def _excel_paste_table(rows: list[list[Any]], start_cell: Optional[str] = None) -> Dict[str, Any]:
    """Paste a 2-D table into Excel via clipboard (Tab-separated TSV).

    Excel's paste handler interprets Tab as column-separator and Newline as
    row-separator, so a TSV blob in the clipboard pastes into a rectangular
    cell range starting from the active selection. Much more reliable than
    typing each cell + Tab + ... .

    If ``start_cell`` is provided (e.g. ``"A1"``), we Ctrl+G to it first so
    the paste lands in the right place.
    """
    if sys.platform != "win32":
        return {"success": False, "error": "excel_paste_table only supported on Windows"}
    try:
        import pyautogui
        import pyperclip
    except ImportError as exc:
        return {"success": False, "error": f"pyautogui/pyperclip missing: {exc}"}

    if not rows or not isinstance(rows, list):
        return {"success": False, "error": "rows must be a non-empty list of lists"}

    # Build TSV. Cells with newlines/tabs get sanitized by replacing.
    tsv_lines: list[str] = []
    for row in rows:
        if not isinstance(row, list):
            return {"success": False, "error": f"each row must be a list, got {type(row).__name__}"}
        cells: list[str] = []
        for cell in row:
            s = str(cell) if cell is not None else ""
            s = s.replace("\t", "    ").replace("\r", "").replace("\n", " ")
            cells.append(s)
        tsv_lines.append("\t".join(cells))
    tsv = "\n".join(tsv_lines)

    # Optional: navigate to start cell
    import time

    if start_cell:
        pyautogui.hotkey("ctrl", "g")
        time.sleep(0.4)
        pyperclip.copy(start_cell)
        pyautogui.hotkey("ctrl", "v")
        time.sleep(0.2)
        pyautogui.press("enter")
        time.sleep(0.3)

    pyperclip.copy(tsv)
    time.sleep(0.1)
    pyautogui.hotkey("ctrl", "v")
    time.sleep(0.4)
    return {
        "success": True,
        "rows_pasted": len(rows),
        "cols_per_row": len(rows[0]) if rows else 0,
        "start_cell": start_cell,
        "tsv_size_chars": len(tsv),
    }


def _skill_save_and_index(app: str, skill_name: str, frontmatter: Dict[str, Any],
                          body: str) -> Dict[str, Any]:
    """Atomic write + index: SKILL.md to disk, then upsert to Qdrant.

    Replaces the multi-step "write file then call indexer" dance — the
    coordinator emits one tool call and gets back both the file_path and
    the Qdrant point id.
    """
    if not _ensure_skill_lib_on_path():
        return {"success": False, "error": f"skill library not found at {_SKILL_LIB_ROOT}"}
    try:
        import yaml
    except ImportError:
        return {"success": False, "error": "PyYAML not installed"}

    safe_app = "".join(c for c in app.lower() if c.isalnum() or c in "-_")
    safe_name = "".join(c for c in skill_name.lower() if c.isalnum() or c in "-_")
    if not safe_app or not safe_name:
        return {"success": False, "error": "app and skill_name must be non-empty alnum/dash"}

    target_dir = os.path.join(_SKILL_LIB_ROOT, safe_app, safe_name)
    target_file = os.path.join(target_dir, "SKILL.md")
    os.makedirs(target_dir, exist_ok=True)

    # Stamp metadata if not provided
    fm = dict(frontmatter or {})
    fm.setdefault("name", f"{safe_app}-{safe_name}")
    fm.setdefault("app", safe_app)
    fm.setdefault("agents", ["*"])
    fm.setdefault("confidence", 0.0)
    fm.setdefault("attempts", 0)
    fm.setdefault("successes", 0)
    if "last_adjusted" not in fm:
        from datetime import datetime, timezone
        fm["last_adjusted"] = datetime.now(timezone.utc).isoformat()

    # YAML dump deterministic
    fm_yaml = yaml.safe_dump(fm, sort_keys=False, allow_unicode=True).rstrip()
    content = f"---\n{fm_yaml}\n---\n\n{body.strip()}\n"
    with open(target_file, "w", encoding="utf-8") as f:
        f.write(content)

    # Now upsert to Qdrant
    try:
        import _indexer  # type: ignore[import-not-found]
        import _loader  # type: ignore[import-not-found]
        skill_obj = _loader.parse_skill_file(__import__("pathlib").Path(target_file))
        _indexer.upsert_skill(skill_obj)
        indexed = True
        index_error = None
    except Exception as exc:
        indexed = False
        index_error = f"{type(exc).__name__}: {exc}"

    return {
        "success": True,
        "file_path": target_file,
        "indexed": indexed,
        "index_error": index_error,
        "frontmatter": fm,
    }


def _skill_list(app: Optional[str] = None) -> Dict[str, Any]:
    if not _ensure_skill_lib_on_path():
        return {"success": False, "error": f"skill library not found at {_SKILL_LIB_ROOT}"}
    try:
        import _loader  # type: ignore[import-not-found]
        skills = _loader.discover_skills()
        if app:
            skills = [s for s in skills if s.app == app]
        return {
            "success": True,
            "count": len(skills),
            "skills": [
                {
                    "name": s.name,
                    "app": s.app,
                    "description": s.description,
                    "confidence": s.confidence,
                    "attempts": s.attempts,
                    "successes": s.successes,
                    "agents": s.agents,
                    "file_path": str(s.path),
                }
                for s in skills
            ],
        }
    except Exception as exc:
        return {"success": False, "error": f"{type(exc).__name__}: {exc}"}

# Handoff system imports
from agents.handoff import (
    PlanningTeam,
    ValidationTeam,
    AgentRuntime,
    UserTask,
)

# Claude CLI import
from agents.orchestrator import ClaudeCLIWrapper

# Global instances (initialized lazily)
_planning_team: Optional[PlanningTeam] = None
_validation_team: Optional[ValidationTeam] = None
_runtime: Optional[AgentRuntime] = None
_claude_cli: Optional[ClaudeCLIWrapper] = None


def get_claude_cli() -> ClaudeCLIWrapper:
    """Get or create the Claude CLI wrapper (singleton)."""
    global _claude_cli
    if _claude_cli is None:
        _claude_cli = ClaudeCLIWrapper()
    return _claude_cli


async def get_planning_team() -> PlanningTeam:
    """Get or create the planning team (singleton)."""
    global _planning_team
    if _planning_team is None:
        _planning_team = PlanningTeam(max_debate_rounds=2, use_llm=True)
        await _planning_team.start()
    return _planning_team


async def get_validation_team() -> ValidationTeam:
    """Get or create the validation team (singleton)."""
    global _validation_team
    if _validation_team is None:
        _validation_team = ValidationTeam(confidence_threshold=0.6)
        await _validation_team.start()
    return _validation_team


async def get_runtime() -> AgentRuntime:
    """Get or create the agent runtime (singleton)."""
    global _runtime
    if _runtime is None:
        _runtime = AgentRuntime()
    return _runtime


# Tool definitions
TOOLS = [
    Tool(
        name="handoff_plan",
        description="Create a desktop automation plan using LLM-powered Planner + Critic. "
                    "Returns a plan with steps, approval status, issues, and confidence score.",
        inputSchema={
            "type": "object",
            "properties": {
                "goal": {
                    "type": "string",
                    "description": "What to accomplish (e.g., 'open notepad and type hello')"
                },
                "context": {
                    "type": "object",
                    "description": "Additional context (e.g., {message: 'hello', user_feedback: '...'})"
                }
            },
            "required": ["goal"]
        }
    ),
    Tool(
        name="handoff_execute",
        description="Execute a plan's automation steps. Each step can be: hotkey, sleep, write, press, click, find_and_click.",
        inputSchema={
            "type": "object",
            "properties": {
                "plan": {
                    "type": "array",
                    "description": "List of plan steps to execute",
                    "items": {
                        "type": "object",
                        "properties": {
                            "type": {"type": "string"},
                            "description": {"type": "string"}
                        }
                    }
                }
            },
            "required": ["plan"]
        }
    ),
    Tool(
        name="handoff_validate",
        description="Find and validate a UI element on screen. Returns location coordinates and confidence.",
        inputSchema={
            "type": "object",
            "properties": {
                "target": {
                    "type": "string",
                    "description": "Element to find (e.g., 'chat input field', 'send button')"
                },
                "expected_state": {
                    "type": "object",
                    "description": "Optional expected screen state for validation"
                }
            },
            "required": ["target"]
        }
    ),
    Tool(
        name="handoff_action",
        description=(
            "Execute a direct automation action. Pass `action_type` and the "
            "relevant args either flat (`{action_type: 'type', text: 'Hi'}`) "
            "OR nested in `params` (`{action_type: 'type', params: {text: 'Hi'}}`). "
            "Both forms work."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "action_type": {
                    "type": "string",
                    "enum": ["hotkey", "type", "press", "click", "sleep", "scroll"],
                    "description": "Type of action: hotkey | type | press | click | sleep | scroll."
                },
                "keys": {"type": "string", "description": "For hotkey: keys like 'ctrl+alt+space' (flat or under params)."},
                "text": {"type": "string", "description": "For type: text to type (clipboard-pasted, supports newlines/tabs)."},
                "key": {"type": "string", "description": "For press: key name like 'enter'."},
                "x": {"type": "integer", "description": "For click/scroll: x coordinate (absolute pixel)."},
                "y": {"type": "integer", "description": "For click/scroll: y coordinate."},
                "seconds": {"type": "number", "description": "For sleep: duration in seconds."},
                "direction": {"type": "string", "enum": ["up", "down"], "description": "For scroll: direction."},
                "amount": {"type": "integer", "description": "For scroll: number of scroll clicks (default 3)."},
                "params": {
                    "type": "object",
                    "description": "Optional: nested params dict (legacy form). If omitted, top-level keys are used."
                }
            },
            "required": ["action_type"]
        }
    ),
    Tool(
        name="handoff_status",
        description="Get the status of the handoff system including runtime stats.",
        inputSchema={
            "type": "object",
            "properties": {}
        }
    ),
    Tool(
        name="handoff_read_screen",
        description="Capture screenshot and read text from screen using OCR. Returns visible text content. Uses cached stream frames when available from live streaming.",
        inputSchema={
            "type": "object",
            "properties": {
                "region": {
                    "type": "object",
                    "description": "Optional region to capture {x, y, width, height}. If not provided, captures full screen.",
                    "properties": {
                        "x": {"type": "integer"},
                        "y": {"type": "integer"},
                        "width": {"type": "integer"},
                        "height": {"type": "integer"}
                    }
                },
                "monitor_id": {
                    "type": "integer",
                    "description": "Monitor index for multi-monitor setups (0 = primary, 1 = secondary). Default: 0"
                }
            }
        }
    ),
    Tool(
        name="handoff_get_focus",
        description="Get the currently active/focused window. Returns window title, handle, and process ID. Use this to verify the correct window is focused before typing.",
        inputSchema={
            "type": "object",
            "properties": {}
        }
    ),
    Tool(
        name="handoff_scroll",
        description="Scroll the mouse wheel up or down. Can scroll at current position or at a specific location.",
        inputSchema={
            "type": "object",
            "properties": {
                "direction": {
                    "type": "string",
                    "enum": ["up", "down"],
                    "description": "Direction to scroll"
                },
                "amount": {
                    "type": "integer",
                    "description": "Number of scroll clicks (default: 3). Positive values scroll the content, negative not supported - use direction instead."
                },
                "x": {
                    "type": "integer",
                    "description": "Optional x coordinate to scroll at. If not provided, scrolls at current mouse position."
                },
                "y": {
                    "type": "integer",
                    "description": "Optional y coordinate to scroll at. If not provided, scrolls at current mouse position."
                }
            },
            "required": ["direction"]
        }
    ),
    # Claude CLI Tools
    Tool(
        name="claude_cli_run",
        description="Run a prompt via Claude CLI. Can use skills and output as JSON.",
        inputSchema={
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "The prompt to send to Claude"
                },
                "skill": {
                    "type": "string",
                    "description": "Optional skill name to use"
                },
                "output_format": {
                    "type": "string",
                    "enum": ["text", "json"],
                    "default": "text",
                    "description": "Output format: text or json"
                }
            },
            "required": ["prompt"]
        }
    ),
    Tool(
        name="claude_cli_skill",
        description="Execute a Claude Skill with inputs.",
        inputSchema={
            "type": "object",
            "properties": {
                "skill_name": {
                    "type": "string",
                    "description": "Name of the skill (without .md extension)"
                },
                "inputs": {
                    "type": "object",
                    "description": "Input parameters for the skill"
                },
                "ui_context": {
                    "type": "object",
                    "description": "Optional UI context for desktop automation skills"
                }
            },
            "required": ["skill_name", "inputs"]
        }
    ),
    Tool(
        name="claude_cli_status",
        description="Check if Claude CLI is installed and available.",
        inputSchema={
            "type": "object",
            "properties": {}
        }
    ),
    # Vision Analysis Tool
    Tool(
        name="vision_analyze",
        description="Analyze screenshot with Gemini Vision AI. Returns UI analysis, element locations, and suggested automation actions. Uses cached stream frames when available.",
        inputSchema={
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "What to analyze (e.g., 'Find all buttons', 'What is the current state?', 'Locate the login form')"
                },
                "mode": {
                    "type": "string",
                    "enum": ["element_detection", "state_analysis", "task_planning", "custom"],
                    "description": "Analysis mode: element_detection (find UI elements), state_analysis (describe screen state), task_planning (suggest next actions), custom (free-form analysis)"
                },
                "json_output": {
                    "type": "boolean",
                    "default": True,
                    "description": "Return structured JSON response when possible"
                },
                "monitor_id": {
                    "type": "integer",
                    "default": 0,
                    "description": "Monitor index for multi-monitor setups (0 = primary)"
                }
            },
            "required": ["prompt"]
        }
    ),
    # Clawdbot Messaging Tools
    Tool(
        name="clawdbot_send_message",
        description="Send a message to a contact via messaging platform (WhatsApp, Telegram, Discord, Signal, etc.). "
                    "Resolves contact names with fuzzy matching. The message is routed through the Clawdbot Gateway.",
        inputSchema={
            "type": "object",
            "properties": {
                "recipient": {
                    "type": "string",
                    "description": "Contact name, alias, or key (e.g., 'Peter', 'boss', 'mama'). Supports fuzzy matching."
                },
                "message": {
                    "type": "string",
                    "description": "Message text to send. Supports {variable} placeholders."
                },
                "platform": {
                    "type": "string",
                    "enum": ["whatsapp", "telegram", "discord", "signal", "imessage", "email"],
                    "description": "Messaging platform to use. If omitted, uses the first available platform for the contact."
                }
            },
            "required": ["recipient", "message"]
        }
    ),
    Tool(
        name="clawdbot_get_contacts",
        description="List or search contacts in the registry. Supports fuzzy matching by name or alias. "
                    "Returns contact info including available messaging platforms.",
        inputSchema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query for fuzzy matching (e.g., 'Peter', 'boss'). If empty, lists all contacts."
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of results to return (default: 10)"
                }
            }
        }
    ),
    Tool(
        name="clawdbot_get_status",
        description="Get the Clawdbot bridge status including active sessions, connected platforms, and capabilities.",
        inputSchema={
            "type": "object",
            "properties": {}
        }
    ),
    Tool(
        name="clawdbot_get_variables",
        description="Get predefined variables and message templates. Variables can be used as {variable_name} in messages. "
                    "Templates are predefined message formats.",
        inputSchema={
            "type": "object",
            "properties": {
                "include_templates": {
                    "type": "boolean",
                    "default": True,
                    "description": "Whether to include message templates in the response"
                }
            }
        }
    ),
    # Clawdbot Browser Tools
    Tool(
        name="clawdbot_browser_open",
        description="Open a URL in the browser via Clawdbot. Use this for opening websites.",
        inputSchema={
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "URL to open (e.g., 'https://google.com', 'github.com')"
                }
            },
            "required": ["url"]
        }
    ),
    Tool(
        name="clawdbot_browser_search",
        description="Search the web for a query via Clawdbot. Opens a Google search.",
        inputSchema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query (e.g., 'weather Berlin', 'Python docs')"
                }
            },
            "required": ["query"]
        }
    ),
    Tool(
        name="clawdbot_browser_read_page",
        description="Read the content of the currently open browser page using OCR.",
        inputSchema={
            "type": "object",
            "properties": {}
        }
    ),
    # Clawdbot Reporting Tool
    Tool(
        name="clawdbot_report_findings",
        description="Report/send findings or gathered information to a contact via messaging or as callback. "
                    "Use after browser searches, page reads, or any operation where you want to communicate results. "
                    "If no recipient specified, sends via Clawdbot callback channel.",
        inputSchema={
            "type": "object",
            "properties": {
                "findings": {
                    "type": "string",
                    "description": "The information/results to report (text summary)"
                },
                "recipient": {
                    "type": "string",
                    "description": "Optional: contact name to send findings to (e.g., 'Peter', 'boss'). If omitted, sends via callback."
                },
                "platform": {
                    "type": "string",
                    "enum": ["whatsapp", "telegram", "discord", "signal", "email"],
                    "description": "Optional: messaging platform."
                },
                "title": {
                    "type": "string",
                    "description": "Optional: short title/subject for the report"
                }
            },
            "required": ["findings"]
        }
    ),
    # Clarify (ask the user via HTML form, used by adaptive skills for credentials)
    Tool(
        name="handoff_clarify",
        description="Ask the human user a question via Telegram/UI and (optionally) collect "
                    "structured form input. Returns a clarify_id and form_url; the caller must "
                    "poll handoff_clarify_check until status='answered'. Use form_schema for "
                    "credentials/secrets — values flagged as type=password (or with a "
                    "credential_id field) are stored DPAPI-encrypted and only a vault token is "
                    "returned in the answer payload.",
        inputSchema={
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "The question shown to the user."
                },
                "options": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional list of multiple-choice answers."
                },
                "form_schema": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "label": {"type": "string"},
                            "type": {"type": "string", "enum": ["text", "password", "email", "url", "number", "vault"]},
                            "required": {"type": "boolean"},
                            "placeholder": {"type": "string"}
                        },
                        "required": ["name", "type"]
                    },
                    "description": "Optional list of form-field descriptors for collecting structured input."
                }
            },
            "required": ["question"]
        }
    ),
    Tool(
        name="handoff_clarify_check",
        description="Check whether a previously-issued handoff_clarify has been answered. "
                    "Returns {status, answer} — status is 'pending' until the user submits.",
        inputSchema={
            "type": "object",
            "properties": {
                "clarify_id": {
                    "type": "string",
                    "description": "ID returned by a prior handoff_clarify call."
                }
            },
            "required": ["clarify_id"]
        }
    ),
    Tool(
        name="handoff_approval_request",
        description="Pre-authorisation prompt: asks the user via Telegram + HTML one-click form "
                    "(Approve / Decline buttons) and BLOCKS until either an answer arrives or "
                    "the timeout (default 60s) elapses. On timeout, the configured default is "
                    "returned (default: 'approved' — optimistic, since the user explicitly opted "
                    "into autonomy). Use this BEFORE the first destructive tool call in a skill "
                    "(launching apps, typing/clicking, sending messages, file writes). For "
                    "read-only operations (read_screen, vision_analyze, get_focus, skill_search) "
                    "DO NOT call this — just go ahead.",
        inputSchema={
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": "Short imperative description of what you are about to do, e.g. 'Word starten und 5 Zellen mit Werten füllen'."
                },
                "reason": {
                    "type": "string",
                    "description": "Optional context: why this is needed (e.g. which skill, which expected effect)."
                },
                "timeout_seconds": {
                    "type": "integer",
                    "description": "Seconds to wait for user input before falling back to default. Default 60."
                },
                "default_on_timeout": {
                    "type": "string",
                    "enum": ["approved", "declined"],
                    "description": "What to return if the user doesn't respond. Default 'approved'."
                }
            },
            "required": ["action"]
        }
    ),
    # Skill library lookups — talks to the dedicated `vibemind_skills` Qdrant
    # collection (the qdrant MCP is bound to a different default collection
    # so it can't reach this one without re-config).
    Tool(
        name="skill_search",
        description="Semantic search over the VibeMind adaptive-skill library "
                    "(SKILL.md files indexed in the 'vibemind_skills' Qdrant collection). "
                    "Returns the top-K matches with name, app, description, confidence, file_path, score.",
        inputSchema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Natural-language description of what the user wants to do."},
                "agent": {"type": "string", "description": "Optional: only return skills whose 'agents' frontmatter contains this name (or '*')."},
                "limit": {"type": "integer", "description": "Max results, default 5."}
            },
            "required": ["query"]
        }
    ),
    Tool(
        name="skill_list",
        description="List skills in the library, optionally filtered by app. Reads filesystem directly (no Qdrant call).",
        inputSchema={
            "type": "object",
            "properties": {
                "app": {"type": "string", "description": "Optional: only show skills for this app (e.g. 'excel')."}
            }
        }
    ),
    Tool(
        name="window_maximize",
        description=(
            "Maximize a window matching the title substring (Win32 ShowWindow "
            "SW_MAXIMIZE). Use this before screen-driven skills so the app fills "
            "the screen and Vision can see all UI elements."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "title_substring": {"type": "string", "description": "Case-insensitive title fragment, e.g. 'Excel'."}
            },
            "required": ["title_substring"]
        }
    ),
    Tool(
        name="xlsx_create_from_data",
        description=(
            "DETERMINISTIC: Create an .xlsx file directly via openpyxl — no Excel UI, "
            "no SendKeys. Single-sheet via `rows` OR multi-sheet via `sheets`. "
            "Strings starting with `=` are written as Excel formulas (e.g. '=SUM(A1:A3)' "
            "or '=Tabelle2!B5*0.19'). `cell_styles` allows fill colors, font colors, "
            "borders, alignment, number_format. `freeze_pane` fixes rows/cols above and "
            "left of the given coordinate."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Absolute output path, e.g. 'C:/Users/User/Desktop/HR/foo.xlsx'."},
                "rows": {
                    "type": "array",
                    "items": {"type": "array", "items": {"type": "string"}},
                    "description": "2-D list of cell values for SINGLE-sheet workbook. Use either rows OR sheets."
                },
                "sheets": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "rows": {"type": "array", "items": {"type": "array", "items": {"type": "string"}}},
                            "bold_rows": {"type": "array", "items": {"type": "integer"}},
                            "freeze_pane": {"type": "string"},
                            "cell_styles": {"type": "array", "items": {"type": "object"}}
                        }
                    },
                    "description": "Multi-sheet alternative to rows. List of {name, rows, bold_rows, freeze_pane, cell_styles}."
                },
                "sheet_name": {"type": "string", "description": "Sheet name when using `rows`."},
                "bold_rows": {"type": "array", "items": {"type": "integer"}, "description": "1-based row numbers to bold."},
                "freeze_pane": {"type": "string", "description": "Cell coord (e.g. 'A2' to freeze row 1, 'B2' to freeze row 1 + col A)."},
                "cell_styles": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "range": {"type": "string", "description": "A1-notation, e.g. 'A1:E1' or 'C2:C20'."},
                            "fill_color": {"type": "string", "description": "6-hex without #, e.g. 'FFEB9C'."},
                            "font_color": {"type": "string", "description": "6-hex without #."},
                            "bold": {"type": "boolean"},
                            "italic": {"type": "boolean"},
                            "border": {"type": "boolean", "description": "Thin border around each cell in the range."},
                            "align": {"type": "string", "enum": ["left", "center", "right"]},
                            "number_format": {"type": "string", "description": "Excel format code, e.g. '#,##0.00 €' or 'YYYY-MM-DD'."}
                        },
                        "required": ["range"]
                    }
                },
                "auto_width": {"type": "boolean", "description": "Auto-fit column widths. Default true."},
                "overwrite": {"type": "boolean", "description": "Overwrite if file exists. Default true."}
            },
            "required": ["file_path"]
        }
    ),
    Tool(
        name="docx_create_from_data",
        description=(
            "DETERMINISTIC: Create a .docx file via python-docx from a list of "
            "structured blocks. Use this for HR documents like Arbeitszeugnisse, "
            "Mitarbeitergespräch-Protokolle, Verträge, Kündigungsschreiben. Block types: "
            "heading (level 1-3), paragraph (bold/italic), table (rows[][], header_row), "
            "page_break, list (bullet/number)."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Absolute output path, e.g. 'C:/Users/User/Desktop/HR/zeugnis.docx'."},
                "blocks": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "type": {"type": "string", "enum": ["heading", "paragraph", "table", "page_break", "list"]},
                            "text": {"type": "string"},
                            "level": {"type": "integer", "description": "For heading: 1, 2 or 3."},
                            "bold": {"type": "boolean"},
                            "italic": {"type": "boolean"},
                            "rows": {"type": "array", "items": {"type": "array", "items": {"type": "string"}}, "description": "For table."},
                            "header_row": {"type": "boolean", "description": "For table: bold first row."},
                            "items": {"type": "array", "items": {"type": "string"}, "description": "For list."},
                            "style": {"type": "string", "enum": ["bullet", "number"], "description": "For list."}
                        },
                        "required": ["type"]
                    }
                },
                "overwrite": {"type": "boolean", "description": "Default true."}
            },
            "required": ["file_path", "blocks"]
        }
    ),
    Tool(
        name="csv_create_from_data",
        description=(
            "Write rows as CSV. Default encoding utf-8-sig (BOM) so Excel opens "
            "German umlauts correctly. Use this for DATEV exports, "
            "Sozialversicherungs-Meldungen, Mitarbeiter-Imports."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "file_path": {"type": "string"},
                "rows": {"type": "array", "items": {"type": "array", "items": {"type": "string"}}},
                "delimiter": {"type": "string", "description": "Default ','. Use ';' for German Excel."},
                "encoding": {"type": "string", "description": "Default 'utf-8-sig'."},
                "overwrite": {"type": "boolean"}
            },
            "required": ["file_path", "rows"]
        }
    ),
    Tool(
        name="docx_verify_file",
        description=(
            "Read-only verification of a .docx file. Pendant to excel_verify_file. "
            "Confirms the file exists, is valid, and contains expected content "
            "(substrings, min paragraph/table counts, specific table cell values)."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "file_path": {"type": "string"},
                "expected_substrings": {"type": "array", "items": {"type": "string"}, "description": "All must appear somewhere in the body (case-insensitive)."},
                "min_paragraphs": {"type": "integer"},
                "min_tables": {"type": "integer"},
                "expected_table_cells": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "table_index": {"type": "integer"},
                            "row": {"type": "integer"},
                            "col": {"type": "integer"},
                            "expected_substring": {"type": "string"}
                        }
                    }
                }
            },
            "required": ["file_path"]
        }
    ),
    Tool(
        name="rowboat_upload",
        description=(
            "Upload a file to the Rowboat knowledge backend at :3000. Returns "
            "{success, status_code, url}. If ROWBOAT_API_KEY/ROWBOAT_PROJECT_ID "
            "env vars are missing, returns {skipped: true} gracefully — no crash. "
            "Use this AFTER xlsx_create/docx_create to publish HR artifacts to "
            "the central knowledge graph for cross-system retrieval."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Absolute path of an already-saved file (.xlsx, .docx, .csv, .pdf)."},
                "title": {"type": "string", "description": "Display title in Rowboat. Defaults to filename."},
                "tags": {"type": "array", "items": {"type": "string"}, "description": "Optional tag list, e.g. ['hr','onboarding','2026']."}
            },
            "required": ["file_path"]
        }
    ),
    Tool(
        name="rowboat_chat",
        description=(
            "Ask a natural-language question to the Rowboat HTTP backend "
            "(RAG-augmented over Notes/Bewerbung/Investor Programs/People/Projects/"
            "Topics/etc.). Note: as of 2026-05-07 the local Rowboat container "
            "rejects external API calls with 'Invalid API key' — prefer "
            "rowboat_search (direct MongoDB) for now. This tool stays here so "
            "we can switch to it once the auth setup is fixed. Returns "
            "{success, response, conversation_id}."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "Natural-language question."},
                "context": {"type": "string", "description": "Optional context tag for telemetry. Default: 'default'."},
                "conversation_id": {"type": "string", "description": "Optional conversation continuation id."},
                "timeout": {"type": "integer", "description": "HTTP timeout in seconds. Default 120."}
            },
            "required": ["message"]
        }
    ),
    Tool(
        name="rowboat_search",
        description=(
            "Search the Rowboat knowledge base for documents matching a "
            "substring query. Goes directly against the local MongoDB "
            "(rowboat.source_docs collection) — fast, no auth needed, returns "
            "structured results. The 'folder' filter matches against bubble names "
            "(=sources.name in MongoDB; e.g. 'MiroFish', 'Phase 11', 'Brain'). "
            "It is NOT the same as the Roarboot UI sidebar (those are a UI "
            "concept not stored in Mongo). To list available folders run "
            "rowboat_list_folders first. "
            "Use this BEFORE xlsx_create_from_data when the Excel should "
            "contain *real user data* from Rowboat instead of hardcoded "
            "examples: search → parse → xlsx. "
            "Returns {success, count, results: [{name, source_name, "
            "content_excerpt, content_full_length, created_at, _id}]}."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Case-insensitive substring to find in document content or name. Use a single character like 'a' to match all docs in a folder."},
                "folder": {"type": "string", "description": "Optional substring of bubble/source name (e.g. 'MiroFish', 'Phase 11', 'Brain'). When omitted, searches all bubbles."},
                "limit": {"type": "integer", "description": "Max number of documents to return. Default 20."}
            },
            "required": ["query"]
        }
    ),
    Tool(
        name="rowboat_list_folders",
        description=(
            "List all available bubble/source names in Rowboat. Use this "
            "before rowboat_search to discover what folders/bubbles are "
            "available to filter on. Returns {success, count, folders: "
            "[{name, doc_count, _id}]}."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Max number of folders to return. Default 50."}
            },
        }
    ),
    Tool(
        name="roarboot_list_folders",
        description=(
            "List the top-level folders in the *Roarboot* (Electron app) "
            "knowledge tree at ~/.rowboot/knowledge/. These are the folders "
            "you see in the Roarboot sidebar (Bewerbung, Investor Programs, "
            "Notes, Organizations, People, Projects, Topics, Videos, Voice "
            "Memos, vibemind-discourse). They are real .md files on disk, "
            "git-versioned. Different from rowboat_list_folders which lists "
            "the RAG sources in the rowboat MongoDB. Returns {success, root, "
            "folders: [{name, file_count, subdir_count}]}."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "root": {"type": "string", "description": "Optional override of the knowledge root. Default: ~/.rowboat/knowledge"}
            },
        }
    ),
    Tool(
        name="file_evaluate",
        description=(
            "Critically evaluate a generated .xlsx or .docx file against an "
            "expected intent. A second LLM (default gpt-4o-mini) reads the "
            "rendered file content + the intent + optional source-data "
            "description, and returns a structured quality report: "
            "{score 0-100, issues: [{severity, issue, fix_suggestion}], "
            "completeness, recommendations, summary}. Use this AFTER "
            "xlsx_create_from_data / docx_create_from_data to catch "
            "semantic problems that excel_verify_file misses (wrong row "
            "counts, hallucinated fields, duplicated entries, "
            "type mismatches). Best paired with a clear expected_intent "
            "and source_data_description."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Absolute path to the .xlsx or .docx file."},
                "expected_intent": {"type": "string", "description": "What the file should contain. E.g. 'List of all job applicants from Bewerbung folder, one row per person, with name/position/email/skills'."},
                "source_data_description": {"type": "string", "description": "Optional description of the source data the file was generated from (e.g. 'Roarboot Bewerbung folder, 1 .md file with Felix Baumann CV'). Helps the critic judge completeness."},
                "criteria": {"type": "array", "items": {"type": "string"}, "description": "Optional list of evaluation criteria (German or English). Defaults to a generic set covering completeness/correctness/data-quality/practicality."},
                "model": {"type": "string", "description": "OpenAI model. Default gpt-4o-mini."}
            },
            "required": ["file_path", "expected_intent"]
        }
    ),
    Tool(
        name="roarboot_ask",
        description=(
            "Ask a natural-language question over the Roarboot knowledge "
            "base (.md files in ~/.rowboat/knowledge/). Reads the most "
            "recent files (optionally scoped to one folder), packs them "
            "into a context, and calls OpenAI to produce an answer. This "
            "is the alternative to rowboat_chat — no container/redis/"
            "rowboat_agents service needed; uses your OPENAI_API_KEY env. "
            "Returns {success, answer, files_used, model, tokens}. "
            "Cheaper and faster than the coordinator looping over "
            "roarboot_read_knowledge results manually."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "question": {"type": "string", "description": "Natural-language question, e.g. 'Welche Skills hat Felix Baumann?'"},
                "folder": {"type": "string", "description": "Optional folder to scope the answer to (e.g. 'Bewerbung', 'People', 'Projects'). Default: search across all folders."},
                "max_files": {"type": "integer", "description": "Max number of files to include in context (newest first). Default 10."},
                "max_chars_per_file": {"type": "integer", "description": "Max chars from each file. Default 4000."},
                "model": {"type": "string", "description": "OpenAI model. Default gpt-4o-mini (cheap+fast). Options: gpt-4o-mini, gpt-4o, gpt-5.5."}
            },
            "required": ["question"]
        }
    ),
    Tool(
        name="roarboot_read_knowledge",
        description=(
            "Read .md files from a Roarboot knowledge folder. Use this "
            "AFTER roarboot_list_folders to drill into a specific folder. "
            "Optional 'query' filters by case-insensitive substring match "
            "against path+content. Use this BEFORE xlsx_create_from_data "
            "when the Excel should contain *real* user data from Roarboot. "
            "Examples: folder='Bewerbung' (all Bewerbung entries), "
            "folder='People' (all people profiles), folder='Projects' "
            "with query='VibeMind - Brain' (just Brain-Router project files). "
            "Returns {success, folder, root, count, results: [{path, name, "
            "content_excerpt, content_full_length, modified_at}]}."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "folder": {"type": "string", "description": "Top-level folder name (e.g. 'Bewerbung', 'People', 'Projects') or subpath ('Projects/VibeMind - Brain Capability Router')."},
                "query": {"type": "string", "description": "Optional case-insensitive substring filter on path+content."},
                "name_pattern": {"type": "string", "description": "Optional case-insensitive substring match against FILENAME only. Use '_overview.md' to grab only the overview files of every subfolder."},
                "limit": {"type": "integer", "description": "Max number of files to return. Default 20."},
                "root": {"type": "string", "description": "Optional override of knowledge root. Default ~/.rowboat/knowledge"}
            },
            "required": ["folder"]
        }
    ),
    Tool(
        name="excel_verify_file",
        description=(
            "HARD disk-level verification of an Excel file via openpyxl. "
            "Use this INSTEAD of vision_analyze when you need to confirm a "
            "save actually persisted with the right content. Vision can "
            "hallucinate; openpyxl reading the .xlsx cannot. Returns "
            "{success, exists, valid, checks, failures, summary}."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Absolute path to the .xlsx file."},
                "expected_cells": {
                    "type": "object",
                    "description": "Map of cell coordinates ('A1') to expected substring ('Onboarding'). The cell value must contain the substring.",
                    "additionalProperties": {"type": "string"}
                },
                "min_rows": {"type": "integer", "description": "Minimum number of rows the active sheet must have."},
                "must_contain_text": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of strings that must appear somewhere in the sheet (case-insensitive substring search)."
                }
            },
            "required": ["file_path"]
        }
    ),
    Tool(
        name="excel_paste_table",
        description=(
            "Paste a 2-D table into Excel via clipboard TSV. Way more reliable "
            "than typing cells one-by-one. Pre-condition: Excel must be the "
            "foreground window with a workbook open. If start_cell is given "
            "(e.g. 'A1'), the table is pasted starting there; otherwise it "
            "pastes at the current selection."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "rows": {
                    "type": "array",
                    "items": {
                        "type": "array",
                        "items": {"type": "string"}
                    },
                    "description": "2-D list of cell values (strings). Inner lists = rows, length must match across rows."
                },
                "start_cell": {
                    "type": "string",
                    "description": "Optional A1-notation cell to navigate to before pasting (e.g. 'B5'). If omitted, paste lands at current selection."
                }
            },
            "required": ["rows"]
        }
    ),
    Tool(
        name="skill_save_and_index",
        description="ATOMIC: write a SKILL.md file under skills/<app>/<skill_name>/SKILL.md AND upsert it to the Qdrant 'vibemind_skills' collection in one go. This is the canonical 'persist a learned skill' tool — coordinators MUST use it instead of separate filesystem write + indexer calls. Auto-stamps last_adjusted, defaults missing fields (agents=['*'], confidence=0.0, attempts=0, successes=0).",
        inputSchema={
            "type": "object",
            "properties": {
                "app": {"type": "string", "description": "App slug (e.g. 'excel', 'word', 'chrome'). Used as directory name."},
                "skill_name": {"type": "string", "description": "Skill slug, e.g. 'fill-cell'. The full SKILL.md name will be set to '<app>-<skill_name>' if not provided in frontmatter."},
                "frontmatter": {"type": "object", "description": "YAML frontmatter dict: description, requires_approval, agents, trigger, inputs, expected_state, secrets, confidence, attempts, successes."},
                "body": {"type": "string", "description": "Markdown body — the actual steps the skill performs."}
            },
            "required": ["app", "skill_name", "frontmatter", "body"]
        }
    ),
    # App lifecycle tools — launch/focus/list desktop apps from the whitelist
    Tool(
        name="app_launch",
        description="Launch a desktop application from the whitelist (notepad, calc, excel, winword, chrome, msedge, code/vscode, explorer, claude, telegram, spotify, ...). Returns {pid, command} on success.",
        inputSchema={
            "type": "object",
            "properties": {
                "app": {"type": "string", "description": "Whitelisted app name."},
                "args": {"type": "array", "items": {"type": "string"}, "description": "Optional CLI args."}
            },
            "required": ["app"]
        }
    ),
    Tool(
        name="app_focus",
        description="Bring an already-running window matching the title substring to the foreground.",
        inputSchema={
            "type": "object",
            "properties": {
                "title_substring": {"type": "string", "description": "Case-insensitive fragment of the window title (e.g. 'Excel', 'Visual Studio Code')."}
            },
            "required": ["title_substring"]
        }
    ),
    Tool(
        name="app_list_running",
        description="Enumerate all visible top-level windows (title, hwnd, pid). Useful before deciding whether to focus or launch.",
        inputSchema={"type": "object", "properties": {}}
    ),
    Tool(
        name="app_launch_or_focus",
        description="Convenience wrapper: if a window matching title_hint (or app name) is already open, focus it; otherwise launch the app from the whitelist. Returns {action: 'focused'|'launched', ...}.",
        inputSchema={
            "type": "object",
            "properties": {
                "app": {"type": "string", "description": "Whitelisted app name."},
                "title_hint": {"type": "string", "description": "Optional title fragment to match against running windows. Defaults to the app name."},
                "args": {"type": "array", "items": {"type": "string"}, "description": "Optional CLI args."}
            },
            "required": ["app"]
        }
    )
]


# Tool handlers
async def handle_plan(goal: str, context: Optional[Dict] = None) -> Dict[str, Any]:
    """Create a plan using PlanningTeam."""
    team = await get_planning_team()
    result = await team.create_plan(goal, context=context or {})
    return result


async def handle_execute(plan: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Execute plan steps."""
    import pyautogui
    import pyperclip

    results = []
    success = True

    for i, step in enumerate(plan):
        step_type = step.get('type', '')
        step_result = {"step": i + 1, "type": step_type, "success": False}

        try:
            if step_type == 'hotkey':
                keys = step.get('keys', '').replace('+', ' ').split()
                if keys:
                    pyautogui.hotkey(*keys)
                step_result["success"] = True

            elif step_type == 'sleep':
                duration = step.get('duration', step.get('seconds', 1))
                actual = float(duration) * 0.5  # 50% reduced
                await asyncio.sleep(actual)
                step_result["success"] = True

            elif step_type == 'write':
                text = step.get('text', step.get('content', ''))
                if text:
                    pyperclip.copy(text)
                    pyautogui.hotkey('ctrl', 'v')
                step_result["success"] = True

            elif step_type == 'press':
                key = step.get('key', 'enter')
                pyautogui.press(key)
                step_result["success"] = True

            elif step_type == 'click':
                x = step.get('x', 0)
                y = step.get('y', 0)
                pyautogui.click(int(x), int(y))
                step_result["success"] = True

            elif step_type == 'find_and_click':
                target = step.get('target', step.get('text', ''))
                if target:
                    team = await get_validation_team()
                    loc_result = await team.validate_element(target)
                    if loc_result.get('element_location'):
                        loc = loc_result['element_location']
                        pyautogui.click(loc['x'], loc['y'])
                        step_result["success"] = True
                        step_result["location"] = loc
                    else:
                        step_result["error"] = f"Could not find '{target}'"
                        success = False
            else:
                step_result["error"] = f"Unknown step type: {step_type}"

            # Small delay between steps
            await asyncio.sleep(0.15)

        except Exception as e:
            step_result["error"] = str(e)
            success = False

        results.append(step_result)

    return {
        "success": success,
        "steps_executed": len(results),
        "results": results
    }


async def handle_validate(target: str, expected_state: Optional[Dict] = None) -> Dict[str, Any]:
    """Validate a UI element."""
    team = await get_validation_team()
    result = await team.validate_element(target, expected_state)
    return result


async def handle_get_focus() -> Dict[str, Any]:
    """Get the currently active window."""
    from agents.handoff.window_focus import get_active_window
    return await get_active_window()


async def handle_set_focus(window_title: str) -> Dict[str, Any]:
    """Focus a window by (partial) title match."""
    from agents.handoff.window_focus import verify_window_focus
    return await verify_window_focus(window_title, timeout=3.0, auto_focus=True)


async def handle_list_windows() -> Dict[str, Any]:
    """List all visible windows with titles."""
    from agents.handoff.window_focus import list_visible_windows
    windows = list_visible_windows()
    return {"success": True, "windows": windows, "count": len(windows)}


async def handle_mouse_move(x: int, y: int, duration: float = 0.5) -> Dict[str, Any]:
    """Move mouse smoothly to position without clicking."""
    import pyautogui
    try:
        pyautogui.moveTo(int(x), int(y), duration=min(duration, 2.0))
        return {"success": True, "x": x, "y": y, "duration": duration}
    except Exception as e:
        return {"success": False, "error": str(e)}


async def handle_scroll(direction: str, amount: int = 3, x: Optional[int] = None, y: Optional[int] = None) -> Dict[str, Any]:
    """Scroll the mouse wheel up or down."""
    import pyautogui

    try:
        # Move to position if specified
        if x is not None and y is not None:
            pyautogui.moveTo(x, y)

        # Determine scroll amount (positive = up, negative = down)
        clicks = abs(amount) if amount else 3
        if direction == "down":
            clicks = -clicks

        # Perform scroll
        pyautogui.scroll(clicks)

        return {
            "success": True,
            "action": "scroll",
            "direction": direction,
            "clicks": abs(amount) if amount else 3,
            "position": {"x": x, "y": y} if x is not None else "current"
        }

    except Exception as e:
        return {"success": False, "error": str(e)}


async def handle_action(action_type: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """Execute a direct action with optional focus verification."""
    import pyautogui
    import pyperclip

    # Optional focus verification before action
    verify_focus = params.get('verify_focus', False)
    target_window = params.get('target_window', None)

    if verify_focus and target_window:
        from agents.handoff.window_focus import verify_window_focus
        focus_result = await verify_window_focus(target_window, timeout=3.0)
        if not focus_result["success"]:
            return {
                "success": False,
                "error": f"Window not focused: {target_window}",
                "focus_result": focus_result
            }

    try:
        if action_type == 'hotkey':
            keys = params.get('keys', '').replace('+', ' ').split()
            if keys:
                pyautogui.hotkey(*keys)
            return {"success": True, "action": "hotkey", "keys": keys}

        elif action_type == 'type':
            text = params.get('text', '')
            if text:
                pyperclip.copy(text)
                pyautogui.hotkey('ctrl', 'v')
            return {"success": True, "action": "type", "text_length": len(text)}

        elif action_type == 'press':
            key = params.get('key', 'enter')
            pyautogui.press(key)
            return {"success": True, "action": "press", "key": key}

        elif action_type == 'click':
            x = params.get('x', 0)
            y = params.get('y', 0)
            pyautogui.moveTo(int(x), int(y), duration=0.3)
            pyautogui.click()
            return {"success": True, "action": "click", "x": x, "y": y}

        elif action_type == 'sleep':
            seconds = params.get('seconds', 1)
            actual = float(seconds) * 0.5  # 50% reduced
            await asyncio.sleep(actual)
            return {"success": True, "action": "sleep", "seconds": seconds, "actual": actual}

        elif action_type == 'scroll':
            direction = params.get('direction', 'down')
            amount = params.get('amount', 3)
            x = params.get('x')
            y = params.get('y')

            # Move to position if specified
            if x is not None and y is not None:
                pyautogui.moveTo(x, y)

            # Scroll (positive = up, negative = down)
            clicks = abs(amount) if amount else 3
            if direction == "down":
                clicks = -clicks
            pyautogui.scroll(clicks)

            return {
                "success": True,
                "action": "scroll",
                "direction": direction,
                "clicks": abs(amount) if amount else 3,
                "position": {"x": x, "y": y} if x is not None else "current"
            }

        else:
            return {"success": False, "error": f"Unknown action type: {action_type}"}

    except Exception as e:
        return {"success": False, "error": str(e)}


async def handle_status() -> Dict[str, Any]:
    """Get system status."""
    runtime = await get_runtime()
    stats = runtime.get_stats()

    return {
        "runtime": {
            "tasks_processed": stats.get('tasks_processed', 0),
            "handoffs_routed": stats.get('handoffs_routed', 0),
            "errors": stats.get('errors', 0)
        },
        "planning_team": {
            "initialized": _planning_team is not None,
            "llm_enabled": _planning_team._use_llm if _planning_team else False
        },
        "validation_team": {
            "initialized": _validation_team is not None,
            "confidence_threshold": _validation_team.confidence_threshold if _validation_team else 0.6
        }
    }


async def handle_read_screen(region: Optional[Dict[str, int]] = None, monitor_id: int = 0) -> Dict[str, Any]:
    """Capture screen and read text using OCR.

    First checks StreamFrameCache for a fresh frame from live streaming.
    Falls back to pyautogui screenshot if no cached frame available.
    """
    import pyautogui
    from PIL import Image
    import io
    import base64

    try:
        screenshot = None
        screenshot_base64 = None
        from_cache = False

        # Try to get frame from StreamFrameCache first (if live streaming)
        try:
            from stream_frame_cache import StreamFrameCache
            cached_frame = StreamFrameCache.get_fresh_frame(monitor_id=monitor_id, max_age_ms=500)
            if cached_frame:
                screenshot = cached_frame.to_pil_image()
                screenshot_base64 = cached_frame.data
                if screenshot_base64 and screenshot_base64.startswith("data:"):
                    screenshot_base64 = screenshot_base64.split(",", 1)[1]
                from_cache = True
                logger.info(f"[handle_read_screen] Using cached frame for monitor {monitor_id} (age: {cached_frame.age_ms:.0f}ms)")
        except ImportError:
            pass  # StreamFrameCache not available
        except Exception as cache_error:
            logger.debug(f"[handle_read_screen] Cache lookup failed: {cache_error}")

        # Fall back to pyautogui screenshot if no cached frame
        if screenshot is None:
            if region:
                x = region.get('x', 0)
                y = region.get('y', 0)
                width = region.get('width', 800)
                height = region.get('height', 600)
                screenshot = pyautogui.screenshot(region=(x, y, width, height))
            else:
                screenshot = pyautogui.screenshot()

        # Convert to base64 for potential vision processing
        buffer = io.BytesIO()
        screenshot.save(buffer, format='PNG')
        screenshot_base64 = base64.b64encode(buffer.getvalue()).decode('utf-8')

        text_content = ""
        ocr_method = "none"

        # Try pytesseract first (more reliable, local)
        try:
            import pytesseract
            import shutil
            # Auto-detect Tesseract path or use environment variable
            tesseract_path = os.getenv("TESSERACT_PATH") or shutil.which("tesseract")
            if tesseract_path:
                pytesseract.pytesseract.tesseract_cmd = tesseract_path
            text_content = pytesseract.image_to_string(screenshot)
            ocr_method = "pytesseract"
        except ImportError:
            pass  # pytesseract not installed, try MoireServer
        except Exception as tess_error:
            pass  # pytesseract failed, try MoireServer

        # If pytesseract failed, try MoireServer as fallback
        if not text_content.strip():
            try:
                from bridge.websocket_client import MoireWebSocketClient
                client = MoireWebSocketClient(host="localhost", port=8766)
                await asyncio.wait_for(client.connect(), timeout=5.0)

                # Wait for complete capture with OCR (timeout 60s for large screens)
                result = await client.capture_and_wait_for_complete(timeout=60.0)

                if result.success and result.ui_context:
                    # Extract texts from UIContext elements
                    texts = []
                    for element in result.ui_context.elements:
                        if element.text:
                            texts.append(element.text)
                    text_content = "\n".join(texts)
                    ocr_method = "moire_server"

                    # Update screenshot from MoireServer if available
                    if result.screenshot_base64:
                        screenshot_base64 = result.screenshot_base64
                        if screenshot_base64.startswith('data:'):
                            screenshot_base64 = screenshot_base64.split(',', 1)[1]

                await client.disconnect()

            except Exception as moire_error:
                import logging
                logging.getLogger(__name__).warning(f"MoireServer fallback failed: {moire_error}")

        result = {
            "success": True,
            "text": text_content,
            "text_length": len(text_content),
            "ocr_method": ocr_method,
            "from_cache": from_cache,
            "monitor_id": monitor_id,
            "screenshot_size": {
                "width": screenshot.width,
                "height": screenshot.height
            }
        }

        # Include base64 screenshot if OCR failed (empty text)
        if not text_content.strip():
            result["screenshot_base64"] = screenshot_base64
            result["note"] = "OCR returned empty. Screenshot base64 included for vision analysis."

        return result

    except Exception as e:
        return {"success": False, "error": str(e)}


# Claude CLI Handlers
async def handle_claude_run(prompt: str, skill: Optional[str] = None, output_format: str = "text") -> Dict[str, Any]:
    """Run a prompt via Claude CLI."""
    try:
        cli = get_claude_cli()

        if not cli.is_available():
            return {
                "success": False,
                "error": "Claude CLI not available. Install with: npm install -g @anthropic-ai/claude-cli"
            }

        # Build command args
        args = []
        if skill:
            args.extend(["--skill", skill])
        if output_format == "json":
            args.append("--output-format=json")

        # Run the command (async)
        result = await cli.run_command(prompt, skill=skill, output_format=output_format)

        return {
            "success": result.get("success", False),
            "output": result.get("output"),
            "error": result.get("error"),
            "prompt": prompt,
            "skill": skill,
            "output_format": output_format
        }

    except Exception as e:
        return {"success": False, "error": str(e)}


async def handle_claude_skill(skill_name: str, inputs: Dict[str, Any], ui_context: Optional[Dict] = None) -> Dict[str, Any]:
    """Execute a Claude Skill with inputs."""
    try:
        cli = get_claude_cli()

        if not cli.is_available():
            return {
                "success": False,
                "error": "Claude CLI not available. Install with: npm install -g @anthropic-ai/claude-cli"
            }

        # Run the skill (async)
        result = await cli.run_skill(skill_name, inputs, ui_context=ui_context)

        return {
            "success": result.get("success", False),
            "output": result.get("output"),
            "error": result.get("error"),
            "skill_name": skill_name,
            "inputs": inputs
        }

    except Exception as e:
        return {"success": False, "error": str(e)}


async def handle_claude_status() -> Dict[str, Any]:
    """Check if Claude CLI is installed and available."""
    try:
        cli = get_claude_cli()

        available = cli.is_available()
        cli_path = cli.cli_path if hasattr(cli, 'cli_path') else None

        result = {
            "success": True,
            "available": available,
            "cli_path": cli_path
        }

        if available:
            # Try to get version or additional info
            try:
                skills = cli.list_skills()
                result["skills_count"] = len(skills)
            except:
                pass

        return result

    except Exception as e:
        return {"success": False, "error": str(e)}


async def handle_vision_analyze(
    prompt: str,
    mode: str = "custom",
    json_output: bool = True,
    monitor_id: int = 0,
    viewport: Dict[str, int] = None
) -> Dict[str, Any]:
    """Analyze screenshot with Gemini Vision AI."""
    import io
    import base64

    try:
        # Try to import vision agent
        try:
            from agents.vision_agent import get_vision_agent
            vision_agent = get_vision_agent()
            has_vision = vision_agent is not None and vision_agent.is_available()
        except ImportError:
            has_vision = False
            vision_agent = None

        if not has_vision:
            return {
                "success": False,
                "error": "Vision agent not available. Check OpenRouter API key and vision_agent.py"
            }

        # Get screenshot from cache or capture directly
        screenshot_bytes = None
        frame_source = None

        # Try backend API first (cached frames from WebSocket stream)
        try:
            import httpx
            async with httpx.AsyncClient(timeout=2.0) as client:
                response = await client.get(
                    f"http://localhost:8007/api/desktop/cached-frame/{monitor_id}",
                    params={"max_age_ms": 500}
                )
                if response.status_code == 200:
                    data = response.json()
                    if data.get("success") and data.get("frame_data"):
                        frame_data = data["frame_data"]
                        # Remove data URL prefix if present
                        if frame_data.startswith("data:"):
                            frame_data = frame_data.split(",", 1)[1]
                        screenshot_bytes = base64.b64decode(frame_data)
                        frame_source = "api_cache"
                        logger.info(f"Using cached frame from API for monitor {monitor_id} (age: {data.get('age_ms', 0):.0f}ms)")
        except Exception as e:
            logger.debug(f"Backend API cache not available: {e}")

        # Fallback: Try local StreamFrameCache (if MCP runs in same process)
        if screenshot_bytes is None:
            try:
                from stream_frame_cache import StreamFrameCache
                cached_frame = StreamFrameCache.get_fresh_frame(
                    monitor_id=monitor_id,
                    max_age_ms=500
                )
                if cached_frame:
                    screenshot_bytes = cached_frame.to_bytes()
                    frame_source = "local_cache"
                    logger.info(f"Using local cached frame for monitor {monitor_id}")
            except Exception as e:
                logger.debug(f"Local StreamFrameCache not available: {e}")

        # Fallback to pyautogui screenshot
        if screenshot_bytes is None:
            import pyautogui
            screenshot = pyautogui.screenshot()
            buffer = io.BytesIO()
            screenshot.save(buffer, format='PNG')
            screenshot_bytes = buffer.getvalue()
            logger.info("Using pyautogui screenshot")

        # Crop to viewport if specified
        viewport_offset = None
        if viewport and screenshot_bytes:
            try:
                from PIL import Image
                img = Image.open(io.BytesIO(screenshot_bytes))
                vx = max(0, min(int(viewport.get("x", 0)), img.width - 1))
                vy = max(0, min(int(viewport.get("y", 0)), img.height - 1))
                vw = min(int(viewport.get("width", img.width)), img.width - vx)
                vh = min(int(viewport.get("height", img.height)), img.height - vy)
                if vw > 50 and vh > 50:  # Minimum 50x50
                    cropped = img.crop((vx, vy, vx + vw, vy + vh))
                    buffer = io.BytesIO()
                    cropped.save(buffer, format='PNG')
                    screenshot_bytes = buffer.getvalue()
                    viewport_offset = {"x": vx, "y": vy, "width": vw, "height": vh}
                    logger.info(f"[vision_analyze] Cropped to viewport ({vx},{vy}) {vw}x{vh}")
            except Exception as e:
                logger.warning(f"[vision_analyze] Viewport crop failed: {e}")

        # Build analysis prompt based on mode
        mode_prompts = {
            "element_detection": f"Find all interactive UI elements (buttons, inputs, links, etc.) on this screen. {prompt}. Return as JSON with elements array containing: type, text, approximate_location (x, y), confidence.",
            "state_analysis": f"Analyze the current state of this screen. {prompt}. Describe what application is shown, what's visible, and the current state.",
            "task_planning": f"Based on this screen, suggest the next automation steps to accomplish: {prompt}. Return as JSON with steps array.",
            "custom": prompt
        }

        analysis_prompt = mode_prompts.get(mode, prompt)

        # Inject viewport offset instructions so vision model reports absolute coordinates
        if viewport_offset:
            analysis_prompt += (
                f"\n\nIMPORTANT: This image is a CROPPED VIEWPORT from screen position "
                f"({viewport_offset['x']},{viewport_offset['y']}) size {viewport_offset['width']}x{viewport_offset['height']}. "
                f"When reporting element coordinates, ADD the offset: "
                f"absolute_x = element_x_in_image + {viewport_offset['x']}, "
                f"absolute_y = element_y_in_image + {viewport_offset['y']}. "
                f"All coordinates in your response MUST be absolute screen coordinates."
            )

        # Run vision analysis
        result = await vision_agent.analyze_with_prompt(
            screenshot_bytes,
            analysis_prompt
        )

        ret = {
            "success": True,
            "analysis": result,
            "mode": mode,
            "monitor_id": monitor_id,
            "source": frame_source if frame_source else "pyautogui"
        }
        if viewport_offset:
            ret["viewport"] = viewport_offset
        return ret

    except Exception as e:
        logger.error(f"Vision analysis failed: {e}")
        return {
            "success": False,
            "error": str(e),
            "mode": mode,
            "monitor_id": monitor_id
        }


# ============================================
# Clawdbot Tool Handlers
# ============================================

CLAWDBOT_API_BASE = "http://localhost:8007/api/clawdbot"


async def handle_clawdbot_send_message(
    recipient: str,
    message: str,
    platform: Optional[str] = None
) -> Dict[str, Any]:
    """Send a message to a contact via Clawdbot."""
    try:
        import httpx

        async with httpx.AsyncClient(timeout=10.0) as client:
            # Resolve contact first
            params = {}
            if platform:
                params["platform"] = platform

            resolve_resp = await client.post(
                f"{CLAWDBOT_API_BASE}/contacts/{recipient}/resolve",
                params=params
            )

            if resolve_resp.status_code == 404:
                return {
                    "success": False,
                    "error": f"Contact '{recipient}' not found",
                    "suggestion": "Use clawdbot_get_contacts to search for contacts"
                }

            resolve_data = resolve_resp.json()

            if not resolve_data.get("found"):
                return {
                    "success": False,
                    "error": f"Contact '{recipient}' not found",
                    "suggestions": resolve_data.get("suggestions", [])
                }

            contact = resolve_data["contact"]
            contact_name = contact.get("name", recipient)

            # Determine platform and recipient_id
            target_platform = platform
            recipient_id = None

            if platform:
                recipient_id = contact.get(platform.lower())

            if not recipient_id:
                for p in ["whatsapp", "telegram", "discord", "signal", "imessage", "email"]:
                    if contact.get(p):
                        target_platform = p
                        recipient_id = contact[p]
                        break

            if not recipient_id:
                return {
                    "success": False,
                    "error": f"Contact '{contact_name}' has no messaging platform configured"
                }

            # Send via the command endpoint
            cmd_resp = await client.post(
                f"{CLAWDBOT_API_BASE}/command",
                json={
                    "command": f"send to {recipient} {message}",
                    "user_id": "mcp_agent",
                    "platform": target_platform
                }
            )

            result = cmd_resp.json()

            return {
                "success": result.get("success", False),
                "message": result.get("message", ""),
                "recipient": contact_name,
                "platform": target_platform,
                "recipient_id": recipient_id,
                "data": result.get("data")
            }

    except ImportError:
        return {"success": False, "error": "httpx not installed"}
    except Exception as e:
        return {"success": False, "error": str(e)}


async def handle_clawdbot_get_contacts(
    query: Optional[str] = None,
    limit: int = 10
) -> Dict[str, Any]:
    """List or search contacts."""
    try:
        import httpx

        async with httpx.AsyncClient(timeout=10.0) as client:
            if query:
                resp = await client.get(
                    f"{CLAWDBOT_API_BASE}/contacts/search",
                    params={"q": query, "limit": limit}
                )
            else:
                resp = await client.get(f"{CLAWDBOT_API_BASE}/contacts")

            if resp.status_code != 200:
                return {"success": False, "error": f"API error: {resp.status_code}"}

            data = resp.json()

            return {
                "success": True,
                "query": query,
                "contacts": data
            }

    except Exception as e:
        return {"success": False, "error": str(e)}


async def handle_clawdbot_get_status() -> Dict[str, Any]:
    """Get Clawdbot bridge status."""
    try:
        import httpx

        async with httpx.AsyncClient(timeout=5.0) as client:
            status_resp = await client.get(f"{CLAWDBOT_API_BASE}/status")
            status_data = status_resp.json() if status_resp.status_code == 200 else {}

            sessions_resp = await client.get(f"{CLAWDBOT_API_BASE}/sessions")
            sessions_data = sessions_resp.json() if sessions_resp.status_code == 200 else []

            return {
                "success": True,
                "bridge_status": status_data.get("status", "unknown"),
                "initialized": status_data.get("initialized", False),
                "active_sessions": len(sessions_data),
                "sessions": sessions_data,
                "capabilities": status_data.get("capabilities", [])
            }

    except Exception as e:
        return {"success": False, "error": str(e)}


async def handle_clawdbot_get_variables(
    include_templates: bool = True
) -> Dict[str, Any]:
    """Get variables and templates."""
    try:
        import httpx

        async with httpx.AsyncClient(timeout=5.0) as client:
            var_resp = await client.get(f"{CLAWDBOT_API_BASE}/variables")
            variables = var_resp.json() if var_resp.status_code == 200 else {}

            result = {
                "success": True,
                "variables": variables
            }

            if include_templates:
                tmpl_resp = await client.get(f"{CLAWDBOT_API_BASE}/templates")
                result["templates"] = tmpl_resp.json() if tmpl_resp.status_code == 200 else {}

            return result

    except Exception as e:
        return {"success": False, "error": str(e)}


async def handle_clawdbot_browser_open(url: str) -> Dict[str, Any]:
    """Open a URL in the browser via Clawdbot."""
    try:
        import httpx

        if not url.startswith("http://") and not url.startswith("https://"):
            url = f"https://{url}"

        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"{CLAWDBOT_API_BASE}/command",
                json={"command": f"open {url}", "user_id": "mcp_agent", "platform": "browser"}
            )
            result = resp.json()

        return {"success": result.get("success", False), "message": result.get("message", ""), "url": url}
    except Exception as e:
        return {"success": False, "error": str(e)}


async def handle_clawdbot_browser_search(query: str) -> Dict[str, Any]:
    """Search the web via Clawdbot."""
    try:
        import httpx

        search_url = f"https://www.google.com/search?q={query.replace(' ', '+')}"

        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"{CLAWDBOT_API_BASE}/command",
                json={"command": f"open {search_url}", "user_id": "mcp_agent", "platform": "browser"}
            )
            result = resp.json()

        return {"success": result.get("success", False), "message": result.get("message", ""), "query": query, "url": search_url}
    except Exception as e:
        return {"success": False, "error": str(e)}


async def handle_clawdbot_browser_read_page() -> Dict[str, Any]:
    """Read current browser page via screen OCR."""
    try:
        result = await handle_read_screen(monitor_id=0)
        result.pop("screenshot_base64", None)
        return {
            "success": result.get("success", False),
            "text": result.get("text", ""),
            "text_length": result.get("text_length", 0),
            "source": "screen_ocr"
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


async def handle_clawdbot_report_findings(
    findings: str,
    recipient: Optional[str] = None,
    platform: Optional[str] = None,
    title: Optional[str] = None
) -> Dict[str, Any]:
    """Report findings via Clawdbot - either to a contact or via callback."""
    try:
        import httpx

        # Format the report message
        report_msg = ""
        if title:
            report_msg = f"📋 {title}\n\n"
        report_msg += findings

        # Truncate if too long
        if len(report_msg) > 4000:
            report_msg = report_msg[:3950] + "\n\n... [gekürzt]"

        if recipient:
            # Send to a specific contact
            result = await handle_clawdbot_send_message(
                recipient=recipient,
                message=report_msg,
                platform=platform
            )
            result["report_type"] = "contact_message"
            return result
        else:
            # Send via Clawdbot callback
            callback_payload = {
                "user_id": "mcp_agent",
                "platform": platform or "api",
                "success": True,
                "message": report_msg,
                "data": {
                    "type": "findings_report",
                    "title": title,
                    "findings_length": len(findings)
                }
            }

            async with httpx.AsyncClient(timeout=10.0) as client:
                # Try Clawdbot Gateway callback
                try:
                    resp = await client.post(
                        "http://localhost:18789/plugins/automation-ui/results",
                        json=callback_payload
                    )
                    if resp.status_code == 200:
                        return {
                            "success": True,
                            "message": "Findings reported via Clawdbot callback",
                            "report_type": "callback",
                            "findings_length": len(findings)
                        }
                except Exception:
                    pass

                # Fallback: notify via Clawdbot API
                try:
                    resp = await client.post(
                        f"{CLAWDBOT_API_BASE}/notify",
                        params={
                            "user_id": "mcp_agent",
                            "platform": platform or "api",
                            "message": report_msg,
                            "notification_type": "info"
                        }
                    )
                    if resp.status_code == 200:
                        return {
                            "success": True,
                            "message": "Findings sent as notification",
                            "report_type": "notification",
                            "findings_length": len(findings)
                        }
                except Exception:
                    pass

                return {
                    "success": True,
                    "message": "Findings captured (no callback endpoint available)",
                    "report_type": "local",
                    "findings_preview": findings[:500],
                    "findings_length": len(findings)
                }

    except Exception as e:
        return {"success": False, "error": str(e)}


# Create MCP server
server = Server("handoff")


@server.list_tools()
async def list_tools() -> List[Tool]:
    """List available tools."""
    return TOOLS


@server.call_tool()
async def call_tool(name: str, arguments: Dict[str, Any]) -> List[TextContent]:
    """Handle tool calls."""
    try:
        if name == "handoff_plan":
            result = await handle_plan(
                goal=arguments.get("goal", ""),
                context=arguments.get("context")
            )
        elif name == "handoff_execute":
            result = await handle_execute(
                plan=arguments.get("plan", [])
            )
        elif name == "handoff_validate":
            result = await handle_validate(
                target=arguments.get("target", ""),
                expected_state=arguments.get("expected_state")
            )
        elif name == "handoff_action":
            # LLMs frequently pass action params at the top level (e.g.
            # ``{"action_type": "type", "text": "Hi"}``) instead of nested
            # in ``params``. Be tolerant: collect any non-action_type key
            # as a fallback ``params`` so the call still works.
            action_type = arguments.get("action_type", "")
            params = arguments.get("params")
            if params is None or not params:
                params = {k: v for k, v in arguments.items() if k != "action_type"}
            result = await handle_action(action_type=action_type, params=params)
        elif name == "handoff_status":
            result = await handle_status()
        elif name == "handoff_read_screen":
            result = await handle_read_screen(
                region=arguments.get("region"),
                monitor_id=arguments.get("monitor_id", 0)
            )
        elif name == "handoff_get_focus":
            result = await handle_get_focus()
        elif name == "handoff_scroll":
            result = await handle_scroll(
                direction=arguments.get("direction", "down"),
                amount=arguments.get("amount", 3),
                x=arguments.get("x"),
                y=arguments.get("y")
            )
        # Claude CLI Tools
        elif name == "claude_cli_run":
            result = await handle_claude_run(
                prompt=arguments.get("prompt", ""),
                skill=arguments.get("skill"),
                output_format=arguments.get("output_format", "text")
            )
        elif name == "claude_cli_skill":
            result = await handle_claude_skill(
                skill_name=arguments.get("skill_name", ""),
                inputs=arguments.get("inputs", {}),
                ui_context=arguments.get("ui_context")
            )
        elif name == "claude_cli_status":
            result = await handle_claude_status()
        # Vision Analysis Tool
        elif name == "vision_analyze":
            result = await handle_vision_analyze(
                prompt=arguments.get("prompt", ""),
                mode=arguments.get("mode", "custom"),
                json_output=arguments.get("json_output", True),
                monitor_id=arguments.get("monitor_id", 0)
            )
        # Clawdbot Messaging Tools
        elif name == "clawdbot_send_message":
            result = await handle_clawdbot_send_message(
                recipient=arguments.get("recipient", ""),
                message=arguments.get("message", ""),
                platform=arguments.get("platform")
            )
        elif name == "clawdbot_get_contacts":
            result = await handle_clawdbot_get_contacts(
                query=arguments.get("query"),
                limit=arguments.get("limit", 10)
            )
        elif name == "clawdbot_get_status":
            result = await handle_clawdbot_get_status()
        elif name == "clawdbot_get_variables":
            result = await handle_clawdbot_get_variables(
                include_templates=arguments.get("include_templates", True)
            )
        # Clawdbot Browser Tools
        elif name == "clawdbot_browser_open":
            result = await handle_clawdbot_browser_open(
                url=arguments.get("url", "")
            )
        elif name == "clawdbot_browser_search":
            result = await handle_clawdbot_browser_search(
                query=arguments.get("query", "")
            )
        elif name == "clawdbot_browser_read_page":
            result = await handle_clawdbot_browser_read_page()
        elif name == "clawdbot_report_findings":
            result = await handle_clawdbot_report_findings(
                findings=arguments.get("findings", ""),
                recipient=arguments.get("recipient"),
                platform=arguments.get("platform"),
                title=arguments.get("title")
            )
        # Clarify (HTML form for credentials / structured user input)
        elif name == "handoff_clarify":
            from agents.handoff.clarify_notify import handoff_clarify as _clarify
            result = _clarify(
                question=arguments.get("question", ""),
                options=arguments.get("options"),
                form_schema=arguments.get("form_schema"),
            )
        elif name == "handoff_clarify_check":
            from agents.handoff.clarify_notify import handoff_clarify_check as _clarify_check
            result = _clarify_check(clarify_id=arguments.get("clarify_id", ""))
        elif name == "handoff_approval_request":
            from agents.handoff.clarify_notify import handoff_approval_request as _approve
            result = _approve(
                action=arguments.get("action", ""),
                reason=arguments.get("reason"),
                timeout_seconds=int(arguments.get("timeout_seconds", 60)),
                default_on_timeout=arguments.get("default_on_timeout", "approved"),
            )
        # Adaptive-skill library lookups
        elif name == "skill_search":
            result = _skill_search(
                query=arguments.get("query", ""),
                agent=arguments.get("agent"),
                limit=int(arguments.get("limit", 5)),
            )
        elif name == "skill_list":
            result = _skill_list(app=arguments.get("app"))
        elif name == "excel_paste_table":
            result = _excel_paste_table(
                rows=arguments.get("rows") or [],
                start_cell=arguments.get("start_cell"),
            )
        elif name == "window_maximize":
            result = _window_maximize(title_substring=arguments.get("title_substring", ""))
        elif name == "xlsx_create_from_data":
            result = _xlsx_create_from_data(
                file_path=arguments.get("file_path", ""),
                rows=arguments.get("rows"),
                sheet_name=arguments.get("sheet_name"),
                bold_rows=arguments.get("bold_rows"),
                auto_width=arguments.get("auto_width", True),
                overwrite=arguments.get("overwrite", True),
                sheets=arguments.get("sheets"),
                cell_styles=arguments.get("cell_styles"),
                freeze_pane=arguments.get("freeze_pane"),
            )
        elif name == "docx_create_from_data":
            result = _docx_create_from_data(
                file_path=arguments.get("file_path", ""),
                blocks=arguments.get("blocks") or [],
                overwrite=arguments.get("overwrite", True),
            )
        elif name == "csv_create_from_data":
            result = _csv_create_from_data(
                file_path=arguments.get("file_path", ""),
                rows=arguments.get("rows") or [],
                delimiter=arguments.get("delimiter", ","),
                encoding=arguments.get("encoding", "utf-8-sig"),
                overwrite=arguments.get("overwrite", True),
            )
        elif name == "docx_verify_file":
            result = _docx_verify_file(
                file_path=arguments.get("file_path", ""),
                expected_substrings=arguments.get("expected_substrings"),
                min_paragraphs=arguments.get("min_paragraphs"),
                min_tables=arguments.get("min_tables"),
                expected_table_cells=arguments.get("expected_table_cells"),
            )
        elif name == "rowboat_upload":
            result = _rowboat_upload(
                file_path=arguments.get("file_path", ""),
                title=arguments.get("title"),
                tags=arguments.get("tags"),
            )
        elif name == "rowboat_chat":
            result = _rowboat_chat(
                message=arguments.get("message", ""),
                context=arguments.get("context", "default"),
                conversation_id=arguments.get("conversation_id"),
                timeout=int(arguments.get("timeout", 120)),
            )
        elif name == "rowboat_search":
            result = _rowboat_search(
                query=arguments.get("query", ""),
                folder=arguments.get("folder"),
                limit=int(arguments.get("limit", 20)),
            )
        elif name == "rowboat_list_folders":
            result = _rowboat_list_folders(limit=int(arguments.get("limit", 50)))
        elif name == "roarboot_list_folders":
            result = _roarboot_list_folders(root=arguments.get("root"))
        elif name == "roarboot_ask":
            result = _roarboot_ask(
                question=arguments.get("question", ""),
                folder=arguments.get("folder"),
                max_files=int(arguments.get("max_files", 10)),
                max_chars_per_file=int(arguments.get("max_chars_per_file", 4000)),
                model=arguments.get("model", "gpt-4o-mini"),
                root=arguments.get("root"),
            )
        elif name == "file_evaluate":
            result = _file_evaluate(
                file_path=arguments.get("file_path", ""),
                expected_intent=arguments.get("expected_intent", ""),
                source_data_description=arguments.get("source_data_description"),
                criteria=arguments.get("criteria"),
                model=arguments.get("model", "gpt-4o-mini"),
            )
        elif name == "roarboot_read_knowledge":
            result = _roarboot_read_knowledge(
                folder=arguments.get("folder", ""),
                query=arguments.get("query"),
                limit=int(arguments.get("limit", 20)),
                name_pattern=arguments.get("name_pattern"),
                root=arguments.get("root"),
            )
        elif name == "excel_verify_file":
            result = _excel_verify_file(
                file_path=arguments.get("file_path", ""),
                expected_cells=arguments.get("expected_cells"),
                min_rows=arguments.get("min_rows"),
                must_contain_text=arguments.get("must_contain_text"),
            )
        elif name == "skill_save_and_index":
            result = _skill_save_and_index(
                app=arguments.get("app", ""),
                skill_name=arguments.get("skill_name", ""),
                frontmatter=arguments.get("frontmatter") or {},
                body=arguments.get("body", ""),
            )
        # App lifecycle
        elif name == "app_launch":
            result = _app_launch(app_name=arguments.get("app", ""), args=arguments.get("args"))
        elif name == "app_focus":
            result = _app_focus(title_substring=arguments.get("title_substring", ""))
        elif name == "app_list_running":
            result = _app_list_running()
        elif name == "app_launch_or_focus":
            result = _app_launch_or_focus(
                app_name=arguments.get("app", ""),
                title_hint=arguments.get("title_hint"),
                args=arguments.get("args"),
            )
        else:
            result = {"error": f"Unknown tool: {name}"}

        return [TextContent(type="text", text=json.dumps(result, indent=2))]

    except Exception as e:
        return [TextContent(type="text", text=json.dumps({"error": str(e)}))]


async def cleanup():
    """Clean up resources on shutdown."""
    global _planning_team, _validation_team, _runtime

    logger.info("Starting cleanup...")

    if _planning_team:
        if hasattr(_planning_team, 'llm_client') and _planning_team.llm_client:
            await _planning_team.llm_client.close()
        await _planning_team.stop()
        _planning_team = None
        logger.info("Planning team stopped")

    if _validation_team:
        await _validation_team.stop()
        _validation_team = None
        logger.info("Validation team stopped")

    if _runtime:
        await _runtime.stop()
        _runtime = None
        logger.info("Runtime stopped")

    logger.info("Cleanup complete")


# Global shutdown flag
_shutdown_requested = False


def signal_handler(signum, frame):
    """Handle shutdown signals for graceful termination."""
    global _shutdown_requested
    signal_name = signal.Signals(signum).name
    logger.info(f"Received signal {signal_name}, initiating graceful shutdown...")
    _shutdown_requested = True


def setup_signal_handlers():
    """Setup signal handlers for graceful shutdown."""
    if sys.platform == 'win32':
        # Windows: Handle SIGINT (Ctrl+C) and SIGBREAK (Ctrl+Break)
        signal.signal(signal.SIGINT, signal_handler)
        try:
            signal.signal(signal.SIGBREAK, signal_handler)
        except AttributeError:
            pass  # SIGBREAK not available on all platforms
    else:
        # Unix: Handle SIGTERM and SIGINT
        signal.signal(signal.SIGTERM, signal_handler)
        signal.signal(signal.SIGINT, signal_handler)


async def main():
    """Run the MCP server."""
    # Setup signal handlers
    setup_signal_handlers()

    # Log startup
    logger.info("=" * 50)
    logger.info("Handoff MCP Server starting...")
    logger.info(f"Python: {sys.version}")
    logger.info(f"Platform: {sys.platform}")
    if _config:
        logger.info(f"Config loaded: MoireServer {_config.moire_host}:{_config.moire_port}")
    logger.info("=" * 50)

    # Record start time
    start_time = datetime.now()

    try:
        async with stdio_server() as (read_stream, write_stream):
            logger.info("MCP stdio server started, ready for connections")
            await server.run(read_stream, write_stream, server.create_initialization_options())
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received")
    except Exception as e:
        logger.error(f"Server error: {e}")
        raise
    finally:
        # Log uptime
        uptime = datetime.now() - start_time
        logger.info(f"Server uptime: {uptime}")

        # Cleanup
        await cleanup()
        logger.info("Handoff MCP Server shutdown complete")


if __name__ == "__main__":
    asyncio.run(main())
