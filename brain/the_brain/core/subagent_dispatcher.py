"""Fail-closed OpenFang dispatch for legacy Brain subagent aliases."""

from __future__ import annotations

import inspect
import logging
import threading
import time
from typing import Any, Dict, Optional

from vibemind_shared import OpenFangUnavailable, get_client, get_client_sync, get_model


logger = logging.getLogger(__name__)


_LEGACY_ROLE_MAP = {
    "claude_subagent": "brain_planning",
    "groq_subagent": "brain_fast_reasoning",
    "openai_subagent": "brain_communication",
    "ollama_subagent": "local_fast",
}


class SubagentDispatcher:
    """Route legacy aliases through the configured OpenFang agents only.

    ``llm_router`` remains accepted so existing construction sites continue to
    work. It is deliberately not used for execution: OpenFang is the sole
    provider boundary for these subagent calls.
    """

    def __init__(self, llm_router: Any = None) -> None:
        self._router = llm_router
        self._lock = threading.Lock()
        self.stats: Dict[str, Any] = {
            "calls_total": 0,
            "calls_per_tool": {},
            "failures": 0,
            "last_error": None,
            "last_call_ts": None,
        }

    def dispatch(self, tool_name: str, **kwargs: Any) -> Dict[str, Any]:
        """Synchronously invoke the configured OpenFang agent for an alias."""
        self._record_call(tool_name)
        role, rejection = self._resolve_role(tool_name, kwargs)
        if rejection is not None:
            return rejection

        prompt = str(kwargs.get("prompt", ""))
        if not prompt.strip():
            return self._failure(tool_name, "empty prompt")

        started_at = time.time()
        try:
            model = self._openfang_model(role)
            client = get_client_sync(role)
            response = client.chat.completions.create(
                model=model,
                messages=self._messages(prompt, kwargs.get("system", "")),
                max_tokens=int(kwargs.get("max_tokens", 1024)),
                temperature=float(kwargs.get("temperature", 0)),
            )
        except OpenFangUnavailable:
            logger.exception("OpenFang unavailable for subagent %s", tool_name)
            self._record_failure(tool_name, "OpenFang unavailable")
            raise
        except Exception as exc:  # The configured OpenFang call failed; do not fall back.
            return self._failure(
                tool_name, f"{type(exc).__name__}: {exc}", self._elapsed_ms(started_at)
            )

        return self._success(
            tool_name, model, response, prompt, self._elapsed_ms(started_at)
        )

    async def adispatch(self, tool_name: str, **kwargs: Any) -> Dict[str, Any]:
        """Asynchronously invoke the configured OpenFang agent for an alias."""
        self._record_call(tool_name)
        role, rejection = self._resolve_role(tool_name, kwargs)
        if rejection is not None:
            return rejection

        prompt = str(kwargs.get("prompt", ""))
        if not prompt.strip():
            return self._failure(tool_name, "empty prompt")

        started_at = time.time()
        try:
            model = self._openfang_model(role)
            client = get_client(role)
            if inspect.isawaitable(client):
                client = await client
            response = client.chat.completions.create(
                model=model,
                messages=self._messages(prompt, kwargs.get("system", "")),
                max_tokens=int(kwargs.get("max_tokens", 1024)),
                temperature=float(kwargs.get("temperature", 0)),
            )
            if inspect.isawaitable(response):
                response = await response
        except OpenFangUnavailable:
            logger.exception("OpenFang unavailable for subagent %s", tool_name)
            self._record_failure(tool_name, "OpenFang unavailable")
            raise
        except Exception as exc:  # The configured OpenFang call failed; do not fall back.
            return self._failure(
                tool_name, f"{type(exc).__name__}: {exc}", self._elapsed_ms(started_at)
            )

        return self._success(
            tool_name, model, response, prompt, self._elapsed_ms(started_at)
        )

    def _resolve_role(
        self, tool_name: str, kwargs: Dict[str, Any]
    ) -> tuple[Optional[str], Optional[Dict[str, Any]]]:
        if "model" in kwargs or "provider" in kwargs:
            logger.warning(
                "Ignoring direct provider/model override for subagent %s; "
                "OpenFang role configuration is authoritative",
                tool_name,
            )
        role = _LEGACY_ROLE_MAP.get(tool_name)
        if role is None:
            return None, self._failure(tool_name, f"unknown LLM_AGENT tool: {tool_name}")
        return role, None

    @staticmethod
    def _messages(prompt: str, system: Any) -> list[Dict[str, str]]:
        messages: list[Dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": str(system)})
        messages.append({"role": "user", "content": prompt})
        return messages

    @staticmethod
    def _response_text(response: Any) -> str:
        choices = getattr(response, "choices", None)
        if not choices:
            return ""
        message = getattr(choices[0], "message", None)
        content = getattr(message, "content", "")
        return str(content or "").strip()

    @staticmethod
    def _openfang_model(role: str) -> str:
        model = str(get_model(role))
        if not model.startswith("openfang:"):
            raise RuntimeError(f"role {role!r} is not configured for OpenFang")
        return model

    @staticmethod
    def _elapsed_ms(started_at: float) -> float:
        return round((time.time() - started_at) * 1000.0, 1)

    def _success(
        self,
        tool_name: str,
        model: str,
        response: Any,
        prompt: str,
        latency_ms: float,
    ) -> Dict[str, Any]:
        return {
            "ok": True,
            "tool": tool_name,
            "model": model,
            "text": self._response_text(response),
            "prompt_len": len(prompt),
            "latency_ms": latency_ms,
            "error": None,
        }

    def _failure(
        self, tool_name: str, reason: str, latency_ms: Optional[float] = None
    ) -> Dict[str, Any]:
        self._record_failure(tool_name, reason)
        result: Dict[str, Any] = {
            "ok": False,
            "tool": tool_name,
            "text": "",
            "error": reason,
        }
        if latency_ms is not None:
            result["latency_ms"] = latency_ms
        return result

    def _record_call(self, tool_name: str) -> None:
        with self._lock:
            self.stats["calls_total"] += 1
            self.stats["calls_per_tool"][tool_name] = (
                self.stats["calls_per_tool"].get(tool_name, 0) + 1
            )
            self.stats["last_call_ts"] = time.time()

    def _record_failure(self, tool_name: str, reason: str) -> None:
        with self._lock:
            self.stats["failures"] += 1
            self.stats["last_error"] = f"{tool_name}: {reason}"
        logger.debug("[SubagentDispatcher] %s failed: %s", tool_name, reason)
