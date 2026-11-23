"""Ideen-Abkuerzung im Router (web/routers/introspection.py), ohne torch.

introspection.py importiert FastAPI und das schwere Paket ``core`` (torch). Beides wird
gestubbt, wie in test_multihop_shortcut_gate.py. Damit die Stubs keinen anderen Test
stoeren, laeuft die Pruefung in einem eigenen Prozess; pytest prueft nur dessen Ergebnis.

Direkt:  python brain/the_brain/tests/test_ideen_abkuerzung_router.py
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import types
from pathlib import Path

_BRAIN = Path(__file__).resolve().parents[1]   # brain/the_brain/

KONTEXT = {
    "space": "ideas",
    "bubble": {"id": "b0b1e001", "title": "Messe"},
    "cards": [{"id": "a0000001", "title": "Preise"}, {"id": "a0000002", "title": "Druckerei"}],
    "selection": [], "last_id": "a0000002", "new_id": "7f3a0000-0000-4000-8000-000000000001",
}


def test_router_abkuerzung_in_eigenem_prozess() -> None:
    lauf = subprocess.run([sys.executable, str(Path(__file__).resolve())],
                          capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert lauf.returncode == 0, lauf.stdout + lauf.stderr
    assert "FAIL" not in lauf.stdout and "PASS" in lauf.stdout, lauf.stdout


def _lade(fq_name: str, rel_path: str):
    spec = importlib.util.spec_from_file_location(fq_name, _BRAIN / rel_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[fq_name] = mod
    spec.loader.exec_module(mod)
    return mod


def _hauptlauf() -> int:
    core = types.ModuleType("core")
    core.__path__ = []
    sys.modules["core"] = core
    for name in ("multi_verb", "plan_schema", "ideen_abkuerzung"):
        setattr(core, name, _lade(f"core.{name}", f"core/{name}.py"))
    oam = types.ModuleType("core.openfang_agent_manifest")
    oam.extract_mcp_servers = lambda *a, **k: []
    oam.load_mcp_servers = lambda *a, **k: {}
    sys.modules["core.openfang_agent_manifest"] = oam

    class _StubRouter:
        def __init__(self, *a, **k):
            pass

        def _deco(self, *a, **k):
            return lambda f: f

        get = post = put = delete = patch = _deco

    fastapi = types.ModuleType("fastapi")
    fastapi.APIRouter = _StubRouter
    fastapi.Request = object
    responses = types.ModuleType("fastapi.responses")
    for n in ("HTMLResponse", "JSONResponse", "PlainTextResponse"):
        setattr(responses, n, type(n, (), {"__init__": lambda self, *a, **k: None}))
    fastapi.responses = responses
    sys.modules["fastapi"] = fastapi
    sys.modules["fastapi.responses"] = responses

    intro = _lade("introspection_ideen_test", "web/routers/introspection.py")
    fehler = []

    def check(name, cond):
        print(("  PASS " if cond else "  FAIL ") + name)
        if not cond:
            fehler.append(name)

    # ── fester Satz -> Plan mit einem festen Aufruf ──
    plan, frage, antwort = intro._try_ideen_abkuerzung("Bewerte Preise mit 80", KONTEXT)
    check("Plan fuer festen Satz", plan is not None and frage is None)
    if plan is not None:
        hop = plan.hops[0]
        check("ein Schritt, Ereignis als Faehigkeit", len(plan.hops) == 1 and hop.capability == "idea.rate")
        check("Argumente sind Kennungen", json.loads(hop.arg_template) == {"id": "a0000001", "score": 80})
        check("kein altes Zielfeld, kein arg_kwarg", not hop.execution_target and not hop.arg_kwarg)
        check("Plan-Kennung taugt als Kennung der Aenderung",
              plan.plan_id.startswith("ideen_") and plan.plan_id.isascii())
        check("Antworttext steht fest", antwort == "„Preise“ mit 80 bewertet.")

    plan, frage, antwort = intro._try_ideen_abkuerzung("Verbinde Preise mit Druckerei", KONTEXT)
    check("verbinden nutzt den aelteren Vertrag idea_connect",
          plan is not None and plan.hops[0].capability == "idea_connect"
          and json.loads(plan.hops[0].arg_template) == {"from_id": "a0000001", "to_id": "a0000002"})

    plan, frage, antwort = intro._try_ideen_abkuerzung(
        "Notiz: Preis {{state.x}} klaeren", KONTEXT)
    check("Klammern im Text ueberleben die Vorlage",
          plan is not None and "{{" not in plan.hops[0].arg_template
          and json.loads(plan.hops[0].arg_template)["title"] == "Preis {{state.x}} klaeren")

    # ── Rueckfrage statt Raten ──
    plan, frage, antwort = intro._try_ideen_abkuerzung("Bewerte Urlaub mit 80", KONTEXT)
    check("unbekannter Titel -> Rueckfrage, kein Plan", plan is None and bool(frage) and "Urlaub" in frage)

    # ── alles andere bleibt beim bisherigen Weg ──
    for satz, kontext in (("Wie wird das Wetter?", KONTEXT), ("Bewerte Preise mit 80", None),
                          ("Bewerte Preise mit 80", {"space": "sales"}), ("Ordne die Notizen nach Thema", KONTEXT)):
        check(f"kein fester Satz: {satz!r} / {type(kontext).__name__}",
              intro._try_ideen_abkuerzung(satz, kontext) == (None, None, ""))

    # ── Fehler "Ideenbereich": bubble_list ohne geratenes Suchwort ──
    class _Match:
        match_method = "regex"
        has_execution_target = True

        def __init__(self, capability):
            self.capability = capability

    class _State:
        def __init__(self, capability):
            cap = capability

            class _CR:
                def route(self, intent):
                    return _Match(cap)

                def get_capability(self, name):
                    return {"arg_kwarg": "query"}

            self.capability_router = _CR()

    for satz, erwartet in (("Welche Bubbles habe ich im Ideenbereich?", ""), ("Zeige meine Bubbles", ""),
                           ('Zeige meine Bubbles mit "Messe"', "Messe")):
        plan = intro._try_capability_shortcut(_State("bubble_list"), satz)
        check(f"bubble_list {satz!r} -> Suchwort {erwartet!r}",
              plan is not None and plan.hops[0].arg_template == erwartet)
    plan = intro._try_capability_shortcut(_State("bubble_find"), "Wo ist die Bubble Messe")
    check("bubble_find sucht weiter nach dem genannten Wort",
          plan is not None and plan.hops[0].arg_template == "Messe")

    # ── feste Antwort statt Synthese durch ein Modell ──
    def antwort_text(inhalt):
        return {"isError": False, "content": [{"type": "text", "text": json.dumps(inhalt)}]}

    def plan_mit(plan_id, capability):
        return core.plan_schema.Plan(plan_id=plan_id, intent="x", rationale="x", hops=[
            core.plan_schema.HopSpec(step_id="s1", description="x", capability=capability)])

    def lauf(ok, result=None, error=None):
        return {"ok": ok, "executed": {"s1": {"ok": ok, "result": result, "error": error}}}

    fest = intro._feste_antwort(
        plan_mit("ideen_1", "idea.list"), lauf(True, antwort_text([{"id": "a0000001", "title": "Preise"}])),
        True, "", KONTEXT)
    check("idea.list: Satz und Space", fest == {"final_text": "1 Notiz in „Messe“: „Preise“.", "space": "ideas"})

    fest = intro._feste_antwort(
        plan_mit("shortcut_1", "aenderung_zurueck"), lauf(True, antwort_text({"gruppe": "ideen_1", "anzahl": 1})),
        False, "", None)
    check("zurueck (alter Weg): fester Satz und Space ideas",
          fest == {"final_text": "Zurückgenommen: 1 Änderung.", "space": "ideas"})

    fest = intro._feste_antwort(
        plan_mit("shortcut_2", "bubble_list"), lauf(True, "2 bubbles:\n- Messe (id=a1, score=0)\n- Urlaub (id=b2, score=0)"),
        False, "", None)
    check("bubble_list (alter Weg): Satz ohne Kennungen",
          fest == {"final_text": "2 Bubbles: „Messe“, „Urlaub“.", "space": "bubbles"})

    fest = intro._feste_antwort(
        plan_mit("ideen_2", "idea.rate"), lauf(False, None, "RuntimeError: Ideen-Werkzeug meldet einen Fehler (idea_not_found)"),
        True, "„Preise“ mit 80 bewertet.", KONTEXT)
    check("Fehler: deutscher Satz, technischer Text daneben, kein Erfolgssatz",
          fest == {"final_text": "Diese Karte gibt es nicht mehr.", "space": "ideas",
                   "fehler": "RuntimeError: Ideen-Werkzeug meldet einen Fehler (idea_not_found)"})

    check("andere Abkuerzungen bleiben bei der Synthese",
          intro._feste_antwort(plan_mit("shortcut_3", "bubble_find"), lauf(True, "Found"), False, "", None) is None)
    check("Plan des Planers bleibt bei der Synthese",
          intro._feste_antwort(plan_mit("plan_9", "bubble_list"), lauf(True, "2 bubbles:\n- A (id=1)"), False, "", None) is None)
    check("unlesbares Ergebnis faellt auf die Synthese zurueck",
          intro._feste_antwort(plan_mit("shortcut_4", "bubble_list"), lauf(True, {"x": 1}), False, "", None) is None)

    print(f"{'FEHLER' if fehler else 'OK'}: {len(fehler)} fehlgeschlagen")
    return 1 if fehler else 0


if __name__ == "__main__":
    raise SystemExit(_hauptlauf())
