"""K1 Schlussreview I8: brain_chat-Multi-Hop mit Agenten-Auftrag."""
from types import SimpleNamespace

import pytest

from core import agent_auftraege as aa
from core.brain_chat import BrainChat

AUFTRAG = "abcdef12-3456-7890-abcd-ef1234567890"


class _Adv:
    def should_decompose(self, message):
        return SimpleNamespace(should_decompose=True, triggered_by="test", reason="r")


class _Planner:
    def plan(self, message):
        return SimpleNamespace(plan_id="p_chat", hops=[1, 2], rationale="", final_synthesis_prompt=None)


class _PE:
    def __init__(self, pending=True, agent="rowboat-chat"):
        self.calls, self.pending, self.agent = [], pending, agent

    def execute(self, plan, **kw):
        self.calls.append(kw)
        if not self.pending:
            return {"ok": True, "executed": {"s1": {"ok": True}}, "state": {}, "elapsed_s": 0.1}
        return {"ok": False, "pending": True, "state": {}, "elapsed_s": 0.1,
                "executed": {"s1": {"pending": True, "capability": "rowboat_search",
                                    "result": {"auftrag_id": AUFTRAG, "agent": self.agent,
                                               "status": "offen"}}}}


class _Synth:
    def __init__(self):
        self.called = False

    def synthesize(self, **kw):
        self.called = True
        return "SYNTH"


def _chat(pe, synth):
    bc = BrainChat.__new__(BrainChat)
    bc._total_messages = 0
    bc._continuous_thinking = None
    bc._discourse_engine = None
    bc._detect_user_feedback_reward = lambda m: None
    bc._quick_intent = lambda m: (False, False)
    bc._record_response = lambda *a, **k: None
    bc._multihop_advisor, bc._multihop_planner = _Adv(), _Planner()
    bc._multihop_executor, bc._multihop_synthesizer = pe, synth
    return bc


def test_flag_an_auftrag_quittiert_ohne_synthese(monkeypatch):
    monkeypatch.setattr(aa, "AGENT_AUFTRAEGE_ENABLED", True)
    pe, syn = _PE(), _Synth()
    r = _chat(pe, syn).send("frag rowboat nach x und fasse zusammen")
    assert pe.calls == [{"antwortkanal": {"art": "dashboard"}}]
    assert syn.called is False
    assert r.response_text == ("Auftrag angenommen, Nr. abcdef12 – zuständig: rowboat-chat. "
                               "Das Ergebnis kommt, sobald es geprüft ist.")
    assert r.routing_mode == "multihop" and r.multihop["auftraege"][0]["auftrag_id"] == AUFTRAG


def test_flag_an_ohne_auftrag_synthese_wie_bisher(monkeypatch):
    monkeypatch.setattr(aa, "AGENT_AUFTRAEGE_ENABLED", True)
    pe, syn = _PE(pending=False), _Synth()
    r = _chat(pe, syn).send("a und b")
    assert pe.calls == [{"antwortkanal": {"art": "dashboard"}}]
    assert syn.called is True and r.response_text == "SYNTH"


def test_flag_aus_unveraendert(monkeypatch):
    monkeypatch.setattr(aa, "AGENT_AUFTRAEGE_ENABLED", False)
    pe, syn = _PE(pending=False), _Synth()
    r = _chat(pe, syn).send("a und b")
    assert pe.calls == [{}]
    assert syn.called is True and r.response_text == "SYNTH"


@pytest.mark.parametrize("agent,erwartet", [("rowboat-chat", "rowboat-chat"), (None, "rowboat_search"),
                                            ("", "rowboat_search")])
def test_quittung_faellt_auf_capability_zurueck(agent, erwartet):
    res = _PE(agent=agent).execute(None)
    auftraege = aa.offene_auftraege(res)
    assert aa.quittung(auftraege) == (f"Auftrag angenommen, Nr. abcdef12 – zuständig: {erwartet}. "
                                      "Das Ergebnis kommt, sobald es geprüft ist.")


def test_offene_auftraege_ohne_pending_leer():
    assert aa.offene_auftraege({"ok": True, "executed": {"s1": {"ok": True}}}) == []
    assert aa.offene_auftraege({"ok": False, "pending": True,
                                "executed": {"s1": {"pending": True, "result": {"x": 1}}}}) == []
