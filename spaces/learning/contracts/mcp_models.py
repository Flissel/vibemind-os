from __future__ import annotations

import re
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from .events import EVENT_TOOL_MAP, WRITE_EVENTS, LearningEventType, LearningToolName


SafeToken = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")]
IdempotencyKey = Annotated[str, Field(min_length=1, max_length=128)]

_FORBIDDEN_KEYS = frozenset(
    {
        "api_key",
        "openai_api_key",
        "provider",
        "provider_key",
        "secret",
        "password",
        "access_token",
        "filesystem_path",
        "file_path",
        "path",
    }
)
_WINDOWS_PATH = re.compile(r"^[A-Za-z]:[\\/]")


def _assert_public_value(value: JsonValue, location: str = "payload") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if key.lower() in _FORBIDDEN_KEYS:
                raise ValueError(f"forbidden public payload field: {location}.{key}")
            _assert_public_value(child, f"{location}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _assert_public_value(child, f"{location}[{index}]")
    elif isinstance(value, str):
        lowered = value.lower()
        if lowered.startswith("file://") or _WINDOWS_PATH.match(value) or value.startswith("/"):
            raise ValueError(f"forbidden public payload host path: {location}")


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ActorV1(ContractModel):
    actor_id: SafeToken
    actor_type: Literal["local_user", "brain", "openfang", "system"]


class ConfirmationV1(ContractModel):
    confirmed: Literal[True]
    approval_ref: SafeToken


class EventEnvelopeV1(ContractModel):
    version: Literal["1"] = "1"
    event_type: LearningEventType
    invocation_id: UUID
    correlation_id: UUID
    actor: ActorV1
    course_id: UUID | None = None
    session_id: UUID | None = None
    idempotency_key: IdempotencyKey | None = None
    expected_revision: Annotated[int, Field(ge=0)] | None = None
    confirmation: ConfirmationV1 | None = None
    payload: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_authority_fields(self) -> EventEnvelopeV1:
        if self.event_type in WRITE_EVENTS and self.idempotency_key is None:
            raise ValueError("write events require idempotency_key")
        if self.event_type is LearningEventType.COURSE_PUBLISH:
            if self.confirmation is None:
                raise ValueError("course publication requires confirmation")
            if self.expected_revision is None:
                raise ValueError("course publication requires expected_revision")
        if self.event_type in {
            LearningEventType.CANVAS_SAVE,
            LearningEventType.CANVAS_SUBMIT,
        } and self.expected_revision is None:
            raise ValueError("canvas mutation requires expected_revision")
        if (
            self.event_type is LearningEventType.CANVAS_SUBMIT
            and self.confirmation is None
        ):
            raise ValueError("canvas submission requires confirmation")
        _assert_public_value(self.payload)
        return self


class ToolRequestV1(ContractModel):
    version: Literal["1"] = "1"
    tool: LearningToolName
    event: EventEnvelopeV1

    @model_validator(mode="after")
    def validate_tool_event_pair(self) -> ToolRequestV1:
        expected = EVENT_TOOL_MAP[self.event.event_type]
        if self.tool is not expected:
            raise ValueError(f"tool {self.tool} does not match event {self.event.event_type}")
        return self
