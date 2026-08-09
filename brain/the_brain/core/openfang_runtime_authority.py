"""Fail-closed Brain client for OpenFang runtime-admission authority."""

from __future__ import annotations

import hashlib
import logging
import os
import time
from dataclasses import dataclass
from typing import Any

import requests

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RuntimeInvocationContext:
    correlation_id: str
    plan_id: str
    plan_revision: int
    step_id: str
    space_id: str
    agent_name: str
    invocation_id: str


@dataclass(frozen=True, repr=False)
class RuntimeAuthorityRefs:
    agent_id: str
    approval_ref: str
    cost_ref: str
    invocation_id: str


@dataclass(frozen=True)
class RuntimeAuthorityPending:
    approval_ref_fingerprint: str
    invocation_id: str


def runtime_invocation_id(plan_id: str, plan_revision: int, step_id: str) -> str:
    material = f"{plan_id}\n{plan_revision}\n{step_id}".encode("utf-8")
    return f"brain-mcp-{hashlib.sha256(material).hexdigest()[:32]}"


class RuntimeAuthorityClient:
    """Prepare one MCP invocation without exposing authority refs to callers."""

    _RETRY_ATTEMPTS = 3
    _RETRY_DELAYS_S = (0.25, 0.5)

    def __init__(self, base: str, api_key: str) -> None:
        self.base = base.rstrip("/")
        self.api_key = api_key

    @classmethod
    def from_environment(cls) -> "RuntimeAuthorityClient":
        base = os.environ.get("OPENFANG_URL", "").strip()
        api_key = os.environ.get("OPENFANG_API_KEY", "").strip()
        if not base:
            raise RuntimeError("OPENFANG_URL is required for OpenFang runtime authority")
        if not api_key:
            raise RuntimeError("OPENFANG_API_KEY is required for OpenFang runtime authority")
        return cls(base, api_key)

    @property
    def _headers(self) -> dict[str, str]:
        return {"Accept": "application/json", "Authorization": f"Bearer {self.api_key}"}

    @staticmethod
    def _transient(exc: requests.exceptions.RequestException) -> bool:
        response = getattr(exc, "response", None)
        status = getattr(response, "status_code", None)
        return status is None or status in {408, 425, 429} or status >= 500

    def _get_agents(self) -> Any:
        timeout = min(float(os.environ.get("CAPABILITY_HTTP_TIMEOUT_S", "60")), 4.0)
        last: requests.exceptions.RequestException | None = None
        for attempt in range(self._RETRY_ATTEMPTS):
            try:
                response = requests.get(f"{self.base}/api/agents", headers=self._headers, timeout=timeout)
                response.raise_for_status()
                return response.json()
            except requests.exceptions.RequestException as exc:
                if not self._transient(exc):
                    raise RuntimeError("OpenFang agent resolution failed") from exc
                last = exc
                if attempt < self._RETRY_ATTEMPTS - 1:
                    time.sleep(self._RETRY_DELAYS_S[attempt])
        raise RuntimeError("OpenFang agent resolution unavailable") from last

    def _agent_id(self, agent_name: str) -> str:
        body = self._get_agents()
        agents = body.get("agents") if isinstance(body, dict) else body
        if not isinstance(agents, list):
            raise RuntimeError("OpenFang /api/agents returned an invalid agent list")
        for agent in agents:
            if isinstance(agent, dict) and str(agent.get("name") or "").lower() == agent_name.lower():
                agent_id = agent.get("id") or agent.get("agent_id")
                if isinstance(agent_id, str) and agent_id.strip():
                    return agent_id.strip()
        raise RuntimeError("OpenFang runtime authority agent is not registered")

    def _post_once(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            response = requests.post(
                f"{self.base}{path}", json=payload, headers=self._headers,
                timeout=float(os.environ.get("CAPABILITY_HTTP_TIMEOUT_S", "60")),
            )
            response.raise_for_status()
            body = response.json()
        except requests.exceptions.RequestException as exc:
            raise RuntimeError(f"OpenFang runtime authority request failed at {path}") from exc
        except ValueError as exc:
            raise RuntimeError(f"OpenFang runtime authority returned invalid JSON at {path}") from exc
        if not isinstance(body, dict):
            raise RuntimeError("OpenFang runtime authority returned an invalid response")
        return body

    def prepare_invocation(
        self, context: RuntimeInvocationContext,
    ) -> RuntimeAuthorityRefs | RuntimeAuthorityPending:
        if context.plan_revision <= 0:
            raise ValueError("plan_revision must be positive")
        agent_id = self._agent_id(context.agent_name)
        approval = self._post_once("/api/runtime/approvals/admit", {
            "contract_version": "v1", "correlation_id": context.correlation_id,
            "plan_id": context.plan_id, "plan_revision": context.plan_revision,
            "space_id": context.space_id, "agent_id": agent_id,
            "max_plan_cost_microusd": 0, "ttl_seconds": 300,
        })
        approval_ref = approval.get("approval_ref")
        if not isinstance(approval_ref, str) or not approval_ref.strip():
            raise RuntimeError("OpenFang runtime approval omitted authority reference")
        if approval.get("status") == "pending_approval":
            return RuntimeAuthorityPending(
                approval_ref_fingerprint=hashlib.sha256(approval_ref.encode("utf-8")).hexdigest()[:16],
                invocation_id=context.invocation_id,
            )
        if approval.get("status") != "approved":
            raise RuntimeError("OpenFang runtime approval was not approved")
        reservation = self._post_once("/api/runtime/cost-reservations", {
            "contract_version": "v1", "correlation_id": context.correlation_id,
            "plan_id": context.plan_id, "plan_revision": context.plan_revision,
            "space_id": context.space_id, "agent_id": agent_id,
            "approval_ref": approval_ref, "invocation_id": context.invocation_id,
            "max_cost_microusd": 0, "ttl_seconds": 300,
        })
        record = reservation.get("reservation")
        cost_ref = record.get("cost_ref") if isinstance(record, dict) else None
        if reservation.get("status") != "reserved" or not isinstance(cost_ref, str) or not cost_ref.strip():
            raise RuntimeError("OpenFang runtime cost reservation was not reserved")
        return RuntimeAuthorityRefs(
            agent_id=agent_id, approval_ref=approval_ref.strip(), cost_ref=cost_ref.strip(),
            invocation_id=context.invocation_id,
        )
