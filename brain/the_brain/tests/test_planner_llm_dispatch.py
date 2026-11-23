"""The planner must reach its model through the dispatcher's role, not name one itself.

Measured in the running brain on 2026-11-23: every intent without a shortcut ended in
"planner returned no plan" after 0.2 s. The planner passed ``model=...`` to the dispatcher,
and the dispatcher rejects any direct provider/model override by design (OpenFang's role
configuration is authoritative). All three planner attempts died on that rejection.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.planner_llm import PlannerLLM  # noqa: E402
from core.subagent_dispatcher import SubagentDispatcher  # noqa: E402

PROMPT = {"user": "u", "system": "s"}
PLAN = '{"plan_id": "p1", "intent": "u", "rationale": "r", "hops": []}'


class Recorder:
    def __init__(self):
        self.calls = []

    def dispatch(self, tool_name, **kwargs):
        self.calls.append((tool_name, kwargs))
        return {"ok": True, "text": PLAN}

    async def adispatch(self, tool_name, **kwargs):
        return self.dispatch(tool_name, **kwargs)


def _forbidden(kwargs):
    return sorted(set(kwargs) & {"model", "provider"})


def test_sync_call_names_no_model():
    recorder = Recorder()
    PlannerLLM(recorder, model="gpt-4o-mini")._call_and_parse(PROMPT)
    tool, kwargs = recorder.calls[0]
    assert tool == "openai_subagent", "the model name still picks the role"
    assert _forbidden(kwargs) == []


def test_async_call_names_no_model():
    recorder = Recorder()
    asyncio.run(PlannerLLM(recorder, model="anthropic/claude-haiku-4.5")._acall_and_parse(PROMPT))
    tool, kwargs = recorder.calls[0]
    assert tool == "claude_subagent"
    assert _forbidden(kwargs) == []


def test_what_the_planner_sends_passes_the_real_gate():
    recorder = Recorder()
    PlannerLLM(recorder, model="llama3.1:latest")._call_and_parse(PROMPT)
    tool, kwargs = recorder.calls[0]
    role, rejection = SubagentDispatcher()._resolve_role(tool, kwargs)
    assert rejection is None, rejection
    assert role == "local_fast"
