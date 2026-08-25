from __future__ import annotations

import json
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest

from spaces.learning.services.course_factory.model_gateway import (
    GatewaySchemaError,
    GatewayUnavailable,
    ModelInvocation,
    OpenFangModelGateway,
)
from spaces.learning.services.course_factory.roles.schemas import ArchitectOutput


def _architect_payload() -> dict:
    return {
        "schema_version": "architect-v1",
        "audience": "Advanced practitioners",
        "prerequisites": ["Python"],
        "outcomes": ["Build a grounded agent system"],
        "chapters": ["Authority", "Retrieval"],
    }


def _invocation() -> ModelInvocation:
    return ModelInvocation(
        role="architect",
        stage="structuring",
        correlation_id=str(uuid4()),
        prompt_version="architect-prompt-v1",
        output_schema_version="architect-v1",
        input_payload={"course_id": str(uuid4()), "source_refs": ["source://one/2"]},
    )


@pytest.mark.asyncio
async def test_gateway_uses_only_authenticated_openfang_contract_and_correlation() -> None:
    invocation = _invocation()

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == httpx.URL("http://openfang.test/v1/chat/completions")
        assert request.headers["Authorization"] == "Bearer " + "k" * 32
        assert request.headers["X-VibeMind-Correlation-ID"] == invocation.correlation_id
        body = json.loads(request.content)
        assert body["model"] == "learning-course-factory"
        assert "tools" not in body
        assert body["messages"][0]["role"] == "system"
        assert body["messages"][1]["role"] == "user"
        return httpx.Response(
            200,
            json={
                "id": "completion-1",
                "choices": [
                    {"message": {"content": json.dumps(_architect_payload())}}
                ],
            },
        )

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://openfang.test"
    )
    gateway = OpenFangModelGateway(
        "http://openfang.test",
        "k" * 32,
        client=client,
    )

    result = await gateway.generate(invocation, ArchitectOutput)

    assert result.output.schema_version == "architect-v1"
    assert result.evidence_ref == "openfang://completion/completion-1"
    await client.aclose()


@pytest.mark.asyncio
async def test_gateway_retries_only_bounded_transient_failures() -> None:
    calls = 0
    sleeper = AsyncMock()

    async def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls < 3:
            return httpx.Response(503, json={"error": "not ready"})
        return httpx.Response(
            200,
            json={
                "id": "completion-3",
                "choices": [
                    {"message": {"content": json.dumps(_architect_payload())}}
                ],
            },
        )

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://openfang.test"
    )
    gateway = OpenFangModelGateway(
        "http://openfang.test",
        "k" * 32,
        client=client,
        max_attempts=3,
        sleeper=sleeper,
    )

    result = await gateway.generate(_invocation(), ArchitectOutput)

    assert result.evidence_ref.endswith("completion-3")
    assert calls == 3
    assert sleeper.await_count == 2
    await client.aclose()


@pytest.mark.asyncio
async def test_gateway_fails_closed_on_timeout_without_leaking_secret() -> None:
    secret = "s" * 32
    sleeper = AsyncMock()

    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("raw upstream timeout " + secret, request=request)

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://openfang.test"
    )
    gateway = OpenFangModelGateway(
        "http://openfang.test",
        secret,
        client=client,
        max_attempts=2,
        sleeper=sleeper,
    )

    with pytest.raises(GatewayUnavailable) as caught:
        await gateway.generate(_invocation(), ArchitectOutput)

    assert secret not in str(caught.value)
    assert sleeper.await_count == 1
    await client.aclose()


@pytest.mark.asyncio
async def test_gateway_rejects_schema_invalid_output_without_retry() -> None:
    calls = 0

    async def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            json={
                "id": "completion-invalid",
                "choices": [{"message": {"content": "{\"schema_version\":\"wrong\"}"}}],
            },
        )

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://openfang.test"
    )
    gateway = OpenFangModelGateway(
        "http://openfang.test",
        "k" * 32,
        client=client,
    )

    with pytest.raises(GatewaySchemaError):
        await gateway.generate(_invocation(), ArchitectOutput)

    assert calls == 1
    await client.aclose()


def test_gateway_rejects_unapproved_roles_and_credential_fields() -> None:
    values = {
        "role": "architect",
        "stage": "structuring",
        "correlation_id": str(uuid4()),
        "prompt_version": "architect-prompt-v1",
        "output_schema_version": "architect-v1",
        "input_payload": {"course_id": str(uuid4())},
    }

    with pytest.raises(ValueError, match="authorized"):
        ModelInvocation(**{**values, "role": "publisher"})
    with pytest.raises(ValueError, match="credentials"):
        ModelInvocation(
            **{**values, "input_payload": {"provider_api_key": "forbidden"}}
        )


@pytest.mark.asyncio
async def test_gateway_stops_reading_oversized_response() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * 1_000_001)

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://openfang.test"
    )
    gateway = OpenFangModelGateway(
        "http://openfang.test",
        "k" * 32,
        client=client,
    )

    with pytest.raises(GatewaySchemaError, match="size"):
        await gateway.generate(_invocation(), ArchitectOutput)

    await client.aclose()
