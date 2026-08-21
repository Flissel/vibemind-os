import pytest
from pydantic import BaseModel, Field

from djset.agents.base import Budget, BudgetExceeded, SchemaViolation, run_agent
from tests.fixtures.fake_client import FakeClient


class Answer(BaseModel):
    value: int = Field(ge=0)


def _budget() -> Budget:
    return Budget(max_tokens=10_000)


def test_parses_a_valid_response():
    client = FakeClient(['{"value": 7}'])
    result = run_agent(client, "gib mir sieben", Answer, _budget())
    assert result.value == 7
    assert client.calls == 1


def test_retries_on_broken_json_and_succeeds():
    client = FakeClient(["kein json", '{"value": 3}'])
    result = run_agent(client, "gib mir drei", Answer, _budget())
    assert result.value == 3
    assert client.calls == 2


def test_retry_prompt_contains_the_error():
    client = FakeClient(['{"value": -1}', '{"value": 1}'])
    run_agent(client, "gib mir eins", Answer, _budget())
    assert "greater than or equal to 0" in client.prompts[1]


def test_gives_up_after_the_retry_limit():
    client = FakeClient(["nein", "immer noch nein", "weiterhin nein"])
    with pytest.raises(SchemaViolation):
        run_agent(client, "unmoeglich", Answer, _budget(), retries=2)
    assert client.calls == 3


def test_strips_markdown_fences():
    client = FakeClient(['```json\n{"value": 5}\n```'])
    result = run_agent(client, "gib mir fuenf", Answer, _budget())
    assert result.value == 5


def test_budget_stops_further_calls():
    budget = Budget(max_tokens=10)
    client = FakeClient(['{"value": 1}'])
    with pytest.raises(BudgetExceeded):
        run_agent(client, "x" * 400, Answer, budget)
    assert client.calls == 0
