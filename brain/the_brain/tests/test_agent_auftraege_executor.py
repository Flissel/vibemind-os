"""K1 Task 4: AuftragsExecutor + Einbau in den Plan-Executor (ohne Netz)."""
import logging
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import agent_auftraege as aa  # noqa: E402
from core import capability_targets as ct  # noqa: E402
from core.plan_executor import PlanExecutor  # noqa: E402
from core.plan_schema import HopSpec, Plan  # noqa: E402


class TabelleAttrappe:
    def __init__(self):
        self.angelegt, self.plan_rest = [], {}

    def anlegen(self, **kw):
        self.angelegt.append(kw)
        return "auftrag-1"

    def plan_rest_setzen(self, i, rest):
        self.plan_rest[i] = rest


@pytest.fixture()
def tabelle(monkeypatch):
    t = TabelleAttrappe()
    monkeypatch.setattr(aa, "AGENT_AUFTRAEGE_ENABLED", True)
    monkeypatch.setattr(aa, "tabelle_aus_umgebung", lambda: t)
    return t


def test_build_executor_waehlt_auftrag_nur_mit_schalter(monkeypatch, tabelle):
    assert isinstance(ct.build_executor("openfang:rowboat-chat"), ct.AuftragsExecutor)
    monkeypatch.setattr(aa, "AGENT_AUFTRAEGE_ENABLED", False)
    assert not isinstance(ct.build_executor("openfang:rowboat-chat"), ct.AuftragsExecutor)


def test_executor_legt_auftrag_an_und_meldet_pending(tabelle):
    r = ct.AuftragsExecutor("rowboat-chat").call_with_arg(
        "suche x", extra_params={"_capability": "rowboat_search", "_step_id": "s1", "_trace_id": "test_1",
                                 "_plan_id": "p1", "_antwortkanal": {"art": "telegram"}, "_uebergabe": {"x": 1}})
    assert r["pending"] is True and r["invocation_id"] == "auftrag-1"
    a = tabelle.angelegt[0]
    assert (a["agent"], a["capability"], a["auftrag"], a["antwortkanal"]) == (
        "rowboat-chat", "rowboat_search", "suche x", {"art": "telegram"})


def test_anlegen_fehlgeschlagen_ist_fehler_nicht_pending(monkeypatch):
    monkeypatch.setattr(aa, "AGENT_AUFTRAEGE_ENABLED", True)

    def kaputt():
        raise RuntimeError("kein Service-Schluessel")

    monkeypatch.setattr(aa, "tabelle_aus_umgebung", kaputt)
    r = ct.AuftragsExecutor("rowboat-chat").call_with_arg("x", extra_params={"_capability": "c", "_step_id": "s1"})
    assert r.get("pending") is not True and r["ok"] is False and "Auftrag" in r["error"]


def _plan_zwei_hops():
    s1 = HopSpec("s1", "suche", capability="rowboat_search",
                 execution_target="openfang:rowboat-chat", output_var="treffer")
    s2 = HopSpec("s2", "weiter", execution_target="direct:test:weiter",
                 depends_on=["s1"], arg_template="{{state.treffer}}")
    return Plan("plan-k1", "finde etwas", "", [s1, s2], trace_id="test_k1")


def test_plan_executor_speichert_plan_rest_im_auftrag(tabelle):
    r = PlanExecutor().execute(_plan_zwei_hops(), antwortkanal={"art": "telegram"})
    assert r["pending"] is True and r["invocation_id"] == "auftrag-1"
    assert r["executed"]["s1"]["pending"] is True
    rest = tabelle.plan_rest["auftrag-1"]
    assert [h["step_id"] for h in rest["plan"]["hops"]] == ["s2"]
    assert rest["output_var"] == "treffer"
    assert rest["pending_hop"] == "s1"
    assert rest["antwortkanal"] == {"art": "telegram"}
    a = tabelle.angelegt[0]
    assert a["plan_id"] == "plan-k1" and a["hop_id"] == "s1" and a["antwortkanal"] == {"art": "telegram"}


def test_plan_rest_fehler_wird_geloggt_und_nicht_geworfen(tabelle, caplog):
    def kaputt(i, rest):
        raise RuntimeError("db weg")

    tabelle.plan_rest_setzen = kaputt
    with caplog.at_level(logging.WARNING):
        r = PlanExecutor().execute(_plan_zwei_hops())
    assert r["pending"] is True
    assert any("auftrag-1" in m for m in caplog.messages)


def test_start_state_wird_gerendert(monkeypatch):
    monkeypatch.setattr(aa, "AGENT_AUFTRAEGE_ENABLED", False)
    seen = []

    class _Exe:
        def call_with_arg(self, arg, arg_kwarg=None, extra_params=None):
            seen.append(arg)
            return {"ok": True, "result": "fertig"}

    monkeypatch.setattr("core.capability_targets.build_executor", lambda _t: _Exe())
    s2 = HopSpec("s2", "weiter", execution_target="direct:test:weiter", arg_template="{{state.treffer}}")
    r = PlanExecutor().execute(Plan("plan-k1b", "x", "", [s2]), start_state={"treffer": "abc"})
    assert seen == ["abc"] and r["ok"] is True


# ---- Fix-Runde 1 ----------------------------------------------------------

def _of(sid, deps=(), ov=""):
    return HopSpec(sid, sid, capability="rowboat_search", execution_target="openfang:rowboat-chat",
                   depends_on=list(deps), output_var=ov)


def _direkt(sid, deps=()):
    return HopSpec(sid, sid, execution_target="direct:test:x", depends_on=list(deps))


def test_flag_aus_extra_params_unveraendert(monkeypatch):
    from core.handoff_bundle import baue_handoff_bundle
    monkeypatch.setattr(aa, "AGENT_AUFTRAEGE_ENABLED", False)
    gesehen = []

    class _Exe:
        def call_with_arg(self, arg, arg_kwarg=None, extra_params=None):
            gesehen.append(dict(extra_params or {}))
            return {"ok": True, "result": "x"}

    monkeypatch.setattr("core.capability_targets.build_executor", lambda _t: _Exe())
    b = baue_handoff_bundle(plan_id="p", trace_id="t", intent="i", hop_id="s1",
                            capability="rowboat_search", agent="rowboat-chat")
    PlanExecutor()._exec_hop(_of("s1"), {}, plan_ctx=dict(b, plan_intent="i", plan_id="p", trace_id="t"))
    assert gesehen and set(gesehen[0]) == {"_intent", "_description", "_step_id", "_capability"}


def test_kette_rest_enthaelt_alle_nachfolger(tabelle):
    plan = Plan("pk", "x", "", [_of("s1", ov="o"), _direkt("s2", ["s1"]), _direkt("s3", ["s2"])])
    r = PlanExecutor().execute(plan)
    assert r["pending"] is True
    hops = {h["step_id"]: h for h in tabelle.plan_rest["auftrag-1"]["plan"]["hops"]}
    assert set(hops) == {"s2", "s3"}
    assert hops["s3"]["depends_on"] == ["s2"] and hops["s2"]["depends_on"] == []


def test_zwei_unabhaengige_openfang_hops_ein_auftrag(tabelle):
    plan = Plan("pk2", "x", "", [_of("s1"), _of("s2")])
    r = PlanExecutor().execute(plan)
    assert r["pending"] is True and len(tabelle.angelegt) == 1
    ids = [h["step_id"] for h in tabelle.plan_rest["auftrag-1"]["plan"]["hops"]]
    assert ids == ["s2"]


def test_nicht_openfang_hop_laeuft_im_batch_mit(tabelle, monkeypatch):
    lief = []
    orig = ct.build_executor

    class _Exe:
        def call_with_arg(self, arg, arg_kwarg=None, extra_params=None):
            lief.append(1)
            return {"ok": True, "result": "x"}

    monkeypatch.setattr("core.capability_targets.build_executor",
                        lambda t: _Exe() if t.startswith("direct:") else orig(t))
    PlanExecutor().execute(Plan("pk3", "x", "", [_of("s1"), _direkt("d1")]))
    assert lief == [1] and len(tabelle.angelegt) == 1


def test_flag_aus_paralleles_verhalten_unveraendert(monkeypatch):
    monkeypatch.setattr(aa, "AGENT_AUFTRAEGE_ENABLED", False)
    ziele = []

    class _Exe:
        def call_with_arg(self, arg, arg_kwarg=None, extra_params=None):
            ziele.append(extra_params["_step_id"])
            return {"ok": True, "result": "x"}

    monkeypatch.setattr("core.capability_targets.build_executor", lambda _t: _Exe())
    monkeypatch.setattr(PlanExecutor, "_ist_openfang_hop", lambda *_: pytest.fail("Flag aus"))
    plan = Plan("pk4", "x", "", [_direkt("a"), _direkt("b")])
    r = PlanExecutor().execute(plan)
    assert sorted(ziele) == ["a", "b"] and r["ok"] is True


def test_state_nicht_serialisierbar_wird_stringifiziert_oder_weggelassen(tabelle):
    class Komisch:
        def __str__(self):
            return "komisch"

    plan = Plan("pk5", "x", "", [_of("s1"), _direkt("s2", ["s1"])])
    PlanExecutor().execute(plan, start_state={"k": Komisch()})
    assert tabelle.plan_rest["auftrag-1"]["state"] == {"k": "komisch"}
