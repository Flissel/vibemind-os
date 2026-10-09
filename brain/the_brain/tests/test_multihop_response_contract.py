"""MH-5a (Phase 0) — /api/multihop/execute carries a reward-capable plan_id.

Verified reward-endpoint contract (the decision the plan demanded be made
against real code, not guessed): the canonical multihop reward correlate IS
`plan_id` — `POST /api/multihop/plan/{plan_id}/reward` (introspection.py
~:1958, body {delta, reason}) and `POST /api/decisions/reward` (body
{plan_id, reward}) both key on it. No separate routing_id exists brain-side.

RED against today's tree: the success envelope is
`{"ok": ..., "trace_id": ..., **exec_result}` where plan_id sits ONLY nested
at `plan.plan_id` — `data.get("plan_id")` in brain_multihop_bridge.py (:108)
reads None, so a voice-side reward could never fire in production.

GREEN after MH-5a: top-level `plan_id` on the executed-plan envelope,
matching the nested plan's id. Tested against the REAL router via
fastapi TestClient (no fabricated mock payload) — the {plan: ...} branch
skips planner/difficulty entirely, so only plan_executor is stubbed.
"""
from fastapi import FastAPI
from fastapi.testclient import TestClient

from web.routers.introspection import router


class _StubRecorder:
    def attach_final(self, plan_id, text):
        pass

    def record_lite(self, *a, **kw):
        pass

    def append_stage(self, *a, **kw):
        pass


class _StubPlanExecutor:
    """Mimics PlanExecutor.execute's REAL return shape (no top-level plan_id
    in exec_result — plan_executor.py returns ok/plan/executed/state/...)."""

    recorder = _StubRecorder()

    def execute(self, plan, **kw):
        return {
            "ok": True,
            "plan": plan.to_dict(),
            "executed": {"s1": {"ok": True, "validator_verdict": None}},
            "state": {},
            "elapsed_s": 0.01,
            "replans": 0,
        }


class _PendingPlanExecutor:
    recorder = _StubRecorder()

    def execute(self, plan, **kw):
        return {
            "ok": False,
            "pending": True,
            "authority_status": "pending_approval",
            "invocation_id": "brain-mcp-pending",
            "plan": plan.to_dict(),
            "executed": {"s1": {"ok": False, "pending": True}},
            "state": {}, "elapsed_s": 0.01, "replans": 0,
        }


def _make_client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.state.plan_executor = _StubPlanExecutor()
    app.state.multihop_planner = None
    app.state.final_synthesizer = None
    return TestClient(app)


def _make_pending_client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.state.plan_executor = _PendingPlanExecutor()
    app.state.multihop_planner = None
    app.state.final_synthesizer = None
    return TestClient(app)


PLAN_DICT = {
    "plan_id": "plan_contract_test",
    "intent": "response contract test",
    "rationale": "",
    "hops": [
        {
            "step_id": "s1",
            "description": "noop hop",
            "capability": "",
            "execution_target": "direct:tests:noop",
            "arg_template": "",
        }
    ],
}


class TestMultihopResponseContract:
    def test_multihop_response_carries_plan_id(self):
        client = _make_client()
        resp = client.post("/api/multihop/execute", json={"plan": PLAN_DICT})
        assert resp.status_code == 200
        body = resp.json()
        assert body.get("ok") is True
        # the reward-capable correlate must be TOP-LEVEL (bridge reads
        # data.get("plan_id")), and must match the nested plan id
        assert body.get("plan_id") == "plan_contract_test", (
            "top-level plan_id missing — voice-side reward can never fire "
            f"(keys: {sorted(body.keys())})"
        )
        assert body.get("plan", {}).get("plan_id") == "plan_contract_test"

    def test_trace_id_present(self):
        client = _make_client()
        resp = client.post("/api/multihop/execute", json={"plan": PLAN_DICT})
        assert resp.json().get("trace_id", "").startswith("tr_")

    def test_nonempty_client_trace_id_is_preserved(self):
        client = _make_client()
        resp = client.post(
            "/api/multihop/execute",
            json={"plan": PLAN_DICT, "trace_id": "trace-client-supplied"},
        )
        assert resp.status_code == 200
        assert resp.json()["trace_id"] == "trace-client-supplied"

    def test_pending_authority_response_preserves_plan_and_stable_invocation_id(self):
        resp = _make_pending_client().post("/api/multihop/execute", json={"plan": PLAN_DICT})
        body = resp.json()
        assert body["ok"] is False
        assert body["pending"] is True
        assert body["authority_status"] == "pending_approval"
        assert body["invocation_id"] == "brain-mcp-pending"
        assert body["plan"]["plan_id"] == PLAN_DICT["plan_id"]
        assert body["plan"]["plan_revision"] == 1
        assert body["plan"]["trace_id"] == body["trace_id"]
        assert body["executed"] == {"s1": {"ok": False, "pending": True}}
