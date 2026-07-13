"""
Multihop -> KotlinGraph ingest adapter.

The PlanExecutor produces one HopResult per completed hop of a multi-hop
plan (`core/plan_schema.py`), each now carrying gate-derived learning
signals (`contract_pass`, `reward`). Nothing writes those hops into the
domain-agnostic episodic memory (`core/kotlin_graph.py`, coordinated by
`core/dual_graph.py`) yet. This module is that write path.

`record_plan(dual_graph, plan, executed, trace_id=...)` turns a completed
plan's hops (in completion order) into one `add_event` call per hop via
`dual_graph.record_event`.

Design notes
------------
- **State is a small, size-bounded fingerprint, not raw results.** Each
  hop's `state`/`next_state` carries `capability`, `completed_hops`,
  `plan_hops`, and a rolling `context_hash` (sha256 chain seeded from
  `plan.intent`). We deliberately never put raw hop results, tool output,
  or user text (beyond the intent hash) into state — KotlinGraph state
  dicts are hashed/indexed and persisted to disk (plan decision #4: size +
  PII).

- **`done` closes the episode; it is a *structural* boundary, not a
  success signal.** KotlinGraph has one global `current_episode_id`;
  every event with `done=False` stays in the current episode, and the
  first `done=True` event closes it and advances the counter. Because
  hops are written in completion order, `done=True` is set ONLY on the
  LAST event of THIS plan — otherwise a later plan's events would be
  folded into this plan's episode (or vice versa). This is orthogonal to
  whether the plan actually *succeeded*. If the write loop dies mid-plan
  (a `record_event` raises), we best-effort emit a synthetic closing
  event (`done=True`, `action="none::aborted"`, `metadata["aborted"]`)
  so the aborted episode does not stay OPEN and swallow the next plan's
  events.

- **Episode SUCCESS is a separate, richer signal carried in metadata.**
  KG-C3 (`KotlinGraph.is_episode_done`) is the 3-condition rule (last hop
  + validator passed if present + no pending hops) for whether an episode
  should be considered a *clean success* for pattern mining purposes. We
  compute it here as `metadata["episode_success"]` on the last event
  (with `pending_hops=0` since `executed` only contains completed hops),
  alongside a simpler `metadata["plan_ok"]` (all hops' `ok` truthy). Both
  are metadata annotations on the boundary event, never conflated with
  the `done` flag itself. `validator_present`/`validator_passed` are fed
  from the hop's *effective* contract_pass (explicit field, else derived
  via `contract_pass_from`) rather than a literal "verdict is a dict"
  check — a hard hop failure (`ok=False`) is a definite non-pass and must
  not fall into KG-C3's vacuous-truth-when-unverified branch; only a
  genuinely unverified *success* (`ok=True`, no usable verdict) is
  vacuously satisfied. The same effective value is what lands in
  `metadata["contract_pass"]` (it is the signal that actually drove
  reward/episode_success).

- **Episode purity under concurrency.** PlanExecutor runs up to 3 plans
  concurrently. `KotlinGraph.add_event` is thread-safe per call, but two
  plans interleaving their `add_event` calls would land in the SAME
  episode (there is one global `current_episode_id`). `record_plan` holds
  a module-level lock for the entire write of one plan's hops so each
  episode contains exactly one plan's events; the critical section does
  no I/O besides the `record_event` calls themselves.
"""

from __future__ import annotations

import hashlib
import logging
import os
import threading
from typing import Any, Dict, Optional

from core.kotlin_graph import KotlinGraph
from core.plan_schema import contract_pass_from

logger = logging.getLogger(__name__)

# Guards the whole per-plan write so two concurrently-executing plans never
# interleave their events into a single KotlinGraph episode.
_WRITE_LOCK = threading.Lock()

_DISABLE_VALUES = {"0", "false", "False"}


def ingest_enabled() -> bool:
    """MULTIHOP_KOTLIN_INGEST env flag, default enabled ("1")."""
    val = os.environ.get("MULTIHOP_KOTLIN_INGEST", "1")
    return val not in _DISABLE_VALUES


def _get(hop: Any, name: str, default: Any = None) -> Any:
    """Duck-typed field access: works for dataclass-like objects (HopResult)
    and plain dicts alike."""
    if isinstance(hop, dict):
        return hop.get(name, default)
    return getattr(hop, name, default)


def _action_for(target: Optional[str], capability: Optional[str]) -> str:
    """target like 'openfang:brain-coder' -> kind='openfang', rest='brain-coder'.
    None/empty target -> kind='none', rest=''."""
    cap = capability or ""
    if target:
        if ":" in target:
            kind, rest = target.split(":", 1)
        else:
            kind, rest = target, ""
    else:
        kind, rest = "none", ""
    return f"{kind}:{rest}:{cap}"


def _effective_contract_pass(ok: bool, contract_pass: Optional[bool], verdict: Any) -> Optional[bool]:
    """The hop's gate verdict: the explicit `contract_pass` field if the hop
    carries one, else derived via `contract_pass_from` (which itself maps
    ok=False -> False regardless of any validator, and ok=True with no/bad
    verdict -> None = unverified)."""
    if contract_pass is not None:
        return contract_pass
    return contract_pass_from(bool(ok), verdict if isinstance(verdict, dict) else None)


def _reward_for(reward: Any, effective_contract_pass: Optional[bool]) -> float:
    if reward is not None:
        try:
            return float(reward)
        except (TypeError, ValueError):
            pass
    if effective_contract_pass is True:
        return 1.0
    if effective_contract_pass is False:
        return -1.0
    return 0.0


def _close_aborted_episode(
    dual_graph: Any,
    plan_id: str,
    trace_id: str,
    completed_hops: int,
    total: int,
    context_hash: str,
    task_class_id: str = "",
) -> None:
    """Best-effort synthetic done=True event so a mid-write failure does not
    leave the KotlinGraph episode OPEN (which would fold the NEXT plan's
    events into this plan's episode). If this also fails, log and give up."""
    try:
        state = {
            "capability": "",
            "completed_hops": completed_hops,
            "plan_hops": total,
            "context_hash": context_hash,
        }
        next_state = dict(state)
        metadata: Dict[str, Any] = {
            "source": "multihop",
            "plan_id": plan_id,
            "trace_id": trace_id,
            "aborted": True,
            "episode_success": False,
            "plan_ok": False,
        }
        if task_class_id:
            metadata["task_class_id"] = task_class_id
        dual_graph.record_event(
            state,
            "none::aborted",
            next_state,
            -1.0,
            True,
            metadata=metadata,
        )
    except Exception:
        logger.warning(
            "record_plan: could not close aborted episode for plan %s — "
            "episode stays open, next plan's events may join it",
            plan_id,
            exc_info=True,
        )


def record_plan(
    dual_graph: Any, plan: Any, executed: Optional[Dict[str, Any]], *,
    trace_id: str = "", task_class_id: str = "",
) -> int:
    """Write one KotlinGraph event per completed hop of `plan`.

    Returns the number of events written: 0 on no-op (flag off, no
    dual_graph, empty executed); partial count on mid-loop failure (plus a
    best-effort synthetic closing event so the episode does not stay open).
    Never raises.
    """
    if not ingest_enabled():
        return 0
    if dual_graph is None:
        return 0
    if not executed:
        return 0

    written = 0
    with _WRITE_LOCK:
        plan_id = ""
        eff_trace_id = trace_id
        episode_closed = False
        total = 0
        h = ""
        try:
            plan_id = getattr(plan, "plan_id", "") or ""
            eff_trace_id = trace_id or getattr(plan, "trace_id", "") or ""
            items = list(executed.items())
            total = len(items)
            intent = getattr(plan, "intent", "") or ""

            h = hashlib.sha256(intent.encode()).hexdigest()[:16]
            all_ok = True

            for i, (step_id, hop) in enumerate(items):
                ok = bool(_get(hop, "ok", False))
                contract_pass = _get(hop, "contract_pass", None)
                reward_field = _get(hop, "reward", None)
                verdict = _get(hop, "validator_verdict", None)
                capability = _get(hop, "capability", None)
                target = _get(hop, "target", None)

                all_ok = all_ok and ok

                action = _action_for(target, capability)
                effective_contract_pass = _effective_contract_pass(ok, contract_pass, verdict)
                reward = _reward_for(reward_field, effective_contract_pass)

                is_last = i == total - 1
                next_h = hashlib.sha256(
                    (h + action + ("ok" if ok else "fail")).encode()
                ).hexdigest()[:16]

                state = {
                    "capability": capability or "",
                    "completed_hops": i,
                    "plan_hops": total,
                    "context_hash": h,
                }
                next_state = {
                    "capability": capability or "",
                    "completed_hops": i + 1,
                    "plan_hops": total,
                    "context_hash": next_h,
                }

                metadata: Dict[str, Any] = {
                    "source": "multihop",
                    "plan_id": plan_id,
                    "trace_id": eff_trace_id,
                    "step_id": step_id,
                    "capability": capability or "",
                    "target": target,
                    "ok": ok,
                    # the COMPUTED effective gate verdict — the value that
                    # actually drove reward/episode_success
                    "contract_pass": effective_contract_pass,
                }
                if task_class_id:
                    metadata["task_class_id"] = task_class_id

                done = is_last
                if is_last:
                    # validator_present widens beyond "verdict is a dict": a
                    # hard hop failure (ok=False) is a DEFINITE non-pass, not
                    # an ambiguous/unverified case, so it must not fall into
                    # KG-C3's vacuous-truth-when-unverified branch. Both
                    # effective_contract_pass=False (from a failing verdict
                    # OR from ok=False) and =True (verdict passed) count as
                    # "a validator ran"; only None (truly unverified success)
                    # is vacuously satisfied.
                    validator_present = effective_contract_pass is not None
                    validator_passed = effective_contract_pass is True
                    metadata["episode_success"] = KotlinGraph.is_episode_done(
                        is_last_hop=True,
                        validator_present=validator_present,
                        validator_passed=validator_passed,
                        pending_hops=0,
                    )
                    metadata["plan_ok"] = all_ok

                dual_graph.record_event(
                    state,
                    action,
                    next_state,
                    reward,
                    done,
                    metadata=metadata,
                )
                written += 1
                if done:
                    episode_closed = True
                h = next_h

        except Exception:
            logger.warning(
                "record_plan: ingest failed after %d events (plan %s)",
                written,
                plan_id,
                exc_info=True,
            )
            # Episode purity: if we wrote any events but never the done=True
            # boundary, the episode is still OPEN — close it (still under the
            # lock, so no other plan can interleave before the close).
            if written > 0 and not episode_closed:
                _close_aborted_episode(
                    dual_graph, plan_id, eff_trace_id, written, total, h,
                    task_class_id=task_class_id,
                )

    return written
