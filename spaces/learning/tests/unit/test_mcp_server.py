from __future__ import annotations

import json
from uuid import uuid4

from spaces.learning.contracts.events import LearningToolName
from spaces.learning.mcp import server


def _call(name: str, arguments: dict) -> dict:
    return server.handle_message(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }
    )


def _event(event_type: str, **overrides: object) -> dict:
    values: dict[str, object] = {
        "event_type": event_type,
        "invocation_id": str(uuid4()),
        "correlation_id": str(uuid4()),
        "actor": {"actor_id": "local-owner", "actor_type": "local_user"},
        "payload": {},
    }
    values.update(overrides)
    return values


def _payload(response: dict) -> dict:
    return json.loads(response["result"]["content"][0]["text"])


def test_initialize_and_tool_catalog_are_complete() -> None:
    initialized = server.handle_message(
        {"jsonrpc": "2.0", "id": 1, "method": "initialize"}
    )
    listed = server.handle_message(
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}
    )

    assert initialized["result"]["serverInfo"]["name"] == "spaces-learning"
    assert {tool["name"] for tool in listed["result"]["tools"]} == {
        tool.value for tool in LearningToolName
    }
    assert all(tool["inputSchema"]["additionalProperties"] is False for tool in listed["result"]["tools"])


def test_status_is_structural_and_never_claims_live() -> None:
    response = _call("learning_status", _event("learning.status"))
    payload = _payload(response)

    assert response["result"]["isError"] is False
    assert payload["state"] == "completed"
    assert payload["result"] == {"status": "configured", "live_claim": False}
    assert payload["evidence"]["evidence_type"] == "structural_status"


def test_unconnected_product_tools_fail_typed_unavailable() -> None:
    response = _call(
        "learning_course_create",
        _event(
            "learning.course.create",
            idempotency_key="create-1",
            payload={"title": "AI Safety"},
        ),
    )
    payload = _payload(response)

    assert response["result"]["isError"] is True
    assert payload["state"] == "unavailable"
    assert payload["error"]["code"] == "learning_backend_unavailable"


def test_unknown_tool_and_extra_arguments_are_rejected() -> None:
    unknown = _call("learning_delete_everything", {})
    assert unknown["result"]["isError"] is True
    assert _payload(unknown)["error"] == "unknown_tool"

    invalid = _call(
        "learning_status", {**_event("learning.status"), "api_key": "secret"}
    )
    assert invalid["result"]["isError"] is True
    assert _payload(invalid)["error"] == "invalid_arguments"


def test_publish_without_confirmation_is_rejected_before_dispatch() -> None:
    response = _call(
        "learning_course_publish",
        _event(
            "learning.course.publish",
            idempotency_key="publish-1",
            expected_revision=2,
            payload={"course_id": "course-1"},
        ),
    )

    assert response["result"]["isError"] is True
    assert _payload(response)["error"] == "invalid_arguments"
