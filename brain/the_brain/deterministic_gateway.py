"""One-tool, no-model gateway for the deterministic Rowboat status check.

The gateway deliberately has no planner, model client, provider client, or
application transport.  ``McpExecutor`` remains the sole execution path so
OpenFang keeps agent, approval, cost, and MCP authority.
"""

from __future__ import annotations

import json
import sys
from typing import Any, TextIO

from core.capability_targets import McpExecutor
from core.openfang_runtime_authority import RuntimeInvocationContext, runtime_invocation_id


ROWBOAT_SPACE = "rowboat"
ROWBOAT_AGENT = "rowboat-chat"
ROWBOAT_TOOL = "rowboat_status"
ROWBOAT_TARGET = "mcp:rowboat-chat:spaces-rowboat:rowboat_status"
_STEP_ID = "rowboat.status"
_REQUEST_FIELDS = frozenset({
    "correlation_id",
    "plan_id",
    "plan_revision",
    "space_id",
    "agent_name",
    "tool",
})


class DeterministicRowboatGateway:
    """Invoke only the canonical Rowboat status MCP tool through OpenFang."""

    def execute(
        self,
        *,
        correlation_id: object,
        plan_id: object,
        plan_revision: object,
        space_id: object,
        agent_name: object,
        tool: object,
    ) -> dict[str, Any]:
        """Run the closed gateway or return a safe, non-retryable outcome."""
        if not self._has_canonical_input(
            correlation_id=correlation_id,
            plan_id=plan_id,
            plan_revision=plan_revision,
            space_id=space_id,
            agent_name=agent_name,
            tool=tool,
        ):
            return self._blocked("invalid deterministic Rowboat gateway input")

        context = RuntimeInvocationContext(
            correlation_id=correlation_id,
            plan_id=plan_id,
            plan_revision=plan_revision,
            step_id=_STEP_ID,
            space_id=ROWBOAT_SPACE,
            agent_name=ROWBOAT_AGENT,
            invocation_id=runtime_invocation_id(plan_id, plan_revision, _STEP_ID),
        )
        response = McpExecutor(ROWBOAT_TARGET).call(_runtime_authority=context)
        if response.get("ok") is True:
            result = response.get("result")
            if isinstance(result, dict):
                return {
                    "ok": True,
                    "status": "completed",
                    "target": ROWBOAT_TARGET,
                    "result": result,
                }
            return self._blocked("OpenFang MCP result was malformed")
        if response.get("pending") is True:
            return self._status("pending")

        error = str(response.get("error") or "")
        normalized = error.lower()
        if "outcome_unknown" in normalized or "in_progress" in normalized:
            return self._status("outcome_unknown")
        if "approval was not approved" in normalized or "denied" in normalized:
            return self._status("denied")
        if "openfang_url" in normalized:
            return self._blocked("OPENFANG_URL is required")
        if "openfang_api_key" in normalized:
            return self._blocked("OPENFANG_API_KEY is required")
        return self._blocked("OpenFang deterministic MCP invocation failed")

    @staticmethod
    def _has_canonical_input(
        *,
        correlation_id: object,
        plan_id: object,
        plan_revision: object,
        space_id: object,
        agent_name: object,
        tool: object,
    ) -> bool:
        return (
            isinstance(correlation_id, str)
            and bool(correlation_id.strip())
            and isinstance(plan_id, str)
            and bool(plan_id.strip())
            and isinstance(plan_revision, int)
            and not isinstance(plan_revision, bool)
            and plan_revision > 0
            and space_id == ROWBOAT_SPACE
            and agent_name == ROWBOAT_AGENT
            and tool == ROWBOAT_TOOL
        )

    @staticmethod
    def _status(status: str) -> dict[str, Any]:
        return {
            "ok": False,
            "status": status,
            "target": ROWBOAT_TARGET,
            "retryable": False,
        }

    @staticmethod
    def _blocked(reason: str) -> dict[str, Any]:
        return {
            "ok": False,
            "status": "blocked",
            "target": ROWBOAT_TARGET,
            "reason": reason,
        }


def main(*, stdin: TextIO | None = None, stdout: TextIO | None = None) -> int:
    """Read one JSON request from stdin and write one fail-closed JSON result."""
    input_stream = stdin if stdin is not None else sys.stdin
    output_stream = stdout if stdout is not None else sys.stdout
    try:
        request = json.load(input_stream)
        if not isinstance(request, dict) or set(request) != _REQUEST_FIELDS:
            raise ValueError("invalid request shape")
        result = DeterministicRowboatGateway().execute(**request)
        if not isinstance(result, dict):
            raise RuntimeError("invalid gateway result")
    except (json.JSONDecodeError, TypeError, ValueError):
        result = DeterministicRowboatGateway._blocked(
            "invalid deterministic Rowboat gateway request"
        )
    except Exception:
        result = DeterministicRowboatGateway._blocked(
            "deterministic Rowboat gateway request failed"
        )
    json.dump(result, output_stream, separators=(",", ":"), sort_keys=True)
    output_stream.write("\n")
    return 0 if result.get("ok") is True else 2


if __name__ == "__main__":
    raise SystemExit(main())
