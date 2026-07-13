"""Phase 1 — Observability für das episodische Tagebuch (Live-Beweis-Grundlage)."""
from fastapi import FastAPI
from fastapi.testclient import TestClient

from web.routers.introspection import router
from core.dual_graph import DualGraph
from core.multihop_kotlin_adapter import record_plan


class _Plan:
    plan_id = "plan_diary_test"
    intent = "diary endpoint test"
    trace_id = "tr_diary"


def _client(dual_graph):
    app = FastAPI()
    app.include_router(router)
    app.state.dual_graph = dual_graph
    return TestClient(app)


def test_diary_stats_reports_multihop_events(tmp_path):
    dg = DualGraph(save_dir=str(tmp_path), auto_mine_interval=10_000)
    executed = {
        "s1": {"ok": True, "contract_pass": True, "reward": 1.0,
               "capability": "bubble_create", "target": "supabase:bubble.create"},
        "s2": {"ok": True, "contract_pass": None, "reward": 0.0,
               "capability": "idea_add", "target": "supabase:idea.create"},
    }
    assert record_plan(dg, _Plan(), executed) == 2

    resp = _client(dg).get("/api/diary/stats")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_events"] == 2
    assert body["total_episodes"] == 1
    assert body["multihop_events"] == 2
    assert body["last_event"]["done"] is True
    assert body["last_event"]["plan_id"] == "plan_diary_test"


def test_diary_stats_503_without_dual_graph():
    app = FastAPI()
    app.include_router(router)
    app.state.dual_graph = None
    resp = TestClient(app).get("/api/diary/stats")
    assert resp.status_code == 503
