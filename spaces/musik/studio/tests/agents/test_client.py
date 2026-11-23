"""Das Zeitlimit des echten Clients - ohne das echte SDK und ohne Modell.

`query` wird durch eine gefaelschte asynchrone Generatorfunktion ersetzt.
"""

import asyncio

import pytest
from pydantic import BaseModel

from djset.agents import client as client_modul
from djset.agents.base import Budget, TransportFailure, run_agent
from djset.agents.client import MODELL_ZEITLIMIT_S, ClaudeClient


class _Antwort(BaseModel):
    ok: bool


def _haengt(aufrufe: list[str]):
    async def query(prompt: str, options: object):  # noqa: ARG001
        aufrufe.append(prompt)
        await asyncio.sleep(30)
        yield None  # pragma: no cover - wird nie erreicht

    return query


def test_das_zeitlimit_ist_eine_konstante_von_300_sekunden():
    assert MODELL_ZEITLIMIT_S == 300.0
    assert ClaudeClient()._zeitlimit_s == MODELL_ZEITLIMIT_S


def test_ein_haengender_aufruf_wird_nach_dem_zeitlimit_abgebrochen(monkeypatch):
    aufrufe: list[str] = []
    monkeypatch.setattr(client_modul, "query", _haengt(aufrufe))

    with pytest.raises(TimeoutError, match="Zeitlimit"):
        ClaudeClient(zeitlimit_s=0.05).complete("Hallo", {})

    assert len(aufrufe) == 1


def test_die_zeitueberschreitung_wird_als_transportfehler_wiederholt(monkeypatch):
    """base.py wiederholt jede Ausnahme, die kein AgentError ist, mit Backoff
    und meldet danach TransportFailure - genau das muss ein Timeout sein."""
    aufrufe: list[str] = []
    monkeypatch.setattr(client_modul, "query", _haengt(aufrufe))
    pausen: list[float] = []

    with pytest.raises(TransportFailure, match="Zeitlimit"):
        run_agent(
            ClaudeClient(zeitlimit_s=0.05),
            "Hallo",
            _Antwort,
            Budget(max_tokens=10_000, max_seconds=60.0),
            sleep=pausen.append,
        )

    assert len(aufrufe) == 3
    assert pausen == [1.0, 2.0]
