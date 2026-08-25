from __future__ import annotations

import json
from uuid import UUID, uuid4

import httpx
import pytest

from spaces.learning.bridge.ui_bridge import UiBridge, UiBridgeMalformedResponse, UiBridgeRevisionConflict, UiBridgeTransportError
from spaces.learning.contracts.ui_intents import OpenCourseIntentV1


COURSE_ID = UUID("00000000-0000-0000-0000-000000000101")


def _intent(*, revision: int = 4) -> OpenCourseIntentV1:
    return OpenCourseIntentV1(
        aggregate_id=str(COURSE_ID),
        aggregate_revision=revision,
        course_id=COURSE_ID,
    )


def test_ui_bridge_delivers_revisioned_intent_with_correlation_header() -> None:
    correlation_id = uuid4()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/ui/intents"
        assert request.headers["X-Correlation-ID"] == str(correlation_id)
        assert json.loads(request.content) == _intent().model_dump(mode="json")
        return httpx.Response(202, json={"accepted": True, "aggregate_revision": 4})

    bridge = UiBridge(
        base_url="http://127.0.0.1:5151",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    receipt = bridge.deliver(_intent(), correlation_id=correlation_id)

    assert receipt.accepted is True
    assert receipt.aggregate_revision == 4


def test_ui_bridge_fails_closed_for_stale_malformed_and_http_responses() -> None:
    intent = _intent()
    correlation_id = uuid4()

    stale = UiBridge(
        base_url="http://127.0.0.1:5151",
        http_client=httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(202, json={"accepted": True, "aggregate_revision": 3}))),
    )
    with pytest.raises(UiBridgeRevisionConflict, match="revision"):
        stale.deliver(intent, correlation_id=correlation_id)

    malformed = UiBridge(
        base_url="http://127.0.0.1:5151",
        http_client=httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(202, json={"accepted": True}))),
    )
    with pytest.raises(UiBridgeMalformedResponse, match="malformed"):
        malformed.deliver(intent, correlation_id=correlation_id)

    failed = UiBridge(
        base_url="http://127.0.0.1:5151",
        http_client=httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(500, json={"detail": "token-value"}))),
    )
    with pytest.raises(UiBridgeTransportError, match="delivery failed") as error:
        failed.deliver(intent, correlation_id=correlation_id)
    assert "token-value" not in str(error.value)


def test_ui_bridge_rejects_non_loopback_url() -> None:
    with pytest.raises(ValueError, match="loopback"):
        UiBridge(base_url="http://renderer.example.invalid")
