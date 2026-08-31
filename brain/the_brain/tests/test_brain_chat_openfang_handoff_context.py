from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "brain" / "the_brain"))

from core.brain_chat import BrainChat
from core.plan_schema import Plan


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


def _brain_with_executor(executor: _Executor) -> BrainChat:
    brain = BrainChat()
    brain.set_multihop(_Advisor(), _Planner(), executor, _Synthesizer())
    return brain


def test_send_passes_the_authoritative_bundle_unchanged_to_plan_executor() -> None:
    executor = _Executor()
    bundle = _bundle()

    response = _brain_with_executor(executor).send(
        "Coordinate the research and write the brief",
        openfang_handoff_bundle=bundle,
    )

    assert response.routing_mode == "multihop"
    assert executor.calls == [{"openfang_handoff_bundle": bundle}]
    assert executor.calls[0]["openfang_handoff_bundle"] is bundle


def test_send_without_a_bundle_does_not_add_a_fallback_execute_argument() -> None:
    executor = _Executor()

    _brain_with_executor(executor).send("Coordinate the research and write the brief")

    assert executor.calls == [{}]
