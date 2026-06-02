"""SoM Planner — FastMCP-Server.

Exponiert die SoM-Tools, damit ein claude-code-Agent SELBST Daten beschaffen,
denken und den Plan iterativ updaten kann (aktive Society-of-Mind-Agents statt
vorgekautem Kontext).

Tools:
  think(thought)              — Scratchpad (gibt Gedanke zurück)
  data_read(path)             — beliebige Datei/YAML aus erlaubten Roots lesen
  capability_list()           — Brain-Capabilities (Name+Beschreibung)
  skill_search(query)         — Skills per SKILL.md-Scan finden
  plan_read(run_id, kind)     — plan/exec/verdict/matrix.yaml lesen
  plan_write(run_id, kind, …) — schreiben + Versions-Bump

Start (global Python hat mcp):
  python spaces/autogen/planner/mcp_server.py
Wird vom Agent via --mcp-config als stdio-Server gestartet.

Sicherheit: data_read ist auf erlaubte Roots beschränkt (Akte + Repo-Teile),
kein beliebiger FS-Zugriff.
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

_PLANNER = Path(__file__).resolve().parent
_REPO = _PLANNER.parents[2]   # vibemind-os/


def _load(name: str, file: Path):
    spec = importlib.util.spec_from_file_location(name, file)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_state = _load("som_state", _PLANNER / "_lib" / "state.py")
_tools = _load("som_tools", _PLANNER / "_lib" / "tools.py")

_HOME = Path(os.path.expanduser("~"))

# Erlaubte LESE-Roots — generischer Wissens-Scope für beliebige Plan-Typen.
# Optional via env SOM_ALLOWED_ROOTS (os.pathsep-getrennt) erweiterbar/ersetzbar.
_DEFAULT_READ_ROOTS = [
    _HOME / ".rowboat" / "knowledge",   # persönlicher Wissensgraph (People, Projects, Topics...)
    _HOME / ".rowboat" / "projects",    # Projekt-Daten
    _HOME / ".rowboat" / "bases",       # Datenbanken
    _HOME / "Documents" / "Buergergeld",  # Akten
    _REPO,                               # vibemind-os Code (für Code-Pläne)
    _PLANNER / "state",                  # Planner-State
]


def _read_roots() -> list[Path]:
    env = os.environ.get("SOM_ALLOWED_ROOTS")
    if env:
        return [Path(p).expanduser() for p in env.split(os.pathsep) if p.strip()]
    return _DEFAULT_READ_ROOTS


# HARTE Deny-Liste: nie lesen, nie schreiben — auch wenn unter einem Allow-Root.
# Secrets, Credentials, Keys. Substring-Match auf den normalisierten Pfad (lower).
_DENY_SUBSTRINGS = [
    ".rowboat/config", ".rowboat\\config",   # OAuth-Credentials (gdrive/gmail/mcp)
    "credential", "secret", ".env", "/.ssh", "\\.ssh",
    "token.json", ".pem", ".key", "password", "daemon.json",
]

# Schreib-Roots: enger als Lesen. Planner-State + Wissensgraph (kein config!).
_WRITE_ROOTS = [
    _PLANNER / "state",
    _HOME / ".rowboat" / "knowledge",
]


def _denied(p: Path) -> bool:
    s = str(p).replace("\\", "/").lower()
    return any(d.replace("\\", "/") in s for d in _DENY_SUBSTRINGS)


def _under(p: Path, roots: list[Path]) -> bool:
    rp = str(p.resolve())
    return any(rp.startswith(str(r.resolve())) for r in roots)

mcp = FastMCP("SoM Planner Tools")


@mcp.tool()
def think(thought: str) -> str:
    """Scratchpad zum lauten Denken. Gibt den Gedanken zurück, kein Seiteneffekt.
    Nutze das um zu reflektieren welche Daten du brauchst, bevor du planst."""
    return f"[think] {thought}"


@mcp.tool()
def data_read(path: str, max_chars: int = 6000) -> str:
    """Liest eine Datei aus dem Wissens-Scope (~/.rowboat/knowledge, projects, bases,
    ~/Documents/Buergergeld, vibemind-os Code, planner/state). Credentials/Secrets
    sind gesperrt. Nutze das um zu sehen WELCHE Daten vorliegen, bevor du 'fehlt' annimmst."""
    p = Path(path).expanduser().resolve()
    if _denied(p):
        return f"[error] gesperrt (Secret/Credential): {p}"
    if not _under(p, _read_roots()):
        return f"[error] Pfad nicht im Wissens-Scope: {p}"
    if not p.exists():
        return f"[error] nicht gefunden: {p}"
    try:
        return p.read_text(encoding="utf-8", errors="ignore")[:max_chars]
    except Exception as e:  # noqa: BLE001
        return f"[error] {type(e).__name__}: {e}"


@mcp.tool()
def data_list(directory: str) -> list[str]:
    """Listet Einträge in einem Verzeichnis des Wissens-Scopes. Nutze das um zu
    sehen welche Dokumente/Wissens-Knoten vorliegen (z.B. ~/.rowboat/knowledge/People)."""
    p = Path(directory).expanduser().resolve()
    if _denied(p):
        return [f"[error] gesperrt: {p}"]
    if not _under(p, _read_roots()):
        return [f"[error] Pfad nicht im Wissens-Scope: {p}"]
    if not p.exists():
        return [f"[error] nicht gefunden: {p}"]
    try:
        return [(f.name + "/" if f.is_dir() else f.name) for f in sorted(p.iterdir())]
    except Exception as e:  # noqa: BLE001
        return [f"[error] {e}"]


@mcp.tool()
def knowledge_write(path: str, content: str) -> str:
    """Schreibt eine Erkenntnis in den Wissensgraph (~/.rowboat/knowledge) oder den
    Planner-State. NUR dort erlaubt, NIE in config/Secrets. Bestehende Dateien werden
    vor dem Überschreiben nach .backups/ gesichert. Nutze das um gewonnenes Wissen
    persistent abzulegen."""
    p = Path(path).expanduser().resolve()
    if _denied(p):
        return f"[error] gesperrt (Secret/Credential): {p}"
    if not _under(p, _WRITE_ROOTS):
        return (f"[error] Schreiben nur in {[str(r) for r in _WRITE_ROOTS]} erlaubt, "
                f"nicht: {p}")
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        if p.exists():
            bdir = p.parent / ".backups"
            bdir.mkdir(exist_ok=True)
            import shutil
            # Backup-Name ohne Date.now (Prozess-Zeit ok hier): mtime-basiert
            mt = int(p.stat().st_mtime)
            shutil.copy2(p, bdir / f"{p.name}.{mt}.bak")
        p.write_text(content, encoding="utf-8")
        return f"[ok] geschrieben: {p} ({len(content)} chars)"
    except Exception as e:  # noqa: BLE001
        return f"[error] {type(e).__name__}: {e}"


@mcp.tool()
def capability_list() -> list[dict]:
    """Alle Brain-Capabilities: {name, description, execution_target, agents}.
    Planner nutzt name+description (WAS), Executor nutzt execution_target + agents
    (WER/WIE). Wenn execution_target=null und agents=[], nutze agent_list um den
    passenden Agent selbst zu wählen."""
    return _tools.capability_list()


@mcp.tool()
def agent_list(query: str = "", limit: int = 0) -> list[dict]:
    """Alle OpenFang-Agents {name, description}. Nutze das (besonders als Executor)
    um den KONKRETEN Agent für eine Capability zu wählen, wenn capability_list kein
    execution_target/agents liefert. query filtert per Stichwort (z.B. 'coding',
    'research', 'email'). Beispiel: coding_task → agent_list('coding') → openclaude-coder."""
    return _tools.agent_list(query, limit)


@mcp.tool()
def skill_search(query: str, limit: int = 8) -> list[dict]:
    """Sucht Skills die zu einer Aufgabe passen (welche Daten/Schritte typisch sind)."""
    return _tools.skill_search(query, limit)


@mcp.tool()
def plan_read(run_id: str, kind: str) -> dict:
    """Liest den aktuellen Stand: kind = plan | exec | verdict | matrix."""
    return _state.plan_read(run_id, kind)


@mcp.tool()
def plan_write(run_id: str, kind: str, data: dict) -> dict:
    """Schreibt/aktualisiert plan/exec/verdict/matrix.yaml + bumpt die Version.
    So hältst du den Plan workflow-mäßig aktuell."""
    return _state.plan_write(run_id, kind, data)


if __name__ == "__main__":
    mcp.run()
