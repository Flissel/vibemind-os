"""SoM Planner — Tool-Set für die Agents.

Jedes Tool ist eine reine Funktion (kein AutoGen-Import), später als AutoGen-Tool
ODER Brain-Capability gebunden. Eigenständig testbar.

- think:           no-op Scratchpad (gibt Gedanke zurück, kein Seiteneffekt)
- plan_read/write: State-Zugriff (delegiert an state.py)
- capability_list: liest brain capabilities.yaml (Name + description)
- skill_search:    sucht relevante Skills (fungus falls da, sonst SKILL.md-Scan)
"""

from __future__ import annotations

import importlib.util
import os
import re
from pathlib import Path
from typing import Any

import yaml

_PLANNER_ROOT = Path(__file__).resolve().parents[1]
_REPO = _PLANNER_ROOT.parents[2]   # vibemind-os/

# state.py als Modul laden (gleicher _lib-Ordner)
_spec = importlib.util.spec_from_file_location("som_state", _PLANNER_ROOT / "_lib" / "state.py")
_state = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_state)


# ── think ───────────────────────────────────────────────────────────────────
def think(thought: str) -> str:
    """No-op Scratchpad. Der Agent denkt 'laut'; Rückgabe = der Gedanke selbst.
    Landet NICHT im finalen JSON-Output, nur im Konversations-Verlauf."""
    return f"[think] {thought}"


# ── State ────────────────────────────────────────────────────────────────────
def plan_read(run_id: str, kind: str) -> dict[str, Any]:
    return _state.plan_read(run_id, kind)


def plan_write(run_id: str, kind: str, data: dict[str, Any]) -> dict[str, Any]:
    return _state.plan_write(run_id, kind, data)


# ── capability_list ──────────────────────────────────────────────────────────
def _capabilities_path() -> Path:
    env = os.environ.get("BRAIN_CAPABILITIES_YAML")
    if env and Path(env).exists():
        return Path(env)
    return _REPO / "brain" / "the_brain" / "data" / "capabilities.yaml"


# ── kind-Heuristik (Fix 2026-06-02) ──────────────────────────────────────────
# capabilities.yaml hat KEIN Feld das sagt was eine Capability *produziert*
# (verifiziert: 9 Felder, kein produces/output_type). Folge: der Planner wählt
# z.B. knowledge_query (RAG/Search) für "CV entwerfen" (Content-Erstellung). Wir
# leiten ein `kind` heuristisch aus name+description+execution_target ab — eine
# kleine Keyword-Tabelle, kein LLM. Nicht-invasiv; capabilities.yaml bleibt
# unangetastet. (Später optional: explizites kind:-Feld dort pflegen.)
#
# kind ∈ {search, content, file, action, orchestration, plan}
_KIND_RULES: list[tuple[str, list[str]]] = [
    # Reihenfolge = Priorität (spezifisch vor generisch)
    ("plan",          ["plan", "planung", "society-of-mind", "som", "multihop", "orchestr"]),
    ("orchestration", ["coordinat", "dispatch", "delegat", "route", "skill-coordinator", "aggregat"]),
    ("content",       ["schreib", "write", "writer", "verfass", "entwurf", "draft", "generier", "erstell", "compose", "anschreiben", "text erzeug", "summar", "zusammenfass"]),
    # search VOR file: "search/such/query" ist eindeutiger als das breite "code"
    # (code_search ist Suche, nicht Datei-Erstellung).
    ("search",        ["search", "such", "query", "abfrage", "rag", "knowledge", "recall", "lookup", "retriev", "fact", "statist"]),
    ("file",          ["code", "coding", "datei", "file", "speicher", "save", "docx", "pdf", "xlsx", "build", "commit", "patch"]),
    ("action",        ["create", "update", "evaluate", "add", "send", "trigger", "execute", "run", "automat", "click", "browser"]),
]


def _derive_kind(name: str, description: str, execution_target: str | None) -> str:
    """Leitet kind aus name+description+target ab (Keyword-Match, erste Regel
    gewinnt). Fallback 'action' (etwas das einen Effekt hat)."""
    hay = f"{name} {description} {execution_target or ''}".lower()
    for kind, keywords in _KIND_RULES:
        if any(kw in hay for kw in keywords):
            return kind
    return "action"


def capability_list() -> list[dict]:
    """Gibt [{name, description, kind, execution_target, agents}] aller Brain-
    Capabilities zurück. Der Planner nutzt name+description+kind (WAS + WAS es
    PRODUZIERT), der Executor nutzt execution_target + agents (WER/WIE führt es
    aus). `kind` verhindert dass Search-Caps für Content-Erstellung gewählt
    werden (run_0020-Mangel). So muss niemand raten."""
    path = _capabilities_path()
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or []
    out = []
    for c in data:
        if isinstance(c, dict) and c.get("capability"):
            agents = c.get("agents") or {}
            name = c["capability"]
            desc = (c.get("description") or "")[:300]
            target = c.get("execution_target")  # z.B. "supabase:bubble.evaluate" oder None
            out.append({
                "name": name,
                "description": desc,
                "kind": c.get("kind") or _derive_kind(name, desc, target),  # explizit > heuristisch
                "execution_target": target,
                "agents": (agents.get("primary") or []) if isinstance(agents, dict) else [],
            })
    return out


# ── agent_list ───────────────────────────────────────────────────────────────
def _agents_dir() -> Path:
    env = os.environ.get("OPENFANG_AGENTS_DIR")
    if env and Path(env).exists():
        return Path(env)
    return Path(os.path.expanduser("~/.openfang/agents"))


def agent_list(query: str = "", limit: int = 0) -> list[dict[str, str]]:
    """Gibt [{name, description}] aller OpenFang-Agents (aus ~/.openfang/agents/
    */agent.toml). Damit kann der Executor den KONKRETEN Agent für eine Capability
    auswählen (z.B. coding_task → openclaude-coder, weil dessen Beschreibung
    'Coding-Agent' sagt). Optional query filtert per Stichwort, limit begrenzt."""
    d = _agents_dir()
    if not d.exists():
        return []
    results = []
    for toml in sorted(d.glob("*/agent.toml")):
        try:
            text = toml.read_text(encoding="utf-8", errors="ignore")
        except Exception:  # noqa: BLE001
            continue
        name = toml.parent.name
        # description aus der toml (erste description-Zeile, doppelte Quotes)
        desc = ""
        for line in text.splitlines():
            ls = line.strip()
            if ls.startswith("description"):
                # description = "..."
                parts = ls.split("=", 1)
                if len(parts) == 2:
                    desc = parts[1].strip().strip('"').strip("'")[:200]
                break
        results.append({"name": name, "description": desc})
    if query:
        q = set(re.findall(r"\w+", query.lower()))
        scored = []
        for r in results:
            hay = (r["name"] + " " + r["description"]).lower()
            score = sum(1 for t in q if t in hay)
            if score > 0:
                scored.append((score, r))
        scored.sort(key=lambda x: x[0], reverse=True)
        results = [r for _, r in scored]
    return results[:limit] if limit else results


# ── skill_search ─────────────────────────────────────────────────────────────
def skill_search(query: str, limit: int = 8) -> list[dict[str, str]]:
    """Sucht relevante Skills für 'welche Daten braucht ein guter Plan'.
    Primär: Scan über skills/**/SKILL.md (description-Match). Fungus-MCP wäre
    besser, ist aber nicht immer verbunden — Datei-Scan ist der robuste Fallback."""
    skills_root = _REPO / "skills"
    if not skills_root.exists():
        return []
    q_tokens = set(re.findall(r"\w+", query.lower()))
    results = []
    for skill_md in skills_root.rglob("SKILL.md"):
        try:
            text = skill_md.read_text(encoding="utf-8", errors="ignore")
        except Exception:  # noqa: BLE001
            continue
        # description aus Frontmatter
        m = re.search(r"description:\s*(.+)", text)
        desc = (m.group(1).strip() if m else "")[:200]
        name_m = re.search(r"name:\s*(.+)", text)
        name = name_m.group(1).strip() if name_m else skill_md.parent.name
        hay = (name + " " + desc).lower()
        score = sum(1 for t in q_tokens if t in hay)
        if score > 0:
            results.append({"name": name, "description": desc, "score": score})
    results.sort(key=lambda r: r["score"], reverse=True)
    return results[:limit]


TOOLS = {
    "think": think,
    "plan_read": plan_read,
    "plan_write": plan_write,
    "capability_list": capability_list,
    "agent_list": agent_list,
    "skill_search": skill_search,
}


if __name__ == "__main__":
    import tempfile
    os.environ["SOM_STATE_ROOT"] = tempfile.mkdtemp()
    assert think("hallo").startswith("[think]")
    caps = capability_list()
    print(f"capability_list: {len(caps)} caps")
    assert len(caps) > 0, "keine capabilities gefunden"
    skills = skill_search("plan jobcenter antrag")
    print(f"skill_search('plan jobcenter antrag'): {len(skills)} hits")
    _state.run_create("t", "x")
    plan_write("t", "plan", {"steps": []})
    assert plan_read("t", "plan")["_meta"]["version"] == 1
    print("tools.py selftest OK")
