"""Plan Executor — Phase 6 core.

Takes a validated Plan, walks the DAG topologically, runs each Hop via the
existing capability_router → capability_targets → capability_validator
pipeline, threads results through `pipeline_state` with `{{state.X}}`
substitution, and emits SSE-ready events for the UI.

Reuses Phase 1.5/4 (DirectExecutor + multi-protocol targets) and Phase 3
(validator). Does NOT reuse the older `multi_agent_executor.execute_pipeline`
because that one operates on registered tool-pools, not on capability-router
matches — different abstraction. We keep the same DAG ideas but on Phase 6
primitives.

Public surface:
    pe = PlanExecutor(capability_router, validator, dispatcher)
    result = pe.execute(plan)            # blocking; returns dict
    q = pe.subscribe()                   # asyncio.Queue of SSE events
    pe.unsubscribe(q)
    pe.recorder                          # PlanRecorder instance (Phase 6.12)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import threading
import time
import weakref
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed, Future
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable, Deque, Dict, List, Optional, Set, Tuple

from .plan_schema import (
    HopResult, HopSpec, Plan,
    ON_FAIL_ABORT, ON_FAIL_CONTINUE, ON_FAIL_REPLAN,
)

logger = logging.getLogger(__name__)


# Phase 11.N — accept both `{{state.ideas.0}}` (dot) and `{{state.ideas[0]}}`
# (bracket) syntaxes. The capture group is the path AFTER the leading
# `state.` prefix, possibly mixing `.` and `[N]` segments.
_TEMPLATE_RE = re.compile(r"\{\{\s*state\.([a-zA-Z_][\w.\[\]]*?)\s*\}\}")
_MAX_REPLANS = int(os.environ.get("PLAN_MAX_REPLANS", "1"))
_MAX_PARALLEL = int(os.environ.get("PLAN_MAX_PARALLEL", "4"))


# ── Plan recording (Phase 6.12) ──────────────────────────────────────


class PlanRecorder:
    """Bounded in-memory ring + JSONL append. Survives Brain restart for
    history-replay UI."""

    def __init__(self, path: Optional[Path] = None, max_in_memory: int = 100) -> None:
        if path is None:
            path = Path(__file__).resolve().parent.parent / "data" / "multihop_history.jsonl"
        self.path = Path(path)
        self._recent: Deque[Dict[str, Any]] = deque(maxlen=max_in_memory)
        self._by_id: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.RLock()
        self._load()

    def _load(self) -> None:
        try:
            if self.path.exists():
                with self.path.open("r", encoding="utf-8") as f:
                    for line in f.readlines()[-100:]:
                        try:
                            d = json.loads(line)
                            if d.get("plan_id"):
                                self._recent.append(d)
                                self._by_id[d["plan_id"]] = d
                        except Exception:
                            continue
        except Exception as e:
            logger.debug(f"[plan-recorder] load skipped: {e}")

    def record(self, snapshot: Dict[str, Any]) -> None:
        with self._lock:
            self._recent.append(snapshot)
            if snapshot.get("plan_id"):
                self._by_id[snapshot["plan_id"]] = snapshot
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(snapshot, ensure_ascii=False, default=str) + "\n")
            except Exception as e:
                logger.debug(f"[plan-recorder] persist failed: {e}")

    def list(self, *, limit: int = 20) -> List[Dict[str, Any]]:
        with self._lock:
            items = list(self._recent)[-limit:][::-1]
            return [
                {
                    "plan_id": s.get("plan_id"),
                    "ts": s.get("ts"),
                    "intent": (s.get("intent") or "")[:200],
                    "hop_count": s.get("hop_count"),
                    "ok": s.get("ok"),
                    "elapsed_s": s.get("elapsed_s"),
                }
                for s in items
            ]

    def get(self, plan_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            return self._by_id.get(plan_id)


# ── Plan executor ─────────────────────────────────────────────────────


class PlanExecutor:
    def __init__(
        self,
        capability_router: Any = None,
        validator: Any = None,
        dispatcher: Any = None,
        kg: Any = None,
        recorder: Optional[PlanRecorder] = None,
    ) -> None:
        self.capability_router = capability_router
        self.validator = validator
        self.dispatcher = dispatcher
        self.kg = kg                               # for KG-hit capture per hop
        self.recorder = recorder or PlanRecorder()
        self._subscribers: "weakref.WeakSet[asyncio.Queue]" = weakref.WeakSet()
        self._publish_loop: Optional[asyncio.AbstractEventLoop] = None
        self._lock = threading.RLock()
        # Phase 6.14.1 — single-plan-at-a-time mutex. Prevents two
        # parallel plans from racing on shared in-memory state, KG, or
        # Supabase writes. Non-blocking acquire; second concurrent
        # caller gets a clean "busy" envelope.
        self._exec_lock = threading.Lock()
        self._active_plan_id: Optional[str] = None
        self._active_plan_started_at: Optional[float] = None
        # Phase 6.14.2 — DiscourseEngine reference, set via attach_discourse_engine
        self._discourse_engine = None
        # Phase 6.14.3 — KG settle window (env-overridable)
        self._kg_settle_s = float(os.environ.get("MULTIHOP_KG_SETTLE_S", "0"))
        # Phase 6.14.4 — write plan + hop snapshots to brain-episodic
        self._episodic_enabled = os.environ.get(
            "MULTIHOP_EPISODIC_WRITE", "1",
        ) not in ("0", "false", "False")
        self.stats: Dict[str, Any] = {
            "plans_executed": 0,
            "hops_executed": 0,
            "hops_ok": 0,
            "hops_failed": 0,
            "replans_triggered": 0,
            "validator_blocks": 0,
            "total_elapsed_s": 0.0,
            "last_error": None,
            "rejected_busy": 0,
            "kg_settles": 0,
            "episodic_writes": 0,
        }

    # ── Pub/Sub for SSE (Phase 6.11) ───────────────────────────────

    def attach_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Bind the FastAPI event-loop so background threads can publish."""
        self._publish_loop = loop

    def attach_discourse_engine(self, de) -> None:
        """Phase 6.14.2 — wire DiscourseEngine so plan execution can pause
        idle/response loops and avoid concurrent KG writes."""
        self._discourse_engine = de

    def attach_continuous_thinking(self, cte) -> None:
        """Phase 7.5 — wire ContinuousThinkingEngine so plan completions and
        rewards become meaningful seeds for the thought stream."""
        self._continuous_thinking = cte

    def attach_decision_graph(self, dg) -> None:
        """Phase 8.B — wire Neo4j decision graph so plans/hops are visible
        in the decision-theatre UI."""
        self._decision_graph = dg

    # Phase 7.3 — provider success tracker. Maps (capability, target_kind)
    # to {success, fail} counts. After each hop we update the score; the
    # planner can later read it to break ties when multiple targets are
    # available. Read via get_provider_scores().
    def record_provider_outcome(
        self, capability: Optional[str], target: Optional[str], ok: bool,
    ) -> None:
        if not target:
            return
        kind = target.split(":", 1)[0].lower() if ":" in target else target.lower()
        key = f"{capability or '_any_'}:{kind}"
        with self._lock:
            scores = getattr(self, "_provider_scores", None)
            if scores is None:
                scores = {}
                self._provider_scores = scores
            entry = scores.setdefault(key, {"success": 0, "fail": 0})
            if ok:
                entry["success"] += 1
            else:
                entry["fail"] += 1

    def get_provider_scores(self) -> Dict[str, Any]:
        with self._lock:
            scores = dict(getattr(self, "_provider_scores", {}) or {})
        out = {}
        for key, e in scores.items():
            total = e["success"] + e["fail"]
            rate = (e["success"] / total) if total else 0.0
            out[key] = {**e, "total": total, "rate": round(rate, 3)}
        return out

    def record_plan_reward(self, plan_id: str, delta: float, reason: str = "") -> Dict[str, Any]:
        """Phase 7.1 — attach a user-feedback reward to a recently-executed plan.
        Updates the recorder snapshot's `reward_score` field and best-effort
        re-upserts the plan_execution episodic node so downstream consolidation
        sees the score. Idempotent for the same (plan_id, reason) pair."""
        snap = self.recorder.get(plan_id)
        if snap is None:
            return {"ok": False, "error": f"plan {plan_id} not in recorder"}
        prev = float(snap.get("reward_score") or 0.0)
        new_score = max(-2.0, min(2.0, prev + float(delta)))
        snap["reward_score"] = round(new_score, 3)
        snap["reward_reason"] = reason
        snap["reward_ts"] = time.time()
        # Re-publish into episodic with updated score so cross-session
        # consolidation picks the better-rated plans up.
        if self._episodic_enabled:
            try:
                self._episodic_write(snap)
            except Exception as e:
                logger.debug(f"[plan-executor] reward episodic re-upsert failed: {e}")
        # Also fire an SSE event so any listening UI updates the history badge
        self._publish("plan_rewarded", {
            "plan_id": plan_id, "delta": delta,
            "reward_score": new_score, "reason": reason,
        })
        # Phase 7.5 — surface to ContinuousThinkingEngine
        cte = getattr(self, "_continuous_thinking", None)
        if cte is not None:
            try:
                cte.record_event("plan_rewarded", {
                    "plan_id": plan_id,
                    "intent": (snap.get("intent") or "")[:120],
                    "score": new_score,
                    "reason": reason,
                })
            except Exception:
                pass
        with self._lock:
            self.stats.setdefault("rewards_recorded", 0)
            self.stats["rewards_recorded"] += 1
        return {"ok": True, "plan_id": plan_id, "reward_score": new_score, "delta": delta}

    def is_busy(self) -> bool:
        return self._exec_lock.locked()

    def busy_status(self) -> Dict[str, Any]:
        return {
            "busy": self._exec_lock.locked(),
            "active_plan_id": self._active_plan_id,
            "active_for_s": (
                round(time.time() - self._active_plan_started_at, 2)
                if self._active_plan_started_at else None
            ),
        }

    def _expand_repeat_hop(
        self, hop: HopSpec, state: Dict[str, Any], executed: Dict[str, "HopResult"],
    ) -> "Tuple[List[HopSpec], HopResult]":
        """Phase 6.15.1 — expand a repeat-hop into N sibling sub-hops.
        Returns (children, fallback_parent_summary). If items list is
        empty, children=[] and parent_summary describes the no-op."""
        cfg = hop.repeat or {}
        items: List[Any] = []

        # Inline list takes precedence
        explicit = cfg.get("items")
        if isinstance(explicit, list):
            items = list(explicit)

        # Pull from state
        from_path = cfg.get("items_from")
        if not items and isinstance(from_path, str) and from_path:
            # Strip leading "state." if planner wrote it that way
            path = from_path
            if path.startswith("state."):
                path = path[len("state."):]
            cur: Any = state.get(path.split(".", 1)[0])
            for p in path.split(".")[1:]:
                if isinstance(cur, dict):
                    cur = cur.get(p)
                else:
                    cur = None
                    break
            if isinstance(cur, list):
                items = list(cur)

        # count: shorthand for "0..N-1" placeholders when planner couldn't
        # invent items but knows how many it wants.
        if not items:
            n = cfg.get("count")
            if isinstance(n, int) and n > 0:
                items = list(range(int(n)))

        # Hard cap so a runaway plan can't generate 10k sub-hops
        max_items = int(os.environ.get("MULTIHOP_REPEAT_MAX", "50"))
        if len(items) > max_items:
            items = items[:max_items]

        if not items:
            # Emit a synthetic parent result so dependents can proceed
            return [], HopResult(
                step_id=hop.step_id, ok=True,
                result={"expanded_into": [], "child_count": 0,
                        "note": "repeat-hop had no items"},
                capability=hop.capability, target=hop.execution_target,
                elapsed_s=0.0,
            )

        children: List[HopSpec] = []
        for i, item in enumerate(items):
            child_id = f"{hop.step_id}.{i}"
            children.append(HopSpec(
                step_id=child_id,
                description=f"{hop.description} [{i+1}/{len(items)}]",
                capability=hop.capability,
                execution_target=hop.execution_target,
                arg_kwarg=hop.arg_kwarg,
                arg_template=hop.arg_template,
                # children depend on the parent's deps, not on the parent
                # itself (parent is a synthetic aggregator)
                depends_on=list(hop.depends_on),
                # children write into per-iteration output_var
                output_var=f"{hop.output_var}_{i}" if hop.output_var else "",
                on_fail=hop.on_fail,
                validator=hop.validator,
                timeout_s=hop.timeout_s,
                retries=hop.retries,
                repeat=None,  # children are leaves
            ))
            # Attach the iteration context so _exec_hop can render {{item}}
            children[-1].__dict__["_repeat_ctx"] = {
                "item": item, "index": i + 1, "index0": i, "value": item,
            }
        # Update children's depends_on to include parent so any hop that
        # depends on the parent will wait for ALL children
        return children, HopResult(
            step_id=hop.step_id, ok=True,
            result={"expanded_into": [c.step_id for c in children],
                    "child_count": len(children)},
            capability=hop.capability, target=hop.execution_target,
            elapsed_s=0.0,
        )

    def _episodic_write(self, snapshot: Dict[str, Any]) -> None:
        """Phase 6.14.4 — push a plan-execution summary into brain-episodic
        as a thought-shaped node so consolidation + cross-session recall
        find it. Best-effort; never raises.
        """
        if self.kg is None:
            return
        upsert = getattr(self.kg, "_upsert_point", None)
        if upsert is None:
            return
        plan_id = snapshot.get("plan_id") or "plan_unknown"
        intent = snapshot.get("intent") or ""
        ok = bool(snapshot.get("ok"))
        executed = snapshot.get("executed") or {}
        hop_lines: List[str] = []
        for sid, hr in executed.items():
            mark = "OK" if hr.get("ok") else "FAIL"
            cap = hr.get("capability") or hr.get("target") or "?"
            err = hr.get("error") or ""
            line = f"  - {sid} [{mark}] {cap}"
            if err:
                line += f" :: {err[:120]}"
            hop_lines.append(line)
        text = (
            f"Multi-hop plan {plan_id} for intent: {intent}\n"
            f"Ok={ok} hops={snapshot.get('hop_count')} "
            f"elapsed={snapshot.get('elapsed_s')}s replans={snapshot.get('replans')}\n"
            + "\n".join(hop_lines[:10])
        )
        payload = {
            "plan_id": plan_id,
            "intent": intent[:500],
            "rationale": (snapshot.get("rationale") or "")[:500],
            "hop_count": snapshot.get("hop_count"),
            "ok": ok,
            "elapsed_s": snapshot.get("elapsed_s"),
            "source": "multihop_plan_executor",
            "tags": ["multihop", "plan_summary"],
            "ts": snapshot.get("ts"),
            # Phase 7.1 — user-attributed reward, default 0 until feedback
            "reward_score": snapshot.get("reward_score") or 0.0,
            "reward_reason": snapshot.get("reward_reason") or "",
        }
        try:
            pid = upsert(plan_id, "plan_execution", text, payload)
            if pid:
                with self._lock:
                    self.stats["episodic_writes"] += 1
        except Exception as e:
            logger.debug(f"[plan-executor] episodic upsert failed: {e}")

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=200)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        try:
            self._subscribers.discard(q)
        except Exception:
            pass

    def _publish(self, kind: str, payload: Dict[str, Any]) -> None:
        if not self._subscribers:
            return
        event = {"kind": kind, "payload": payload, "ts": time.time()}
        loop = self._publish_loop
        for q in list(self._subscribers):
            try:
                if loop and loop.is_running():
                    loop.call_soon_threadsafe(self._safe_put, q, event)
                else:
                    # Best-effort sync fallback (test environment without a loop)
                    try:
                        q.put_nowait(event)
                    except Exception:
                        pass
            except Exception:
                pass

    @staticmethod
    def _safe_put(q: asyncio.Queue, event: Dict[str, Any]) -> None:
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            try:
                _ = q.get_nowait()  # drop oldest
                q.put_nowait(event)
            except Exception:
                pass

    # ── Public execute ────────────────────────────────────────────

    def execute(
        self,
        plan: Plan,
        *,
        replanner: Optional[Callable[[Plan, HopResult], Optional[Plan]]] = None,
    ) -> Dict[str, Any]:
        """Walk the DAG. Returns a dict with `executed` (step_id → HopResult),
        `state`, `plan`, `ok`, `elapsed_s`, `replans`.

        Phase 6.14.1 — single-plan-at-a-time: parallel callers get a
        `busy` envelope back instead of stomping on shared state.
        Phase 6.14.2 — pauses DiscourseEngine for the duration of the run
        so idle/response ticks don't compete for KG/Supabase writes.
        """
        # Plan-Mutex: acquire-or-busy-envelope
        if not self._exec_lock.acquire(blocking=False):
            with self._lock:
                self.stats["rejected_busy"] += 1
            return {
                "ok": False,
                "busy": True,
                "active_plan_id": self._active_plan_id,
                "active_for_s": (
                    round(time.time() - (self._active_plan_started_at or time.time()), 2)
                ),
                "error": "another plan is already executing — try again when it completes",
                "plan_id": plan.plan_id,
            }

        t0 = time.time()
        self._active_plan_id = plan.plan_id
        self._active_plan_started_at = t0
        with self._lock:
            self.stats["plans_executed"] += 1

        # Phase 6.14.2 — pause idle/response discourse loops
        de = self._discourse_engine
        de_was_paused = False
        try:
            if de is not None and not de.is_paused():
                de.pause()
            elif de is not None:
                de_was_paused = True
        except Exception:
            de = None  # broken engine — proceed without pause

        self._publish("plan_started", plan.to_dict())

        # ── Phase 10 — Self-Reflective pre-execution context ─────────
        # Fire decision-recall + self-prior + critic. All best-effort:
        # any failure here must NEVER block the actual execution.
        decision_context: Dict[str, Any] = {
            "recall": [], "self_prior": {}, "critic": {},
        }
        try:
            from . import decision_recall, decision_self_prior, plan_critic
            if self.kg is not None:
                try:
                    decision_context["recall"] = decision_recall.recall(
                        plan.intent or "", self.kg, k=5,
                    )
                except Exception as e:
                    logger.debug(f"[plan-executor] recall failed: {e}")
                try:
                    decision_context["self_prior"] = decision_self_prior.prior(
                        plan.intent or "", self.kg, k=8,
                    )
                except Exception as e:
                    logger.debug(f"[plan-executor] self_prior failed: {e}")
            try:
                decision_context["critic"] = plan_critic.critique(
                    plan, plan.intent or "", self.dispatcher,
                )
            except Exception as e:
                logger.debug(f"[plan-executor] critic failed: {e}")
            self._publish("plan_context", {
                "plan_id": plan.plan_id,
                "recall_count": len(decision_context["recall"] or []),
                "self_prior_best": (decision_context.get("self_prior") or {}).get("best_capability"),
                "critic_recommend": (decision_context.get("critic") or {}).get("recommend"),
                "critic_score": (decision_context.get("critic") or {}).get("score", 0.0),
            })
        except Exception as e:
            logger.debug(f"[plan-executor] phase-10 pre-context failed: {e}")
        # End Phase 10 pre-context

        # Phase 11.B — expose context to _execute_hop via instance attrs
        # so the hop-level OpenFang routing can build the envelope.
        self._current_decision_context = decision_context
        self._current_plan_intent = plan.intent or ""
        self._current_plan_rationale = getattr(plan, "rationale", "") or ""
        self._current_plan_id = plan.plan_id

        executed: Dict[str, HopResult] = {}
        state: Dict[str, Any] = {}
        replan_count = 0

        all_hops_by_id: Dict[str, HopSpec] = {h.step_id: h for h in plan.hops}

        try:
            # Iterate until everyone is executed or we hit a dead-end.
            while len(executed) < len(all_hops_by_id):
                ready = [
                    h for h in all_hops_by_id.values()
                    if h.step_id not in executed
                    and all(d in executed for d in h.depends_on)
                ]
                if not ready:
                    # Either all blocked due to upstream failure, or weird state.
                    break

                # Skip steps whose dependencies failed AND on_fail says abort
                still_ready: List[HopSpec] = []
                for h in ready:
                    failed_deps = [d for d in h.depends_on if not executed[d].ok]
                    if failed_deps:
                        # Mark skipped — record a synthetic result
                        skipped = HopResult(
                            step_id=h.step_id,
                            ok=False,
                            error=f"dependency failed: {failed_deps}",
                            capability=h.capability,
                            target=h.execution_target,
                        )
                        executed[h.step_id] = skipped
                        with self._lock:
                            self.stats["hops_failed"] += 1
                        self._publish("hop_completed", _hop_event(h, skipped))
                    else:
                        still_ready.append(h)

                # Phase 6.15.1 — expand repeat-hops into sub-hops at runtime.
                # We do this lazily so `repeat.items_from` can read state
                # produced by earlier hops in the same plan.
                expanded: List[HopSpec] = []
                for h in still_ready:
                    if not getattr(h, "repeat", None):
                        expanded.append(h)
                        continue
                    children, parent_summary = self._expand_repeat_hop(h, state, executed)
                    if not children:
                        # No items → mark parent as a no-op success and move on
                        executed[h.step_id] = parent_summary
                        with self._lock:
                            self.stats["hops_executed"] += 1
                            self.stats["hops_ok"] += 1
                        self._publish("hop_completed", _hop_event(h, parent_summary))
                        continue
                    # Add children to the master dict so future iterations see them
                    for c in children:
                        all_hops_by_id[c.step_id] = c
                    # Parent is replaced by aggregator — record it now so
                    # the loop doesn't reprocess
                    executed[h.step_id] = HopResult(
                        step_id=h.step_id,
                        ok=True,
                        result={"expanded_into": [c.step_id for c in children],
                                "child_count": len(children)},
                        capability=h.capability,
                        target=h.execution_target,
                        rendered_arg=h.arg_template,
                        elapsed_s=0.0,
                    )
                    with self._lock:
                        self.stats["hops_executed"] += 1
                        self.stats["hops_ok"] += 1
                    self._publish("hop_completed", _hop_event(h, executed[h.step_id]))
                    expanded.extend(children)
                still_ready = expanded

                if not still_ready:
                    continue

                # Run ready batch in parallel
                with ThreadPoolExecutor(max_workers=_MAX_PARALLEL) as pool:
                    futures: Dict[Future, HopSpec] = {
                        pool.submit(self._exec_hop, h, state): h
                        for h in still_ready
                    }
                    self._publish_started(still_ready, state)
                    for f in as_completed(futures):
                        h = futures[f]
                        try:
                            hr: HopResult = f.result()
                        except Exception as e:
                            hr = HopResult(
                                step_id=h.step_id, ok=False,
                                error=f"executor crash: {type(e).__name__}: {e}",
                                capability=h.capability, target=h.execution_target,
                            )
                        executed[h.step_id] = hr
                        with self._lock:
                            self.stats["hops_executed"] += 1
                            if hr.ok:
                                self.stats["hops_ok"] += 1
                            else:
                                self.stats["hops_failed"] += 1
                        if hr.ok and h.output_var:
                            state[h.output_var] = hr.result
                            # Phase 11.H — for repeat-block sub-hops (output_var
                            # like "ideas_0", "ideas_1", ...), also aggregate
                            # all results into a list under the parent's name
                            # ("ideas") so subsequent hops can use
                            # `repeat: { items_from: state.ideas }`.
                            try:
                                ov = h.output_var
                                # Detect "<base>_<int>" pattern
                                if "_" in ov and ov.rsplit("_", 1)[-1].isdigit():
                                    base = ov.rsplit("_", 1)[0]
                                    # Find ALL siblings, sort by index, build list
                                    sibling_keys = sorted(
                                        (k for k in state.keys()
                                         if k.startswith(base + "_")
                                         and k.rsplit("_", 1)[-1].isdigit()),
                                        key=lambda x: int(x.rsplit("_", 1)[-1]),
                                    )
                                    aggregated = []
                                    for k in sibling_keys:
                                        v = state[k]
                                        # Try to extract idea_id / bubble_id
                                        # from result-string or dict; otherwise
                                        # store raw result.
                                        if isinstance(v, str):
                                            import re as _re
                                            # Phase 11.H: prefer id= extraction for things like
                                            # bubble_create("Created bubble 'X' (id=abc)").
                                            m = _re.search(r"id=([^\s)]+)", v)
                                            if m:
                                                aggregated.append(m.group(1))
                                            else:
                                                # Phase 11.N: idea_add returns
                                                # "Added 'Idea N: title'" — extract the
                                                # quoted title so downstream format-hops
                                                # can address it by name (fuzzy match).
                                                tm = _re.search(
                                                    r"(?:Added|Created|Updated)\s+['\"]([^'\"]+)['\"]",
                                                    v,
                                                )
                                                aggregated.append(tm.group(1) if tm else v)
                                        elif isinstance(v, dict):
                                            aggregated.append(
                                                v.get("idea_id")
                                                or v.get("id")
                                                or v.get("bubble_id")
                                                or v.get("result")
                                                or v
                                            )
                                        else:
                                            aggregated.append(v)
                                    state[base] = aggregated
                            except Exception as _agg_err:
                                logger.debug(
                                    f"[plan-executor] sub-hop aggregation failed: {_agg_err}"
                                )
                        self._publish("hop_completed", _hop_event(h, hr))

                        # Replan trigger
                        if (
                            not hr.ok
                            and h.on_fail == ON_FAIL_REPLAN
                            and replanner is not None
                            and replan_count < _MAX_REPLANS
                        ):
                            replan_count += 1
                            with self._lock:
                                self.stats["replans_triggered"] += 1
                            new_plan = replanner(plan, hr)
                            if new_plan is not None:
                                # Merge: keep already-executed hops, replace remaining
                                done_ids = set(executed.keys())
                                fresh = [hs for hs in new_plan.hops if hs.step_id not in done_ids]
                                for hs in fresh:
                                    all_hops_by_id[hs.step_id] = hs
                                self._publish("plan_replanned", {
                                    "plan_id": plan.plan_id,
                                    "new_hops": [h.step_id for h in fresh],
                                    "trigger_step": h.step_id,
                                })

            elapsed = time.time() - t0
            with self._lock:
                self.stats["total_elapsed_s"] += elapsed
            ok = all(hr.ok for hr in executed.values())
            result = {
                "ok": ok,
                "plan": plan.to_dict(),
                "executed": {sid: asdict(hr) for sid, hr in executed.items()},
                "state": _safe_state_snapshot(state),
                "elapsed_s": round(elapsed, 2),
                "replans": replan_count,
                "decision_context": decision_context,
            }
            self._publish("plan_completed", {
                "plan_id": plan.plan_id,
                "ok": ok,
                "elapsed_s": result["elapsed_s"],
                "hop_count": len(executed),
            })

            # Phase 8.B — sync to Neo4j decision graph
            dg = getattr(self, "_decision_graph", None)
            if dg is not None and dg.is_connected():
                try:
                    dg.upsert_plan(
                        plan_id=plan.plan_id,
                        intent=plan.intent,
                        ok=ok,
                        hop_count=len(executed),
                        reward_score=0.0,
                    )
                    for sid, hr in executed.items():
                        dg.upsert_hop(
                            plan_id=plan.plan_id,
                            step_id=sid,
                            capability=hr.capability or "",
                            target=hr.target or "",
                            ok=bool(hr.ok),
                            elapsed_s=float(hr.elapsed_s or 0.0),
                        )
                        # Phase 9.0.2 — persist captured MCP tool-calls
                        if hr.tool_calls:
                            hop_node_id = f"{plan.plan_id}:{sid}"
                            # Phase 9.0.4 — annotate risk + approval status
                            try:
                                from .approval_gate import annotate_tool_calls
                                annotate_tool_calls(hr.tool_calls)
                            except Exception as e:
                                logger.debug(f"[plan-executor] risk-annotate failed: {e}")
                            try:
                                dg.upsert_tool_calls(hop_node_id, hr.tool_calls)
                            except Exception as e:
                                logger.debug(f"[plan-executor] tool-calls upsert failed: {e}")
                except Exception as e:
                    logger.debug(f"[plan-executor] decision-graph sync failed: {e}")

            # ── Phase 10 — persist decision_record + update self-prior ───
            # Best-effort. Decisions go to brain-decisions for recall, and
            # each capability used updates the self-model belief.
            try:
                from . import decision_recall, decision_self_prior
                hop_results_list = list(executed.values())
                outcome = "success" if ok else (
                    "partial" if any(hr.ok for hr in hop_results_list) else "failure"
                )
                if self.kg is not None:
                    decision_recall.record(
                        plan_id=plan.plan_id,
                        intent=plan.intent or "",
                        plan=plan,
                        hop_results=hop_results_list,
                        outcome=outcome,
                        reward=None,  # explicit reward arrives later via /api/decisions/reward
                        kg=self.kg,
                        duration_ms=int(elapsed * 1000),
                    )
                    # Update self-model: one trait per capability used
                    seen_caps: set = set()
                    for hr in hop_results_list:
                        cap = hr.capability or ""
                        if not cap or cap in seen_caps:
                            continue
                        seen_caps.add(cap)
                        decision_self_prior.update(
                            intent_text=plan.intent or "",
                            capability=cap,
                            success=bool(hr.ok),
                            reward=None,
                            plan_id=plan.plan_id,
                            kg=self.kg,
                        )
            except Exception as e:
                logger.debug(f"[plan-executor] phase-10 persist failed: {e}")

            # Phase 7.5 — push meaningful event to ContinuousThinkingEngine
            cte = getattr(self, "_continuous_thinking", None)
            if cte is not None:
                try:
                    cte.record_event("plan_completed", {
                        "plan_id": plan.plan_id,
                        "intent": plan.intent,
                        "ok": ok,
                        "hop_count": len(executed),
                        "elapsed_s": result["elapsed_s"],
                    })
                except Exception:
                    pass
            return result

        finally:
            elapsed_total = time.time() - t0
            snapshot = None
            try:
                snapshot = {
                    "plan_id": plan.plan_id,
                    "ts": time.time(),
                    "intent": plan.intent,
                    "rationale": plan.rationale,
                    "hop_count": len(plan.hops),
                    "hops": [asdict(h) for h in plan.hops],
                    "executed": {sid: asdict(hr) for sid, hr in executed.items()},
                    "state": _safe_state_snapshot(state),
                    "ok": all(hr.ok for hr in executed.values()) if executed else False,
                    "elapsed_s": round(elapsed_total, 2),
                    "replans": replan_count,
                }
                self.recorder.record(snapshot)
            except Exception as e:
                logger.debug(f"[plan-executor] record failed: {e}")

            # Phase 6.14.4 — also push plan summary into brain-episodic
            # so consolidation + cross-session recall can see plans.
            if snapshot and self._episodic_enabled:
                try:
                    self._episodic_write(snapshot)
                except Exception as e:
                    logger.debug(f"[plan-executor] episodic write failed: {e}")

            # Phase 6.14.2 — resume discourse if we paused it
            try:
                if de is not None and not de_was_paused:
                    de.resume()
            except Exception:
                pass

            # Phase 6.14.1 — release the plan-mutex
            self._active_plan_id = None
            self._active_plan_started_at = None
            try:
                self._exec_lock.release()
            except Exception:
                pass

    def stats_dict(self) -> Dict[str, Any]:
        s = dict(self.stats)
        if s["plans_executed"] > 0:
            s["avg_hops_per_plan"] = round(
                s["hops_executed"] / s["plans_executed"], 2,
            )
            s["avg_elapsed_s"] = round(
                s["total_elapsed_s"] / s["plans_executed"], 2,
            )
        return s

    # ── Internals ──────────────────────────────────────────────

    def _exec_hop(self, hop: HopSpec, state: Dict[str, Any]) -> HopResult:
        """Resolve template, build executor, call, validate, capture KG hits."""
        t0 = time.time()
        repeat_ctx = getattr(hop, "_repeat_ctx", None)
        rendered_arg = _render_template(hop.arg_template, state, repeat_ctx=repeat_ctx)

        # KG-hit capture (cheap — top-3 semantic hits for the hop description)
        kg_hits = self._capture_kg_hits(hop, rendered_arg)

        # Resolve target — prefer explicit, else look up via capability_router
        target = hop.execution_target
        if not target and hop.capability and self.capability_router is not None:
            try:
                detail = self.capability_router.get_capability(hop.capability)
                if detail:
                    target = detail.get("execution_target")
                    if not hop.arg_kwarg:
                        hop.arg_kwarg = detail.get("arg_kwarg")
                    if not hop.validator and detail.get("validator"):
                        hop.validator = detail.get("validator")
            except Exception as e:
                return HopResult(
                    step_id=hop.step_id, ok=False,
                    error=f"capability lookup: {type(e).__name__}: {e}",
                    capability=hop.capability, target=None,
                    rendered_arg=rendered_arg, kg_hits=kg_hits,
                    elapsed_s=time.time() - t0,
                )

        # Phase 11.B — if registry maps this capability/event to an OpenFang agent,
        # build a vibemind.intent.v1 envelope and route through that agent
        # instead of direct-calling. The agent's MCP-allowed list contains the
        # right MCP-server (e.g. spaces-ideas), so Sonnet there picks the tool
        # and runs it with full context (recall+self_prior+previous_outputs).
        # If registry doesn't claim this event, falls back to direct target.
        try:
            from .agent_yaml_registry import get_registry
            from . import intent_envelope as _envelope_mod
            _registry = get_registry()
            # Map capability name to event_id (e.g. bubble_create -> bubble.create)
            cap_to_event = {
                "bubble_create": "bubble.create",
                "bubble_update": "bubble.update",
                "bubble_evaluate": "bubble.evaluate",
                "bubble_delete": "bubble.delete",
                "idea_create": "idea.create",
                "idea_add": "idea.create",
                "idea_update": "idea.update",
                "idea_expand": "idea.expand",
                "idea_connect": "idea.connect",
            }
            event_id = cap_to_event.get(hop.capability or "", hop.capability or "")
            if "." not in event_id:
                # If the capability already has a namespace.action pattern
                # in some other form, leave as-is; otherwise it won't match
                # a registry entry and we'll fall back to direct.
                pass
            assigned_agent = _registry.get_event_agent(event_id) if event_id else None

            if assigned_agent and target and not target.startswith("openfang:"):
                # Probe: is the agent reachable in OpenFang? If not, skip
                # Phase 11.B routing and fall through to the direct target.
                _agent_known = False
                try:
                    _of_url = os.environ.get("OPENFANG_URL", "http://127.0.0.1:4200")
                    _r = __import__("requests").get(f"{_of_url}/api/agents", timeout=3)
                    if _r.ok:
                        _ag = _r.json()
                        _ag_list = _ag if isinstance(_ag, list) else _ag.get("agents", [])
                        _agent_known = any(
                            a.get("name") == assigned_agent for a in _ag_list
                        )
                except Exception as _e:
                    logger.debug(f"[plan-executor] openfang probe: {_e}")

                if _agent_known:
                    # Build envelope and override target
                    params = {}
                    if isinstance(rendered_arg, dict):
                        params = rendered_arg
                    elif isinstance(rendered_arg, str):
                        try:
                            params = json.loads(rendered_arg)
                            if not isinstance(params, dict):
                                params = {"value": params}
                        except Exception:
                            params = {"value": rendered_arg}
                    dc = getattr(self, "_current_decision_context", {}) or {}
                    envelope = _envelope_mod.build_envelope(
                        event_id=event_id,
                        params=params,
                        plan_intent=getattr(self, "_current_plan_intent", ""),
                        plan_rationale=getattr(self, "_current_plan_rationale", ""),
                        plan_id=getattr(self, "_current_plan_id", ""),
                        step_id=hop.step_id,
                        preferred_tool=hop.capability or "",
                        decision_context=dc,
                        prev_outputs=state if state else {},
                    )
                    target = f"openfang:{assigned_agent}"
                    rendered_arg = _envelope_mod.envelope_to_message(envelope)
                    hop.arg_kwarg = None
                    logger.info(
                        f"[plan-executor] Phase 11.B route: {event_id} via openfang:{assigned_agent}"
                    )
                else:
                    logger.info(
                        f"[plan-executor] Phase 11.B: agent '{assigned_agent}' "
                        f"not in OpenFang — using direct target"
                    )
        except Exception as e:
            logger.debug(f"[plan-executor] Phase 11.B routing skipped: {e}")

        if not target:
            return HopResult(
                step_id=hop.step_id, ok=False,
                error=f"no execution target for capability '{hop.capability}'",
                capability=hop.capability, target=None,
                rendered_arg=rendered_arg, kg_hits=kg_hits,
                elapsed_s=time.time() - t0,
            )

        # Build the right executor for the target prefix (Phase 4)
        try:
            from .capability_targets import build_executor
            exe = build_executor(target)
        except Exception as e:
            return HopResult(
                step_id=hop.step_id, ok=False,
                error=f"executor build: {e}",
                capability=hop.capability, target=target,
                rendered_arg=rendered_arg, kg_hits=kg_hits,
                elapsed_s=time.time() - t0,
            )

        # Call with retry support
        last = None
        for attempt in range(max(1, hop.retries)):
            try:
                if hop.arg_kwarg:
                    last = exe.call_with_arg(rendered_arg, arg_kwarg=hop.arg_kwarg)
                else:
                    last = exe.call_with_arg(rendered_arg)
            except Exception as e:
                last = {
                    "ok": False,
                    "error": f"executor crash: {type(e).__name__}: {e}",
                    "elapsed_s": 0.0,
                    "target": target,
                }
            if last.get("ok"):
                break

        ok = bool(last.get("ok"))
        result_payload = last.get("result")
        err = None if ok else (last.get("error") or "executor returned not ok")

        # Phase 9.0 — extract MCP tool-call trace from streaming OpenFang
        # responses. Other executor kinds (direct, brain, http) return
        # plain payloads without tool_calls — that's fine, list stays empty.
        captured_tool_calls: List[Dict[str, Any]] = []
        if isinstance(result_payload, dict):
            tcs = result_payload.get("tool_calls")
            if isinstance(tcs, list):
                captured_tool_calls = tcs

        # Validation (Phase 3)
        verdict = None
        if ok and hop.validator and self.validator is not None:
            try:
                verdict = self.validator.validate(
                    hop.validator,
                    intent=hop.description,
                    arg=rendered_arg,
                    raw_result=result_payload,
                )
                # on_fail=block converts to overall fail
                if verdict and not verdict.get("valid") and verdict.get("on_fail") == "block":
                    ok = False
                    err = f"validator blocked: {verdict.get('reason')}"
                    with self._lock:
                        self.stats["validator_blocks"] += 1
            except Exception as e:
                logger.warning(f"[plan-executor] validator threw: {e}")
                verdict = {"valid": False, "reason": f"validator error: {e}"}

        # Phase 6.13 — Optional TriBE bio-grounding. Off by default; opt
        # in via MULTIHOP_TRIBE_GROUNDING=1. Captures Brain's 8 bridge
        # activations (cortex/limbic/defense/motor/visceral/social/
        # integration/memory) for the hop's result text. UI shows them
        # as bridge-bars on the hop card.
        bridges = self._maybe_tribe_bridges(hop, result_payload, ok)

        # Phase 6.14.3 — KG settle-fence. After a hop that writes into
        # KG/Supabase, wait briefly so downstream hops see the new state.
        # Fixed-window (env MULTIHOP_KG_SETTLE_S, default 0). Enabled
        # automatically for capabilities whose name suggests a write.
        if ok and self._kg_settle_s > 0 and _looks_like_kg_write(hop):
            time.sleep(self._kg_settle_s)
            with self._lock:
                self.stats["kg_settles"] += 1

        # Phase 7.3 — record provider outcome for adaptive routing
        try:
            self.record_provider_outcome(hop.capability, target, ok)
        except Exception:
            pass

        return HopResult(
            step_id=hop.step_id, ok=ok, result=result_payload, error=err,
            elapsed_s=round(time.time() - t0, 2),
            validator_verdict=verdict,
            capability=hop.capability, target=target,
            rendered_arg=rendered_arg, kg_hits=kg_hits,
            retried=max(0, attempt),
            bridges=bridges,
            tool_calls=captured_tool_calls,
        )

    def _maybe_tribe_bridges(
        self, hop: HopSpec, result_payload: Any, ok: bool,
    ) -> Optional[Dict[str, float]]:
        """Phase 6.13 — gated TriBE call. Returns 8-bridge dict or None.
        Never raises (TriBE may be disabled, lazy-loading, or in dummy mode)."""
        if os.environ.get("MULTIHOP_TRIBE_GROUNDING", "0") not in ("1", "true", "True"):
            return None
        try:
            text = self._tribe_text(hop, result_payload, ok)
            if not text:
                return None
            from core.tribe_encoder import bridge_levels_for_text
            br = bridge_levels_for_text(text)
            if not br:
                return None
            # Truncate to known bridge keys + round for clean JSON
            return {k: round(float(v), 3) for k, v in br.items()}
        except Exception as e:
            logger.debug(f"[plan-executor] tribe hook failed: {e}")
            return None

    @staticmethod
    def _tribe_text(hop: HopSpec, result_payload: Any, ok: bool) -> str:
        """Pick a stimulus string for TriBE. Prefer the hop description +
        a short result preview — combined ~400 chars max so we don't
        burn TriBE-latency on huge structured payloads."""
        parts: List[str] = []
        if hop.description:
            parts.append(hop.description.strip())
        if ok and result_payload is not None:
            try:
                preview = repr(result_payload)
                if len(preview) > 240:
                    preview = preview[:240] + "..."
                parts.append(preview)
            except Exception:
                pass
        text = " | ".join(parts)
        return text[:400]

    def _capture_kg_hits(self, hop: HopSpec, rendered_arg: Any) -> List[Dict[str, Any]]:
        """Cheap KG-search using the rendered hop arg or hop description.
        Best-effort; never fails the hop. Returns a slim list for the UI."""
        if self.kg is None:
            return []
        try:
            query = str(rendered_arg) if rendered_arg else hop.description
            if not query:
                return []
            hits = []
            try:
                # QdrantKG.search(query, limit, score_threshold) — most flexible
                results = self.kg.search(query, limit=3, score_threshold=0.4)
            except TypeError:
                results = self.kg.search(query, limit=3)
            for h in results or []:
                if isinstance(h, dict):
                    hits.append({
                        "title": h.get("title") or h.get("payload", {}).get("title"),
                        "node_type": h.get("node_type") or h.get("payload", {}).get("node_type"),
                        "score": h.get("score"),
                        "collection": h.get("collection"),
                    })
            return hits[:3]
        except Exception as e:
            logger.debug(f"[plan-executor] kg capture failed: {e}")
            return []

    def _publish_started(self, hops: List[HopSpec], state: Dict[str, Any]) -> None:
        for h in hops:
            self._publish("hop_started", {
                "step_id": h.step_id,
                "description": h.description,
                "capability": h.capability,
                "execution_target": h.execution_target,
                "depends_on": list(h.depends_on),
                "rendered_arg": _render_template(
                    h.arg_template, state,
                    repeat_ctx=getattr(h, "_repeat_ctx", None),
                ),
            })


# ── Helpers ───────────────────────────────────────────────────────────


# Phase 6.14.3 — capabilities whose results need a settle-fence so the
# next hop reads consistent KG/Supabase state. Match by name OR by the
# direct: target containing one of these substrings.
_KG_WRITE_HINTS = (
    "create", "add", "promote", "score", "update", "delete", "move",
    "_set", "save", "store", "register", "seed", "consolidate",
)


def _looks_like_kg_write(hop) -> bool:
    cap = (getattr(hop, "capability", None) or "").lower()
    tgt = (getattr(hop, "execution_target", None) or "").lower()
    for h in _KG_WRITE_HINTS:
        if h in cap or h in tgt:
            return True
    return False


_REPEAT_TOKEN_RE = re.compile(
    r"\{\{\s*(item|loop\.index0?|loop\.value)\s*\}\}",
)


def _render_template(
    tmpl: str,
    state: Dict[str, Any],
    repeat_ctx: Optional[Dict[str, Any]] = None,
) -> str:
    """Render a hop's arg_template:
       - `{{state.foo}}`    → state['foo']  (dotted path supported)
       - `{{item}}`         → repeat_ctx['item']
       - `{{loop.index}}`   → repeat_ctx['index'] (1-based)
       - `{{loop.index0}}`  → repeat_ctx['index0'] (0-based)
       - `{{loop.value}}`   → repeat_ctx['value']  (alias for item)
    """
    if not tmpl:
        return ""

    def state_repl(m):
        # Phase 11.N — Capture group has the leading `state.` already stripped
        # by the regex. Path can mix dot- and bracket-segments, e.g.
        # `ideas[0]`, `ideas.0`, `foo[2].bar`. Tokenize uniformly.
        raw = m.group(1)
        # Convert bracket-style `[N]` to dot-style `.N` so `re.split(r'\.')`
        # parses both forms identically.
        raw = re.sub(r"\[(\d+)\]", r".\1", raw).strip(".")
        path = [p for p in raw.split(".") if p]
        if not path:
            return ""
        cur: Any = state.get(path[0])
        for p in path[1:]:
            if isinstance(cur, dict):
                cur = cur.get(p)
            elif isinstance(cur, list):
                try:
                    idx = int(p)
                    cur = cur[idx] if -len(cur) <= idx < len(cur) else None
                except (ValueError, TypeError):
                    cur = None
                    break
            else:
                cur = None
                break
        if cur is None:
            return ""
        return str(cur)

    out = _TEMPLATE_RE.sub(state_repl, tmpl)

    if repeat_ctx is not None:
        def repeat_repl(m):
            tok = m.group(1)
            if tok == "item":
                return str(repeat_ctx.get("item", ""))
            if tok == "loop.index":
                return str(repeat_ctx.get("index", ""))
            if tok == "loop.index0":
                return str(repeat_ctx.get("index0", ""))
            if tok == "loop.value":
                return str(repeat_ctx.get("value", repeat_ctx.get("item", "")))
            return ""
        out = _REPEAT_TOKEN_RE.sub(repeat_repl, out)

    return out


def _safe_state_snapshot(state: Dict[str, Any]) -> Dict[str, Any]:
    """Truncate state values for record/UI so big payloads don't blow JSONL."""
    out: Dict[str, Any] = {}
    for k, v in state.items():
        try:
            s = repr(v)
        except Exception:
            s = "<unrepr>"
        if len(s) > 1500:
            s = s[:1500] + "...<truncated>"
        out[k] = s
    return out


def _hop_event(h: HopSpec, hr: HopResult) -> Dict[str, Any]:
    """Trim hop result for SSE event payload."""
    res = hr.result
    try:
        result_preview = repr(res)[:500]
    except Exception:
        result_preview = "<unrepr>"
    return {
        "step_id": hr.step_id,
        "description": h.description,
        "capability": hr.capability,
        "target": hr.target,
        "ok": hr.ok,
        "error": hr.error,
        "elapsed_s": hr.elapsed_s,
        "validator": hr.validator_verdict,
        "result_preview": result_preview,
        "rendered_arg": hr.rendered_arg,
        "kg_hits": hr.kg_hits,
        "retried": hr.retried,
        "bridges": hr.bridges,
    }
