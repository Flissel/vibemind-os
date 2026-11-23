"""Das Zeitlimit des echten Clients - ohne das echte SDK und ohne Modell.

`query` wird durch eine gefaelschte asynchrone Generatorfunktion ersetzt.
"""

import asyncio
import math

import anyio
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


# --- Nachzug B5: das Zeitlimit ist ein anyio-Abbruch, das Aufraeumen laeuft --


def _haengt_mit_aufraeumen(protokoll: list[str]):
    """Wie das SDK: der Generator raeumt in seinem finally auf, und zwar in
    einem abgeschirmten anyio-Bereich (SubprocessCLITransport.close). Das
    Abschirmen wirkt nur gegen einen Abbruch, der aus einem anyio-Bereich
    kommt - ein roher asyncio-Abbruch (wait_for) ueberspringt es."""

    async def query(prompt: str, options: object):  # noqa: ARG001
        protokoll.append(f"frist:{anyio.current_effective_deadline()}")
        try:
            await anyio.sleep(30)
            yield None  # pragma: no cover - wird nie erreicht
        finally:
            with anyio.CancelScope(shield=True):
                await anyio.sleep(0.01)
                protokoll.append("aufgeraeumt")

    return query


def test_das_zeitlimit_ist_eine_anyio_frist_die_das_sdk_sieht(monkeypatch):
    """Vorher asyncio.wait_for: das SDK sah keine Frist (unendlich) und bekam
    einen rohen asyncio-Abbruch, den close() laut eigener Doku nicht
    behandelt - terminate/kill fiel aus, der CLI-Kindprozess lief weiter."""
    protokoll: list[str] = []
    monkeypatch.setattr(client_modul, "query", _haengt_mit_aufraeumen(protokoll))

    with pytest.raises(TimeoutError, match="Zeitlimit"):
        ClaudeClient(zeitlimit_s=0.05).complete("Hallo", {})

    frist = float(protokoll[0].removeprefix("frist:"))
    assert math.isfinite(frist)


def test_beim_zeitlimit_laeuft_das_aufraeumen_des_generators(monkeypatch):
    protokoll: list[str] = []
    monkeypatch.setattr(client_modul, "query", _haengt_mit_aufraeumen(protokoll))

    with pytest.raises(TimeoutError, match="Zeitlimit"):
        ClaudeClient(zeitlimit_s=0.05).complete("Hallo", {})

    assert protokoll[-1] == "aufgeraeumt"
