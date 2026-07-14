"""
Multihop diary queue -> dual_graph DRAIN (the read side of the swarm fix).

WHY this module exists
-----------------------
Every executed multi-hop plan is meant to become ONE episode in the episodic
diary (`KotlinGraph` inside `DualGraph`). In the production Docker Swarm this
was measurably broken:

- `brain-core` (the HTTP service) runs **2 uvicorn worker processes** -> two
  separate in-memory `dual_graph` instances, neither of which is "the"
  memory.
- `brain-core` sets `BRAIN_BACKGROUND_LOOPS=0`. In `web/brain_server.py` the
  `MemoryConsolidator` -- the ONLY caller of `dual_graph.save()` -- is gated
  by `if _loops_enabled(): consolidator.start()`, so on brain-core it never
  starts. Nothing ever persists.
- Swarm reschedules brain-core tasks constantly -> whatever sits in that
  in-memory graph evaporates on every restart.
- `brain-loops` (a separate container, `BRAIN_BACKGROUND_LOOPS=1`,
  `BRAIN_ROLE=learner`) DOES run the consolidator and DOES persist -- but it
  builds its own independent `dual_graph` and never sees a single hop,
  because hops are executed on brain-core's side.

The fix mirrors this repo's proven `routing_matrix_autotrain.py` ->
`production/autotrain_drain.py` pattern: the write side
(`core/multihop_kotlin_adapter.enqueue_plan`, built in the previous task)
appends each completed plan as ONE JSONL line to a queue file in the shared
volume. THIS module is the read side: it runs inside the loop-process
(`brain-loops` / `brain_loops_worker.py`, wherever `_loops_enabled()` is
true) and replays those lines into the `dual_graph` that process actually
persists. brain-core only ever appends; it never touches the graph that
gets saved.

Offset / partial-line rules
----------------------------
The queue is append-only JSONL, written under a cross-process file lock
(`_locked_append` in `multihop_kotlin_adapter.py`) but a reader can still be
mid-scan while a writer is mid-append -- the lock only serializes *complete*
appends against each other, it does not stop a concurrent *read* from
observing a write in progress.

1. **Trailing partial line.** We track a byte offset (in a sibling
   `<queue>.state.json` file) and read everything from there to EOF. Any
   bytes AFTER the last `\n` in that chunk are, by definition, a write that
   has not finished yet (or never will, e.g. a torn write) -- we never parse
   them and never advance the offset past them. They are simply re-read,
   complete, on the next cycle once the writer finishes its `\n`.
2. **Idempotent.** The offset is a monotonically advancing watermark; a
   second drain with nothing new past it does no work and writes nothing.
3. **Rotation.** Size alone is NOT a rotation signal: a queue that is
   truncated and then refilled can coincidentally reach the same (or a
   larger) size than the offset we stored, and we would then happily resume
   mid-file and skip real episodes. So we also fingerprint the file's HEAD
   (`head_sha`: sha256 of its first `min(256, size)` bytes) into the state
   file. We reset the offset to 0 if the size is smaller than the stored
   offset OR the current head_sha differs from the stored one — the head
   bytes change iff the file was replaced/rotated, at any size.
4. **Corrupt line.** A line that fails `json.loads` is logged and skipped
   (the offset still advances past it -- a permanently malformed line must
   not stall the queue forever); every other line keeps draining.
5. **A failing replay.** If replaying an episode's events into `dual_graph`
   raises partway through, we log a warning and STOP the cycle right there
   -- the offset is NOT advanced past that episode's line, so it is retried
   whole on the next cycle. Whatever was cleanly drained earlier in the same
   cycle is still counted and persisted.
6. **Never raises.** `drain_once` is wrapped end to end; any unexpected
   failure yields `{"episodes": 0, "events": 0, "offset": 0}` rather than
   propagating into the caller's (daemon-thread) loop.

Replay strategy
----------------
Each queue line is exactly the `build_episode(...)` dict `enqueue_plan`
wrote: `events` is already a list of
`{"state", "action", "next_state", "reward", "done", "metadata"}` dicts in
completion order -- precisely the positional shape `DualGraph.record_event`
takes. We replay those dicts directly (`dual_graph.record_event(state,
action, next_state, reward, done, metadata=metadata)`) rather than
reconstructing a fake `plan`/`executed` pair and going back through
`record_plan`/`build_episode` a second time: the queue line already IS the
built episode, so re-deriving it would just be redundant work (and a second
place that could silently disagree with the first). Because the drain runs
single-threaded, single-process, and processes one whole queue line (= one
whole plan) at a time, episode purity (one KotlinGraph episode == one plan)
falls out structurally -- no lock is needed here the way `record_plan`
needs `_WRITE_LOCK` for concurrent in-process writers.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from pathlib import Path
from typing import Any, Dict, Optional, Union

from core.multihop_kotlin_adapter import QUEUE_PATH as _DEFAULT_QUEUE_PATH

logger = logging.getLogger(__name__)

PathLike = Union[str, Path]

_EMPTY_RESULT: Dict[str, int] = {"episodes": 0, "events": 0, "offset": 0}

# How many leading bytes of the queue identify "this file, not a new one".
_HEAD_BYTES = 256


def _default_state_path(queue_path: Path) -> Path:
    return Path(str(queue_path) + ".state.json")


def _head_sha(queue_path: Path, offset: int) -> str:
    """Fingerprint of the queue's first `min(_HEAD_BYTES, offset)` bytes.

    This is the ROTATION signal. Size cannot serve that role: a queue that is
    truncated and refilled may land at a coincidentally identical size, and
    resuming from the stale offset would then silently skip real episodes.
    The head bytes, by contrast, change whenever the file is replaced.

    We deliberately hash a prefix of the bytes we have ALREADY CONSUMED
    (bounded by `offset`), not of the file's current size. Under an
    append-only queue the consumed prefix is IMMUTABLE, so the fingerprint is
    stable across cycles by construction. Hashing `min(_HEAD_BYTES, size)`
    instead would be unstable whenever the file is still shorter than
    _HEAD_BYTES: the next append would change those head bytes, we would read
    that as a rotation, reset to 0 and REPLAY already-drained episodes twice.

    offset<=0 -> "" (nothing consumed yet, so there is nothing to protect and
    no reset can be warranted). Unreadable -> "" (unknown; never forces a
    spurious reset)."""
    n = min(_HEAD_BYTES, int(offset))
    if n <= 0:
        return ""
    try:
        with queue_path.open("rb") as f:
            return hashlib.sha256(f.read(n)).hexdigest()
    except Exception:
        return ""


def _load_state(state_path: Path) -> Dict[str, Any]:
    """Best-effort read of the drain's own progress file. Missing/corrupt
    -> a fresh zero state (never raises)."""
    default: Dict[str, Any] = {
        "offset": 0,
        "episodes_drained": 0,
        "events_written": 0,
        "last_plan_id": "",
        "last_ts": 0.0,
        "head_sha": "",
    }
    try:
        if not state_path.exists():
            return default
        raw = json.loads(state_path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return default
        default.update({k: raw[k] for k in default if k in raw})
        return default
    except Exception:
        logger.warning("multihop_diary_drain: state file %s unreadable; "
                        "starting fresh", state_path, exc_info=True)
        return default


def _write_state(state_path: Path, state: Dict[str, Any]) -> None:
    """The drain is the ONLY writer of this file (a later task lets
    brain-core READ it). Best-effort; a failed write here must not turn
    into a crashed drain cycle."""
    try:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = Path(str(state_path) + ".tmp")
        tmp.write_text(json.dumps(state), encoding="utf-8")
        tmp.replace(state_path)
    except Exception:
        logger.warning("multihop_diary_drain: could not write state file %s",
                        state_path, exc_info=True)


def _replay_episode(dual_graph: Any, episode: Dict[str, Any]) -> int:
    """Replay one episode's events into `dual_graph`, in order. Returns the
    number of events successfully written. Raises on the first failing
    `record_event` call -- the caller (`drain_once`) decides what that means
    for the offset (rule 5: do not advance past this episode)."""
    written = 0
    for event in episode.get("events") or []:
        dual_graph.record_event(
            event["state"],
            event["action"],
            event["next_state"],
            event["reward"],
            event["done"],
            metadata=event.get("metadata"),
        )
        written += 1
    return written


def drain_once(
    dual_graph: Any, *,
    queue_path: Optional[PathLike] = None,
    state_path: Optional[PathLike] = None,
) -> Dict[str, int]:
    """Drain whatever is new in the diary queue into `dual_graph`, once.

    Returns `{"episodes": int, "events": int, "offset": int}` describing
    THIS cycle (not cumulative totals -- those live in the persisted state
    file). Never raises; see module docstring rules 1-6.
    """
    try:
        q_path = Path(queue_path) if queue_path is not None else _DEFAULT_QUEUE_PATH
        s_path = Path(state_path) if state_path is not None else _default_state_path(q_path)

        if not q_path.exists():
            return dict(_EMPTY_RESULT)

        prev = _load_state(s_path)
        offset = int(prev.get("offset", 0) or 0)

        file_size = q_path.stat().st_size
        prev_head = prev.get("head_sha", "") or ""
        # Fingerprint the SAME prefix the stored head_sha covered, i.e. one
        # bounded by the offset we are about to resume from.
        current_head = _head_sha(q_path, offset)

        # Rule 3: rotated/truncated out from under us. Two independent
        # signals, because size alone is insufficient — a truncate-and-refill
        # can coincidentally reach the same size, and we would resume from a
        # stale offset in the middle of a BRAND NEW file, silently skipping
        # every episode before it. The head fingerprint catches exactly that.
        if file_size < offset or (prev_head and current_head != prev_head):
            offset = 0

        with q_path.open("rb") as f:
            f.seek(offset)
            chunk = f.read()

        last_nl = chunk.rfind(b"\n")
        if last_nl == -1:
            # No complete line at all past the current offset (rule 1: pure
            # partial-write tail, or genuinely nothing new).
            complete = b""
        else:
            complete = chunk[: last_nl + 1]

        cumulative_offset = offset
        episodes_this = 0
        events_this = 0
        last_plan_id = prev.get("last_plan_id", "")
        last_ts = prev.get("last_ts", 0.0)

        if complete:
            # `complete` always ends in b"\n" -> the final split element is
            # the empty tail; drop it.
            raw_lines = complete.split(b"\n")[:-1]

            for raw_line in raw_lines:
                line_len = len(raw_line) + 1  # +1 for the stripped '\n'
                stripped = raw_line.strip()
                if not stripped:
                    cumulative_offset += line_len
                    continue

                try:
                    episode = json.loads(stripped.decode("utf-8"))
                except Exception:
                    # Rule 4: corrupt line -> warn, skip, keep going. The
                    # offset still advances past it -- a permanently broken
                    # line must not stall the queue forever.
                    logger.warning(
                        "multihop_diary_drain: corrupt queue line at "
                        "offset %d in %s; skipping",
                        cumulative_offset, q_path, exc_info=True,
                    )
                    cumulative_offset += line_len
                    continue

                try:
                    events_this += _replay_episode(dual_graph, episode)
                except Exception:
                    # Rule 5: replay failed partway -- STOP here, do not
                    # advance the offset past this episode's line, so it is
                    # retried whole next cycle. Whatever this cycle already
                    # drained cleanly stays counted/persisted below.
                    logger.warning(
                        "multihop_diary_drain: replay failed for plan %r "
                        "at offset %d in %s; stopping this cycle, will "
                        "retry",
                        episode.get("plan_id"), cumulative_offset, q_path,
                        exc_info=True,
                    )
                    break

                episodes_this += 1
                last_plan_id = episode.get("plan_id", "") or last_plan_id
                last_ts = episode.get("ts", last_ts)
                cumulative_offset += line_len

        new_state = {
            "offset": cumulative_offset,
            "episodes_drained": int(prev.get("episodes_drained", 0) or 0) + episodes_this,
            "events_written": int(prev.get("events_written", 0) or 0) + events_this,
            "last_plan_id": last_plan_id,
            "last_ts": last_ts,
            # Fingerprint the prefix we have now consumed — that is exactly
            # the region the next cycle will re-check for rotation.
            "head_sha": _head_sha(q_path, cumulative_offset),
        }
        _write_state(s_path, new_state)

        return {
            "episodes": episodes_this,
            "events": events_this,
            "offset": cumulative_offset,
        }
    except Exception:
        logger.warning("multihop_diary_drain: drain_once failed unexpectedly",
                        exc_info=True)
        return dict(_EMPTY_RESULT)


class DiaryDrain:
    """Daemon-thread wrapper: calls `drain_once` on a fixed interval.

    Started only where `_loops_enabled()` is true (brain-loops /
    `brain_loops_worker.py`, or a native single-process run) -- see
    `web/brain_server.py`. brain-core never starts this; it only enqueues.
    """

    def __init__(
        self, dual_graph: Any, interval_s: float = 30.0, *,
        queue_path: Optional[PathLike] = None,
        state_path: Optional[PathLike] = None,
    ) -> None:
        self.dual_graph = dual_graph
        self.interval_s = interval_s
        self.queue_path = queue_path
        self.state_path = state_path
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                drain_once(
                    self.dual_graph,
                    queue_path=self.queue_path,
                    state_path=self.state_path,
                )
            except Exception:
                logger.warning("DiaryDrain: cycle failed unexpectedly",
                                exc_info=True)
            self._stop_event.wait(self.interval_s)

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run, name="DiaryDrain", daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
