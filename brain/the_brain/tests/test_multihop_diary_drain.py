"""Phase 1 — Drain: Queue -> dual_graph im Single-Writer-Prozess."""
import json

import pytest

from core.dual_graph import DualGraph
from core.multihop_diary_drain import drain_once
from core.multihop_kotlin_adapter import enqueue_plan


class _Plan:
    def __init__(self, pid):
        self.plan_id = pid
        self.intent = f"intent {pid}"
        self.trace_id = f"tr_{pid}"


EXEC_OK = {
    "s1": {"ok": True, "contract_pass": True, "reward": 1.0,
           "capability": "bubble_create", "target": "supabase:bubble.create"},
    "s2": {"ok": True, "contract_pass": True, "reward": 1.0,
           "capability": "idea_add", "target": "supabase:idea.create"},
}


@pytest.fixture()
def dg(tmp_path):
    return DualGraph(save_dir=str(tmp_path / "mb"), auto_mine_interval=10_000)


def test_drains_one_episode_into_the_graph(tmp_path, dg):
    q = tmp_path / "d.jsonl"
    enqueue_plan(_Plan("plan_a"), EXEC_OK, queue_path=q)

    out = drain_once(dg, queue_path=q, state_path=tmp_path / "d.state.json")

    assert out["episodes"] == 1 and out["events"] == 2
    kg = dg.kotlingraph
    assert kg.stats["total_events"] == 2
    assert kg.stats["total_episodes"] == 1
    assert kg.events[-1].done is True
    assert kg.events[-1].metadata["plan_id"] == "plan_a"


def test_second_drain_is_a_noop(tmp_path, dg):
    q = tmp_path / "d.jsonl"
    st = tmp_path / "d.state.json"
    enqueue_plan(_Plan("plan_a"), EXEC_OK, queue_path=q)
    drain_once(dg, queue_path=q, state_path=st)

    out = drain_once(dg, queue_path=q, state_path=st)

    assert out["episodes"] == 0
    assert dg.kotlingraph.stats["total_events"] == 2


def test_only_new_lines_are_drained(tmp_path, dg):
    q = tmp_path / "d.jsonl"
    st = tmp_path / "d.state.json"
    enqueue_plan(_Plan("plan_a"), EXEC_OK, queue_path=q)
    drain_once(dg, queue_path=q, state_path=st)
    enqueue_plan(_Plan("plan_b"), EXEC_OK, queue_path=q)

    out = drain_once(dg, queue_path=q, state_path=st)

    assert out["episodes"] == 1
    assert dg.kotlingraph.stats["total_episodes"] == 2


def test_episode_purity_across_plans(tmp_path, dg):
    q = tmp_path / "d.jsonl"
    for pid in ("plan_a", "plan_b", "plan_c"):
        enqueue_plan(_Plan(pid), EXEC_OK, queue_path=q)

    drain_once(dg, queue_path=q, state_path=tmp_path / "d.state.json")

    kg = dg.kotlingraph
    assert kg.stats["total_episodes"] == 3
    by_ep = {}
    for e in kg.events:
        by_ep.setdefault(e.episode_id, set()).add(e.metadata["plan_id"])
    assert all(len(pids) == 1 for pids in by_ep.values())


def test_state_file_records_progress(tmp_path, dg):
    q = tmp_path / "d.jsonl"
    st = tmp_path / "d.state.json"
    enqueue_plan(_Plan("plan_a"), EXEC_OK, queue_path=q)
    drain_once(dg, queue_path=q, state_path=st)

    s = json.loads(st.read_text(encoding="utf-8"))
    assert s["episodes_drained"] == 1
    assert s["events_written"] == 2
    assert s["last_plan_id"] == "plan_a"
    assert s["offset"] == q.stat().st_size


def test_trailing_partial_line_is_not_consumed(tmp_path, dg):
    """Ein Writer kann mitten im Append sein: Bytes nach dem letzten \\n sind
    eine unfertige Zeile -> NICHT parsen, Offset davor stehen lassen."""
    q = tmp_path / "d.jsonl"
    st = tmp_path / "d.state.json"
    enqueue_plan(_Plan("plan_a"), EXEC_OK, queue_path=q)
    complete_size = q.stat().st_size
    with q.open("a", encoding="utf-8") as f:          # halbe Zeile anhaengen
        f.write('{"v": 1, "plan_id": "plan_half", "eve')

    out = drain_once(dg, queue_path=q, state_path=st)

    assert out["episodes"] == 1                        # nur die vollstaendige
    s = json.loads(st.read_text(encoding="utf-8"))
    assert s["offset"] == complete_size                # Offset VOR dem Fragment

    # jetzt wird die Zeile fertiggeschrieben -> naechster Drain sieht sie ganz
    q.write_text(q.read_text(encoding="utf-8")[:complete_size], encoding="utf-8")
    enqueue_plan(_Plan("plan_b"), EXEC_OK, queue_path=q)
    out2 = drain_once(dg, queue_path=q, state_path=st)
    assert out2["episodes"] == 1
    assert dg.kotlingraph.stats["total_episodes"] == 2


def test_truncated_queue_resets_offset(tmp_path, dg):
    q = tmp_path / "d.jsonl"
    st = tmp_path / "d.state.json"
    enqueue_plan(_Plan("plan_a"), EXEC_OK, queue_path=q)
    drain_once(dg, queue_path=q, state_path=st)
    q.write_text("", encoding="utf-8")
    enqueue_plan(_Plan("plan_b"), EXEC_OK, queue_path=q)

    out = drain_once(dg, queue_path=q, state_path=st)

    assert out["episodes"] == 1


def test_corrupt_line_is_skipped_not_fatal(tmp_path, dg):
    q = tmp_path / "d.jsonl"
    q.write_text('{"broken":\n', encoding="utf-8")
    enqueue_plan(_Plan("plan_a"), EXEC_OK, queue_path=q)

    out = drain_once(dg, queue_path=q, state_path=tmp_path / "d.state.json")

    assert out["episodes"] == 1


def test_missing_queue_is_a_noop(tmp_path, dg):
    out = drain_once(dg, queue_path=tmp_path / "nope.jsonl",
                     state_path=tmp_path / "nope.state.json")
    assert out == {"episodes": 0, "events": 0, "offset": 0}


def test_failing_record_event_does_not_advance_offset(tmp_path, dg):
    """Regel 5: Replay-Fehler -> Offset NICHT vorruecken, naechster Lauf retryt."""
    class _Broken:
        kotlingraph = None
        def record_event(self, **kw):
            raise RuntimeError("graph down")

    q = tmp_path / "d.jsonl"
    st = tmp_path / "d.state.json"
    enqueue_plan(_Plan("plan_a"), EXEC_OK, queue_path=q)

    out = drain_once(_Broken(), queue_path=q, state_path=st)
    assert out["episodes"] == 0                        # nichts, aber kein Crash

    # ein gesunder Graph holt die Episode beim naechsten Lauf nach
    out2 = drain_once(dg, queue_path=q, state_path=st)
    assert out2["episodes"] == 1
    assert dg.kotlingraph.stats["total_events"] == 2
