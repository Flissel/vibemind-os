"""Phase 1 — Tagebuch-Queue: Episode bauen + append-only enqueuen.

Warum: brain-core (HTTP) hat 2 Worker und speichert nie; nur brain-loops
persistiert. Also schreibt brain-core die Episode als EINE Zeile in eine
geteilte Queue, die brain-loops drainiert.
"""
import json

from core.multihop_kotlin_adapter import build_episode, enqueue_plan


class _Plan:
    plan_id = "plan_q1"
    intent = "queue test"
    trace_id = "tr_q1"


EXECUTED = {
    "s1": {"ok": True, "contract_pass": True, "reward": 1.0,
           "capability": "bubble_create", "target": "supabase:bubble.create"},
    "s2": {"ok": True, "contract_pass": None, "reward": 0.0,
           "capability": "idea_add", "target": "supabase:idea.create"},
}


class TestBuildEpisode:
    def test_shape_and_event_count(self):
        ep = build_episode(_Plan(), EXECUTED, trace_id="tr_q1")
        assert ep["v"] == 1
        assert ep["plan_id"] == "plan_q1"
        assert ep["trace_id"] == "tr_q1"
        assert len(ep["events"]) == 2

    def test_only_last_event_closes_the_episode(self):
        ep = build_episode(_Plan(), EXECUTED)
        assert [e["done"] for e in ep["events"]] == [False, True]
        last = ep["events"][-1]
        assert "episode_success" in last["metadata"]
        assert "plan_ok" in last["metadata"]

    def test_is_json_serializable(self):
        line = json.dumps(build_episode(_Plan(), EXECUTED))
        assert "\n" not in line
        assert json.loads(line)["plan_id"] == "plan_q1"

    def test_empty_executed_yields_no_events(self):
        assert build_episode(_Plan(), {})["events"] == []

    def test_task_class_id_lands_in_every_event(self):
        ep = build_episode(_Plan(), EXECUTED, task_class_id="tc_abc")
        assert all(e["metadata"]["task_class_id"] == "tc_abc" for e in ep["events"])

    def test_no_task_class_key_when_empty(self):
        ep = build_episode(_Plan(), EXECUTED)
        assert all("task_class_id" not in e["metadata"] for e in ep["events"])


class TestEnqueuePlan:
    def test_appends_exactly_one_line_per_plan(self, tmp_path):
        q = tmp_path / "diary.jsonl"
        assert enqueue_plan(_Plan(), EXECUTED, queue_path=q) is True
        assert enqueue_plan(_Plan(), EXECUTED, queue_path=q) is True
        lines = q.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 2
        assert json.loads(lines[0])["plan_id"] == "plan_q1"
        assert len(json.loads(lines[0])["events"]) == 2

    def test_creates_parent_dir(self, tmp_path):
        q = tmp_path / "nested" / "deep" / "diary.jsonl"
        assert enqueue_plan(_Plan(), EXECUTED, queue_path=q) is True
        assert q.exists()

    def test_empty_executed_writes_nothing(self, tmp_path):
        q = tmp_path / "diary.jsonl"
        assert enqueue_plan(_Plan(), {}, queue_path=q) is False
        assert not q.exists()

    def test_flag_off_writes_nothing(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MULTIHOP_KOTLIN_INGEST", "0")
        q = tmp_path / "diary.jsonl"
        assert enqueue_plan(_Plan(), EXECUTED, queue_path=q) is False
        assert not q.exists()

    def test_never_raises_on_bad_path(self, tmp_path):
        blocker = tmp_path / "blocker"
        blocker.write_text("x", encoding="utf-8")
        assert enqueue_plan(_Plan(), EXECUTED, queue_path=blocker / "sub" / "q.jsonl") is False

    def test_concurrent_appends_do_not_interleave(self, tmp_path):
        """8 Threads, 25 Plaene each -> 200 intakte JSON-Zeilen, keine zerrissene."""
        import threading
        q = tmp_path / "diary.jsonl"
        barrier = threading.Barrier(8)

        def worker(k):
            barrier.wait()
            for i in range(25):
                class P:
                    plan_id = f"plan_{k}_{i}"
                    intent = "x"
                    trace_id = ""
                enqueue_plan(P(), EXECUTED, queue_path=q)

        ts = [threading.Thread(target=worker, args=(k,)) for k in range(8)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        lines = q.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 200
        ids = {json.loads(l)["plan_id"] for l in lines}
        assert len(ids) == 200
