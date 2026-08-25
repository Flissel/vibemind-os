from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Generic, Protocol, TypeVar
from uuid import UUID

import httpx
from pydantic import BaseModel, ValidationError


ROLE_CONTRACTS = {
    "architect": ("structuring", "architect-prompt-v1", "architect-v1"),
    "concept_mapper": ("structuring", "concept-map-prompt-v1", "concept-map-v1"),
    "lesson_author": ("authoring", "lesson-prompt-v1", "lesson-v1"),
    "assessment_designer": (
        "assessing",
        "assessment-prompt-v1",
        "assessment-v1",
    ),
    "source_verifier": (
        "verifying",
        "source-verification-prompt-v2",
        "source-verification-v2",
    ),
    "quality_reviewer": (
        "quality_gate",
        "quality-review-prompt-v1",
        "quality-review-v1",
    ),
    "rubric_evaluator": (
        "evaluation",
        "rubric-prompt-v1",
        "rubric-evaluation-v1",
    ),
}
ROLE_PROMPTS = {
    "architect": "Design a source-grounded curriculum structure for the stated audience and outcome.",
    "concept_mapper": "Map concepts, prerequisites, dependencies, and chapter coverage from the proposed structure.",
    "lesson_author": "Draft rigorous lessons from the supplied structure and source references.",
    "assessment_designer": "Design authentic activities that assess the mapped concepts without inventing facts.",
    "source_verifier": "Return a complete course draft whose claims and expected answers cite exact supplied source locators; list every unsupported claim.",
    "quality_reviewer": "Review coverage, coherence, difficulty, and task quality; never publish the course.",
    "rubric_evaluator": "Evaluate the response against every rubric criterion using only the supplied source references.",
}
_SECRET_KEYS = ("api_key", "token", "secret", "password", "credential")
_MAX_REQUEST_BYTES = 500_000
_MAX_RESPONSE_BYTES = 1_000_000


class ModelGatewayError(RuntimeError):
    pass


class GatewayUnavailable(ModelGatewayError):
    pass


class GatewayRejected(ModelGatewayError):
    pass


class GatewaySchemaError(ModelGatewayError):
    pass


class GatewayInputError(ModelGatewayError, ValueError):
    pass


@dataclass(frozen=True)
class ModelInvocation:
    role: str
    stage: str
    correlation_id: str
    prompt_version: str
    output_schema_version: str
    input_payload: dict[str, object]

    def __post_init__(self) -> None:
        try:
            UUID(self.correlation_id)
        except ValueError as error:
            raise GatewayInputError("model correlation ID must be a UUID") from error
        expected = ROLE_CONTRACTS.get(self.role)
        if expected != (
            self.stage,
            self.prompt_version,
            self.output_schema_version,
        ):
            raise GatewayInputError("model role contract is not authorized")
        _reject_secret_fields(self.input_payload)
        try:
            encoded = _canonical_json(self.input_payload).encode()
        except (TypeError, ValueError) as error:
            raise GatewayInputError("model input must be JSON serializable") from error
        if len(encoded) > _MAX_REQUEST_BYTES:
            raise GatewayInputError("model input exceeds the bounded request size")


OutputT = TypeVar("OutputT", bound=BaseModel)


@dataclass(frozen=True)
class GatewayResult(Generic[OutputT]):
    output: OutputT
    evidence_ref: str


class ModelGateway(Protocol):
    async def generate(
        self, invocation: ModelInvocation, output_model: type[OutputT]
    ) -> GatewayResult[OutputT]: ...


class OpenFangModelGateway:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        model: str = "learning-course-factory",
        timeout_seconds: float = 60.0,
        max_attempts: int = 3,
        client: httpx.AsyncClient | None = None,
        sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if not base_url.startswith(("http://", "https://")):
            raise ValueError("OpenFang URL must use HTTP or HTTPS")
        if len(api_key) < 16:
            raise ValueError("OpenFang API key is not configured securely")
        if not model or len(model) > 200 or timeout_seconds <= 0:
            raise ValueError("OpenFang model gateway configuration is invalid")
        if max_attempts < 1 or max_attempts > 3:
            raise ValueError("OpenFang attempts must stay between one and three")
        self._api_key = api_key
        self._model = model
        self._max_attempts = max_attempts
        self._sleeper = sleeper
        self._client = client or httpx.AsyncClient(
            base_url=base_url.rstrip("/"), timeout=timeout_seconds
        )

    async def generate(
        self, invocation: ModelInvocation, output_model: type[OutputT]
    ) -> GatewayResult[OutputT]:
        body = {
            "model": self._model,
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
            "messages": [
                {
                    "role": "system",
                    "content": (
                        ROLE_PROMPTS[invocation.role]
                        + " Return only JSON matching schema "
                        + invocation.output_schema_version
                        + "."
                    ),
                },
                {
                    "role": "user",
                    "content": _canonical_json(invocation.input_payload),
                },
            ],
        }
        for attempt in range(self._max_attempts):
            try:
                async with self._client.stream(
                    "POST",
                    "/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "X-VibeMind-Correlation-ID": invocation.correlation_id,
                    },
                    json=body,
                ) as response:
                    status_code = response.status_code
                    if 200 <= status_code < 300:
                        content = await _read_bounded_response(response)
            except (httpx.TimeoutException, httpx.NetworkError) as error:
                if attempt + 1 == self._max_attempts:
                    raise GatewayUnavailable(
                        "OpenFang model gateway is unavailable"
                    ) from error
                await self._sleeper(2**attempt)
                continue
            if status_code == 429 or status_code >= 500:
                if attempt + 1 == self._max_attempts:
                    raise GatewayUnavailable("OpenFang model gateway is unavailable")
                await self._sleeper(2**attempt)
                continue
            if status_code < 200 or status_code >= 300:
                raise GatewayRejected("OpenFang model request was rejected")
            return _validated_result(content, invocation, output_model)
        raise GatewayUnavailable("OpenFang model gateway is unavailable")


def build_openfang_model_gateway() -> OpenFangModelGateway:
    return OpenFangModelGateway(
        os.environ.get("LEARNING_OPENFANG_URL", "").strip(),
        os.environ.get("LEARNING_OPENFANG_API_KEY", ""),
        model=os.environ.get(
            "LEARNING_OPENFANG_MODEL", "learning-course-factory"
        ).strip(),
    )


def _validated_result(
    content_bytes: bytes,
    invocation: ModelInvocation,
    output_model: type[OutputT],
) -> GatewayResult[OutputT]:
    try:
        envelope = json.loads(content_bytes)
        completion_id = envelope["id"]
        content = envelope["choices"][0]["message"]["content"]
        if not isinstance(completion_id, str) or not completion_id:
            raise ValueError
        if not isinstance(content, str):
            raise ValueError
        output = output_model.model_validate(json.loads(content))
        if getattr(output, "schema_version", None) != invocation.output_schema_version:
            raise ValueError
    except (KeyError, IndexError, TypeError, ValueError, ValidationError) as error:
        raise GatewaySchemaError("OpenFang model response is schema-invalid") from error
    return GatewayResult(
        output=output,
        evidence_ref=f"openfang://completion/{completion_id}",
    )


async def _read_bounded_response(response: httpx.Response) -> bytes:
    content = bytearray()
    async for chunk in response.aiter_bytes():
        content.extend(chunk)
        if len(content) > _MAX_RESPONSE_BYTES:
            raise GatewaySchemaError(
                "OpenFang model response exceeds the size limit"
            )
    return bytes(content)


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _reject_secret_fields(value: object) -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            normalized = str(key).lower()
            if any(secret_key in normalized for secret_key in _SECRET_KEYS):
                raise GatewayInputError(
                    "provider credentials are forbidden in model input"
                )
            _reject_secret_fields(nested)
    elif isinstance(value, list | tuple):
        for nested in value:
            _reject_secret_fields(nested)
