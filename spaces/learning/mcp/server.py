from __future__ import annotations

import json
import os
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from spaces.learning.bridge.dispatcher import (
    ApplicationGateway,
    LearningDispatcher,
    ReceiptStore,
    UiIntentDelivery,
)
from spaces.learning.bridge.ui_bridge import UiBridge
from spaces.learning.bridge.learnhouse_client import LearnHouseClient
from spaces.learning.bridge.penecho_client import PenEchoClient
from spaces.learning.contracts.events import EVENT_TOOL_MAP, LearningToolName
from spaces.learning.contracts.mcp_models import EventEnvelopeV1, ToolRequestV1
from spaces.learning.mcp.tools.courses import build_course_gateways
from spaces.learning.mcp.tools.navigation import build_navigation_gateways
from spaces.learning.mcp.tools.generation import build_generation_gateways
from spaces.learning.mcp.tools.sessions import build_session_gateways
from spaces.learning.mcp.tools.tutor import TutorGateway
from spaces.learning.mcp.tools.canvas import (
    CanvasEvaluationClient,
    CanvasGateway,
    build_canvas_gateways,
)
from spaces.learning.mcp.tools.status import (
    build_default_dispatcher as build_status_dispatcher,
)
from spaces.learning.services.course_factory.artifact_store import (
    CourseFactoryArtifactStore,
)
from spaces.learning.services.course_factory.learnhouse_gateway import (
    build_learnhouse_http_gateway,
)
from spaces.learning.services.course_factory.publisher import CourseDraftPublisher
from spaces.learning.services.course_factory.repository import CourseFactoryRepository
from spaces.learning.services.db.session import build_session_factory, create_learning_engine
from spaces.learning.services.adaptive_engine.session_service import AdaptiveSessionService
from spaces.learning.services.evaluation.canvas_artifacts import CanvasArtifactStore


SERVER_NAME = "spaces-learning"
SERVER_VERSION = "1.0.0"
PROTOCOL_VERSION = "2024-11-05"

_EVENT_BY_TOOL = {tool: event for event, tool in EVENT_TOOL_MAP.items()}


def _tool_schema(tool: LearningToolName) -> dict[str, Any]:
    schema = EventEnvelopeV1.model_json_schema()
    schema["properties"]["event_type"] = {"const": _EVENT_BY_TOOL[tool].value}
    return schema


TOOLS: list[dict[str, Any]] = [
    {
        "name": tool.value,
        "description": f"Execute the semantic {_EVENT_BY_TOOL[tool].value} operation.",
        "inputSchema": _tool_schema(tool),
    }
    for tool in LearningToolName
]


def build_default_dispatcher(
    *,
    learnhouse: ApplicationGateway | None = None,
    receipts: ReceiptStore | None = None,
    ui_delivery: UiIntentDelivery | None = None,
) -> LearningDispatcher:
    admitted_learnhouse = learnhouse or LearnHouseClient()
    gateways = dict(build_course_gateways(admitted_learnhouse))
    gateways.update(build_navigation_gateways(admitted_learnhouse))
    database_url = os.environ.get("LEARNING_DATABASE_URL", "").strip()
    artifact_root = os.environ.get("LEARNING_ARTIFACT_ROOT", "").strip()
    if database_url and os.environ.get("LEARNING_SERVICE_ROLE") == "mcp":
        engine = create_learning_engine(database_url)
        session_factory = build_session_factory(engine)
        evaluation_url = os.environ.get("LEARNING_EVALUATION_SERVICE_URL", "").strip()
        evaluation_key = os.environ.get("LEARNING_EVALUATION_SERVICE_KEY", "")
        evaluation_client = (
            CanvasEvaluationClient(base_url=evaluation_url, service_key=evaluation_key)
            if evaluation_url and evaluation_key
            else None
        )
        adaptive_sessions = AdaptiveSessionService(
            session_factory,
            rubric_evaluator=evaluation_client,
            external_rubric_types=(
                frozenset({"penecho_canvas"})
                if evaluation_client is not None
                else frozenset()
            ),
        )
        gateways.update(build_session_gateways(adaptive_sessions))
        if evaluation_client is not None:
            gateways[LearningToolName.TUTOR_ASK] = TutorGateway(
                sessions=adaptive_sessions,
                tutor=evaluation_client,
            )
            penecho_url = os.environ.get("LEARNING_PENECHO_URL", "").strip()
            penecho_origin = os.environ.get("LEARNING_PENECHO_ORIGIN", "").strip()
            penecho_token = os.environ.get("PENECHO_LEARNING_TOKEN", "")
            if artifact_root and penecho_url and penecho_origin and penecho_token:
                penecho = PenEchoClient(
                    base_url=penecho_url,
                    learning_origin=penecho_origin,
                    auth_token=penecho_token,
                )
                gateways.update(build_canvas_gateways(CanvasGateway(
                    penecho=penecho,
                    artifacts=CanvasArtifactStore(artifact_root=Path(artifact_root)),
                    evaluations=evaluation_client,
                    tutor=evaluation_client,
                )))
        if artifact_root:
            store = CourseFactoryArtifactStore(
                session_factory, artifact_root=Path(artifact_root)
            )
            repository = CourseFactoryRepository(session_factory)
            publisher = CourseDraftPublisher(
                repository=repository,
                artifact_store=store,
                learnhouse=build_learnhouse_http_gateway(),
            )
            gateways.update(
                build_generation_gateways(
                    session_factory=session_factory,
                    repository=repository,
                    publisher=publisher,
                    artifact_store=store,
                    learnhouse_fallback=admitted_learnhouse,
                )
            )
    return build_status_dispatcher(
        gateways=gateways,
        receipts=receipts,
        ui_delivery=ui_delivery,
    )


def _runtime_ui_delivery() -> UiIntentDelivery | None:
    token = os.environ.get("LEARNING_UI_BRIDGE_TOKEN", "")
    if not token:
        return None
    return UiBridge(
        base_url=os.environ.get(
            "LEARNING_UI_BRIDGE_URL", "http://127.0.0.1:5151"
        ),
        auth_token=token,
    )


_DEFAULT_DISPATCHER = build_default_dispatcher(ui_delivery=_runtime_ui_delivery())


def _mcp_result(payload: Mapping[str, Any], *, is_error: bool) -> dict[str, Any]:
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(payload, ensure_ascii=False, sort_keys=True),
            }
        ],
        "isError": is_error,
    }


def _error(request_id: Any, code: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": _mcp_result({"error": code}, is_error=True),
    }


def handle_message(
    message: Any, *, dispatcher: LearningDispatcher | None = None
) -> dict[str, Any] | None:
    if not isinstance(message, Mapping):
        return {
            "jsonrpc": "2.0",
            "id": None,
            "error": {"code": -32600, "message": "invalid request"},
        }
    request_id = message.get("id")
    method = message.get("method")
    if method == "notifications/initialized":
        return None
    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": PROTOCOL_VERSION,
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                "capabilities": {"tools": {}},
            },
        }
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": request_id, "result": {"tools": TOOLS}}
    if method != "tools/call":
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": -32601, "message": "method not found"},
        }

    params = message.get("params")
    if not isinstance(params, Mapping):
        return _error(request_id, "invalid_arguments")
    try:
        tool = LearningToolName(params.get("name"))
    except (TypeError, ValueError):
        return _error(request_id, "unknown_tool")
    arguments = params.get("arguments")
    if not isinstance(arguments, Mapping):
        return _error(request_id, "invalid_arguments")
    try:
        event = EventEnvelopeV1.model_validate(dict(arguments))
        request = ToolRequestV1(tool=tool, event=event)
    except ValidationError:
        return _error(request_id, "invalid_arguments")

    result = (dispatcher or _DEFAULT_DISPATCHER).dispatch(request)
    payload = result.model_dump(mode="json", exclude_none=True)
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": _mcp_result(
            payload,
            is_error=result.state in {"rejected", "unavailable"},
        ),
    }


def main() -> int:
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            message = json.loads(line)
            response = handle_message(message)
        except json.JSONDecodeError:
            response = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": "parse error"},
            }
        if response is not None:
            print(json.dumps(response, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
