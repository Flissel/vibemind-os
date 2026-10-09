"""Task 7 (K1): Route reicht antwortkanal durch und antwortet bei Auftrag sofort."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from web.routers.introspection import router


class _Rec:
    def attach_final(self, *a, **kw):
        pass

    def record_lite(self, *a, **kw):
        pass

    def append_stage(self, *a, **kw):
        pass


class _PE:
    recorder = _Rec()

    def __init__(self, pending=True):
        self.kwargs = None
        self.pending = pending

    def execute(self, plan, **kw):
        self.kwargs = kw
        if not self.pending:
            return {"ok": True, "plan": plan.to_dict(), "executed": {"s1": {"ok": True}},
                    "state": {}, "elapsed_s": 0.01, "replans": 0}
        return {
            "ok": False, "pending": True, "plan": plan.to_dict(),
            "executed": {"s1": {"pending": True, "capability": "rowboat_search",
                                "result": {"auftrag_id": "abcdef12-3456-7890-abcd-ef1234567890",
                                           "agent": "rowboat-chat", "status": "offen"}}},
            "state": {}, "elapsed_s": 0.01, "replans": 0,
        }


class _Syn:
    called = False

    async def asynthesize(self, **kw):
        _Syn.called = True
        return "SYNTH"


PLAN = {"plan_id": "p_auftrag", "intent": "x", "rationale": "",
        "hops": [{"step_id": "s1", "description": "d", "capability": "",
                  "execution_target": "direct:tests:noop", "arg_template": ""}]}


def _client(pe):
    app = FastAPI()
    app.include_router(router)
    app.state.plan_executor = pe
    app.state.multihop_planner = None
    app.state.final_synthesizer = _Syn()
    return TestClient(app)


def test_pending_auftrag_antwortet_sofort_ohne_synthese():
    _Syn.called = False
    pe = _PE()
    body = _client(pe).post("/api/multihop/execute",
                            json={"plan": PLAN, "antwortkanal": {"art": "telegram"}}).json()
    assert body["final_text"].startswith("Auftrag angenommen, Nr. abcdef12")
    assert "rowboat-chat" in body["final_text"]
    assert body["auftraege"] == [{"auftrag_id": "abcdef12-3456-7890-abcd-ef1234567890",
                                  "agent": "rowboat-chat", "capability": "rowboat_search"}]
    assert pe.kwargs["antwortkanal"] == {"art": "telegram"}
    assert _Syn.called is False


@pytest.mark.parametrize("roh", [None, "telegram", {"art": "mail"}, {"chat_id": 5},
                                 {"art": ["x"]}])
def test_ungueltiger_antwortkanal_wird_api(roh):
    pe = _PE(pending=False)
    json_body = {"plan": PLAN}
    if roh is not None:
        json_body["antwortkanal"] = roh
    _client(pe).post("/api/multihop/execute", json=json_body)
    assert pe.kwargs["antwortkanal"] == {"art": "api"}


def test_chat_id_wird_nicht_durchgereicht():
    pe = _PE(pending=False)
    _client(pe).post("/api/multihop/execute",
                     json={"plan": PLAN, "antwortkanal": {"art": "telegram", "chat_id": 99}})
    assert pe.kwargs["antwortkanal"] == {"art": "telegram"}


def test_ohne_auftrag_laeuft_synthese_weiter():
    pe = _PE(pending=False)
    body = _client(pe).post("/api/multihop/execute", json={"plan": PLAN}).json()
    assert body["final_text"] == "SYNTH"
    assert "auftraege" not in body
