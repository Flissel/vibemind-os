"""Broadcast space-changes to the Electron UI.

Reuses the existing `navigate_to_space` / `space_changed` message types
documented in voice/CLAUDE.md. The Electron renderer (glass_bubbles.js)
must listen for these — that wiring is Gap G2 from the audit and lives
outside this MCP.

Two delivery channels (best-effort, both optional):

  1) HTTP POST to electron_backend's bridge port (when NAVIGATOR_BRIDGE_URL set)
  2) Stdout JSON line (picked up by anything reading our stdout)

If neither is available, the broadcast is a no-op and the call still succeeds —
state is updated regardless, so a later reconnect can re-sync.
"""

from __future__ import annotations

import json
import os
import sys
import time
from typing import Any, Dict, Optional

import urllib.request
import urllib.error


_BRIDGE_URL = os.environ.get("NAVIGATOR_BRIDGE_URL", "").strip()
_BRIDGE_TIMEOUT = float(os.environ.get("NAVIGATOR_BRIDGE_TIMEOUT", "1.5"))


def broadcast(message: Dict[str, Any]) -> Dict[str, Any]:
    """Send a navigation message to whatever channels are configured."""
    payload = dict(message)
    payload.setdefault("ts", time.time())
    payload.setdefault("source", "space-navigator")

    delivered_via: list[str] = []

    if _BRIDGE_URL:
        try:
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                _BRIDGE_URL,
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            urllib.request.urlopen(req, timeout=_BRIDGE_TIMEOUT)
            delivered_via.append("http")
        except (urllib.error.URLError, TimeoutError, OSError):
            pass

    try:
        sys.stdout.write(f"__NAV_BROADCAST__ {json.dumps(payload)}\n")
        sys.stdout.flush()
        delivered_via.append("stdout")
    except Exception:
        pass

    return {"delivered_via": delivered_via, "payload": payload}


def navigate(space: str, *, reason: Optional[str] = None) -> Dict[str, Any]:
    return broadcast({
        "type": "navigate_to_space",
        "space": space,
        "reason": reason or "",
    })
