from __future__ import annotations

from ipaddress import ip_address
import re
from typing import Annotated, Literal
from urllib.parse import urlsplit
from uuid import UUID

import httpx
from pydantic import Field, ValidationError, model_validator

from spaces.learning.contracts.mcp_models import ContractModel
from spaces.learning.contracts.penecho import (
    CanvasArtifactV1,
    CanvasDocumentV1,
    CanvasSaveV1,
    PenEchoLaunchContextV1,
)


_MINIMUM_TOKEN_LENGTH = 32
_MAXIMUM_TOKEN_LENGTH = 512
_SESSION_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class PenEchoError(RuntimeError):
    """Base error for the admitted PenEcho learning boundary."""


class PenEchoTransportError(PenEchoError):
    pass


class PenEchoMalformedResponse(PenEchoError):
    pass


class PenEchoRevisionConflict(PenEchoError):
    pass


class PenEchoSessionV1(ContractModel):
    version: Literal["1"] = "1"
    id: UUID
    revision: Annotated[int, Field(ge=0)]
    launch: PenEchoLaunchContextV1 | None = None
    document: CanvasDocumentV1
    artifacts: tuple[CanvasArtifactV1, ...] = ()

    @model_validator(mode="after")
    def validate_revision(self) -> "PenEchoSessionV1":
        if self.document.revision != self.revision:
            raise ValueError("PenEcho session document revision does not match")
        if any(item.canvas_revision != self.revision for item in self.artifacts):
            raise ValueError("PenEcho session artifact revision does not match")
        return self


class PenEchoExportV1(ContractModel):
    version: Literal["1"] = "1"
    id: UUID
    revision: Annotated[int, Field(ge=1)]
    document: CanvasDocumentV1
    artifacts: Annotated[tuple[CanvasArtifactV1, ...], Field(min_length=2, max_length=2)]
    snapshot_png_base64: Annotated[str, Field(min_length=12, max_length=27_000_000)]

    @model_validator(mode="after")
    def validate_export(self) -> "PenEchoExportV1":
        if self.document.revision != self.revision:
            raise ValueError("PenEcho export document revision does not match")
        if {item.artifact_kind for item in self.artifacts} != {
            "structured_canvas",
            "rendered_snapshot",
        }:
            raise ValueError("PenEcho export artifacts are incomplete")
        if any(item.canvas_revision != self.revision for item in self.artifacts):
            raise ValueError("PenEcho export artifact revision does not match")
        return self


def _validate_loopback_origin(value: str, *, label: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "http"
        or not parsed.hostname
        or parsed.port is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(f"{label} must use an uncredentialed http loopback origin")
    try:
        if not ip_address(parsed.hostname).is_loopback:
            raise ValueError(f"{label} must use a loopback address")
    except ValueError as error:
        if str(error).startswith(label):
            raise
        if parsed.hostname != "localhost":
            raise ValueError(f"{label} must use a loopback address") from None
    return value.rstrip("/")


def _camel_key(value: str) -> str:
    first, *rest = value.split("_")
    return first + "".join(part.title() for part in rest)


def _wire(value: object) -> object:
    if isinstance(value, dict):
        return {_camel_key(key): _wire(child) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [_wire(child) for child in value]
    return value


def _snake_key(value: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", value).lower()


def _unwire(value: object) -> object:
    if isinstance(value, dict):
        return {_snake_key(key): _unwire(child) for key, child in value.items()}
    if isinstance(value, list):
        return [_unwire(child) for child in value]
    return value


class PenEchoClient:
    """Typed loopback client for versioned PenEcho learning sessions."""

    def __init__(
        self,
        *,
        base_url: str,
        learning_origin: str,
        auth_token: str,
        timeout_seconds: float = 5.0,
        http_client: httpx.Client | None = None,
    ) -> None:
        self._base_url = _validate_loopback_origin(base_url, label="PenEcho base_url")
        self._learning_origin = _validate_loopback_origin(
            learning_origin, label="Learning origin"
        )
        if (
            not _MINIMUM_TOKEN_LENGTH <= len(auth_token) <= _MAXIMUM_TOKEN_LENGTH
            or auth_token.strip() != auth_token
        ):
            raise ValueError("PenEcho token must be a nontrivial runtime token")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self._auth_token = auth_token
        self._timeout_seconds = timeout_seconds
        self._http_client = http_client or httpx.Client()

    def create_session(
        self, launch: PenEchoLaunchContextV1, document: CanvasDocumentV1
    ) -> PenEchoSessionV1:
        if launch.penecho_origin != self._base_url:
            raise ValueError("PenEcho launch origin does not match the client origin")
        value = self._request(
            "POST",
            "/api/learning/sessions",
            session_ref=launch.bridge_session_ref,
            payload=_wire(
                {
                    "launch": launch.model_dump(mode="json"),
                    "document": document.model_dump(mode="json"),
                }
            ),
        )
        session = self._session(value)
        if session.launch is None or session.id != launch.session_id:
            raise PenEchoMalformedResponse("PenEcho returned malformed session identity")
        return session

    def open_session(self, session_id: UUID, session_ref: str) -> PenEchoSessionV1:
        return self._session(
            self._request(
                "GET",
                f"/api/learning/sessions/{session_id}",
                session_ref=session_ref,
            )
        )

    def save_session(
        self,
        session_id: UUID,
        session_ref: str,
        save: CanvasSaveV1,
        *,
        snapshot_png_base64: str,
    ) -> PenEchoSessionV1:
        return self._session(
            self._request(
                "PUT",
                f"/api/learning/sessions/{session_id}/canvas",
                session_ref=session_ref,
                payload=_wire(
                    {
                        **save.model_dump(mode="json"),
                        "snapshot_png_base64": snapshot_png_base64,
                    }
                ),
            )
        )

    def export_session(self, session_id: UUID, session_ref: str) -> PenEchoExportV1:
        value = self._response_session(
            self._request(
                "GET",
                f"/api/learning/sessions/{session_id}/export",
                session_ref=session_ref,
            )
        )
        current_revision = value.pop("current_revision", None)
        if current_revision != value.get("revision"):
            raise PenEchoMalformedResponse("PenEcho returned malformed export revision")
        try:
            return PenEchoExportV1.model_validate(value)
        except ValidationError as error:
            raise PenEchoMalformedResponse("PenEcho returned malformed canvas export") from error

    def _request(
        self,
        method: Literal["GET", "POST", "PUT"],
        path: str,
        *,
        session_ref: str,
        payload: object | None = None,
    ) -> object:
        if not _SESSION_REF.fullmatch(session_ref):
            raise ValueError("PenEcho session reference is invalid")
        try:
            response = self._http_client.request(
                method,
                f"{self._base_url}{path}",
                headers={
                    "Accept": "application/json",
                    "Authorization": f"Bearer {self._auth_token}",
                    "Origin": self._learning_origin,
                    "X-VibeMind-Learning-Session": session_ref,
                },
                json=payload,
                timeout=self._timeout_seconds,
                follow_redirects=False,
            )
        except httpx.HTTPError as error:
            raise PenEchoTransportError("PenEcho learning request failed") from error
        if response.status_code == 409:
            raise PenEchoRevisionConflict("PenEcho learning canvas revision conflict")
        if response.status_code >= 500:
            raise PenEchoTransportError("PenEcho learning service is unavailable")
        if not 200 <= response.status_code < 300:
            raise PenEchoTransportError("PenEcho learning request was rejected")
        try:
            return response.json()
        except ValueError as error:
            raise PenEchoMalformedResponse("PenEcho returned malformed JSON") from error

    @staticmethod
    def _response_session(value: object) -> dict[str, object]:
        if not isinstance(value, dict) or set(value) != {"session"}:
            raise PenEchoMalformedResponse("PenEcho returned malformed response envelope")
        session = _unwire(value["session"])
        if not isinstance(session, dict):
            raise PenEchoMalformedResponse("PenEcho returned malformed session")
        return session

    @classmethod
    def _session(cls, value: object) -> PenEchoSessionV1:
        try:
            return PenEchoSessionV1.model_validate(cls._response_session(value))
        except ValidationError as error:
            raise PenEchoMalformedResponse("PenEcho returned malformed canvas session") from error
