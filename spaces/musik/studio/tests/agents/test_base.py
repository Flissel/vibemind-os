import pytest
from pydantic import BaseModel, Field

from djset.agents.base import (
    AgentError, Budget, BudgetExceeded, SchemaViolation, TransportFailure,
    run_agent,
)
from tests.fixtures.fake_client import FakeClient


class Answer(BaseModel):
    value: int = Field(ge=0)


def _budget() -> Budget:
    return Budget(max_tokens=10_000, max_seconds=60.0)


class _Sleeps:
    """Nimmt die Backoff-Pausen entgegen, statt sie abzuwarten.

    Die Testsuite darf nicht schlafen; interessant ist ohnehin nur, ob und
    wie lange gewartet WUERDE.
    """

    def __init__(self) -> None:
        self.waited: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.waited.append(seconds)


class _Clock:
    """Gestellte Uhr: liefert die vorgegebenen Zeitpunkte der Reihe nach."""

    def __init__(self, times: list[float]) -> None:
        self._times = list(times)
        self._last = self._times[0] if self._times else 0.0

    def __call__(self) -> float:
        if self._times:
            self._last = self._times.pop(0)
        return self._last


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
    budget = Budget(max_tokens=10, max_seconds=60.0)
    client = FakeClient(['{"value": 1}'])
    with pytest.raises(BudgetExceeded):
        run_agent(client, "x" * 400, Answer, budget)
    assert client.calls == 0


# --- Transportfehler: begrenzter Retry mit Backoff (Spec Abschnitt 7) ------


def test_transport_error_is_retried_and_then_succeeds():
    client = FakeClient([TimeoutError("gateway timeout"), '{"value": 4}'])
    sleeps = _Sleeps()

    result = run_agent(
        client, "gib mir vier", Answer, _budget(),
        backoff_base_s=0.01, sleep=sleeps,
    )

    assert result.value == 4
    assert client.calls == 2
    assert sleeps.waited == [0.01]


def test_transport_backoff_grows_between_attempts():
    client = FakeClient(
        [ConnectionError("weg"), ConnectionError("immer noch weg"), '{"value": 1}']
    )
    sleeps = _Sleeps()

    run_agent(client, "gib mir eins", Answer, _budget(),
              backoff_base_s=0.01, sleep=sleeps)

    assert sleeps.waited == [0.01, 0.02]


def test_transport_error_gives_up_after_the_limit():
    client = FakeClient([TimeoutError("weg")] * 3)
    sleeps = _Sleeps()

    with pytest.raises(TransportFailure, match="nicht erreichbar"):
        run_agent(client, "unerreichbar", Answer, _budget(),
                  backoff_base_s=0.01, sleep=sleeps)

    assert client.calls == 3
    # Nach dem letzten Versuch wird nicht mehr gewartet.
    assert sleeps.waited == [0.01, 0.02]


def test_transport_retries_are_separate_from_schema_retries():
    """Ohne Schema-Wiederholung wird trotzdem ueber den Transport hinweggeholfen."""
    client = FakeClient([TimeoutError("weg"), '{"value": 2}'])

    result = run_agent(client, "gib mir zwei", Answer, _budget(), retries=0,
                       backoff_base_s=0.01, sleep=_Sleeps())

    assert result.value == 2
    assert client.calls == 2


def test_agent_errors_from_the_client_are_not_retried():
    """Ein AgentError ist kein Transportproblem und darf nicht wiederholt werden."""
    client = FakeClient([AgentError("kein Transportfehler")])

    with pytest.raises(AgentError):
        run_agent(client, "x", Answer, _budget(), backoff_base_s=0.01, sleep=_Sleeps())

    assert client.calls == 1


def test_every_transport_attempt_costs_budget():
    """Ein Retry schickt denselben Prompt erneut - und kostet erneut."""
    budget = _budget()
    client = FakeClient([TimeoutError("weg"), '{"value": 0}'])

    run_agent(client, "x" * 400, Answer, budget, backoff_base_s=0.01, sleep=_Sleeps())

    # Zwei Prompts a 100 Tokens plus die eine Antwort.
    assert budget.spent >= 200


# --- Zeitbudget (Spec Abschnitt 7: Tokens UND Wanduhrzeit) ----------------


def test_wall_clock_budget_stops_the_run():
    # Start bei 0, erste Pruefung bei 30 s - das Zeitbudget ist dann um.
    budget = Budget(max_tokens=10_000, max_seconds=5.0, now=_Clock([0.0, 30.0]))
    client = FakeClient(['{"value": 1}'])

    with pytest.raises(BudgetExceeded, match="Zeitbudget"):
        run_agent(client, "zu spaet", Answer, budget)

    assert client.calls == 0


def test_wall_clock_budget_lets_a_quick_run_through():
    budget = Budget(max_tokens=10_000, max_seconds=5.0, now=_Clock([0.0, 1.0, 2.0]))
    client = FakeClient(['{"value": 1}'])

    assert run_agent(client, "schnell genug", Answer, budget).value == 1


def test_budget_reports_the_elapsed_time():
    budget = Budget(max_tokens=10, max_seconds=5.0, now=_Clock([0.0, 3.0]))
    assert budget.elapsed_s == 3.0
