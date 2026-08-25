from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, JsonValue, model_validator

from .mcp_models import ContractModel, SafeToken
from .ui_intents import UiIntent


class AggregateRefV1(ContractModel):
    aggregate_type: SafeToken
    aggregate_id: SafeToken
    revision: Annotated[int, Field(ge=0)]


class EvidenceRefV1(ContractModel):
    owner: SafeToken
    evidence_id: SafeToken
    evidence_type: Literal[
        "application_readback",
        "job_readback",
        "artifact_readback",
        "structural_status",
    ]


class ToolErrorV1(ContractModel):
    code: SafeToken
    message: Annotated[str, Field(min_length=1, max_length=500)]
    retryable: bool = False


class UiDeliveryStatusV1(ContractModel):
    attempted: bool
    delivered: bool
    error_code: SafeToken | None = None
    event_id: SafeToken | None = None

    @model_validator(mode="after")
    def validate_delivery_evidence(self) -> "UiDeliveryStatusV1":
        if self.delivered and (not self.attempted or self.error_code is not None):
            raise ValueError("delivered UI projection requires a clean attempt")
        if not self.delivered and self.event_id is not None:
            raise ValueError("failed UI projection cannot expose an event receipt")
        return self


class ToolResultV1(ContractModel):
    version: Literal["1"] = "1"
    invocation_id: UUID
    correlation_id: UUID
    state: Literal[
        "accepted",
        "rejected",
        "approval_required",
        "unavailable",
        "completed",
    ]
    aggregate: AggregateRefV1 | None = None
    result: dict[str, JsonValue] | None = None
    error: ToolErrorV1 | None = None
    evidence: EvidenceRefV1 | None = None
    ui_intent: UiIntent | None = None
    ui_delivery: UiDeliveryStatusV1 | None = None

    @model_validator(mode="after")
    def validate_state_evidence(self) -> ToolResultV1:
        if self.state == "completed" and (self.aggregate is None or self.evidence is None):
            raise ValueError("completed results require aggregate and truth evidence")
        if self.state == "completed" and self.error is not None:
            raise ValueError("completed results cannot include an error")
        if self.state in {"rejected", "unavailable"} and self.error is None:
            raise ValueError(f"{self.state} results require an error")
        return self


class TruthReadbackV1(ContractModel):
    version: Literal["1"] = "1"
    invocation_id: UUID
    correlation_id: UUID
    owner: SafeToken
    terminal_state: Literal["completed", "failed", "cancelled", "rejected"]
    aggregate: AggregateRefV1
    evidence: EvidenceRefV1
    read_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @model_validator(mode="after")
    def validate_evidence_owner(self) -> TruthReadbackV1:
        if self.evidence.owner != self.owner:
            raise ValueError("truth evidence owner must match readback owner")
        return self
