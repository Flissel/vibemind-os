"""
SubagentDispatcher — execute LLM_AGENT ToolTemplates.

Phase E. Brain's ToolLibrary registers ToolTemplates (claude_subagent,
groq_subagent), but ToolLibrary itself only does parameter inference.
This module is the bridge: given a ToolCall (with parameters filled),
it actually invokes the right LLM via the existing MultiLLMRouter
infrastructure and returns the response.

Why not call MultiLLMRouter directly? Because Brain's call sites should
not need to know the routing details (Anthropic vs Groq, model strings,
fallback behavior, error handling). They just say "claude_subagent" or
"groq_subagent" and get text back.

Design:
  - Synchronous (caller waits for response — short subtasks <30s)
  - Falls back gracefully if router unavailable
  - Tracks per-tool stats (calls, failures, total tokens estimate)
  - Threadsafe (single dispatcher per Brain instance)

API:
    dispatcher = SubagentDispatcher(llm_router)
    result = dispatcher.dispatch("claude_subagent",
                                 prompt="Refactor this function...",
                                 system="You are a senior dev.",
                                 max_tokens=512)
    # result: {"text": str, "tool": str, "model": str, "ok": bool, "error": ...}
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class SubagentDispatcher:
    """Execute LLM_AGENT ToolCalls via Brain's MultiLLMRouter."""

    def __init__(self, llm_router: Any) -> None:
        """
        Args:
            llm_router: a MultiLLMRouter instance (or None — dispatch becomes
                a graceful no-op).
        """
        self._router = llm_router
        self._lock = threading.Lock()
        self.stats: Dict[str, Any] = {
            "calls_total": 0,
            "calls_per_tool": {},
            "failures": 0,
            "last_error": None,
            "last_call_ts": None,
        }

    # ── Public API ────────────────────────────────────────────────────

    def dispatch(self, tool_name: str, **kwargs: Any) -> Dict[str, Any]:
        """Execute an LLM_AGENT subtask.

        Args:
            tool_name: 'claude_subagent' or 'groq_subagent'
            **kwargs: parameters for the tool (prompt, system, model, max_tokens, ...)

        Returns:
            {ok: bool, text: str, tool: str, model: str, latency_ms: float,
             error: Optional[str]}
        """
        with self._lock:
            self.stats["calls_total"] += 1
            self.stats["calls_per_tool"][tool_name] = (
                self.stats["calls_per_tool"].get(tool_name, 0) + 1
            )
            self.stats["last_call_ts"] = time.time()

        if self._router is None:
            self._record_failure(tool_name, "no router")
            return {
                "ok": False, "tool": tool_name, "text": "",
                "error": "MultiLLMRouter not available",
            }

        if tool_name == "claude_subagent":
            return self._dispatch_llm(
                tool_name=tool_name,
                prompt=kwargs.get("prompt", ""),
                system=kwargs.get("system", ""),
                model=kwargs.get("model", "anthropic/claude-haiku-4.5"),
                max_tokens=int(kwargs.get("max_tokens", 1024)),
                temperature=float(kwargs.get("temperature", 0)),
            )
        elif tool_name == "groq_subagent":
            return self._dispatch_llm(
                tool_name=tool_name,
                prompt=kwargs.get("prompt", ""),
                system=kwargs.get("system", ""),
                model=kwargs.get("model", "groq::llama-3.3-70b-versatile"),
                max_tokens=int(kwargs.get("max_tokens", 512)),
                temperature=float(kwargs.get("temperature", 0.3)),
            )
        else:
            self._record_failure(tool_name, "unknown tool")
            return {
                "ok": False, "tool": tool_name, "text": "",
                "error": f"unknown LLM_AGENT tool: {tool_name}",
            }

    # ── Implementation ────────────────────────────────────────────────

    def _dispatch_llm(
        self,
        tool_name: str,
        prompt: str,
        system: str,
        model: str,
        max_tokens: int,
        temperature: float,
    ) -> Dict[str, Any]:
        """Single LLM call via MultiLLMRouter._call_openrouter."""
        if not prompt or len(prompt.strip()) < 1:
            self._record_failure(tool_name, "empty prompt")
            return {
                "ok": False, "tool": tool_name, "model": model, "text": "",
                "error": "empty prompt",
            }

        # Build full prompt with optional system context
        if system:
            full_prompt = f"[System: {system}]\n\n{prompt}"
        else:
            full_prompt = prompt

        t0 = time.time()
        try:
            text = self._router._call_openrouter(
                model=model,
                prompt=full_prompt,
                max_tokens=max_tokens,
                temperature=temperature,
            )
            latency_ms = (time.time() - t0) * 1000.0
            return {
                "ok": True,
                "tool": tool_name,
                "model": model,
                "text": (text or "").strip(),
                "latency_ms": round(latency_ms, 1),
                "prompt_len": len(full_prompt),
            }
        except Exception as e:
            latency_ms = (time.time() - t0) * 1000.0
            self._record_failure(tool_name, f"{type(e).__name__}: {e}")
            return {
                "ok": False,
                "tool": tool_name,
                "model": model,
                "text": "",
                "latency_ms": round(latency_ms, 1),
                "error": f"{type(e).__name__}: {e}",
            }

    def _record_failure(self, tool: str, reason: str) -> None:
        with self._lock:
            self.stats["failures"] += 1
            self.stats["last_error"] = f"{tool}: {reason}"
        logger.debug("[SubagentDispatcher] %s failed: %s", tool, reason)
