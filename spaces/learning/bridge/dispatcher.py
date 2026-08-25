from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from threading import Lock
from typing import Mapping, Protocol

from pydantic import Field, JsonValue

from spaces.learning.bridge.truth_readback import ReadbackError, verify_readback
from spaces.learning.contracts.events import WRITE_EVENTS, LearningToolName
from spaces.learning.contracts.mcp_models import ContractModel, ToolRequestV1
from spaces.learning.contracts.outcomes import (
    AggregateRefV1,
    ToolErrorV1,
    ToolResultV1,
    TruthReadbackV1,
)
from spaces.learning.contracts.ui_intents import UiIntent


class ApplicationOutcomeV1(ContractModel):
    state: str = Field(
        pattern=r"^(accepted|rejected|approval_required|unavailable|completed)$"
    )
    aggregate: AggregateRefV1 | None = None
    result: dict[str, JsonValue] | None = None
    error: ToolErrorV1 | None = None
    ui_intent: UiIntent | None = None


class ApplicationGateway(Protocol):
    def execute(self, request: ToolRequestV1) -> ApplicationOutcomeV1: ...

    def readback(
        self, request: ToolRequestV1, outcome: ApplicationOutcomeV1
    ) -> TruthReadbackV1 | None: ...


@dataclass(frozen=True)
class Receipt:
    request_digest: str
    result: ToolResultV1
    terminal: bool = True


@dataclass(frozen=True)
class ReceiptClaim:
    receipt: Receipt
    owned: bool


class ReceiptStore(Protocol):
    def get(self, idempotency_key: str) -> Receipt | None: ...

    def put(self, idempotency_key: str, receipt: Receipt) -> None: ...

    def claim(self, idempotency_key: str, receipt: Receipt) -> ReceiptClaim: ...

    def finalize(self, idempotency_key: str, receipt: Receipt) -> Receipt: ...


class InMemoryReceiptStore:
    """Test/bootstrap receipt store; durable storage replaces it in Phase 1 Task 6."""

    def __init__(self) -> None:
        self._receipts: dict[str, Receipt] = {}
        self._lock = Lock()

    def get(self, idempotency_key: str) -> Receipt | None:
        with self._lock:
            return self._receipts.get(idempotency_key)

    def put(self, idempotency_key: str, receipt: Receipt) -> None:
        with self._lock:
            existing = self._receipts.get(idempotency_key)
            if existing is None:
                self._receipts[idempotency_key] = receipt
                return
            if existing != receipt:
                raise ValueError("idempotency receipt is immutable")

    def claim(self, idempotency_key: str, receipt: Receipt) -> ReceiptClaim:
        with self._lock:
            existing = self._receipts.get(idempotency_key)
            if existing is not None:
                return ReceiptClaim(receipt=existing, owned=False)
            self._receipts[idempotency_key] = receipt
            return ReceiptClaim(receipt=receipt, owned=True)

    def finalize(self, idempotency_key: str, receipt: Receipt) -> Receipt:
        with self._lock:
            existing = self._receipts.get(idempotency_key)
            if existing is None:
                raise ValueError("idempotency claim is missing")
            if existing.request_digest != receipt.request_digest:
                raise ValueError("idempotency receipt is immutable")
            if existing.terminal:
                return existing
            self._receipts[idempotency_key] = receipt
            return receipt


def _request_digest(request: ToolRequestV1) -> str:
    canonical = request.model_dump(mode="json")
    event = canonical["event"]
    if isinstance(event, dict):
        event.pop("invocation_id", None)
        event.pop("correlation_id", None)
    encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _error_result(
    request: ToolRequestV1,
    *,
    state: str,
    code: str,
    message: str,
    retryable: bool = False,
) -> ToolResultV1:
    return ToolResultV1(
        invocation_id=request.event.invocation_id,
        correlation_id=request.event.correlation_id,
        state=state,
        error=ToolErrorV1(code=code, message=message, retryable=retryable),
    )


class LearningDispatcher:
    def __init__(
        self,
        *,
        gateways: Mapping[LearningToolName, ApplicationGateway],
        receipts: ReceiptStore,
    ) -> None:
        self._gateways = dict(gateways)
        self._receipts = receipts

    def dispatch(self, request: ToolRequestV1) -> ToolResultV1:
        key = request.event.idempotency_key
        digest = _request_digest(request)
        if key is not None:
            claim = self._receipts.claim(
                key,
                Receipt(
                    request_digest=digest,
                    result=ToolResultV1(
                        invocation_id=request.event.invocation_id,
                        correlation_id=request.event.correlation_id,
                        state="accepted",
                    ),
                    terminal=False,
                ),
            )
            if not claim.owned:
                if claim.receipt.request_digest == digest:
                    return claim.receipt.result
                return _error_result(
                    request,
                    state="rejected",
                    code="idempotency_conflict",
                    message="idempotency key was used for a different request",
                )

        gateway = self._gateways.get(request.tool)
        if gateway is None:
            result = _error_result(
                request,
                state="unavailable",
                code="learning_backend_unavailable",
                message=f"no admitted backend is connected for {request.tool}",
                retryable=True,
            )
            return self._finalize(key, digest, result)

        try:
            outcome = gateway.execute(request)
        except Exception:
            result = _error_result(
                request,
                state="unavailable",
                code="learning_backend_unavailable",
                message="the admitted backend call failed",
                retryable=True,
            )
            return self._finalize(key, digest, result)

        if outcome.state != "completed":
            result = ToolResultV1(
                invocation_id=request.event.invocation_id,
                correlation_id=request.event.correlation_id,
                state=outcome.state,
                aggregate=outcome.aggregate,
                result=outcome.result,
                error=outcome.error,
                ui_intent=outcome.ui_intent,
            )
            return self._finalize(key, digest, result)

        if outcome.aggregate is None:
            return self._finalize(
                key,
                digest,
                self._unverified(request, "completed transport omitted aggregate"),
            )
        try:
            readback = gateway.readback(request, outcome)
            if readback is None:
                raise ReadbackError("application returned no readback")
            verified = verify_readback(
                readback,
                invocation_id=request.event.invocation_id,
                correlation_id=request.event.correlation_id,
                aggregate=outcome.aggregate,
            )
        except Exception:
            return self._finalize(
                key,
                digest,
                self._unverified(request, "terminal application readback failed"),
            )

        result = ToolResultV1(
            invocation_id=request.event.invocation_id,
            correlation_id=request.event.correlation_id,
            state="completed",
            aggregate=outcome.aggregate,
            result=outcome.result,
            evidence=verified.evidence,
            ui_intent=outcome.ui_intent,
        )
        return self._finalize(key, digest, result)

    def _unverified(self, request: ToolRequestV1, message: str) -> ToolResultV1:
        return _error_result(
            request,
            state="unavailable",
            code="truth_readback_unverified",
            message=message,
            retryable=request.event.event_type not in WRITE_EVENTS,
        )

    def _finalize(
        self, key: str | None, digest: str, result: ToolResultV1
    ) -> ToolResultV1:
        if key is None:
            return result
        return self._receipts.finalize(
            key,
            Receipt(request_digest=digest, result=result, terminal=True),
        ).result
