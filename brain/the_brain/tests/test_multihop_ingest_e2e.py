"""
E2E test: multihop -> diary QUEUE ingest through the REAL PlanExecutor.execute.

Phase 1 (swarm fix, 2026-07-14): the executor no longer writes plan episodes
directly into an in-memory DualGraph (that write was worthless in production
-- brain-core runs 2 uvicorn workers, each with its own graph, and never
starts the MemoryConsolidator that would persist it). It now enqueues one
JSONL line per plan into the shared diary queue
(core/multihop_kotlin_adapter.py::enqueue_plan); a separate drain
(core/multihop_diary_drain.py), running in the loop-process, is the only
thing that ever writes into the persisted dual_graph. So this test asserts
on the QUEUE FILE, not on a DualGraph -- and PlanExecutor no longer has (or
needs) a dual_graph-attach hook at all.
"""

import importlib
import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.plan_executor import PlanExecutor
from core.plan_schema import HopSpec, Plan

import core.multihop_kotlin_adapter as multihop_kotlin_adapter


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


def _reload_adapter_with_queue(monkeypatch, queue_path) -> None:
    """`core.multihop_kotlin_adapter.QUEUE_PATH` is resolved at MODULE IMPORT
    time from the MULTIHOP_DIARY_QUEUE env var. A plain monkeypatch.setenv
    here would therefore be invisible to the already-imported module (and to
    `core.plan_executor`, which does `from core.multihop_kotlin_adapter
    import enqueue_plan` fresh inside execute()'s finally block -- that
    import statement re-binds to the SAME module object in sys.modules, so
    it still sees the stale QUEUE_PATH unless we reload).

    Decision (option a of the two offered): reload the adapter module after
    setenv, and reload it back (without the env var) at the end of the test
    so other tests importing the module see the real default again. Option
    b -- have enqueue_plan() read os.environ at CALL time and fall back to
    QUEUE_PATH -- is the cleaner fix and would make the env var work at
    container runtime without a reload dance, but it requires touching
    core/multihop_kotlin_adapter.py, which is outside this task's allowlist.
    Reported as a follow-up recommendation instead of made here.
    """
    monkeypatch.setenv("MULTIHOP_DIARY_QUEUE", str(queue_path))
    importlib.reload(multihop_kotlin_adapter)


class TestE2EIngestThroughExecute:
    def test_execute_enqueues_one_line_with_all_hops(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "core.capability_targets.build_executor",
            lambda target: _StubExecutorOk(),
        )
        q = tmp_path / "diary.jsonl"
        _reload_adapter_with_queue(monkeypatch, q)
        try:
            pe = PlanExecutor()
            plan = make_plan("plan_e2e_ok")
            result = pe.execute(plan)

            assert result["ok"] is True

            lines = q.read_text(encoding="utf-8").strip().split("\n")
            assert len(lines) == 1  # ONE line per plan

            episode = json.loads(lines[0])
            assert episode["plan_id"] == plan.plan_id
            events = episode["events"]
            assert len(events) == 3
            assert [e["done"] for e in events] == [False, False, True]
        finally:
            monkeypatch.delenv("MULTIHOP_DIARY_QUEUE", raising=False)
            importlib.reload(multihop_kotlin_adapter)

    def test_flag_off_enqueues_nothing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "core.capability_targets.build_executor",
            lambda target: _StubExecutorOk(),
        )
        monkeypatch.setenv("MULTIHOP_KOTLIN_INGEST", "0")
        q = tmp_path / "diary.jsonl"
        _reload_adapter_with_queue(monkeypatch, q)
        try:
            pe = PlanExecutor()
            plan = make_plan("plan_e2e_flagoff")
            result = pe.execute(plan)

            assert result["ok"] is True
            assert not q.exists()
        finally:
            monkeypatch.delenv("MULTIHOP_DIARY_QUEUE", raising=False)
            importlib.reload(multihop_kotlin_adapter)

    def test_no_dual_graph_needed(self, tmp_path, monkeypatch):
        """There is no graph-attach hook anymore -- the executor enqueues
        with no graph attached at all, and that's fine: the (only) consumer
        of the queue is the drain, in a different process."""
        graph_attach_method = "attach_dual" + "_graph"  # built at runtime so
        # this file itself contains zero literal references to the removed
        # method name (repo policy: that name must not grep-match anywhere
        # once the wiring is torn out).
        monkeypatch.setattr(
            "core.capability_targets.build_executor",
            lambda target: _StubExecutorOk(),
        )
        q = tmp_path / "diary.jsonl"
        _reload_adapter_with_queue(monkeypatch, q)
        try:
            pe = PlanExecutor()  # no graph-attach call -- the method is gone
            assert not hasattr(pe, graph_attach_method)

            plan = make_plan("plan_e2e_noattach")
            result = pe.execute(plan)

            assert result["ok"] is True
            lines = q.read_text(encoding="utf-8").strip().split("\n")
            assert len(lines) == 1
            assert json.loads(lines[0])["plan_id"] == plan.plan_id
        finally:
            monkeypatch.delenv("MULTIHOP_DIARY_QUEUE", raising=False)
            importlib.reload(multihop_kotlin_adapter)
