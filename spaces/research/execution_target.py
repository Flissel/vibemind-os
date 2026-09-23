"""Verified Research execution over existing OpenFang tools.

The target deliberately rejects prose-only agent answers. A successful result
requires the agent to have actually written a report file to a path we
dictated, and that report must contain at least one source URL that we count
ourselves. The OpenFang claude-code driver returns `tool_calls: []`
unconditionally (openfang-runtime/src/drivers/claude_code.rs:579), so a check
against `tool_calls` can never be satisfied -- the report file is the only
evidence this process can actually observe.
"""

from __future__ import annotations

import os
import pathlib
import re
from typing import Any, Dict, List

import requests

from spaces.research import brief


_URL_RE = re.compile(r"https?://[^\s<>()\]\[\"']+")
_OPERATIONS = {"web", "scrape", "summarize", "to_idea"}
_CANONICAL_AGENT = "brain-researcher"

ARTIFACT_DIR = pathlib.Path.home() / ".openfang" / "research-artifacts"


def _report_path(run_id: str) -> pathlib.Path:
    return ARTIFACT_DIR / f"research_{run_id}.md"


def _urls(value: Any) -> List[str]:
    found: List[str] = []
    if isinstance(value, str):
        found.extend(_URL_RE.findall(value))
    elif isinstance(value, dict):
        for nested in value.values():
            found.extend(_urls(nested))
    elif isinstance(value, list):
        for nested in value:
            found.extend(_urls(nested))
    return list(dict.fromkeys(url.rstrip(".,;:") for url in found))


class ResearchTarget:
    """One health-checkable Research operation backed by real agent tools."""

    def __init__(self, target: str) -> None:
        operation = target.split(":", 1)[1] if ":" in target else ""
        if operation not in _OPERATIONS:
            raise ValueError(f"unsupported research operation: {operation!r}")
        self.target = target
        self.operation = operation

        # Imported lazily to keep the Space boundary independently testable.
        from core.capability_targets import OpenFangExecutor

        self._agent = OpenFangExecutor(f"openfang:{_CANONICAL_AGENT}")

    def call(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        try:
            payload = self._payload(args, kwargs)
            run_id = brief.new_job_id()
            report = _report_path(run_id)
            ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
            delegated = self._agent.call(message=self._instruction(payload, run_id))
            if not delegated.get("ok"):
                return {
                    "ok": False,
                    "error": f"research infrastructure unavailable: {delegated.get('error', 'agent call failed')}",
                    "target": self.target,
                }
            # Der Beleg ist die Datei, nicht die Antwort des Agenten. Der
            # claude-code-Treiber liefert tool_calls grundsaetzlich leer
            # (openfang-runtime/src/drivers/claude_code.rs:579), darum waere
            # jede Pruefung darauf unerfuellbar.
            if not report.is_file():
                return self._unverified("report file was never written")
            text = report.read_text(encoding="utf-8", errors="replace")
            sources = _urls(text)
            if not sources:
                return self._unverified("no citation found in report")
            return {
                "ok": True,
                "result": {
                    "operation": self.operation,
                    "content": text,
                    "sources": sources,
                    "evidence": {
                        "report_path": str(report),
                        "citation_count": brief.count_citations(text),
                    },
                },
                "target": self.target,
            }
        except Exception as exc:
            return {
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
                "target": self.target,
            }

    def call_with_arg(
        self,
        arg: Any,
        arg_kwarg: str | None = None,
        extra_params: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        payload = dict(extra_params or {})
        payload[arg_kwarg or "input"] = arg
        return self.call(**payload)

    def is_resolvable(self) -> bool:
        return self.health_check()["ok"]

    def health_check(self) -> Dict[str, Any]:
        openfang_url = os.environ.get("OPENFANG_URL", "http://127.0.0.1:4200").rstrip("/")
        qdrant_url = os.environ.get("QDRANT_URL", "http://127.0.0.1:16333").rstrip("/")
        components: Dict[str, Dict[str, Any]] = {}
        # Per-target headers: the OpenFang Bearer token must never ride along
        # to Qdrant's or any other component's health endpoint.
        target_headers: Dict[str, Dict[str, str]] = {
            "openfang": {"Authorization": f"Bearer {os.environ.get('OPENFANG_API_KEY', '')}"},
        }
        for name, url in {
            "openfang": f"{openfang_url}/api/agents",
            "qdrant": f"{qdrant_url}/healthz",
        }.items():
            try:
                response = requests.get(url, headers=target_headers.get(name), timeout=(2, 3))
                response.raise_for_status()
                components[name] = {"ok": True, "url": url}
            except Exception as exc:
                components[name] = {
                    "ok": False,
                    "url": url,
                    "error": f"{type(exc).__name__}: {exc}",
                }
        return {"ok": all(item["ok"] for item in components.values()), "components": components}

    def stats_dict(self) -> Dict[str, Any]:
        return {"target": self.target, "operation": self.operation, "health": self.health_check()}

    def _payload(self, args: tuple[Any, ...], kwargs: Dict[str, Any]) -> Dict[str, Any]:
        if kwargs:
            return {key: value for key, value in kwargs.items() if value not in (None, "")}
        if args and isinstance(args[0], dict):
            return args[0]
        return {"input": args[0]} if args else {}

    def _instruction(self, payload: Dict[str, Any], run_id: str) -> str:
        return (
            f"Execute research.{self.operation} with the available Fetch and web tools. "
            "Do not answer from memory. Every claim needs a source URL.\n"
            "Write the full report with file_write to EXACTLY this absolute path: "
            f"{_report_path(run_id)}\n"
            f"Input: {payload!r}"
        )

    def _unverified(self, reason: str) -> Dict[str, Any]:
        return {"ok": False, "error": f"unverified research result: {reason}", "target": self.target}
