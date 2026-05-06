"""Phase 11.A — Spaces-Ideas MCP Server.

Exposes the existing ideas + bubbles tools (in spaces/ideas/tools/*.py) over
the MCP stdio protocol so that:
  - OpenFang agents (brain-ideas, brain-bubbles) can call them via mcp_allowed
  - Claude Code can call them as deferred tools
  - Brain's plan-executor can dispatch to brain-ideas which then resolves
    to one of these MCP tools

Bubble = container for ideas (data-package), so both lives in one MCP
server. Naming convention: bubble.* and idea.* mirror the 137 events
inventory in brain-procedural KG.

Phase 11.F — every tool-call also publishes a space-event to brain's
event bus via _emit() so the dashboard can show live activity.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

# Bootstrap path: voice/python/ is the canonical home where these tools were
# originally written + run (Electron voice backend). It has llm_config.py,
# data/ package, tools/workspace_tools.py — all transitive deps of idea_tools.
_ROOT = Path(__file__).resolve().parents[2]
_VOICE_PY = _ROOT / "voice" / "python"
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_VOICE_PY))

# Phase 11.I — load .env so OPENROUTER_API_KEY / OPENAI_API_KEY / GROQ_API_KEY
# reach idea_tools.expand_ideas / classify_idea / etc. The MCP-server is
# spawned without inherited env, and these tools call OpenRouter/OpenAI directly.
try:
    from dotenv import load_dotenv as _load_dotenv
    _ENV_FILE = _ROOT.parent / ".env"  # vibemind-os/.. = repo root
    if _ENV_FILE.exists():
        _load_dotenv(_ENV_FILE)
        print(f"[spaces-ideas-mcp] loaded env from {_ENV_FILE}", file=sys.stderr)
    else:
        # Fallback: try cwd
        _load_dotenv()
except Exception as _e:
    print(f"[spaces-ideas-mcp] dotenv unavailable: {_e}", file=sys.stderr)

# spaces.ideas.__init__ pulls in heavy swarm-agent code we don't need here.
import types as _types
if "spaces" not in sys.modules:
    sys.modules["spaces"] = _types.ModuleType("spaces")
    sys.modules["spaces"].__path__ = [str(_ROOT / "spaces")]
if "spaces.ideas" not in sys.modules:
    sys.modules["spaces.ideas"] = _types.ModuleType("spaces.ideas")
    sys.modules["spaces.ideas"].__path__ = [str(_ROOT / "spaces" / "ideas")]
if "spaces.ideas.tools" not in sys.modules:
    sys.modules["spaces.ideas.tools"] = _types.ModuleType("spaces.ideas.tools")
    sys.modules["spaces.ideas.tools"].__path__ = [str(_ROOT / "spaces" / "ideas" / "tools")]

from mcp.server.fastmcp import FastMCP

import importlib.util


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_TOOLS_DIR = Path(__file__).resolve().parent / "tools"
bubble_tools = _load("spaces.ideas.tools.bubble_tools", _TOOLS_DIR / "bubble_tools.py")
idea_tools = _load("spaces.ideas.tools.idea_tools", _TOOLS_DIR / "idea_tools.py")

# Phase 11.L — format tools (table / note / action_list / pros_cons / hierarchy
# / specs / kanban / mindmap / swot / user_story / flowchart / convert / revert).
# Lives in tools/format_dispatcher.py with all wrappers around convert_format().
try:
    format_dispatcher = _load(
        "spaces.ideas.tools.format_dispatcher",
        _TOOLS_DIR / "format_dispatcher.py",
    )
except Exception as _e:
    print(f"[spaces-ideas-mcp] format_dispatcher unavailable: {_e}", file=sys.stderr)
    format_dispatcher = None

# Phase 11.F — publish each tool-call to brain's space-event-bus
try:
    publisher = _load("_brain_event_publisher",
                      _TOOLS_DIR / "_brain_event_publisher.py")
    _publish = publisher.publish
except Exception as _e:
    print(f"[spaces-ideas-mcp] event publisher unavailable: {_e}", file=sys.stderr)
    def _publish(*args, **kwargs):
        pass


mcp = FastMCP(
    "Spaces-Ideas",
    instructions=(
        "Tools for the Ideas-Space and its Bubble containers. "
        "Bubbles bundle Ideas — both live here. "
        "Use bubble.* tools to manage bubble containers (create, evaluate, "
        "promote to project). Use idea.* tools to manage ideas inside bubbles "
        "(create, expand, connect, format)."
    ),
)


# ──────────────────────────────────────────────────────────────────────
# Helper: invoke a legacy tool, normalize its return, publish space-event
# ──────────────────────────────────────────────────────────────────────

def _normalize(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        return {"ok": True, "message": raw}
    return {"ok": True, "result": raw}


def _call(event_id: str, fn, params: Dict[str, Any]) -> Dict[str, Any]:
    """Run fn(params), publish to brain event bus, return normalized result."""
    raw = fn(params)
    res = _normalize(raw)
    try:
        result_text = ""
        if isinstance(raw, str):
            result_text = raw[:300]
        elif isinstance(raw, dict):
            result_text = str(raw.get("message") or raw.get("title")
                              or raw.get("ok") or raw)[:300]
        _publish(
            event_id=event_id,
            params=params,
            result=result_text,
            ok=res.get("ok", True),
            source=f"spaces-ideas/{event_id.replace('.', '_')}",
        )
    except Exception:
        pass
    return res


# ══════════════════════════════════════════════════════════════════════
# BUBBLE TOOLS
# ══════════════════════════════════════════════════════════════════════

@mcp.tool()
def bubble_list(limit: int = 20, query: str = "") -> Dict[str, Any]:
    """List all bubbles (idea containers) in the canvas."""
    return _call("bubble.list", bubble_tools.list_bubbles, {"limit": limit, "query": query})


@mcp.tool()
def bubble_find(name: str) -> Dict[str, Any]:
    """Find a bubble by fuzzy name match. Returns bubble_id + metadata."""
    return _call("bubble.find", bubble_tools.find_bubble, {"name": name})


@mcp.tool()
def bubble_create(title: str, description: str = "") -> Dict[str, Any]:
    """Create a new bubble (idea container).

    Args:
        title: Bubble name. Required.
        description: Optional context for what the bubble is about.
    """
    return _call("bubble.create", bubble_tools.create_bubble,
                 {"title": title, "description": description})


@mcp.tool()
def bubble_update(bubble_name: str, new_title: str = "", new_description: str = "") -> Dict[str, Any]:
    """Update an existing bubble's title or description."""
    return _call("bubble.update", bubble_tools.update_bubble, {
        "bubble_name": bubble_name,
        "new_title": new_title,
        "new_description": new_description,
    })


@mcp.tool()
def bubble_get_current() -> Dict[str, Any]:
    """Return the currently-active bubble (the one the user is inside)."""
    cur = bubble_tools.get_current_bubble()
    res = _normalize(cur if cur else {"ok": False, "message": "no active bubble"})
    try:
        _publish(event_id="bubble.current", params={}, result=str(cur)[:300],
                 ok=res.get("ok", True), source="spaces-ideas/bubble_get_current")
    except Exception:
        pass
    return res


@mcp.tool()
def bubble_stats(bubble_name: str = "") -> Dict[str, Any]:
    """Get statistics for a bubble: idea count, last_updated, score."""
    return _call("bubble.stats", bubble_tools.get_bubble_stats,
                 {"bubble_name": bubble_name})


@mcp.tool()
def bubble_score(bubble_name: str = "") -> Dict[str, Any]:
    """Compute a maturity/quality score for a bubble (0-100)."""
    return _call("bubble.score", bubble_tools.score_bubble,
                 {"bubble_name": bubble_name})


@mcp.tool()
def bubble_evaluate(bubble_name: str = "") -> Dict[str, Any]:
    """Run multi-perspective evaluation on a bubble: ready to promote?"""
    return _call("bubble.evaluate", bubble_tools.evaluate_bubble_evolution,
                 {"bubble_name": bubble_name})


@mcp.tool()
def bubble_promote(bubble_name: str = "", project_name: str = "") -> Dict[str, Any]:
    """Promote a mature bubble to a full project."""
    return _call("bubble.promote", bubble_tools.promote_bubble,
                 {"bubble_name": bubble_name, "project_name": project_name})


@mcp.tool()
def bubble_delete(bubble_name: str, force: bool = False) -> Dict[str, Any]:
    """Delete a bubble and all its contents."""
    return _call("bubble.delete", bubble_tools.delete_bubble,
                 {"bubble_name": bubble_name, "force": force})


@mcp.tool()
def bubble_enter(bubble_name: str) -> Dict[str, Any]:
    """Enter a bubble — subsequent idea ops will target this bubble."""
    return _call("bubble.enter", bubble_tools.enter_bubble,
                 {"bubble_name": bubble_name})


@mcp.tool()
def bubble_exit() -> Dict[str, Any]:
    """Exit current bubble — return to canvas top-level."""
    return _call("bubble.exit", bubble_tools.exit_bubble, {})


@mcp.tool()
def bubble_generate_embeddings(bubble_name: str = "") -> Dict[str, Any]:
    """Re-compute semantic embeddings for all ideas in a bubble."""
    return _call("bubble.embeddings", bubble_tools.generate_bubble_embeddings,
                 {"bubble_name": bubble_name})


# ══════════════════════════════════════════════════════════════════════
# IDEA TOOLS
# ══════════════════════════════════════════════════════════════════════

@mcp.tool()
def idea_list(limit: int = 50, bubble_id: str = "", query: str = "") -> Dict[str, Any]:
    """List ideas, optionally scoped to a bubble or query-filtered."""
    return _call("idea.list", idea_tools.list_ideas,
                 {"limit": limit, "bubble_id": bubble_id, "query": query})


@mcp.tool()
def idea_count(bubble_id: str = "") -> Dict[str, Any]:
    """Count ideas total or within a bubble."""
    return _call("idea.count", idea_tools.count_ideas, {"bubble_id": bubble_id})


@mcp.tool()
def idea_create(
    title: str, content: str = "", bubble_id: str = "",
    tags: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Create a single idea inside a bubble."""
    return _call("idea.create", idea_tools.create_idea, {
        "title": title, "content": content,
        "bubble_id": bubble_id, "tags": tags or [],
    })


@mcp.tool()
def idea_create_batch(
    ideas: List[Dict[str, Any]], bubble_id: str = "",
) -> Dict[str, Any]:
    """Create multiple ideas at once."""
    return _call("idea.create_batch", idea_tools.create_idea_batch,
                 {"ideas": ideas, "bubble_id": bubble_id})


@mcp.tool()
def idea_find(name: str, bubble_id: str = "") -> Dict[str, Any]:
    """Find an idea by fuzzy name within a bubble (or globally)."""
    return _call("idea.find", idea_tools.find_idea,
                 {"name": name, "bubble_id": bubble_id})


@mcp.tool()
def idea_update(
    idea_id: str, new_title: str = "", new_content: str = "",
    add_tags: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Update an idea's title, content, or tags."""
    return _call("idea.update", idea_tools.update_idea, {
        "idea_id": idea_id, "new_title": new_title,
        "new_content": new_content, "add_tags": add_tags or [],
    })


@mcp.tool()
def idea_delete(idea_id: str, force: bool = False) -> Dict[str, Any]:
    """Delete an idea. Requires force=True if it has connections."""
    return _call("idea.delete", idea_tools.delete_idea,
                 {"idea_id": idea_id, "force": force})


@mcp.tool()
def idea_classify(idea_id: str) -> Dict[str, Any]:
    """Auto-classify an idea (taxonomy / category assignment)."""
    return _call("idea.classify", idea_tools.classify_idea, {"idea_id": idea_id})


@mcp.tool()
def idea_connect(source_id: str, target_id: str, relation: str = "related") -> Dict[str, Any]:
    """Create a connection between two ideas."""
    return _call("idea.connect", idea_tools.connect_ideas, {
        "source_id": source_id, "target_id": target_id, "relation": relation,
    })


@mcp.tool()
def idea_disconnect(source_id: str, target_id: str) -> Dict[str, Any]:
    """Remove a connection between two ideas."""
    return _call("idea.disconnect", idea_tools.disconnect_ideas,
                 {"source_id": source_id, "target_id": target_id})


@mcp.tool()
def idea_connect_multi(connections: List[Dict[str, str]]) -> Dict[str, Any]:
    """Create multiple connections at once."""
    return _call("idea.connect_multi", idea_tools.connect_ideas_multi,
                 {"connections": connections})


@mcp.tool()
def idea_link_to_root(idea_id: str) -> Dict[str, Any]:
    """Link an idea directly to the bubble's root (top-level)."""
    return _call("idea.link_to_root", idea_tools.link_idea_to_root,
                 {"idea_id": idea_id})


@mcp.tool()
def idea_move(idea_id: str, target_bubble_id: str) -> Dict[str, Any]:
    """Move an idea from its current bubble to a different bubble."""
    return _call("idea.move", idea_tools.move_idea,
                 {"idea_id": idea_id, "target_bubble_id": target_bubble_id})


@mcp.tool()
def idea_expand(
    idea_id: str = "", topic: str = "", count: int = 3, depth: int = 1,
) -> Dict[str, Any]:
    """AI-Scientist tree-search expansion of an idea or topic."""
    return _call("idea.expand", idea_tools.expand_ideas, {
        "idea_id": idea_id, "topic": topic, "count": count, "depth": depth,
    })


@mcp.tool()
def idea_explain(idea_id: str) -> Dict[str, Any]:
    """Generate a natural-language explanation of an idea + its context."""
    return _call("idea.explain", idea_tools.explain_idea, {"idea_id": idea_id})


@mcp.tool()
def idea_add_image(
    idea_id: str, image_url: str = "", prompt: str = "",
) -> Dict[str, Any]:
    """Attach an image to an idea — either via URL or AI-generated."""
    return _call("idea.add_image", idea_tools.add_image, {
        "idea_id": idea_id, "image_url": image_url, "prompt": prompt,
    })


@mcp.tool()
def idea_auto_link(bubble_id: str = "") -> Dict[str, Any]:
    """Automatically detect and create connections between ideas in a bubble."""
    return _call("idea.auto_link", idea_tools.auto_link_ideas,
                 {"bubble_id": bubble_id})


@mcp.tool()
def idea_analyze_links(bubble_id: str = "") -> Dict[str, Any]:
    """Analyze existing connections in a bubble and suggest improvements."""
    return _call("idea.analyze_links", idea_tools.analyze_and_suggest_links,
                 {"bubble_id": bubble_id})


# ══════════════════════════════════════════════════════════════════════
# FORMAT TOOLS (Phase 11.L)
# All built on format_dispatcher.convert_format under the hood.
# Each takes an idea_name (fuzzy match in current bubble) and returns
# success/error message after persisting the format conversion.
# ══════════════════════════════════════════════════════════════════════

def _fmt_call(event_id: str, fn_name: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """Run format_dispatcher.<fn_name>(params) with publish + normalize."""
    if format_dispatcher is None:
        return {"ok": False, "message": "format_dispatcher unavailable"}
    fn = getattr(format_dispatcher, fn_name, None)
    if fn is None:
        return {"ok": False, "message": f"format function {fn_name!r} not found"}
    return _call(event_id, fn, params)


@mcp.tool()
def idea_format_table(idea_name: str = "", columns: str = "") -> Dict[str, Any]:
    """Format an idea as a comparison table.

    Voice triggers: "Formatiere als Tabelle", "Mach eine Tabelle daraus".

    Args:
        idea_name: Fuzzy name of the idea in the current bubble. If empty,
                   the most recently formatted/created idea is used.
        columns: Optional comma-separated column names for table headers.
    """
    return _fmt_call("idea.format_table", "format_idea_table",
                     {"idea_name": idea_name, "columns": columns})


@mcp.tool()
def idea_format_note(idea_name: str = "") -> Dict[str, Any]:
    """Format an idea as a simple note (revert structured → free-text).

    Voice triggers: "Zurueck zur Notiz", "Mach es wieder zu Text".
    """
    return _fmt_call("idea.format_note", "format_idea_note",
                     {"idea_name": idea_name})


@mcp.tool()
def idea_format_action_list(idea_name: str = "") -> Dict[str, Any]:
    """Format an idea as an action/task checklist.

    Voice triggers: "Mach eine Aufgabenliste", "Als Tasks", "Als Todos".
    """
    return _fmt_call("idea.format_action_list", "format_idea_action_list",
                     {"idea_name": idea_name})


@mcp.tool()
def idea_format_pros_cons(idea_name: str = "") -> Dict[str, Any]:
    """Format an idea as a pros/cons list.

    Voice triggers: "Pro-Contra", "Vorteile-Nachteile-Liste".
    """
    return _fmt_call("idea.format_pros_cons", "format_idea_pros_cons",
                     {"idea_name": idea_name})


@mcp.tool()
def idea_format_hierarchy(idea_name: str = "") -> Dict[str, Any]:
    """Format an idea as a hierarchy/outline.

    Voice triggers: "Strukturiere als Gliederung", "Outline".
    """
    return _fmt_call("idea.format_hierarchy", "format_idea_hierarchy",
                     {"idea_name": idea_name})


@mcp.tool()
def idea_format_specs(idea_name: str = "") -> Dict[str, Any]:
    """Format an idea as a technical specification.

    Voice triggers: "Wandle in Spezifikation um".
    """
    return _fmt_call("idea.format_specs", "format_idea_specs",
                     {"idea_name": idea_name})


@mcp.tool()
def idea_format_kanban(idea_name: str = "") -> Dict[str, Any]:
    """Format an idea as a Kanban board (todo / doing / done).

    Voice triggers: "Mach ein Kanban-Board", "Als Brett".
    """
    return _fmt_call("idea.format_kanban", "format_idea_kanban",
                     {"idea_name": idea_name})


@mcp.tool()
def idea_format_mindmap(idea_name: str = "") -> Dict[str, Any]:
    """Format an idea as a mind map.

    Voice triggers: "Erstelle eine Mindmap", "Gedankenkarte".
    """
    return _fmt_call("idea.format_mindmap", "format_idea_mindmap",
                     {"idea_name": idea_name})


@mcp.tool()
def idea_format_swot(idea_name: str = "") -> Dict[str, Any]:
    """Format an idea as a SWOT analysis.

    Voice triggers: "SWOT-Analyse", "Stärken-Schwächen".
    """
    return _fmt_call("idea.format_swot", "format_idea_swot",
                     {"idea_name": idea_name})


@mcp.tool()
def idea_format_user_story(idea_name: str = "") -> Dict[str, Any]:
    """Format an idea as user stories / requirements.

    Voice triggers: "Als User-Stories", "Anforderungen".
    """
    return _fmt_call("idea.format_user_story", "format_idea_user_story",
                     {"idea_name": idea_name})


@mcp.tool()
def idea_format_flowchart(idea_name: str = "") -> Dict[str, Any]:
    """Format an idea as a flowchart / process diagram.

    Voice triggers: "Als Flowchart", "Prozess-Diagramm", "Ablauf".
    """
    return _fmt_call("idea.format_flowchart", "format_idea_flowchart",
                     {"idea_name": idea_name})


@mcp.tool()
def idea_convert_format(idea_name: str = "", target_format: str = "",
                        columns: str = "") -> Dict[str, Any]:
    """Convert an idea to any supported format type.

    Args:
        idea_name: Fuzzy name of the idea in the current bubble.
        target_format: One of: table, note, action_list, pros_cons, hierarchy,
                       specs, kanban, mindmap, swot, user_story, flowchart.
        columns: Optional comma-separated column names (table only).
    """
    return _fmt_call("idea.convert_format", "convert_format",
                     {"idea_name": idea_name, "target_format": target_format,
                      "columns": columns})


@mcp.tool()
def idea_format_revert(idea_name: str = "") -> Dict[str, Any]:
    """Revert an idea node to its previous format (undo last format change)."""
    return _fmt_call("idea.format_revert", "revert_format",
                     {"idea_name": idea_name})


@mcp.tool()
def idea_format_list() -> Dict[str, Any]:
    """List all available format types and their descriptions."""
    return _fmt_call("idea.list_formats", "list_available_formats", {})


@mcp.tool()
def idea_format_get(idea_name: str = "") -> Dict[str, Any]:
    """Return the current format of an idea (note, table, action_list, ...)."""
    return _fmt_call("idea.get_format", "get_idea_format",
                     {"idea_name": idea_name})


@mcp.tool()
def idea_get_current_space() -> Dict[str, Any]:
    """Return the currently active idea/bubble context."""
    return _call("idea.current_space", idea_tools.get_current_space, {})


# ══════════════════════════════════════════════════════════════════════
# Entrypoint
# ══════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print(f"[spaces-ideas-mcp] starting (root={_ROOT})", file=sys.stderr)
    mcp.run()
