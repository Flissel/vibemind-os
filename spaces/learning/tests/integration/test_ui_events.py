from __future__ import annotations

from uuid import UUID, uuid4

import httpx

from spaces.learning.bridge.ui_bridge import UiBridge
from spaces.learning.contracts.ui_intents import OpenCourseIntentV1


COURSE_ID = UUID("00000000-0000-0000-0000-000000000101")
BRIDGE_TOKEN = "local-renderer-token-with-at-least-32-characters"


def _intent(*, revision: int = 4) -> OpenCourseIntentV1:
    return OpenCourseIntentV1(
        aggregate_id="course-101",
        aggregate_revision=revision,
        course_id=COURSE_ID,
    )


def test_authenticated_renderer_delivery_preserves_backend_success_evidence() -> None:
    correlation_id = uuid4()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == f"Bearer {BRIDGE_TOKEN}"
        assert request.headers["X-Correlation-ID"] == str(correlation_id)
        return httpx.Response(
            202,
            json={"accepted": True, "aggregate_revision": 4, "event_id": "evt-4"},
        )

    bridge = UiBridge(
        base_url="http://127.0.0.1:5151",
        auth_token=BRIDGE_TOKEN,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    delivery = bridge.try_deliver(_intent(), correlation_id=correlation_id)

    assert delivery.delivered is True
    assert delivery.receipt is not None
    assert delivery.receipt.event_id == "evt-4"
    assert delivery.error_code is None


def test_renderer_failure_is_separate_from_completed_backend_readback() -> None:
    bridge = UiBridge(
        base_url="http://127.0.0.1:5151",
        auth_token=BRIDGE_TOKEN,
        http_client=httpx.Client(
            transport=httpx.MockTransport(lambda _: httpx.Response(503, text="secret response"))
        ),
    )
    backend_result = {"state": "completed", "evidence": "application_readback"}

    delivery = bridge.try_deliver(_intent(), correlation_id=uuid4())

    assert backend_result == {"state": "completed", "evidence": "application_readback"}
    assert delivery.delivered is False
    assert delivery.receipt is None
    assert delivery.error_code == "transport_error"
    assert "secret response" not in repr(delivery)


def test_bridge_requires_a_nontrivial_runtime_token() -> None:
    for token in (None, "", "short"):
        try:
            UiBridge(base_url="http://127.0.0.1:5151", auth_token=token)
        except ValueError as error:
            assert "token" in str(error)
        else:
            raise AssertionError("bridge accepted an unsafe renderer token")
