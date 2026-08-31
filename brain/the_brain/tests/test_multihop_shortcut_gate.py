"""E3 (task-brain-0019) — Shortcut-Gate: Multi-Verb-Intents nehmen NIE den 1-Hop.

Verifiziert die Verdrahtung in web/routers/introspection.py:
  - `_looks_like_multi_action` erkennt Multi-Verb-Intents auch dann, wenn die
    alte Verb-Regex (_SHORTCUT_VERB_RE) das zweite Verb nicht kennt
    („füge" fehlt dort z.B.) — via core/multi_verb.
  - `_try_capability_shortcut` gibt für Multi-Verb-Intents None zurück
    (deferiert an den Planner/SoM-Pfad), selbst wenn der Capability-Router
    einen Regex-Match mit execution_target hätte.
  - Gegenprobe: Single-Action-Intents bauen weiterhin den 1-Hop-Plan
    (kein Regressions-Verlust des Shortcuts).

introspection.py importiert FastAPI + das schwere `core`-Paket (torch) auf
Modulebene — beides wird hier gestubbt; core/multi_verb und core/plan_schema
werden als ECHTE Module per Datei-Spec geladen (sie sind dependenzfrei).

Aufruf:
    python brain/the_brain/tests/test_multihop_shortcut_gate.py
"""

from __future__ import annotations

import importlib.util
import io
import sys
import types
from pathlib import Path

try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

_BRAIN = Path(__file__).resolve().parents[1]   # brain/the_brain/


def _load_real(fq_name: str, rel_path: str):
    spec = importlib.util.spec_from_file_location(fq_name, _BRAIN / rel_path)
    mod = importlib.util.module_from_spec(spec)
    # VOR exec_module registrieren — @dataclass löst cls.__module__ über
    # sys.modules auf und crasht sonst mit NoneType.__dict__.
    sys.modules[fq_name] = mod
    spec.loader.exec_module(mod)
    return mod


# ── Stubs: core-Paket (torch!) und fastapi ───────────────────────────────────
_core_pkg = types.ModuleType("core")
_core_pkg.__path__ = []  # als Paket markieren
sys.modules["core"] = _core_pkg

_mv = _load_real("core.multi_verb", "core/multi_verb.py")
_ps = _load_real("core.plan_schema", "core/plan_schema.py")
_core_pkg.multi_verb = _mv
_core_pkg.plan_schema = _ps

_oam = types.ModuleType("core.openfang_agent_manifest")
_oam.extract_mcp_servers = lambda *a, **k: []
_oam.load_mcp_servers = lambda *a, **k: {}
sys.modules["core.openfang_agent_manifest"] = _oam
_core_pkg.openfang_agent_manifest = _oam


class _StubRouter:
    def __init__(self, *a, **k):
        pass

    def _deco(self, *a, **k):
        return lambda f: f

    get = post = put = delete = patch = _deco


_fastapi = types.ModuleType("fastapi")
_fastapi.APIRouter = _StubRouter
_fastapi.Request = object
_responses = types.ModuleType("fastapi.responses")
for _n in ("HTMLResponse", "JSONResponse", "PlainTextResponse"):
    setattr(_responses, _n, type(_n, (), {"__init__": lambda self, *a, **k: None}))
_fastapi.responses = _responses
sys.modules.setdefault("fastapi", _fastapi)
sys.modules.setdefault("fastapi.responses", _responses)

_intro = _load_real("introspection_under_test", "web/routers/introspection.py")

_passed: list[str] = []
_failed: list[str] = []


def check(name, cond):
    (_passed if cond else _failed).append(name)
    print(("  PASS " if cond else "  FAIL ") + name)


# ── Capability-Router-Stub: würde IMMER 1-Hop-fähig matchen ──────────────────
class _Match:
    capability = "bubble_find"          # weder multi-arg noch state-dependent
    match_method = "regex"
    has_execution_target = True


class _CR:
    def route(self, intent):
        return _Match()

    def get_capability(self, name):
        return {"arg_kwarg": "query"}


class _State:
    capability_router = _CR()


def test_multi_action_detection():
    print("Test 1: _looks_like_multi_action erkennt Multi-Verb (E3)")
    f = _intro._looks_like_multi_action
    # Alte Regex kennt "füge" nicht — nur die neue Erkennung schlägt an
    check("koordinierte Verben (füge fehlt in alter Regex)",
          f("erstelle eine idee und füge sie der bubble hinzu") is True)
    check("Sequenz-Ellipse (cat0 0.6)",
          f("mach erst a, dann b und dann c") is True)
    check("englisch koordiniert", f("open the dashboard and check the status") is True)
    # Abgrenzung: bleibt Shortcut-fähig
    check("Single-Action bleibt False", f("lösche die bubble marketing") is False)
    check("Aufzählung unter EINEM Verb bleibt False",
          f("lösche die ideen a, b und c") is False)
    check("Nomen-Koordination bleibt False",
          f("erstelle eine idee für apis und microservices") is False)


def test_shortcut_defers_multi_verb():
    print("Test 2: _try_capability_shortcut → None bei Multi-Verb trotz Regex-Match")
    plan = _intro._try_capability_shortcut(
        _State(), "erstelle eine idee und füge sie der bubble hinzu")
    check("Multi-Verb → kein 1-Hop-Plan", plan is None)
    plan2 = _intro._try_capability_shortcut(
        _State(), "mach erst A, dann B und dann C")
    check("Sequenz-Ellipse → kein 1-Hop-Plan", plan2 is None)


def test_shortcut_still_builds_single_hop():
    print("Test 3: Gegenprobe — Single-Action baut weiterhin den 1-Hop-Plan")
    plan = _intro._try_capability_shortcut(_State(), "finde bubble Marketing")
    check("Plan gebaut", plan is not None)
    if plan is not None:
        check("genau 1 Hop", len(plan.hops) == 1)
        check("Capability übernommen", plan.hops[0].capability == "bubble_find")


if __name__ == "__main__":
    test_multi_action_detection()
    test_shortcut_defers_multi_verb()
    test_shortcut_still_builds_single_hop()
    print()
    print(f"=== {len(_passed)} PASSED, {len(_failed)} FAILED ===")
    if _failed:
        print("FEHLGESCHLAGEN:", _failed)
        sys.exit(1)
