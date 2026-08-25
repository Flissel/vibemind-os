from __future__ import annotations

import json
from uuid import uuid4

import httpx
import pytest

from spaces.learning.services.course_factory.learnhouse_gateway import (
    LearnHouseGatewayError,
    LearnHouseHttpGateway,
)
from spaces.learning.tests.unit.test_source_verification import _draft, _ids


@pytest.mark.asyncio
async def test_gateway_stages_exact_factory_draft_with_internal_credentials() -> None:
    ids = _ids()
    draft = _draft(ids)
    course_id = str(uuid4())
    correlation_id = str(uuid4())
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        payload = json.loads(request.content)
        assert payload["draft"] == draft.model_dump(mode="json")
        return httpx.Response(
            200,
            json={
                "draft": {
                    "factory_job_id": draft.job_id,
                    "attempt_number": draft.attempt_number,
                    "draft_hash": payload["draft_hash"],
                    "revision": 8,
                    "chapter_count": len(draft.chapters),
                    "activity_count": len(draft.lessons) + len(draft.activities),
                }
            },
        )

    client = httpx.AsyncClient(
        base_url="http://learnhouse-api:9000/api/v1/learning/",
        transport=httpx.MockTransport(handler),
    )
    gateway = LearnHouseHttpGateway(
        "http://learnhouse-api:9000/api/v1/learning",
        "s" * 32,
        client=client,
    )

    receipt = await gateway.stage_draft(
        course_id=course_id,
        expected_revision=7,
        draft=draft,
        draft_hash="a" * 64,
        correlation_id=correlation_id,
    )

    assert receipt.learnhouse_revision == 8
    assert requests[0].url.path.endswith(
        f"/api/v1/learning/courses/{course_id}/factory-draft"
    )
    assert requests[0].headers["X-LearnHouse-Learning-Service-Key"] == "s" * 32
    assert requests[0].headers["X-VibeMind-Correlation-ID"] == correlation_id
    await client.aclose()


def test_gateway_rejects_external_hosts_and_short_keys() -> None:
    with pytest.raises(ValueError, match="internal or loopback"):
        LearnHouseHttpGateway("https://example.com/api", "s" * 32)
    with pytest.raises(ValueError, match="securely"):
        LearnHouseHttpGateway("http://127.0.0.1:1338/api/v1/learning", "short")


@pytest.mark.asyncio
async def test_gateway_rejects_unbounded_responses() -> None:
    client = httpx.AsyncClient(
        base_url="http://learnhouse-api:9000/api/v1/learning/",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=b"x" * (2 * 1024 * 1024 + 1))
        ),
    )
    gateway = LearnHouseHttpGateway(
        "http://learnhouse-api:9000/api/v1/learning", "s" * 32, client=client
    )

    with pytest.raises(LearnHouseGatewayError, match="size limit"):
        await gateway.read_course(
            course_id=str(uuid4()), correlation_id=str(uuid4())
        )
    await client.aclose()
