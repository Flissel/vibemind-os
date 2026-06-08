"""Phase B — Tests für den dynamischen Capability-Team-Builder (som_team_runner).

Das insane-Geschütz: pro Intent wählt der Builder die N relevantesten
Capabilities (Qwen-Cosine auf Intent vs. Capability-Beschreibung) und baut je
einen AutoGen-AssistantAgent, der seine Capability als Tool ausführen kann →
SelectorGroupChat. Deterministisch testbar ist die AUSWAHL + der TEAM-BAU
(Agent-Namen/Anzahl), NICHT der Live-LLM-Lauf (der braucht echte Modelle).

Stub-Embedder (4-dim Achsen) + Stub-capability_list machen die Auswahl
deterministisch ohne Modell-Load. Aufruf:
    voice/.venv312/Scripts/python spaces/autogen/planner/tests/test_team_runner.py
"""

from __future__ import annotations

import importlib.util
import io
import sys
from pathlib import Path

import numpy as np

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

_PLANNER = Path(__file__).resolve().parents[1]

_spec = importlib.util.spec_from_file_location(
    "som_team_runner", _PLANNER / "runner" / "som_team_runner.py")
_tr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_tr)

_passed: list[str] = []
_failed: list[str] = []


def check(name, cond):
    (_passed if cond else _failed).append(name)
    print(("  PASS " if cond else "  FAIL ") + name)


# Stub-Embedder: jede Capability/Intent traegt ein [[tag]] -> eigene Achse.
_AXES = {"excel": 0, "email": 1, "search": 2, "deploy": 3, "bubble": 4}


class StubEmbedder:
    def _axis(self, text: str) -> int:
        for tag, i in _AXES.items():
            if f"[[{tag}]]" in text or tag in text.lower():
                return i
        return 0

    def encode(self, text: str):
        v = np.zeros(len(_AXES), dtype=np.float32)
        v[self._axis(text)] = 1.0
        return v.tolist()

    def encode_batch(self, texts):
        return [self.encode(t) for t in texts]


_STUB_CAPS = [
    {"name": "desktop_skill", "description": "automate excel word powerpoint", "kind": "action", "execution_target": "openfang:desktop-skill"},
    {"name": "email_action", "description": "compose send email message", "kind": "action", "execution_target": "openfang:email"},
    {"name": "code_search", "description": "find code search the codebase", "kind": "search", "execution_target": "openfang:fungus-search"},
    {"name": "coding_task", "description": "deploy write run real source code", "kind": "action", "execution_target": "openfang:openclaude-coder"},
    {"name": "bubble_create", "description": "create a new bubble space topic", "kind": "action", "execution_target": "supabase:bubble.create"},
]


def _builder():
    """Builder mit Stub-Embedder + Stub-capability_list (kein Modell, keine YAML)."""
    return _tr.CapabilityTeamBuilder(
        embedder=StubEmbedder(),
        capability_list_fn=lambda: list(_STUB_CAPS),
    )


# ── Test 1: Top-N Cap-Auswahl ist intent-relevant ────────────────────────────
def test_selects_relevant_caps():
    print("Test 1: waehlt die intent-relevantesten Capabilities")
    b = _builder()
    sel = b.select_capabilities("erstelle eine excel tabelle", top_n=2)
    names = [c["name"] for c in sel]
    check("excel-Intent -> desktop_skill dabei", "desktop_skill" in names)
    check("genau top_n=2 ausgewaehlt", len(sel) == 2)
    sel2 = b.select_capabilities("deploy mein code", top_n=2)
    check("deploy-Intent -> coding_task dabei", "coding_task" in [c["name"] for c in sel2])


# ── Test 2: Team-Bau erzeugt je Cap einen Agenten ─────────────────────────────
def test_builds_agents_per_cap():
    print("Test 2: build_team erzeugt je Capability einen AssistantAgent")
    b = _builder()
    team = b.build_team("erstelle eine excel tabelle", top_n=3)
    check("Team gebaut (nicht None)", team is not None)
    parts = getattr(team, "_participants", None) or getattr(team, "participants", None) or []
    names = {getattr(p, "name", "") for p in parts}
    # top_n Capability-Agenten + 1 Synthesizer (der den Lauf abschliesst)
    check("Agenten-Anzahl == top_n + Synthesizer", len(parts) == 4)
    check("Synthesizer dabei", any("synth" in n.lower() for n in names))
    check("Agent-Namen aus Cap-Namen abgeleitet", any("desktop_skill" in n or "desktop" in n for n in names))


# ── Test 3: Fallback wenn kein Embedder — nimmt erste N Caps, kein Crash ──────
def test_fallback_no_embedder():
    print("Test 3: ohne Embedder -> erste N Caps (kein Crash)")
    b = _tr.CapabilityTeamBuilder(embedder=None, capability_list_fn=lambda: list(_STUB_CAPS),
                                  disable_semantic=True)
    sel = b.select_capabilities("irgendwas", top_n=2)
    check("liefert 2 Caps", len(sel) == 2)
    check("kein Crash, gueltige Namen", all("name" in c for c in sel))


# ── Test 4: leere Cap-Liste kippt nicht um ────────────────────────────────────
def test_empty_caps_safe():
    print("Test 4: leere Capability-Liste -> leeres Team, kein Crash")
    b = _tr.CapabilityTeamBuilder(embedder=StubEmbedder(), capability_list_fn=lambda: [])
    sel = b.select_capabilities("erstelle excel", top_n=3)
    check("leere Auswahl", sel == [])


if __name__ == "__main__":
    test_selects_relevant_caps()
    test_builds_agents_per_cap()
    test_fallback_no_embedder()
    test_empty_caps_safe()
    print()
    print(f"=== {len(_passed)} PASSED, {len(_failed)} FAILED ===")
    if _failed:
        print("FEHLGESCHLAGEN:", _failed)
        sys.exit(1)
