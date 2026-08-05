"""Capability Execution Targets — Phase 4.

Generalises the Phase 1.5 `direct:module:function` shape to multiple
transport kinds:

  direct:<module>:<function>           # python in-process call (Phase 1.5)
  http:<METHOD>:<url>                  # generic HTTP webhook
  mcp:<agent>:<server>:<tool>          # OpenFang MCP tool with bound agent authority
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
import re
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Optional

import requests
import yaml

from .capability_executor import DirectExecutor

logger = logging.getLogger(__name__)


class OpenFangUnavailable(RuntimeError):
    """OpenFang HTTP API unreachable / transiently failing (connection
    refused, timeout, 5xx) — distinct from the agent being genuinely
    absent from a successfully-returned list.

    Subclasses RuntimeError so _BaseRemoteExecutor.call still catches it
    as Exception and surfaces {"ok": False, "error": ...} unchanged; the
    distinct message lets operators tell an OpenFang outage apart from a
    genuinely-missing agent (2026-05-19 fix — the marathon-session
    'agent not found' bug was OpenFang being transiently down, NOT the
    agent missing; proven via OpenFang SQLite DB inspection)."""


# ── Per-kind executor classes ────────────────────────────────────────


# ─── Honest-ok: detect op-results that are actually FAILURES ──────────────────
# Root-caused 2026-06-24: _BaseRemoteExecutor.call set ok=True whenever _call()
# returned WITHOUT raising — even when the op returned "Failed to create bubble"
# or {'ok': False, 'error': ...}. So supabase write failures (and a down DB) were
# masked as hop ok=True, which silently undermines D.2 / GapSentinel / reliability
# (a claimed-ok hop that changed nothing). This makes the hop ok reflect the result.
_FAILURE_DETECTION = os.environ.get("HOP_RESULT_FAILURE_DETECTION", "1") != "0"

_FAIL_PREFIXES = (
    "failed to", "need a ", "could not", "couldn't", "cannot ", "can't ",
    "unable to", "error:", "no execution target", "does not exist",
)

_FAIL_PATTERNS = (
    " not found", " are not connected", "nothing to update",
    "no previous format", "not persisted", "not readable back",
    " cannot be ",
)


def result_indicates_failure(result: Any) -> bool:
    """True if an executor op-result CLEARLY represents a failure (→ hop ok=False).
    Conservative on purpose: an explicit dict ``ok: False`` (or an ``error`` with
    ok not True), or a string starting with a clear failure phrase. Does NOT flag
    ambiguous-empty reads ("No bubbles found." = a valid empty DB, not a failure)
    nor "(unverified ...)" (that is D.2's UNVERIFIED case, a different signal)."""
    if isinstance(result, dict):
        if result.get("ok") is False:
            return True
        return bool(result.get("error")) and result.get("ok") is not True
    if isinstance(result, str):
        s = result.strip().lower()
        return (any(s.startswith(p) for p in _FAIL_PREFIXES)
                or any(p in s for p in _FAIL_PATTERNS))
    return False


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
            # Honest-ok: a non-raising _call that returned a failure result
            # (e.g. "Failed to create bubble", {'ok': False}) is NOT a success.
            failed = _FAILURE_DETECTION and result_indicates_failure(out)
            if failed:
                self._stats["errors"] += 1
            resp = {
                "ok": not failed,
                "result": out,
                "elapsed_s": elapsed,
                "target": self.target,
            }
            if failed:
                resp["error"] = f"op result indicates failure: {str(out)[:160]}"
            return resp
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

    def call_with_arg(
        self, arg: Any, arg_kwarg: Optional[str] = None,
        extra_params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Phase 11.U.C.11 — accept extra_params symmetrically with
        DirectExecutor.call_with_arg so the plan-executor can pass auxiliary
        context (_intent, _description, ...) to any kind of target.
        Remote executors fold extra_params into the payload alongside the
        primary arg."""
        if arg_kwarg:
            payload = {arg_kwarg: arg}
            if extra_params:
                for k, v in extra_params.items():
                    if k != arg_kwarg and v not in (None, ""):
                        payload[k] = v
            return self.call(**payload)
        if extra_params:
            # Positional arg becomes "value" so we can mix it with extras
            payload = {"value": arg, **{
                k: v for k, v in extra_params.items() if v not in (None, "")
            }}
            return self.call(**payload)
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
    """Health-gated coding-engine control-server endpoint.

    Spec: ``coding-engine:<METHOD>:<route>``.  The pinned coding-engine
    exposes this contract from ``infra/control_server/server.py`` on port
    8000.  Every operation first checks ``/api/health`` and fails closed if
    the service cannot prove itself healthy.
    """

    def __init__(self, target: str) -> None:
        super().__init__(target)
        rest = target.split(":", 1)[1] if target.startswith("coding-engine:") else target
        if ":" not in rest:
            raise ValueError(f"coding-engine target needs method:route: {target!r}")
        method, route = rest.split(":", 1)
        self.method = method.upper().strip()
        if self.method not in {"GET", "POST", "PUT", "DELETE"}:
            raise ValueError(f"unsupported coding-engine method: {self.method!r}")
        self.route = "/" + route.lstrip("/")
        self.base = os.environ.get(
            "CODING_ENGINE_URL", "http://127.0.0.1:8000"
        ).rstrip("/")

    def _assert_healthy(self, timeout: float) -> None:
        response = requests.request(
            "GET", f"{self.base}/api/health", timeout=min(timeout, 5.0)
        )
        response.raise_for_status()
        body = response.json()
        if not isinstance(body, dict) or body.get("healthy") is not True:
            raise RuntimeError(f"coding-engine unhealthy: {body!r}")

    def _call(self, payload: Dict[str, Any]) -> Any:
        timeout = float(os.environ.get("CAPABILITY_HTTP_TIMEOUT_S", "120"))
        self._assert_healthy(timeout)

        route = self.route
        request_payload = dict(payload)
        for name in ("project_id",):
            marker = "{" + name + "}"
            if marker in route:
                value = request_payload.pop(name, None)
                if value in (None, ""):
                    raise ValueError(f"coding-engine route requires {name}")
                route = route.replace(marker, str(value))

        if route == "/api/start" and "requirements_json" not in request_payload:
            description = (
                request_payload.pop("description", None)
                or request_payload.pop("_intent", None)
                or request_payload.pop("input", None)
                or request_payload.pop("value", None)
            )
            requirements = dict(request_payload)
            if description not in (None, ""):
                requirements["description"] = description
            request_payload = {"requirements_json": requirements}

        url = f"{self.base}{route}"
        kwargs: Dict[str, Any] = {"timeout": timeout}
        if self.method in {"GET", "DELETE"}:
            kwargs["params"] = request_payload
        else:
            kwargs["json"] = request_payload
        resp = requests.request(self.method, url, **kwargs)
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

    def _resolve_id(self, force: bool = False) -> Optional[str]:
        """Resolve agent_name -> id via GET /api/agents.

        The id is cached for latency, BUT OpenFang regenerates agent ids on
        every respawn (OpenFang restart, agent re-register). A permanently
        cached id goes stale -> all calls fail `agent not found` until Brain
        restarts. `force=True` bypasses + refreshes the cache; callers do
        this once when a call 404s, making the bridge self-healing against
        agent respawns (the real root-cause of the brain-gateway last-mile
        blocker, 2026-05-19)."""
        if self._agent_id and not force:
            return self._agent_id
        # 2026-05-19: distinguish a TRANSPORT failure (OpenFang down /
        # timeout / 5xx / non-JSON) from a clean enumeration where the
        # agent is genuinely absent. The old broad `except Exception ->
        # return None` conflated both -> spurious "agent not found" every
        # time OpenFang was transiently down. Transport failure now
        # raises OpenFangUnavailable (caller retries); only a 2xx list
        # that truly lacks the name returns None.
        try:
            # 2026-05-19: 10s was too long for a retry loop. The agent
            # LIST is a cheap call; a healthy OpenFang answers in <1s, and
            # connection-refused fails instantly. (connect=3s, read=4s)
            # keeps the bounded resolve_budget (~8s) actually enforceable
            # across 4 attempts.
            resp = requests.get(
                f"{self.base}/api/agents", timeout=(3.05, 4)
            )
            resp.raise_for_status()
        except requests.exceptions.RequestException as e:
            status = getattr(getattr(e, "response", None), "status_code", None)
            # Connection refused / timeout / no HTTP response, or a 5xx
            # (OpenFang up but mid-boot) -> transient, retryable.
            if status is None or status >= 500:
                logger.warning(
                    f"[targets:openfang] OpenFang unreachable at "
                    f"{self.base}: {e}"
                )
                raise OpenFangUnavailable(
                    f"OpenFang unreachable at {self.base}: {e}"
                ) from e
            # A non-5xx HTTP error on the LIST endpoint is not a clean
            # enumeration either — treat as transient, don't claim absent.
            logger.warning(
                f"[targets:openfang] OpenFang list endpoint HTTP {status} "
                f"at {self.base}: {e}"
            )
            raise OpenFangUnavailable(
                f"OpenFang list endpoint HTTP {status} at {self.base}: {e}"
            ) from e
        try:
            agents = resp.json()
        except ValueError as e:
            # 2xx but body isn't JSON -> OpenFang misbehaving, not a
            # trustworthy enumeration. Transient.
            logger.warning(
                f"[targets:openfang] OpenFang returned non-JSON agent list "
                f"at {self.base}: {e}"
            )
            raise OpenFangUnavailable(
                f"OpenFang returned non-JSON agent list at {self.base}: {e}"
            ) from e
        if isinstance(agents, dict):
            agents = agents.get("agents") or []
        for a in agents or []:
            if (a.get("name") or "").lower() == self.agent_name.lower():
                self._agent_id = a.get("id") or a.get("agent_id")
                return self._agent_id
        # Clean 2xx list, name genuinely not present. Clear any stale
        # cache (when forced) so a later call after the agent (re)spawns
        # re-resolves cleanly — preserved exactly from the prior behaviour.
        if force:
            self._agent_id = None
        return None

    @staticmethod
    def _is_agent_gone(exc: Exception) -> bool:
        """True if the exception looks like the cached agent id is stale
        (OpenFang 404 / 'not found' for that agent id)."""
        msg = str(exc).lower()
        if "not found" in msg or "no such agent" in msg:
            return True
        resp = getattr(exc, "response", None)
        return resp is not None and getattr(resp, "status_code", None) == 404

    def _call(self, payload: Dict[str, Any]) -> Any:
        message = payload.get("message") or payload.get("input") or json.dumps(payload)
        # Dynamic tool scope (plans/dynamic-agent-tools-prompt.md, Phase 2):
        # Brain kann via extra_params einen _system_prompt_focus mitgeben (vom
        # ToolScopeSelector) — die per-Intent relevanten Tools. Wir praefixen ihn
        # vor die message, damit das Agent-LLM sich auf diese Tools fokussiert
        # (lenkt gpt-5.5 weg vom 71-Tool-Loop). Wirkt OHNE Rust/per-Request-Filter;
        # die echte tool_allowlist-Durchsetzung kommt spaeter (Rust, zurueckgestellt).
        focus = payload.get("_system_prompt_focus")
        if isinstance(focus, str) and focus.strip():
            message = f"{focus.strip()}\n\n---\n\n{message}"
        timeout = float(os.environ.get("CAPABILITY_HTTP_TIMEOUT_S", "120"))

        def _send(agent_id: str) -> Any:
            # Phase 9.0 — prefer streaming endpoint so we capture tool_use
            # events. Falls back to blocking /message when streaming fails
            # or env opts out.  Streaming swallows its own errors and
            # falls through to blocking; blocking raises, so a stale-id
            # 404 always surfaces from the blocking POST below.
            use_stream = os.environ.get("OPENFANG_STREAM", "1") not in (
                "0", "false", "False",
            )
            if use_stream:
                try:
                    return self._call_streaming(agent_id, message, timeout)
                except Exception as e:
                    if self._is_agent_gone(e):
                        raise  # let _call retry with a fresh id
                    logger.debug(
                        f"[targets:openfang] streaming failed, falling back: {e}"
                    )
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
            return {
                "response": resp.text, "tool_calls": [], "usage": {},
                "stream": False,
            }

        # 2026-05-19: bounded resolve-retry. OpenFang (host process)
        # falls over transiently; a single resolve attempt that hits a
        # dead window used to raise a hard, permanent-sounding "not
        # found". Retry across a short bounded budget so a brief outage
        # self-recovers, but short-circuit a CLEAN absence immediately
        # (it won't self-fix inside the window — only burns budget).
        resolve_budget = min(8.0, timeout)
        sleeps = [0.25, 0.5, 1.0, 2.0]
        # Reserve a GET cost so we stop BEFORE a GET that would overrun
        # the budget. The actual cost varies wildly by failure mode:
        # connection-refused on Windows ~2s (OS TCP behaviour, not
        # tunable), fast 5xx ~0.01s. Use the MEASURED cost of the prior
        # attempt as the prediction for the next; bootstrap with a small
        # floor so attempt 0 doesn't preempt itself.
        get_cost_est = 0.1
        t0 = time.monotonic()
        agent_id: Optional[str] = None
        last_unavail: Optional[OpenFangUnavailable] = None
        attempt = -1
        while True:
            attempt += 1
            # Stop before an attempt whose GET can't finish in budget
            # (always allow attempt 0). The hard 8-attempt cap is just a
            # safety net — the real gate is the elapsed-time budget,
            # which is what matters for keeping the call snappy.
            if attempt > 0 and (
                attempt >= 8
                or time.monotonic() - t0 + get_cost_est >= resolve_budget
            ):
                break
            t_get = time.monotonic()
            try:
                agent_id = self._resolve_id(force=(attempt > 0))
            except OpenFangUnavailable as e:
                last_unavail = e
                # Update the cost estimate from this failed attempt.
                get_cost_est = max(get_cost_est, time.monotonic() - t_get)
                # Sleep before the next try only if a following
                # attempt+GET could still finish within budget.
                nxt = sleeps[min(attempt, len(sleeps) - 1)]
                if time.monotonic() - t0 + nxt + get_cost_est < resolve_budget:
                    time.sleep(nxt)
                    continue
                break
            # Successful (or clean-absence) GET — also update estimate.
            get_cost_est = max(get_cost_est, time.monotonic() - t_get)
            if agent_id:
                break
            # Clean 2xx list, agent genuinely absent. One forced confirm
            # if we haven't already forced, then fail FAST — no retry.
            if attempt == 0:
                try:
                    agent_id = self._resolve_id(force=True)
                except OpenFangUnavailable as e:
                    last_unavail = e
                    break
                if agent_id:
                    break
            raise RuntimeError(
                f"openfang agent '{self.agent_name}' not registered "
                f"(OpenFang reachable, agent absent)"
            )
        if not agent_id:
            elapsed = time.monotonic() - t0
            raise OpenFangUnavailable(
                f"OpenFang unreachable at {self.base} after "
                f"{attempt + 1} attempt(s) ({elapsed:.1f}s) — agent "
                f"'{self.agent_name}' could not be resolved"
            ) from last_unavail
        try:
            return _send(agent_id)
        except Exception as e:
            # Self-heal: the cached id is stale (agent respawned with a new
            # id). Drop the cache, re-resolve ONCE, retry. If still gone,
            # the original error stands.
            if not self._is_agent_gone(e):
                raise
            logger.info(
                f"[targets:openfang] '{self.agent_name}' id stale "
                f"({agent_id}); re-resolving"
            )
            try:
                fresh = self._resolve_id(force=True)
            except OpenFangUnavailable:
                # OpenFang went down mid-self-heal — the original send
                # error stands (don't mask the real 404/gone).
                raise e
            if not fresh or fresh == agent_id:
                raise RuntimeError(
                    f"openfang agent '{self.agent_name}' not found "
                    f"(stale id {agent_id}, no fresh id available)"
                )
            return _send(fresh)

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
    """Execute a namespaced MCP tool through OpenFang's HTTP MCP endpoint.

    Spec: ``mcp:<agent>:<server>:<tool>``.  The agent is a canonical
    OpenFang registry name, resolved to its current UUID through the
    authenticated control-plane API.  That UUID is bound by OpenFang's
    ``X-OpenFang-Agent-Id`` transport header; it is never trusted from the
    JSON-RPC body.  OpenFang applies the agent's tool allowlist, approvals,
    execution policy, and audit attribution.

    This executor deliberately has no local/Brain dispatch fallback.  A
    missing gateway configuration, unknown agent, JSON-RPC error, or
    transport failure returns the standard failed envelope from
    ``_BaseRemoteExecutor``.
    """

    _RETRY_ATTEMPTS = 3
    _RETRY_DELAYS_S = (0.25, 0.5)

    def __init__(self, target: str) -> None:
        super().__init__(target)
        rest = target.split(":", 1)[1] if target.startswith("mcp:") else target
        parts = [part.strip() for part in rest.split(":", 2)]
        if len(parts) != 3 or any(not part for part in parts):
            raise ValueError(
                "mcp target requires canonical mcp:<agent>:<server>:<tool>; "
                f"migrate legacy target {target!r}"
            )
        self.agent_name, self.server, self.tool = parts

    @staticmethod
    def _tool_namespace_component(value: str) -> str:
        """Match OpenFang's ``normalize_name`` used for MCP tool names."""
        return value.lower().replace("-", "_")

    @property
    def namespaced_tool(self) -> str:
        return "mcp_{}_{}".format(
            self._tool_namespace_component(self.server),
            self._tool_namespace_component(self.tool),
        )

    def _configuration(self) -> tuple[str, str]:
        base = os.environ.get("OPENFANG_URL", "").strip().rstrip("/")
        if not base:
            raise RuntimeError("OPENFANG_URL is required for OpenFang MCP execution")
        api_key = os.environ.get("OPENFANG_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError("OPENFANG_API_KEY is required for OpenFang MCP execution")
        return base, api_key

    @staticmethod
    def _is_transient_openfang_error(exc: requests.exceptions.RequestException) -> bool:
        response = getattr(exc, "response", None)
        status = getattr(response, "status_code", None)
        return status is None or status in {408, 425, 429} or status >= 500

    def _request_json(
        self,
        method: str,
        url: str,
        *,
        headers: Dict[str, str],
        payload: Optional[Dict[str, Any]] = None,
        retry_transient: bool = False,
        timeout_cap_s: Optional[float] = None,
    ) -> Any:
        """Issue one OpenFang request, retrying only safe idempotent GETs."""
        configured_timeout = float(os.environ.get("CAPABILITY_HTTP_TIMEOUT_S", "60"))
        timeout = min(configured_timeout, timeout_cap_s) if timeout_cap_s else configured_timeout
        last_error: Optional[requests.exceptions.RequestException] = None
        attempts = self._RETRY_ATTEMPTS if retry_transient else 1
        for attempt in range(attempts):
            try:
                if method == "GET":
                    response = requests.get(url, headers=headers, timeout=timeout)
                else:
                    response = requests.post(
                        url, json=payload, headers=headers, timeout=timeout,
                    )
                response.raise_for_status()
                try:
                    return response.json()
                except ValueError as exc:
                    raise RuntimeError(
                        f"OpenFang returned invalid JSON from {url}"
                    ) from exc
            except requests.exceptions.RequestException as exc:
                if not self._is_transient_openfang_error(exc):
                    raise RuntimeError(f"OpenFang request failed at {url}: {exc}") from exc
                last_error = exc
                if attempt < attempts - 1:
                    time.sleep(self._RETRY_DELAYS_S[attempt])
        raise OpenFangUnavailable(
            f"OpenFang unavailable at {url} after {attempts} attempt(s)"
        ) from last_error

    def _resolve_agent_id(self, base: str, headers: Dict[str, str]) -> str:
        # Resolve for every execution. OpenFang restart/re-registration changes
        # agent UUIDs, so caching would leave a permanent stale authority token.
        body = self._request_json(
            "GET",
            f"{base}/api/agents",
            headers=headers,
            retry_transient=True,
            # The agent list is a cheap health/control-plane read. Bound each
            # retry tightly; do not let a generic 60s capability timeout turn
            # a three-attempt resolve into a multi-minute stall.
            timeout_cap_s=4.0,
        )
        agents = body.get("agents") if isinstance(body, dict) else body
        if not isinstance(agents, list):
            raise RuntimeError("OpenFang /api/agents returned an invalid agent list")
        for agent in agents:
            if not isinstance(agent, dict):
                continue
            if str(agent.get("name") or "").lower() != self.agent_name.lower():
                continue
            agent_id = agent.get("id") or agent.get("agent_id")
            if isinstance(agent_id, str) and agent_id.strip():
                return agent_id.strip()
        raise RuntimeError(
            f"OpenFang agent {self.agent_name!r} is not registered; MCP execution denied"
        )

    def _call(self, payload: Dict[str, Any]) -> Any:
        authority = find_registered_mcp_authority(
            self.agent_name, self.server, self.tool
        )
        if authority is None:
            raise PermissionError(
                "MCP agent/server/tool tuple is not registered; execution denied"
            )
        tool_payload, provenance = _extract_mcp_provenance(payload)
        base, api_key = self._configuration()
        control_headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {api_key}",
        }
        agent_id = self._resolve_agent_id(base, control_headers)
        request_id = f"brain-mcp-{uuid.uuid4().hex}"
        execution_headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "X-OpenFang-Agent-Id": agent_id,
        }
        execution_headers["X-OpenFang-Approval-Ref"] = provenance["approval_ref"]
        execution_headers["X-OpenFang-Cost-Ref"] = provenance["cost_ref"]
        response = self._request_json(
            "POST",
            f"{base}/mcp",
            headers=execution_headers,
            payload={
                "jsonrpc": "2.0",
                "id": request_id,
                "method": "tools/call",
                "params": {"name": self.namespaced_tool, "arguments": tool_payload},
            },
            # Do not retry tools/call: the outcome may be unknown after a
            # transport failure and the tool itself may have mutated state.
            retry_transient=False,
        )
        if not isinstance(response, dict):
            raise RuntimeError("OpenFang MCP returned a non-object JSON-RPC response")
        if response.get("jsonrpc") != "2.0":
            raise RuntimeError("OpenFang MCP returned an invalid JSON-RPC version")
        if response.get("id") != request_id:
            raise RuntimeError("OpenFang MCP returned a mismatched JSON-RPC request id")
        has_result = "result" in response
        has_error = "error" in response
        if has_result == has_error:
            raise RuntimeError(
                "OpenFang MCP JSON-RPC response must contain exactly one result or error"
            )
        if has_error:
            error = _redact_evidence(response["error"])
            message = error.get("message") if isinstance(error, dict) else str(error)
            raise RuntimeError(f"OpenFang MCP JSON-RPC error: {message}")
        result = response["result"]
        if not isinstance(result, dict):
            raise RuntimeError("OpenFang MCP JSON-RPC response omitted result")
        if result.get("isError") is True:
            raise RuntimeError("OpenFang MCP tool returned isError=true")
        return result


_N8N_MUTATING_EVENTS = {
    "n8n.generate", "n8n.activate", "n8n.deactivate", "n8n.delete", "n8n.execute",
}
_N8N_IDENTITY_EVENTS = {
    "n8n.activate", "n8n.deactivate", "n8n.delete", "n8n.execute", "n8n.describe",
}
_REDACTED_KEYS = {
    "authorization", "token", "apikey", "api_key", "password", "secret",
    "credential", "credentials", "headers",
}


def _space_registry_path() -> Path:
    configured = os.environ.get("SPACE_AGENT_REGISTRY_PATH", "").strip()
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parents[3] / "config" / "space_agent_registry.yml"


@dataclass(frozen=True)
class McpAuthority:
    agent: str
    server: str
    tool: str

    @property
    def target(self) -> str:
        return f"mcp:{self.agent}:{self.server}:{self.tool}"


def _load_space_registry() -> Dict[str, Any]:
    path = _space_registry_path()
    if not path.is_file():
        raise RuntimeError(f"canonical space registry unavailable: {path}")
    with path.open("r", encoding="utf-8") as handle:
        document = yaml.safe_load(handle) or {}
    spaces = document.get("spaces") or {}
    if not isinstance(spaces, dict):
        raise RuntimeError("canonical space registry has no spaces")
    return spaces


def _mcp_authority(
    event_id: str, space_id: str, space: Dict[str, Any], spec: Dict[str, Any]
) -> McpAuthority:
    execution = spec.get("execution")
    if not isinstance(execution, dict) or execution.get("kind") != "mcp":
        raise RuntimeError(f"{event_id!r} is not a deterministic MCP event")
    values = {
        "agent": space.get("agent"),
        "server": execution.get("server"),
        "tool": spec.get("tool"),
    }
    missing = [
        name for name, value in values.items()
        if not isinstance(value, str) or not value.strip()
    ]
    if missing:
        raise RuntimeError(
            f"deterministic MCP metadata for '{event_id}' is missing "
            f"{', '.join(missing)}"
        )
    if spec.get("required_provenance") != ["approval_ref", "cost_ref"]:
        raise RuntimeError(
            f"deterministic MCP event '{event_id}' requires closed "
            "required_provenance [approval_ref, cost_ref]"
        )
    allowed_servers = space.get("mcp_servers")
    if not isinstance(allowed_servers, list) or values["server"].strip() not in {
        item.strip() for item in allowed_servers
        if isinstance(item, str) and item.strip()
    }:
        raise RuntimeError(
            f"deterministic MCP server for '{event_id}' is not in "
            f"{space_id}.mcp_servers"
        )
    allowed_tools_by_server = space.get("mcp_tools")
    server_tools = (
        allowed_tools_by_server.get(values["server"].strip())
        if isinstance(allowed_tools_by_server, dict) else None
    )
    if not isinstance(server_tools, list) or values["tool"].strip() not in {
        item.strip() for item in server_tools
        if isinstance(item, str) and item.strip()
    }:
        raise RuntimeError(
            f"deterministic MCP tool for '{event_id}' is not in "
            f"{space_id}.mcp_tools for {values['server'].strip()!r}"
        )
    return McpAuthority(**{name: value.strip() for name, value in values.items()})


def find_registered_mcp_authority(
    agent: str, server: str, tool: str,
) -> Optional[McpAuthority]:
    """Return static allowlist identity for a registered MCP tuple, if any."""
    for space_id, space in _load_space_registry().items():
        if not isinstance(space, dict):
            continue
        events = space.get("events") or {}
        if not isinstance(events, dict):
            continue
        for event_id, spec in events.items():
            if not isinstance(spec, dict):
                continue
            execution = spec.get("execution")
            if not isinstance(execution, dict) or execution.get("kind") != "mcp":
                continue
            authority = _mcp_authority(str(event_id), str(space_id), space, spec)
            if (authority.agent, authority.server, authority.tool) == (agent, server, tool):
                return authority
    return None


def _extract_mcp_provenance(
    payload: Dict[str, Any],
) -> tuple[Dict[str, Any], Dict[str, str]]:
    """Extract invocation-specific provenance without exposing it to the tool."""
    tool_payload = dict(payload)
    provenance: Dict[str, str] = {}
    for key in ("approval_ref", "cost_ref"):
        value = tool_payload.pop(key, None)
        if not isinstance(value, str) or not value.strip():
            raise PermissionError(
                f"MCP invocation requires non-empty {key}; execution denied"
            )
        provenance[key] = value.strip()
    return tool_payload, provenance


def _n8n_event_specs() -> Dict[str, Dict[str, Any]]:
    path = _space_registry_path()
    if not path.is_file():
        raise RuntimeError(f"canonical space registry unavailable: {path}")
    with path.open("r", encoding="utf-8") as handle:
        document = yaml.safe_load(handle) or {}
    events = (((document.get("spaces") or {}).get("n8n") or {}).get("events") or {})
    if not isinstance(events, dict):
        raise RuntimeError("canonical space registry has no n8n events")
    return events


def resolve_registry_execution_target(capability: str) -> Optional[str]:
    """Resolve explicit deterministic registry events to OpenFang MCP targets.

    Only events which declare ``execution.kind: mcp`` participate.  This keeps
    other Space routing unchanged while requiring the canonical agent, server,
    and tool to be complete before a deterministic event can execute.
    """
    if not isinstance(capability, str) or not capability:
        return None

    if capability.startswith("n8n."):
        return f"n8n-mcp:{capability}" if capability in _n8n_event_specs() else None

    spaces = _load_space_registry()
    for space_id, space in spaces.items():
        if not isinstance(space, dict):
            continue
        events = space.get("events") or {}
        spec = events.get(capability) if isinstance(events, dict) else None
        if not isinstance(spec, dict):
            continue
        execution = spec.get("execution")
        if not isinstance(execution, dict) or execution.get("kind") != "mcp":
            return None
        return _mcp_authority(capability, str(space_id), space, spec).target
    return None


_CANONICAL_SPACE_EVENT_IDS = {
    "bubble_create": "bubble.create",
    "bubble_update": "bubble.update",
    "bubble_evaluate": "bubble.evaluate",
    "bubble_delete": "bubble.delete",
    "idea_create": "idea.create",
    "idea_add": "idea.create",
    "idea_update": "idea.update",
    "idea_expand": "idea.expand",
    "idea_connect": "idea.connect",
    "idea_to_project": "idea.to_project",
    "code_generate": "code.generate",
    "code_modify": "code.modify",
    "code_status": "code.status",
    "code_show": "code.show",
    "code_preview_start": "code.preview.start",
    "code_preview_stop": "code.preview.stop",
    "code_list": "code.list",
    "code_cancel": "code.cancel",
}


def canonical_space_event_id(capability: str) -> str:
    """Map capability aliases to their canonical registry event ID."""
    return _CANONICAL_SPACE_EVENT_IDS.get(capability, capability)


def resolve_canonical_execution_target(capability: str) -> tuple[str, Optional[str]]:
    """Resolve a capability alias through the canonical Space registry once."""
    event_id = canonical_space_event_id(capability)
    target = resolve_registry_execution_target(event_id)
    if event_id == "idea.connect" and target is None:
        raise RuntimeError("idea.connect requires canonical MCP execution metadata")
    return event_id, target


def _redact_evidence(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): ("[REDACTED]" if str(key).lower() in _REDACTED_KEYS else _redact_evidence(item))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_evidence(item) for item in value]
    if isinstance(value, str):
        redacted = re.sub(r"(?i)bearer\s+[a-z0-9._~+/=-]+", "Bearer [REDACTED]", value)
        for env_name in ("N8N_MCP_TOKEN", "OPENFANG_API_KEY"):
            runtime_token = os.environ.get(env_name, "")
            if runtime_token:
                redacted = redacted.replace(runtime_token, "[REDACTED]")
        return redacted
    return value


def _mcp_result_payload(body: Dict[str, Any]) -> Any:
    if body.get("error"):
        error = body["error"]
        message = error.get("message") if isinstance(error, dict) else str(error)
        raise RuntimeError(f"n8n MCP error: {message}")
    result = body.get("result")
    if isinstance(result, dict) and result.get("isError") is True:
        raise RuntimeError("n8n MCP tool returned isError=true")
    content = result.get("content") if isinstance(result, dict) else None
    if isinstance(content, list):
        for item in content:
            if not isinstance(item, dict) or item.get("type") != "text":
                continue
            text = item.get("text")
            if isinstance(text, str):
                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    return {"message": text[:500]}
    return result


class N8nMcpExecutor(_BaseRemoteExecutor):
    """Execute canonical n8n events through the provider-backed HTTP MCP endpoint."""

    def __init__(self, target: str) -> None:
        super().__init__(target)
        event = target.split(":", 1)[1] if target.startswith("n8n-mcp:") else target
        self.event = event.strip()
        spec = _n8n_event_specs().get(self.event)
        if not isinstance(spec, dict) or not spec.get("tool"):
            raise ValueError(f"unknown canonical n8n event: {self.event!r}")
        self.tool = str(spec["tool"])

    def _call(self, payload: Dict[str, Any]) -> Any:
        endpoint = os.environ.get("N8N_MCP_URL", "").strip()
        if not endpoint:
            raise RuntimeError("N8N_MCP_URL is required; external n8n MCP is unavailable")

        authorized = payload.pop("authorized", False) is True
        if self.event in _N8N_MUTATING_EVENTS and not authorized:
            raise PermissionError(f"explicit authorization required for {self.event}")

        workflow_id = payload.get("workflow_id") or payload.get("id")
        workflow_name = payload.get("name") or payload.get("workflow_name")
        if self.event in _N8N_IDENTITY_EVENTS and not (workflow_id or workflow_name):
            raise ValueError(f"workflow identity required for {self.event} (workflow_id or name)")

        if self.event == "n8n.activate":
            payload["active"] = True
        elif self.event == "n8n.deactivate":
            payload["active"] = False

        request_id = f"brain-n8n-{uuid.uuid4().hex}"
        headers = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
        token = os.environ.get("N8N_MCP_TOKEN", "").strip()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        timeout = float(os.environ.get("CAPABILITY_HTTP_TIMEOUT_S", "60"))
        response = requests.post(
            endpoint,
            json={
                "jsonrpc": "2.0",
                "id": request_id,
                "method": "tools/call",
                "params": {"name": self.tool, "arguments": payload},
            },
            headers=headers,
            timeout=timeout,
        )
        response.raise_for_status()
        body = response.json()
        if not isinstance(body, dict):
            raise RuntimeError("n8n MCP returned a non-object response")
        provider_result = _redact_evidence(_mcp_result_payload(body))
        workflow = {
            key: value for key, value in {
                "id": workflow_id or (provider_result.get("id") if isinstance(provider_result, dict) else None),
                "name": workflow_name or (provider_result.get("name") if isinstance(provider_result, dict) else None),
            }.items() if value not in (None, "")
        }
        return {
            "event": self.event,
            "tool": self.tool,
            "workflow": workflow,
            "provider_backed": True,
            "request_id": request_id,
            "result": provider_result,
        }
class MiroFishExecutor(_BaseRemoteExecutor):
    """Execute canonical MiroFish space operations against its real backend.

    Long-running operations return a job envelope instead of waiting inside a
    Brain hop.  A 2xx response is not sufficient for success: the backend must
    assert ``success=true`` and return the operation-specific identities.
    """

    OPERATIONS = {
        "simulate", "predict", "graph.build", "graph.search",
        "status", "evaluate", "interview",
    }

    def __init__(self, target: str, base_url: Optional[str] = None) -> None:
        super().__init__(target)
        operation = target.split(":", 1)[1] if target.startswith("mirofish:") else target
        operation = operation.strip().lower()
        if operation not in self.OPERATIONS:
            raise ValueError(
                f"mirofish: unknown operation {operation!r} "
                f"(supported: {sorted(self.OPERATIONS)})"
            )
        self.operation = operation
        self.base = (
            base_url
            or os.environ.get("MIROFISH_BASE_URL", "http://127.0.0.1:5001")
        ).rstrip("/")

    @staticmethod
    def _require(payload: Dict[str, Any], *fields: str) -> None:
        missing = [field for field in fields if payload.get(field) in (None, "")]
        if missing:
            raise ValueError(f"mirofish operation requires {', '.join(missing)}")

    def _request(
        self, method: str, endpoint: str, payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        timeout = float(os.environ.get("MIROFISH_HTTP_TIMEOUT_S", "30"))
        kwargs: Dict[str, Any] = {"timeout": timeout}
        if method == "GET":
            kwargs["params"] = payload
        else:
            kwargs["json"] = payload
        response = requests.request(method, f"{self.base}{endpoint}", **kwargs)
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise RuntimeError("MiroFish returned a non-object response")
        if data.get("success") is not True:
            raise RuntimeError(str(data.get("error") or "MiroFish did not confirm success"))
        body = data.get("data")
        if not isinstance(body, dict):
            raise RuntimeError("MiroFish success response has no data object")
        return body

    @staticmethod
    def _envelope(
        operation: str,
        endpoint: str,
        body: Dict[str, Any],
        *,
        state: str,
        job_id: Optional[str] = None,
        project_id: Optional[str] = None,
        graph_id: Optional[str] = None,
        simulation_id: Optional[str] = None,
        model_id: Optional[str] = None,
        include_data: bool = False,
    ) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "operation": operation,
            "state": state,
            "job_id": job_id,
            "project_id": project_id,
            "graph_id": graph_id,
            "simulation_id": simulation_id,
            "model_id": model_id,
            "evidence": {
                "service": "mirofish",
                "endpoint": endpoint,
                "response_success": True,
            },
        }
        if include_data:
            result["data"] = body
        return result

    def _call(self, payload: Dict[str, Any]) -> Any:
        operation = self.operation

        # PlanExecutor carries the canonical voice/API text as `_intent`.  Use
        # it only for secondary fields; durable IDs still come from explicit
        # regex-extracted arguments and are always validated below.
        intent = str(payload.pop("_intent", "") or "")
        payload.pop("_description", None)
        payload.pop("_step_id", None)
        payload.pop("_capability", None)
        if operation == "graph.search" and not payload.get("query"):
            payload["query"] = intent
        if operation == "interview":
            if not payload.get("prompt"):
                payload["prompt"] = intent
            if payload.get("agent_id") in (None, ""):
                match = re.search(r"\bagent(?:\s+id)?\s*[:#]?\s*(\d+)\b", intent, re.IGNORECASE)
                if match:
                    payload["agent_id"] = int(match.group(1))

        if operation == "graph.build":
            self._require(payload, "project_id")
            endpoint = "/api/graph/build"
            body = self._request("POST", endpoint, payload)
            task_id = body.get("task_id")
            project_id = body.get("project_id")
            if not task_id or project_id != payload["project_id"]:
                raise RuntimeError("MiroFish graph build response lacks matching project_id/task_id")
            return self._envelope(
                operation, endpoint, body, state="queued", job_id=str(task_id),
                project_id=str(project_id), graph_id=body.get("graph_id"),
            )

        if operation == "simulate":
            self._require(payload, "simulation_id")
            endpoint = "/api/simulation/start"
            body = self._request("POST", endpoint, payload)
            simulation_id = body.get("simulation_id")
            if simulation_id != payload["simulation_id"]:
                raise RuntimeError("MiroFish simulation response has mismatched simulation_id")
            state = str(body.get("runner_status") or "running")
            return self._envelope(
                operation, endpoint, body, state=state, job_id=str(simulation_id),
                simulation_id=str(simulation_id),
                model_id=str(payload.get("model") or simulation_id),
            )

        if operation in {"predict", "evaluate"}:
            self._require(payload, "simulation_id")
            endpoint = "/api/report/generate"
            body = self._request("POST", endpoint, payload)
            simulation_id = body.get("simulation_id")
            task_id = body.get("task_id")
            report_id = body.get("report_id")
            if simulation_id != payload["simulation_id"] or not (task_id or report_id):
                raise RuntimeError(
                    "MiroFish report response lacks matching simulation_id and task_id/report_id"
                )
            state = str(body.get("status") or ("queued" if task_id else "completed"))
            return self._envelope(
                operation, endpoint, body, state=state,
                job_id=str(task_id or report_id), simulation_id=str(simulation_id),
                model_id=str(payload.get("model") or simulation_id), include_data=True,
            )

        if operation == "graph.search":
            self._require(payload, "graph_id", "query")
            endpoint = "/api/report/tools/search"
            body = self._request("POST", endpoint, payload)
            return self._envelope(
                operation, endpoint, body, state="completed",
                graph_id=str(payload["graph_id"]), include_data=True,
            )

        if operation == "interview":
            self._require(payload, "simulation_id", "agent_id", "prompt")
            endpoint = "/api/simulation/interview"
            body = self._request("POST", endpoint, payload)
            if body.get("agent_id") != payload["agent_id"]:
                raise RuntimeError("MiroFish interview response has mismatched agent_id")
            return self._envelope(
                operation, endpoint, body, state="completed",
                simulation_id=str(payload["simulation_id"]), include_data=True,
            )

        # status supports each durable identity exposed by the backend.
        identifier = (
            payload.get("job_id") or payload.get("task_id")
            or payload.get("simulation_id") or payload.get("report_id")
        )
        if not identifier:
            raise ValueError(
                "mirofish status requires job_id, task_id, simulation_id, or report_id"
            )
        identifier = str(identifier)
        if identifier.startswith("sim_"):
            endpoint = f"/api/simulation/{identifier}/run-status"
            identity = {"simulation_id": identifier}
        elif identifier.startswith("report_"):
            endpoint = f"/api/report/{identifier}"
            identity = {}
        else:
            endpoint = f"/api/graph/task/{identifier}"
            identity = {}
        body = self._request("GET", endpoint, {})
        state = str(body.get("status") or body.get("runner_status") or "unknown")
        return self._envelope(
            operation, endpoint, body, state=state, job_id=identifier,
            simulation_id=identity.get("simulation_id"), include_data=True,
        )


# ── Supabase executor (Phase 11.U.C.8) ───────────────────────────────


class SupabaseExecutor(_BaseRemoteExecutor):
    """Execute idea-graph operations directly against Supabase REST.

    Spec: `supabase:<operation>` — operation is one of:
      - `idea.connect`        params: idea1, idea2 (titles, fuzzy)
      - `idea.disconnect`     params: idea1, idea2 (titles, fuzzy)
      - `idea.auto_link`      params: bubble (title or id), threshold (opt)

    Returns a string suitable for the rule:string_nonempty validator and
    publishes a brain space-event so the UI bridge can render the new edge.
    """

    OPERATIONS = {
        "idea.connect", "idea.disconnect", "idea.auto_link",
        "idea.create", "idea.update", "bubble.evaluate", "bubble.promote",
        "bubble.enter",
        # Phase 11.U.H — full-cap migration ops
        "bubble.create", "bubble.list", "bubble.find", "bubble.update",
        "bubble.delete", "bubble.stats", "bubble.score", "bubble.noop",
        "idea.list", "idea.count", "idea.find", "idea.delete", "idea.move",
        "idea.format", "idea.llm",
        "idea.to_project",
        # Phase 11.W2 Stage B — component-spec lookup (reuses SupabaseExecutor
        # because it needs the same Supabase client to read the bubble's IST-state)
        "component.requirements",
        "rose.recommend", "rose.accept", "rose.status",
    }

    def __init__(self, target: str) -> None:
        super().__init__(target)
        op = target.split(":", 1)[1] if target.startswith("supabase:") else target
        op = op.strip().lower()
        if op not in self.OPERATIONS:
            raise ValueError(
                f"supabase: unknown operation {op!r} "
                f"(supported: {sorted(self.OPERATIONS)})"
            )
        self.operation = op

    def _call(self, payload: Dict[str, Any]) -> Any:
        import asyncio as _asyncio
        from .supabase_ideas_client import SupabaseIdeasClient
        from . import supabase_ideas_ops as _ops

        client = SupabaseIdeasClient()
        # Run async logic in a fresh event loop (we're already in a
        # ThreadPoolExecutor worker — asyncio.run is safe here).
        # Phase 11.U.H — operation → ops-coroutine mapping (replaces the
        # if-ladder; the format/llm ops read payload['_capability'] to know
        # which of their 15/6 variants to run).
        op_map = {
            "idea.connect": _ops.connect_op,
            "idea.disconnect": _ops.disconnect_op,
            "idea.auto_link": _ops.auto_link_op,
            "idea.create": _ops.create_op,
            "idea.update": _ops.update_op,
            "bubble.evaluate": _ops.evaluate_op,
            "bubble.promote": _ops.bubble_promote_op,
            "bubble.enter": _ops.enter_op,
            "bubble.create": _ops.bubble_create_op,
            "bubble.list": _ops.bubble_list_op,
            "bubble.find": _ops.bubble_find_op,
            "bubble.update": _ops.bubble_update_op,
            "bubble.delete": _ops.bubble_delete_op,
            "bubble.stats": _ops.bubble_stats_op,
            "bubble.score": _ops.bubble_score_op,
            "bubble.noop": _ops.bubble_noop_op,
            "idea.list": _ops.idea_list_op,
            "idea.count": _ops.idea_count_op,
            "idea.find": _ops.idea_find_op,
            "idea.delete": _ops.idea_delete_op,
            "idea.move": _ops.idea_move_op,
            "idea.format": _ops.idea_format_op,
            "idea.llm": _ops.idea_llm_op,
            "idea.to_project": _ops.idea_to_project_op,
        }
        # Phase 11.W2 Stage B — component_specs lookup (separate module so
        # the YAML cache is contained; reuses the same Supabase client to
        # read the bubble's IST-state).
        if self.operation == "component.requirements":
            from . import component_specs_ops as _cso
            return _asyncio.run(_cso.lookup_op(client, payload))
        if self.operation.startswith("rose."):
            from . import flowzen_ops as _flowzen
            flowzen_map = {
                "rose.recommend": _flowzen.recommend_op,
                "rose.accept": _flowzen.accept_op,
                "rose.status": _flowzen.status_op,
            }
            return _asyncio.run(flowzen_map[self.operation](client, payload))
        fn = op_map.get(self.operation)
        if fn is None:
            raise ValueError(f"unhandled operation: {self.operation!r}")
        return _asyncio.run(fn(client, payload))


# ── Factory + registry ───────────────────────────────────────────────


_EXECUTOR_KINDS: Dict[str, type] = {
    "http": HttpExecutor,
    "n8n": N8nExecutor,
    "coding-engine": CodingEngineExecutor,
    "openfang": OpenFangExecutor,
    "brain": BrainSelfExecutor,
    "mcp": McpExecutor,
    "n8n-mcp": N8nMcpExecutor,
    "mirofish": MiroFishExecutor,
    "supabase": SupabaseExecutor,
}


def build_executor(target: str):
    """Build a per-target executor. Returns the right kind for the prefix
    or DirectExecutor for `direct:` (Phase 1.5 production path)."""
    if not target or ":" not in target:
        raise ValueError(f"invalid execution_target: {target!r}")
    kind = target.split(":", 1)[0].lower()
    if kind == "direct":
        return DirectExecutor(target)
    if kind == "research":
        from spaces.research.execution_target import ResearchTarget
        return ResearchTarget(target)
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
        "coding-engine": "coding-engine:<METHOD>:<route>",
        "openfang": "openfang:<agent_name>",
        "brain": "brain:<METHOD>:<route>",
        "mcp": "mcp:<agent>:<server>:<tool>",
        "n8n-mcp": "n8n-mcp:<canonical_event>",
        "mirofish": "mirofish:<simulate|predict|graph.build|graph.search|status|evaluate|interview>",
        "supabase": "supabase:<op>  (idea.connect|idea.disconnect|idea.auto_link)",
        "research": "research:<web|scrape|summarize|to_idea>",
    }
