"""Herkunft vor den Sales-Arten (Spec 8.3): nur aus einem getippten Satz des Betreibers.

Ein Schritt auf ``mcp:brain-sales:auftragsbuch:sales_*`` laeuft nur, wenn ``plan_ctx["eingabe_herkunft"]``
genau ``"getippt"`` ist (die App belegt es je Satz mit ``X-Vibemind-Eingabe``). Sonst scheitert er mit
"Nur aus einer Eingabe des Betreibers", und nichts geht an OpenFang: weder an den Ausfuehrer
noch an die Laufzeit-Freigabe.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "brain" / "the_brain"))

from core import capability_targets as ct  # noqa: E402
from core.herkunft import ABLEHNUNG, tor_mcp  # noqa: E402
from core.ideen_abkuerzung import arg_vorlage  # noqa: E402
from core.openfang_runtime_authority import RuntimeInvocationContext  # noqa: E402
from core.plan_executor import PlanExecutor  # noqa: E402
from core.plan_schema import HopSpec, Plan  # noqa: E402

SALES_ARTEN = ("kontakt_finden", "termine_heute", "freigaben_zaehlen", "post_offen")


class _Ausfuehrer:
    def __init__(self) -> None:
        self.aufrufe = 0
        self.extra = None

    def call_with_arg(self, arg, arg_kwarg=None, extra_params=None):
        self.aufrufe += 1
        self.extra = extra_params
        return {"ok": True, "result": {"auftrag_id": "x", "an": "sales", "stand": "offen"}}


@pytest.fixture
def ausfuehrer(monkeypatch):
    monkeypatch.setattr("core.plan_executor.PlanExecutor._capture_kg_hits", lambda *a: [])
    a = _Ausfuehrer()
    monkeypatch.setattr("core.capability_targets.build_executor", lambda _ziel: a)
    return a


def _hop(capability: str, ziel: str = "") -> HopSpec:
    return HopSpec(step_id="s1", description=capability, capability=capability,
                   execution_target=ziel or None, arg_template=arg_vorlage({"eingabe": {"suche": "Muster"}}))


def _ctx(**extra):
    return {"plan_id": "sales_1", "plan_revision": 1, "trace_id": "t1", **extra}


# --- Verzeichnis -----------------------------------------------------------------------------

@pytest.mark.parametrize("art", SALES_ARTEN)
def test_verzeichnis_verlangt_betreiber_fuer_jede_sales_art(art):
    a = ct.find_registered_mcp_authority("brain-sales", "auftragsbuch", f"sales_{art}")
    assert a is not None and a.required_origin == "betreiber"


@pytest.mark.parametrize("werkzeug", ("auftrag_lesen", "auftrag_probe"))
def test_lesen_und_probe_ohne_herkunft(werkzeug):
    a = ct.find_registered_mcp_authority("brain-sales", "auftragsbuch", werkzeug)
    assert a is not None and a.required_origin == ""


def test_unbekannter_wert_scheitert_geschlossen():
    space = {"agent": "brain-sales", "mcp_servers": ["auftragsbuch"],
             "mcp_tools": {"auftragsbuch": ["sales_kontakt_finden"]}}
    spec = {"tool": "sales_kontakt_finden", "required_provenance": ["approval_ref", "cost_ref"],
            "required_origin": "jeder", "execution": {"kind": "mcp", "server": "auftragsbuch"}}
    with pytest.raises(RuntimeError, match="unknown required_origin"):
        ct._mcp_authority("sales.kontakt_finden", "sales", space, spec)


# --- Tor -------------------------------------------------------------------------------------

@pytest.mark.parametrize("eingabe", (None, "", "GETIPPT", "getippt ", {"art": "getippt"}, "telegram-betreiber"))
def test_tor_nur_genau_getippt(eingabe):
    assert tor_mcp(eingabe, "auftragsbuch", "sales_kontakt_finden", "betreiber") is False
    assert tor_mcp("getippt", "auftragsbuch", "sales_kontakt_finden", "betreiber") is True


def test_tor_ohne_verlangte_herkunft_offen():
    assert tor_mcp(None, "auftragsbuch", "auftrag_lesen", "") is True


def test_tor_schalter_der_desktop_herkunft_oeffnet_nichts(monkeypatch):
    monkeypatch.setenv("VIBEMIND_DESKTOP_HERKUNFT", "aus")
    assert tor_mcp(None, "auftragsbuch", "sales_kontakt_finden", "betreiber") is False


# --- Hop im Planausfuehrer -------------------------------------------------------------------

@pytest.mark.parametrize("art", SALES_ARTEN)
def test_hop_ohne_herkunft_scheitert_und_ruft_nichts(ausfuehrer, art):
    r = PlanExecutor()._exec_hop(_hop(f"sales.{art}"), {}, plan_ctx=_ctx())
    assert r.ok is False and r.error == ABLEHNUNG
    assert ausfuehrer.aufrufe == 0


def test_hop_mit_fremder_herkunft_scheitert(ausfuehrer):
    r = PlanExecutor()._exec_hop(_hop("sales.kontakt_finden"), {},
                                 plan_ctx=_ctx(eingabe_herkunft="agent"))
    assert r.ok is False and r.error == ABLEHNUNG and ausfuehrer.aufrufe == 0


def test_ausdrueckliches_ziel_umgeht_das_tor_nicht(ausfuehrer):
    hop = _hop("irgendwas", ziel="mcp:brain-sales:auftragsbuch:sales_kontakt_finden")
    r = PlanExecutor()._exec_hop(hop, {}, plan_ctx=_ctx())
    assert r.ok is False and r.error == ABLEHNUNG and ausfuehrer.aufrufe == 0


def test_hop_getippt_laeuft_und_traegt_herkunft_weiter(ausfuehrer):
    r = PlanExecutor()._exec_hop(_hop("sales.kontakt_finden"), {},
                                 plan_ctx=_ctx(eingabe_herkunft="getippt"))
    assert r.error != ABLEHNUNG
    assert ausfuehrer.aufrufe == 1
    kontext = ausfuehrer.extra["_runtime_authority"]
    assert kontext.eingabe_herkunft == "getippt"


def test_auftrag_lesen_laeuft_ohne_herkunft(ausfuehrer):
    hop = HopSpec(step_id="s1", description="lesen", capability="sales.auftrag_lesen",
                  arg_template=arg_vorlage({"id": "4aef2c02-0000-4000-8000-000000000001"}))
    r = PlanExecutor()._exec_hop(hop, {}, plan_ctx=_ctx())
    assert r.error != ABLEHNUNG and ausfuehrer.aufrufe == 1


def test_ganzer_plan_ohne_eingangs_kopf_scheitert(ausfuehrer):
    plan = Plan(plan_id="sales_2", intent="Gibt es einen Kontakt namens Muster?", rationale="fest",
                hops=[_hop("sales.kontakt_finden")], final_synthesis_prompt="", estimated_cost_usd=0.0)
    ergebnis = PlanExecutor().execute(plan)
    schritt = ergebnis["executed"]["s1"]
    assert schritt["ok"] is False and schritt["error"] == ABLEHNUNG
    assert ausfuehrer.aufrufe == 0


# --- Zweite Pruefung im MCP-Ausfuehrer -------------------------------------------------------

def _kontext(eingabe_herkunft=None) -> RuntimeInvocationContext:
    return RuntimeInvocationContext(correlation_id="t", plan_id="p", plan_revision=1, step_id="s1",
                                    space_id="sales", agent_name="brain-sales", invocation_id="i",
                                    eingabe_herkunft=eingabe_herkunft)


def test_mcp_ausfuehrer_lehnt_ab_vor_openfang(monkeypatch):
    def _nie(*a, **k):
        raise AssertionError("OpenFang darf nicht gefragt werden")

    monkeypatch.setattr(ct.RuntimeAuthorityClient, "from_environment", classmethod(_nie))
    exe = ct.McpExecutor("mcp:brain-sales:auftragsbuch:sales_kontakt_finden")
    with pytest.raises(PermissionError, match=ABLEHNUNG):
        exe._call({"eingabe": {"suche": "Muster"}, "_runtime_authority": _kontext()})


def test_mcp_ausfuehrer_laesst_getippt_bis_zur_freigabe(monkeypatch):
    class _Halt(Exception):
        pass

    def _freigabe(*a, **k):
        raise _Halt()

    monkeypatch.setattr(ct.RuntimeAuthorityClient, "from_environment", classmethod(_freigabe))
    exe = ct.McpExecutor("mcp:brain-sales:auftragsbuch:sales_kontakt_finden")
    with pytest.raises(_Halt):
        exe._call({"eingabe": {"suche": "Muster"}, "_runtime_authority": _kontext("getippt")})
