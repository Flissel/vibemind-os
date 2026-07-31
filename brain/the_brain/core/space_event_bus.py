"""Phase 11.F — Space Event Bus.

Cross-process pub/sub for VibeMind Space-events (bubble.*, idea.*, etc).
Tools (bubble_tools, idea_tools) running in any process POST to
/api/events/publish; the brain-server maintains a ring-buffer and an
SSE stream that the dashboard consumes.

Distinct from `event_bus.py` (Phase 7 in-process pub/sub for brain modules) —
this one is for cross-process tool-call events visible in the UI.

Schema for one event (all fields optional except event_id):
  {
    "ts": 1234567890.123,        # unix-ts, server-side if missing
    "event_id": "bubble.create", # required
    "params": {...},             # the args the tool was called with
    "result": "...",             # short summary (string, max 300)
    "ok": true,                  # success flag
    "source": "spaces-ideas/bubble_create",  # which tool emitted it
    "agent": "brain-bubbles",    # if dispatched via an agent (Phase 11.B)
    "plan_id": "...",            # if part of a multi-hop plan
    "context": {...},            # optional extra context
  }

Public API:
  bus = SpaceEventBus(maxlen=500)
  bus.publish(event_dict)         # add to ring + notify subscribers
  bus.subscribe() -> queue        # async queue for SSE
  bus.unsubscribe(queue)
  bus.recent(limit=50) -> list    # last N events (for first SSE hit)
"""

from __future__ import annotations

import asyncio
import logging
import time
import threading
import weakref
from collections import deque
from typing import Any, Dict, List, Optional
from uuid import UUID

logger = logging.getLogger(__name__)


class SpaceEventBus:
    """In-memory ring-buffer + asyncio queues for SSE subscribers."""

    def __init__(self, maxlen: int = 500) -> None:
        self._buffer: deque = deque(maxlen=maxlen)
        self._lock = threading.RLock()
        self._subscribers: "weakref.WeakSet[asyncio.Queue]" = weakref.WeakSet()
        self._publish_loop: Optional[asyncio.AbstractEventLoop] = None
        self.stats: Dict[str, int] = {
            "events_published": 0,
            "events_dropped": 0,
            "subscribers_alive": 0,
            "auto_refresh_fired": 0,
            "auto_refresh_coalesced": 0,
        }
        # Phase 11.U.H — debounce the auto ui.refresh_bubbles. A batch of
        # mutating events (e.g. 3 idea.create in <1s) must collapse to ONE
        # refresh, not 3. Without this, every node write triggered a full
        # renderer canvas reload, and combined with the SSE bridge's ~2s
        # reconnect storm it produced the runaway auto-routing the user saw.
        self._last_refresh_ts: float = 0.0
        self._refresh_min_interval_s: float = 2.5

    def attach_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Set the asyncio loop on which subscriber-queues live (the FastAPI loop)."""
        self._publish_loop = loop

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=200)
        with self._lock:
            self._subscribers.add(q)
            self.stats["subscribers_alive"] = len(self._subscribers)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        with self._lock:
            try:
                self._subscribers.remove(q)
            except KeyError:
                pass
            self.stats["subscribers_alive"] = len(self._subscribers)

    def recent(self, limit: int = 50) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self._buffer)[-limit:]

    # Phase 11.U.E — events that mutate canvas state. After publishing
    # one of these, automatically follow up with a `ui.refresh_bubbles`
    # tick so the renderer pulls fresh truth from the DB. This bypasses
    # IPC-schema drift (e.g. node_added with wrong title field) — the
    # renderer just re-fetches and re-renders the affected bubble.
    _AUTO_REFRESH_TRIGGERS = {
        "bubble.create", "bubble.update", "bubble.delete",
        "idea.create",  "idea.update",  "idea.delete",
        "idea.connect", "idea.disconnect", "idea.auto_link",
        "idea.move",
    }
    _BUBBLES_OPERATION_EVENT_ID = "bubbles.operation_projection"
    _BUBBLES_LIFECYCLES = {
        "planned",
        "approval_required",
        "queued",
        "running",
        "completed",
        "succeeded",
        "failed",
        "cancelled",
        "blocked_dependency",
    }
    _COMPLETED_BUBBLES_LIFECYCLES = {"completed", "succeeded"}

    @staticmethod
    def _is_database_uuid(value: object) -> bool:
        if not isinstance(value, str) or not value.strip():
            return False
        try:
            UUID(value)
        except (TypeError, ValueError, AttributeError):
            return False
        return True

    @staticmethod
    def _is_safe_score_snapshot(value: object) -> bool:
        if not isinstance(value, dict) or not value:
            return False
        return all(
            isinstance(key, str)
            and key.strip()
            and (
                item is None
                or type(item) in {str, int, float, bool}
            )
            for key, item in value.items()
        )

    @classmethod
    def _project_bubbles_operation(cls, event: Dict[str, Any]) -> bool:
        """Validate and sanitize the renderer projection envelope only.

        This does not bind to ``BubblesOrchestrationContract`` and cannot
        establish its acceptance, provider execution, or terminal success.
        A malformed envelope remains observable as unverified. Legacy
        ``shuttle.*`` events never enter this branch because they are workflow
        aliases rather than canonical Bubbles operations.
        """
        raw_params = event.get("params")
        params = raw_params if isinstance(raw_params, dict) else {}
        canonical_space_id = params.get("canonical_space_id")
        bubble_id = params.get("bubble_id")
        operation_id = params.get("operation_id")
        lifecycle = params.get("lifecycle")
        score_snapshot = params.get("score_snapshot")
        evidence_refs = params.get("evidence_refs")
        valid_score_snapshot = cls._is_safe_score_snapshot(score_snapshot)
        valid_evidence_refs = isinstance(evidence_refs, list) and all(
            isinstance(reference, str) and reference.strip()
            for reference in evidence_refs
        )

        reason = "renderer_projection_envelope_valid"
        if canonical_space_id != "bubbles":
            reason = (
                "missing_canonical_space_id"
                if canonical_space_id is None
                else "foreign_canonical_space_id"
            )
        elif bubble_id is None or (isinstance(bubble_id, str) and not bubble_id.strip()):
            reason = "missing_bubble_id"
        elif not cls._is_database_uuid(bubble_id):
            reason = "invalid_bubble_id"
        elif not isinstance(operation_id, str) or not operation_id.strip():
            reason = "missing_operation_id"
        elif not isinstance(lifecycle, str) or lifecycle not in cls._BUBBLES_LIFECYCLES:
            reason = "invalid_lifecycle"
        elif not valid_score_snapshot:
            reason = "missing_score_snapshot" if score_snapshot is None else "invalid_score_snapshot"
        elif evidence_refs is None:
            reason = "missing_evidence_refs"
        elif not valid_evidence_refs:
            reason = "invalid_evidence_refs"
        elif lifecycle in cls._COMPLETED_BUBBLES_LIFECYCLES and not evidence_refs:
            reason = "completed_requires_evidence"

        verified = reason == "renderer_projection_envelope_valid"
        event["params"] = {
            "canonical_space_id": "bubbles",
            "bubble_id": str(UUID(bubble_id)) if cls._is_database_uuid(bubble_id) else None,
            "operation_id": operation_id.strip() if isinstance(operation_id, str) else None,
            "lifecycle": (
                lifecycle
                if isinstance(lifecycle, str) and lifecycle in cls._BUBBLES_LIFECYCLES
                else None
            ),
            "score_snapshot": dict(score_snapshot) if valid_score_snapshot else {},
            "evidence_refs": list(evidence_refs) if valid_evidence_refs else [],
            "verified": verified,
            "verification_reason": reason,
        }
        event["ok"] = verified
        event["result"] = (
            "Renderer projection envelope validated; orchestration acceptance and execution are not asserted."
            if verified
            else "Unverified renderer projection envelope: " + reason
        )
        return verified

    def publish(self, event: Dict[str, Any]) -> Dict[str, Any]:
        """Add an event to the ring and broadcast to all subscribers.

        Phase 11.U.E — mutating events trigger an auto-follow-up
        `ui.refresh_bubbles` event so the renderer pulls fresh state.
        Skipped when the event IS `ui.refresh_bubbles` (no recursion).
        """
        if not isinstance(event, dict) or not event.get("event_id"):
            self.stats["events_dropped"] += 1
            return {"ok": False, "error": "missing event_id"}
        event = dict(event)
        is_verified_projection = True
        if event.get("event_id") == self._BUBBLES_OPERATION_EVENT_ID:
            is_verified_projection = self._project_bubbles_operation(event)
        event.setdefault("ts", time.time())
        event["_seq"] = self.stats["events_published"]

        with self._lock:
            self._buffer.append(event)
            self.stats["events_published"] += 1
            subs = list(self._subscribers)
            self.stats["subscribers_alive"] = len(subs)

        loop = self._publish_loop
        if loop is not None:
            for q in subs:
                try:
                    loop.call_soon_threadsafe(q.put_nowait, event)
                except Exception:
                    pass

        # Auto follow-up refresh (recurses through self.publish once, but
        # the recursion stops because `ui.refresh_bubbles` is NOT in the
        # _AUTO_REFRESH_TRIGGERS set).
        eid = event.get("event_id", "")
        if eid in self._AUTO_REFRESH_TRIGGERS:
            now = time.time()
            with self._lock:
                elapsed = now - self._last_refresh_ts
                if elapsed >= self._refresh_min_interval_s:
                    self._last_refresh_ts = now
                    fire = True
                else:
                    fire = False
                    self.stats["auto_refresh_coalesced"] += 1
            if fire:
                self.stats["auto_refresh_fired"] += 1
                try:
                    self.publish({
                        "event_id": "ui.refresh_bubbles",
                        "params": {
                            "trigger": eid,
                            "trigger_seq": event["_seq"],
                        },
                        "ok": True,
                        "result": "auto-resync after " + eid,
                        "source": "space_event_bus/auto_refresh",
                    })
                except Exception:
                    pass
            # When coalesced we deliberately drop the refresh: the renderer
            # already pulls the bubble list on the FIRST refresh of the
            # window, and in-place canvas refresh covers the open bubble.
            # A trailing-edge timer would re-introduce a late surprise
            # refresh — not worth the complexity here.

        return {"ok": is_verified_projection, "seq": event["_seq"]}

    def stats_dict(self) -> Dict[str, Any]:
        with self._lock:
            return {
                **self.stats,
                "buffer_size": len(self._buffer),
                "buffer_max": self._buffer.maxlen,
            }


_INSTANCE: Optional[SpaceEventBus] = None
_INSTANCE_LOCK = threading.Lock()


def get_bus() -> SpaceEventBus:
    global _INSTANCE
    with _INSTANCE_LOCK:
        if _INSTANCE is None:
            _INSTANCE = SpaceEventBus()
        return _INSTANCE
