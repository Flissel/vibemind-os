"""Capability Execution Targets — Phase 4.

Generalises the Phase 1.5 `direct:module:function` shape to multiple
transport kinds:

  direct:<module>:<function>           # python in-process call (Phase 1.5)
  http:<METHOD>:<url>                  # generic HTTP webhook
  mcp:<server>:<tool>                  # local MCP server tool (via brain-core stdio bridge)
  n8n:<workflow_id>                    # n8n workflow trigger
  coding-engine:<endpoint>             # Daves coding-engine HTTP endpoint
  openfang:<agent_name>                # explicit single-agent dispatch via OpenFang
  brain:<route>                        # call back into Brain's own /api/<route>

The router/YAML stays unchanged — it still emits an `execution_target`
string. DiscourseEngine looks up the right executor here based on the
prefix.

Each executor:
  - returns the same envelope shape {ok, result, elapsed_s, error?, target}
  - is lazily resolved (no network call until first use)
  - records its own per-target stats (calls, errors, last_error)

The original DirectExecutor (capability_executor.py) handles `direct:`
and remains the production path for bubble_evaluate. Phase 4 wraps it
plus four new kinds and a unified registry.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Callable, Dict, Optional

import requests

from .capability_executor import DirectExecutor

logger = logging.getLogger(__name__)


# ── Per-kind executor classes ────────────────────────────────────────


class _BaseRemoteExecutor:
    """Common HTTP-style executor base. Subclasses define `_call()`."""

    def __init__(self, target: str) -> None:
        self.target = target
        self._stats: Dict[str, Any] = {
            "calls": 0,
            "errors": 0,
            "last_error": None,
            "last_call_ts": None,
            "total_elapsed_s": 0.0,
        }

    def call(self, *args, **kwargs) -> Dict[str, Any]:
        t0 = time.time()
        self._stats["calls"] += 1
        self._stats["last_call_ts"] = t0
        try:
            payload = self._compose_payload(args, kwargs)
            out = self._call(payload)
            elapsed = time.time() - t0
            self._stats["total_elapsed_s"] += elapsed
            return {
                "ok": True,
                "result": out,
                "elapsed_s": elapsed,
                "target": self.target,
            }
        except Exception as e:
            elapsed = time.time() - t0
            self._stats["errors"] += 1
            self._stats["last_error"] = f"{type(e).__name__}: {e}"
            logger.warning(f"[targets] {self.target} failed: {type(e).__name__}: {e}")
            return {
                "ok": False,
                "error": f"{type(e).__name__}: {e}",
                "elapsed_s": elapsed,
                "target": self.target,
            }

    def call_with_arg(self, arg: Any, arg_kwarg: Optional[str] = None) -> Dict[str, Any]:
        if arg_kwarg:
            return self.call(**{arg_kwarg: arg})
        return self.call(arg)

    def is_resolvable(self) -> bool:
        return True  # remote — only known on first call

    def _compose_payload(self, args, kwargs) -> Dict[str, Any]:
        if kwargs:
            return {k: v for k, v in kwargs.items() if v not in (None, "")}
        if args and isinstance(args[0], dict):
            return {k: v for k, v in args[0].items() if v not in (None, "")}
        if args and args[0] not in (None, ""):
            return {"input": args[0]}
        return {}

    def _call(self, payload: Dict[str, Any]) -> Any:
        raise NotImplementedError

    def stats_dict(self) -> Dict[str, Any]:
        avg_ms = 0.0
        if self._stats["calls"] > 0:
            avg_ms = (self._stats["total_elapsed_s"] / self._stats["calls"]) * 1000
        return {
            "target": self.target,
            "calls": self._stats["calls"],
            "errors": self._stats["errors"],
            "last_error": self._stats["last_error"],
            "avg_call_ms": round(avg_ms, 1),
        }


class HttpExecutor(_BaseRemoteExecutor):
    """Generic HTTP target.

    Spec: `http:<METHOD>:<url>` — METHOD is GET/POST/PUT/DELETE.
    Payload becomes JSON body for POST/PUT, query params for GET/DELETE.
    """

    def __init__(self, target: str) -> None:
        super().__init__(target)
        # Strip 'http:' prefix once, then split METHOD:URL where URL may
        # itself contain a scheme.
        rest = target.split(":", 1)[1] if target.startswith("http:") else target
        if ":" not in rest:
            raise ValueError(f"http target needs method: {target!r}")
        method, url = rest.split(":", 1)
        self.method = method.upper().strip()
        # Re-add scheme if it got eaten — we expect URLs to start with
        # `//` after stripping or contain `//` near the start.
        if not (url.startswith("http://") or url.startswith("https://")):
            url = "http://" + url.lstrip("/")
        self.url = url

    def _call(self, payload: Dict[str, Any]) -> Any:
        timeout = float(os.environ.get("CAPABILITY_HTTP_TIMEOUT_S", "30"))
        if self.method in ("GET", "DELETE"):
            resp = requests.request(self.method, self.url, params=payload, timeout=timeout)
        else:
            resp = requests.request(self.method, self.url, json=payload, timeout=timeout)
        resp.raise_for_status()
        ct = resp.headers.get("content-type", "")
        if ct.startswith("application/json"):
            return resp.json()
        return resp.text


class N8nExecutor(_BaseRemoteExecutor):
    """Trigger an n8n workflow.

    Spec: `n8n:<workflow_id>` — uses the `vibemind-issue-detector` /
    n8n MCP semantics under the hood: HTTP POST to N8N_BASE_URL with
    a JSON body, expecting webhook-trigger semantics. The exact env
    knobs:
      N8N_BASE_URL  (default http://127.0.0.1:5678)
      N8N_API_KEY   (sent as X-N8N-API-KEY header, optional)
    """

    def __init__(self, target: str) -> None:
        super().__init__(target)
        wf = target.split(":", 1)[1] if target.startswith("n8n:") else target
        self.workflow_id = wf.strip()
        self.base = os.environ.get("N8N_BASE_URL", "http://127.0.0.1:5678").rstrip("/")
        self.api_key = os.environ.get("N8N_API_KEY", "")

    def _call(self, payload: Dict[str, Any]) -> Any:
        # n8n exposes per-workflow webhooks at /webhook/<id>
        url = f"{self.base}/webhook/{self.workflow_id}"
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["X-N8N-API-KEY"] = self.api_key
        timeout = float(os.environ.get("CAPABILITY_HTTP_TIMEOUT_S", "60"))
        resp = requests.post(url, json=payload, headers=headers, timeout=timeout)
        resp.raise_for_status()
        ct = resp.headers.get("content-type", "")
        if ct.startswith("application/json"):
            return resp.json()
        return {"raw": resp.text}


class CodingEngineExecutor(_BaseRemoteExecutor):
    """Daves coding-engine HTTP endpoint.

    Spec: `coding-engine:<endpoint>` — POSTs the payload to
    CODING_ENGINE_URL/<endpoint>.
      CODING_ENGINE_URL  (default http://127.0.0.1:5200)
    """

    def __init__(self, target: str) -> None:
        super().__init__(target)
        ep = target.split(":", 1)[1] if target.startswith("coding-engine:") else target
        self.endpoint = ep.strip().lstrip("/")
        self.base = os.environ.get("CODING_ENGINE_URL", "http://127.0.0.1:5200").rstrip("/")

    def _call(self, payload: Dict[str, Any]) -> Any:
        url = f"{self.base}/{self.endpoint}"
        timeout = float(os.environ.get("CAPABILITY_HTTP_TIMEOUT_S", "120"))
        resp = requests.post(url, json=payload, timeout=timeout)
        resp.raise_for_status()
        ct = resp.headers.get("content-type", "")
        if ct.startswith("application/json"):
            return resp.json()
        return {"raw": resp.text}


class OpenFangExecutor(_BaseRemoteExecutor):
    """Single-agent dispatch via OpenFang.

    Spec: `openfang:<agent_name>` — POSTs message to
    OPENFANG_URL/api/agents/<id>/message after resolving id by name.
    """

    def __init__(self, target: str) -> None:
        super().__init__(target)
        name = target.split(":", 1)[1] if target.startswith("openfang:") else target
        self.agent_name = name.strip()
        self.base = os.environ.get("OPENFANG_URL", "http://127.0.0.1:4200").rstrip("/")
        self._agent_id: Optional[str] = None

    def _resolve_id(self) -> Optional[str]:
        if self._agent_id:
            return self._agent_id
        try:
            resp = requests.get(f"{self.base}/api/agents", timeout=10)
            resp.raise_for_status()
            agents = resp.json() if resp.ok else []
            if isinstance(agents, dict):
                agents = agents.get("agents") or []
            for a in agents or []:
                if (a.get("name") or "").lower() == self.agent_name.lower():
                    self._agent_id = a.get("id") or a.get("agent_id")
                    return self._agent_id
        except Exception as e:
            logger.debug(f"[targets:openfang] resolve {self.agent_name}: {e}")
        return None

    def _call(self, payload: Dict[str, Any]) -> Any:
        agent_id = self._resolve_id()
        if not agent_id:
            raise RuntimeError(f"openfang agent '{self.agent_name}' not found")
        message = payload.get("message") or payload.get("input") or json.dumps(payload)
        timeout = float(os.environ.get("CAPABILITY_HTTP_TIMEOUT_S", "120"))
        # Phase 9.0 — prefer streaming endpoint so we capture tool_use events.
        # Falls back to blocking /message when streaming fails or env opts out.
        use_stream = os.environ.get("OPENFANG_STREAM", "1") not in ("0", "false", "False")
        if use_stream:
            try:
                return self._call_streaming(agent_id, message, timeout)
            except Exception as e:
                logger.debug(f"[targets:openfang] streaming failed, falling back: {e}")
        # Blocking fallback
        url = f"{self.base}/api/agents/{agent_id}/message"
        resp = requests.post(url, json={"message": message}, timeout=timeout)
        resp.raise_for_status()
        ct = resp.headers.get("content-type", "")
        if ct.startswith("application/json"):
            data = resp.json()
            # Normalise to streaming-shape so callers don't care
            return {
                "response": data.get("response") or data.get("text") or "",
                "tool_calls": [],
                "usage": {
                    "input_tokens": data.get("input_tokens"),
                    "output_tokens": data.get("output_tokens"),
                    "iterations": data.get("iterations"),
                    "cost_usd": data.get("cost_usd"),
                },
                "stream": False,
            }
        return {"response": resp.text, "tool_calls": [], "usage": {}, "stream": False}

    def _call_streaming(
        self, agent_id: str, message: str, timeout: float,
    ) -> Dict[str, Any]:
        """Phase 9.0 — Use OpenFang's SSE streaming endpoint to capture
        tool_use events. Returns {response, tool_calls: [...], usage}.

        Tool call shape per entry:
            {seq, tool, input, result, ts_start, ts_end, elapsed_ms}

        We don't try to use a streaming SSE library — bare requests with
        stream=True works fine since OpenFang frames are small."""
        import time as _time
        url = f"{self.base}/api/agents/{agent_id}/message/stream"
        resp = requests.post(
            url, json={"message": message},
            stream=True, timeout=timeout,
        )
        resp.raise_for_status()

        response_text_parts: List[str] = []
        tool_calls: List[Dict[str, Any]] = []
        # In-flight tool calls keyed by tool-name (best effort — OpenFang
        # streams use_start then later use_end with the same tool name)
        in_flight: Dict[str, Dict[str, Any]] = {}
        usage_final: Dict[str, Any] = {}
        seq = 0

        current_event = None
        for raw_line in resp.iter_lines(decode_unicode=True):
            if raw_line is None:
                continue
            line = raw_line.strip("\r")
            if not line:
                current_event = None
                continue
            if line.startswith(":"):
                continue  # SSE comment / keepalive
            if line.startswith("event:"):
                current_event = line[6:].strip()
                continue
            if line.startswith("data:"):
                payload_str = line[5:].strip()
                if not payload_str:
                    continue
                try:
                    data = json.loads(payload_str)
                except Exception:
                    continue
                kind = current_event or "chunk"
                if kind == "chunk":
                    if data.get("content"):
                        response_text_parts.append(str(data["content"]))
                elif kind == "tool_use":
                    seq += 1
                    name = data.get("tool") or "unknown_tool"
                    in_flight[name] = {
                        "seq": seq,
                        "tool": name,
                        "input": None,
                        "result": None,
                        "ts_start": _time.time(),
                        "ts_end": None,
                        "elapsed_ms": None,
                    }
                elif kind == "tool_result":
                    name = data.get("tool") or "unknown_tool"
                    entry = in_flight.pop(name, None) or {
                        "seq": (seq := seq + 1),
                        "tool": name,
                        "input": None,
                        "result": None,
                        "ts_start": None,
                        "ts_end": _time.time(),
                        "elapsed_ms": None,
                    }
                    entry["input"] = data.get("input")
                    entry["ts_end"] = _time.time()
                    if entry.get("ts_start"):
                        entry["elapsed_ms"] = round(
                            (entry["ts_end"] - entry["ts_start"]) * 1000, 1,
                        )
                    tool_calls.append(entry)
                elif kind == "done":
                    usage_final = data.get("usage") or {}
                    if data.get("response"):
                        # Some streams send the final concat in 'response'
                        response_text_parts = [str(data["response"])]
                    break
                elif kind == "error":
                    response_text_parts.append(
                        f"[stream error] {data}"
                    )
                    break

        # Anything still in-flight at end → flush as incomplete entries
        for name, entry in in_flight.items():
            entry["ts_end"] = _time.time()
            entry["incomplete"] = True
            tool_calls.append(entry)

        return {
            "response": "".join(response_text_parts).strip(),
            "tool_calls": tool_calls,
            "usage": usage_final,
            "stream": True,
        }


class BrainSelfExecutor(_BaseRemoteExecutor):
    """Call back into Brain's own HTTP API. Useful for chaining
    capabilities without going through full discourse.

    Spec: `brain:<METHOD>:<route>` — e.g. `brain:POST:/api/discourse/intent`.
    """

    def __init__(self, target: str) -> None:
        super().__init__(target)
        rest = target.split(":", 1)[1] if target.startswith("brain:") else target
        if ":" not in rest:
            raise ValueError(f"brain target needs method: {target!r}")
        method, route = rest.split(":", 1)
        self.method = method.upper().strip()
        self.route = "/" + route.lstrip("/")
        self.base = os.environ.get("BRAIN_SELF_URL", "http://127.0.0.1:5000").rstrip("/")

    def _call(self, payload: Dict[str, Any]) -> Any:
        url = f"{self.base}{self.route}"
        timeout = float(os.environ.get("CAPABILITY_HTTP_TIMEOUT_S", "60"))
        if self.method in ("GET", "DELETE"):
            resp = requests.request(self.method, url, params=payload, timeout=timeout)
        else:
            resp = requests.request(self.method, url, json=payload, timeout=timeout)
        resp.raise_for_status()
        ct = resp.headers.get("content-type", "")
        if ct.startswith("application/json"):
            return resp.json()
        return {"raw": resp.text}


class McpExecutor(_BaseRemoteExecutor):
    """MCP tool call — Phase 4 stub.

    Spec: `mcp:<server>:<tool>` — calls a tool on a stdio MCP server via
    the brain-core stdio proxy. Implemented as HTTP POST to a future
    `/api/mcp/dispatch` route on Brain itself, since stdio JSON-RPC
    requires per-tool wiring that is best done as a follow-up.

    For now, this executor returns ok=False with a clear message so a
    capability that uses `mcp:` knows to stay broadcast-only until the
    bridge is finished. The capability still loads cleanly — only calls
    fail until the bridge lands.
    """

    def __init__(self, target: str) -> None:
        super().__init__(target)
        rest = target.split(":", 1)[1] if target.startswith("mcp:") else target
        if ":" not in rest:
            raise ValueError(f"mcp target needs <server>:<tool>: {target!r}")
        self.server, self.tool = rest.split(":", 1)
        self.base = os.environ.get("BRAIN_SELF_URL", "http://127.0.0.1:5000").rstrip("/")

    def _call(self, payload: Dict[str, Any]) -> Any:
        url = f"{self.base}/api/mcp/dispatch"
        body = {"server": self.server, "tool": self.tool, "args": payload}
        timeout = float(os.environ.get("CAPABILITY_HTTP_TIMEOUT_S", "60"))
        resp = requests.post(url, json=body, timeout=timeout)
        if resp.status_code == 404:
            raise RuntimeError(
                "mcp dispatch endpoint not enabled — set MCP_DISPATCH_ENABLED=1"
            )
        resp.raise_for_status()
        return resp.json()


# ── Factory + registry ───────────────────────────────────────────────


_EXECUTOR_KINDS: Dict[str, type] = {
    "http": HttpExecutor,
    "n8n": N8nExecutor,
    "coding-engine": CodingEngineExecutor,
    "openfang": OpenFangExecutor,
    "brain": BrainSelfExecutor,
    "mcp": McpExecutor,
}


def build_executor(target: str):
    """Build a per-target executor. Returns the right kind for the prefix
    or DirectExecutor for `direct:` (Phase 1.5 production path)."""
    if not target or ":" not in target:
        raise ValueError(f"invalid execution_target: {target!r}")
    kind = target.split(":", 1)[0].lower()
    if kind == "direct":
        return DirectExecutor(target)
    cls = _EXECUTOR_KINDS.get(kind)
    if cls is None:
        raise ValueError(f"unsupported execution_target kind: {kind!r}")
    return cls(target)


def supported_kinds() -> Dict[str, str]:
    """Documentation helper — listed in /api/capabilities/targets."""
    return {
        "direct": "direct:<module.path>:<function>",
        "http": "http:<METHOD>:<url>",
        "n8n": "n8n:<workflow_id>",
        "coding-engine": "coding-engine:<endpoint>",
        "openfang": "openfang:<agent_name>",
        "brain": "brain:<METHOD>:<route>",
        "mcp": "mcp:<server>:<tool>",
    }
