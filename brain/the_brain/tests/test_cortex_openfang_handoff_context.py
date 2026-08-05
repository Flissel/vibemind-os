from pathlib import Path
import sys

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "brain" / "the_brain"))

from core.brain_chat import BrainChat
from core.plan_schema import Plan
from web.routers.cortex import router


_DOCUMENTS = (
    "channel_intent",
    "brain_plan",
    "space_execution_contracts",
    "lifecycle",
    "handoff",
)


class _Verdict:
    should_decompose = True
    triggered_by = "test"
    reason = "exercise the multihop path"


class _Advisor:
    def should_decompose(self, message: str) -> _Verdict:
        return _Verdict()


class _Planner:
    def plan(self, message: str) -> Plan:
        return Plan(
            plan_id="plan-authoritative-handoff",
            intent=message,
            rationale="test plan",
            hops=[],
        )


class _Executor:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def execute(self, plan: Plan, **kwargs: object) -> dict[str, object]:
        self.calls.append(kwargs)
        return {"ok": True, "executed": {}, "state": {}, "elapsed_s": 0.0}


class _Synthesizer:
    def synthesize(self, **_kwargs: object) -> str:
        return "completed"


def _bundle() -> dict[str, object]:
    return {
        "channel_intent": {"correlation_id": "server-correlation"},
        "brain_plan": {"plan_id": "server-plan"},
        "space_execution_contracts": [{"space_id": "research"}],
        "lifecycle": {"status": "execution_deferred"},
        "handoff": {
            "approval_ref": "approval:opaque-server-ref",
            "cost_ref": "cost:opaque-server-ref",
        },
    }


def _app(executor: _Executor, server_context: object = None) -> FastAPI:
    app = FastAPI()
    brain = BrainChat()
    brain.set_multihop(_Advisor(), _Planner(), executor, _Synthesizer())
    app.state.brain_chat = brain
    app.state.socialization_metrics = None

    @app.middleware("http")
    async def provide_trusted_context(request: Request, call_next):
        if server_context is not None:
            request.state.openfang_handoff_bundle = server_context
        return await call_next(request)

    app.include_router(router)
    return app


def _untrusted_body() -> dict[str, object]:
    return {
        "message": "Coordinate the research and write the brief",
        "approval_ref": "approval:client-forged",
        "cost_ref": "cost:client-forged",
        "openfang_handoff_bundle": _bundle(),
    }


def test_cortex_ignores_client_handoff_documents_and_preserves_server_identity() -> None:
    executor = _Executor()
    server_bundle = _bundle()
    client_body = _untrusted_body()

    with TestClient(_app(executor, server_bundle)) as client:
        response = client.post("/api/cortex/chat", json=client_body)

    assert response.status_code == 200
    assert executor.calls == [{"openfang_handoff_bundle": server_bundle}]
    assert executor.calls[0]["openfang_handoff_bundle"] is server_bundle
    assert executor.calls[0]["openfang_handoff_bundle"] is not client_body["openfang_handoff_bundle"]


@pytest.mark.parametrize(
    "server_context",
    [
        None,
        "not-a-bundle",
        {"channel_intent": {"correlation_id": "partial"}},
        {name: {} for name in _DOCUMENTS[:-1]},
    ],
)
def test_cortex_never_reconstructs_a_bundle_from_absent_or_incomplete_context(
    server_context: object,
) -> None:
    executor = _Executor()

    with TestClient(_app(executor, server_context)) as client:
        response = client.post("/api/cortex/chat", json=_untrusted_body())

    assert response.status_code == 200
    assert executor.calls == [{}]
