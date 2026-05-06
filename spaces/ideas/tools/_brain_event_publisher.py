"""Phase 11.F — Cross-process event publisher.

Tools (bubble_tools, idea_tools) call this to fire-and-forget post events
to the brain server's event bus. Non-blocking: a daemon thread does the
HTTP POST so the calling tool doesn't pay latency.

Usage from a tool:
    from ._brain_event_publisher import publish
    publish("bubble.create", params={"title": ...}, result="...", ok=True)
"""
from __future__ import annotations

import json
import logging
import os
import queue
import threading
import time
from typing import Any, Dict, Optional
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)


_BRAIN_URL = os.environ.get("BRAIN_URL", "http://127.0.0.1:5000").rstrip("/")
_PUBLISH_PATH = "/api/events/publish"
_HTTP_TIMEOUT = 1.5  # we don't want this to slow down tool-calls


_q: "queue.Queue[Dict[str, Any]]" = queue.Queue(maxsize=1000)
_started = False
_lock = threading.Lock()


def _worker():
    while True:
        try:
            payload = _q.get(timeout=10)
        except queue.Empty:
            continue
        try:
            data = json.dumps(payload).encode("utf-8")
            req = Request(
                _BRAIN_URL + _PUBLISH_PATH,
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            urlopen(req, timeout=_HTTP_TIMEOUT)
        except Exception as e:
            # Brain might be down or another process; never raise
            logger.debug(f"[event-publish] failed: {e}")


def _ensure_worker_started():
    global _started
    with _lock:
        if not _started:
            t = threading.Thread(target=_worker, name="brain-event-publisher",
                                  daemon=True)
            t.start()
            _started = True


def publish(
    event_id: str,
    params: Optional[Dict[str, Any]] = None,
    result: Optional[str] = None,
    ok: bool = True,
    source: str = "",
    agent: str = "",
    plan_id: str = "",
    context: Optional[Dict[str, Any]] = None,
) -> None:
    """Fire-and-forget. Returns immediately; HTTP POST happens in worker."""
    _ensure_worker_started()
    try:
        payload = {
            "ts": time.time(),
            "event_id": event_id,
            "params": params or {},
            "ok": ok,
        }
        if result is not None:
            payload["result"] = (str(result)[:300]) if result else ""
        if source:
            payload["source"] = source
        if agent:
            payload["agent"] = agent
        if plan_id:
            payload["plan_id"] = plan_id
        if context:
            payload["context"] = context
        _q.put_nowait(payload)
    except queue.Full:
        # If brain is offline and queue fills, drop oldest
        try:
            _q.get_nowait()
            _q.put_nowait(payload)
        except Exception:
            pass
