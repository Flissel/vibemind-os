"""Feste Sales-Saetze im Router (web/routers/introspection.py), ohne torch.

Gleiche Stubs wie test_ideen_abkuerzung_router.py, im eigenen Prozess.
Direkt:  python brain/the_brain/tests/test_sales_saetze_router.py
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import types
from pathlib import Path

_BRAIN = Path(__file__).resolve().parents[1]
NR = "4aef2c02-0000-4000-8000-000000000001"


def test_router_sales_in_eigenem_prozess() -> None:
    lauf = subprocess.run([sys.executable, str(Path(__file__).resolve())],
                          capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert lauf.returncode == 0, lauf.stdout + lauf.stderr
    assert "FAIL" not in lauf.stdout and "PASS" in lauf.stdout, lauf.stdout


def test_einhaenger_steht_vor_den_festen_lesesaetzen() -> None:
    quelle = (_BRAIN / "web" / "routers" / "introspection.py").read_text(encoding="utf-8")
    sales = quelle.index('_space == "sales":')
    lesen = quelle.index("_lesen = _verstehen.feste_lesefrage(intent, _space)")
    assert sales < lesen
    assert "if _sales_fest:\n        _fest = _sales_antwort(plan, exec_result)" in quelle


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
    for name in ("multi_verb", "plan_schema", "ideen_abkuerzung", "sales_saetze", "sales_antwort"):
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

    intro = _lade("introspection_sales_test", "web/routers/introspection.py")
    fehler = []

    def check(name, cond):
        print(("  PASS " if cond else "  FAIL ") + name)
        if not cond:
            fehler.append(name)

    k = {"space": "sales"}
    plan, frage = intro._try_sales_abkuerzung("Zeig den Kontakt Muster.", k)
    check("Plan fuer festen Satz", plan is not None and frage is None)
    if plan is not None:
        hop = plan.hops[0]
        check("ein Schritt, Ereignis als Faehigkeit", len(plan.hops) == 1 and hop.capability == "sales.kontakt_finden")
        check("Argumente in Werkzeugform", json.loads(hop.arg_template) == {"eingabe": {"suche": "Muster"}})
        check("Plan-Kennung", plan.plan_id.startswith("sales_"))
        check("kein Modell", plan.final_synthesis_prompt == "" and plan.estimated_cost_usd == 0.0)

    plan, frage = intro._try_sales_abkuerzung("Welche Termine habe ich heute?", k)
    check("Zaehlart ohne Eingabe", plan is not None and json.loads(plan.hops[0].arg_template) == {"eingabe": {}})

    plan, frage = intro._try_sales_abkuerzung("Zeig den Kontakt X.", k)
    check("zu kurze Suche -> Rueckfrage", plan is None and frage == intro_frage(intro))

    for satz, kontext in (("Gib alle Freigaben frei.", k), ("Welche Termine habe ich heute?", {"space": "ideas"}),
                          ("Wie wird das Wetter?", k)):
        check(f"kein fester Satz: {satz!r}", intro._try_sales_abkuerzung(satz, kontext) == (None, None))

    plan, _ = intro._try_sales_abkuerzung("Zeig den Kontakt Muster.", k)
    quittung = {"content": [{"type": "text", "text": json.dumps(
        {"auftrag_id": NR, "an": "sales", "art": "kontakt_finden", "version": 1, "stand": "offen"})}]}
    f = intro._sales_antwort(plan, {"ok": True, "executed": {"s1": {"ok": True, "result": quittung}}})
    check("Quittung -> Satz mit Nummer", f is not None and f["final_text"] == "Ich suche (Auftrag 4aef2c02).")
    check("Quittung -> Sprung nach Sales", f is not None and f.get("ziel") == {"space": "sales", "pfad": "/kontakte"})

    abgelehnt = {"content": [{"type": "text", "text": "Unbekannte Auftragsart"}], "isError": True}
    f = intro._sales_antwort(plan, {"ok": False, "executed": {"s1": {"ok": False, "result": abgelehnt}}})
    check("gesperrt -> fester Satz, kein Werkzeugtext",
          f is not None and f["ok"] is False and f["final_text"].startswith("Das ist noch nicht freigeschaltet.")
          and f["fehler"] == "sales_auftrag_nicht_angelegt")

    f = intro._sales_antwort(plan, {"ok": False, "executed": {}})
    check("ohne Schritt -> nicht erreichbar", f is not None and f["ok"] is False)

    print(f"{'FAIL' if fehler else 'PASS'}: {len(fehler)} Fehler")
    return 1 if fehler else 0


def intro_frage(_intro) -> str:
    return sys.modules["core.sales_saetze"].SUCHE_RUECKFRAGE


if __name__ == "__main__":
    sys.exit(_hauptlauf())
