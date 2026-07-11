"""
E2E test: multihop -> KotlinGraph ingest through the REAL PlanExecutor.execute
(not the adapter directly). Verifies Phase 1 wiring: attach_dual_graph() +
the guarded ingest call in execute()'s finally block.
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.dual_graph import DualGraph
from core.plan_executor import PlanExecutor
from core.plan_schema import HopSpec, Plan


class _StubExecutorOk:
    def call_with_arg(self, *args, **kwargs):
        return {"ok": True, "result": "x", "elapsed_s": 0.0, "target": "stub"}


def make_plan(plan_id: str = "plan_e2e") -> Plan:
    hops = [
        HopSpec(step_id="s1", description="hop one", execution_target="direct:x:y"),
        HopSpec(step_id="s2", description="hop two", execution_target="direct:x:y", depends_on=["s1"]),
        HopSpec(step_id="s3", description="hop three", execution_target="direct:x:y", depends_on=["s2"]),
    ]
    return Plan(plan_id=plan_id, intent="do the e2e thing", rationale="", hops=hops)


class TestE2EIngestThroughExecute:
    def test_execute_writes_one_episode_of_three_events(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "core.capability_targets.build_executor",
            lambda target: _StubExecutorOk(),
        )
        pe = PlanExecutor()
        dg = DualGraph(save_dir=str(tmp_path), auto_mine_interval=10_000)
        pe.attach_dual_graph(dg)

        plan = make_plan("plan_e2e_ok")
        result = pe.execute(plan)

        assert result["ok"] is True
        assert dg.kotlingraph.stats["total_events"] == 3
        assert dg.kotlingraph.stats["total_episodes"] == 1

        events = dg.kotlingraph.events
        assert events[-1].done is True
        for e in events:
            assert e.metadata["plan_id"] == plan.plan_id

    def test_flag_off_writes_zero_events(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "core.capability_targets.build_executor",
            lambda target: _StubExecutorOk(),
        )
        monkeypatch.setenv("MULTIHOP_KOTLIN_INGEST", "0")
        pe = PlanExecutor()
        dg = DualGraph(save_dir=str(tmp_path), auto_mine_interval=10_000)
        pe.attach_dual_graph(dg)

        plan = make_plan("plan_e2e_flagoff")
        result = pe.execute(plan)

        assert result["ok"] is True
        assert dg.kotlingraph.stats["total_events"] == 0

    def test_no_dual_graph_attached_does_not_crash(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "core.capability_targets.build_executor",
            lambda target: _StubExecutorOk(),
        )
        pe = PlanExecutor()  # no attach_dual_graph call

        plan = make_plan("plan_e2e_noattach")
        result = pe.execute(plan)

        assert result["ok"] is True
