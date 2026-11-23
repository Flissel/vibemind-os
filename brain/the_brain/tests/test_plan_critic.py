"""Plan-Kritiker: aus, bis er bewusst eingeschaltet wird; nie mit Modellvorgabe (Lauf 5 der Pruefung)."""
from __future__ import annotations

import importlib
import json
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import plan_critic  # noqa: E402


@pytest.fixture()
def SubagentDispatcher(monkeypatch):
    # Wie tests/test_subagent_dispatcher_openfang.py: vibemind_shared ist hier nicht installiert.
    shared = types.ModuleType("vibemind_shared")
    shared.OpenFangUnavailable = type("OpenFangUnavailable", (RuntimeError,), {})
    shared.get_client = shared.get_client_sync = lambda role: None
    shared.get_model = lambda role: "rolle-aus-openfang"
    monkeypatch.setitem(sys.modules, "vibemind_shared", shared)
    sys.modules.pop("core.subagent_dispatcher", None)
    return importlib.import_module("core.subagent_dispatcher").SubagentDispatcher

PLAN = {"rationale": "x", "hops": [{"step_id": "s1", "description": "Liste", "capability": "idea.list"}]}


def _echter_dispatcher(monkeypatch, gesehen, SubagentDispatcher):
    async def openfang(self, role, prompt, kwargs):
        gesehen.append((role, dict(kwargs)))
        text = json.dumps({"risks": [], "overall_severity": 0.1, "recommend": "proceed"})
        return "rolle-aus-openfang", SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))])

    monkeypatch.setattr(SubagentDispatcher, "_complete_via_openfang", openfang)
    return SubagentDispatcher()


def test_aus_als_vorgabe_ohne_aufruf(monkeypatch, SubagentDispatcher) -> None:
    monkeypatch.delenv("PLAN_CRITIC_ENABLED", raising=False)
    gesehen = []
    urteil = plan_critic.critique(PLAN, "Zeig die Notizen", _echter_dispatcher(monkeypatch, gesehen, SubagentDispatcher))
    assert urteil["skipped"] is True and urteil["recommend"] == "proceed" and gesehen == []


def test_eingeschaltet_laeuft_er_am_echten_dispatcher_ohne_modellvorgabe(monkeypatch, SubagentDispatcher) -> None:
    monkeypatch.setenv("PLAN_CRITIC_ENABLED", "1")
    monkeypatch.setenv("PLAN_CRITIC_MODEL", "irgendein/modell")            # wird nicht mehr gelesen
    gesehen = []
    urteil = plan_critic.critique(PLAN, "Zeig die Notizen", _echter_dispatcher(monkeypatch, gesehen, SubagentDispatcher))
    assert "error" not in urteil and urteil.get("skipped") is not True, urteil
    ((rolle, kwargs),) = gesehen
    assert rolle == "brain_fast_reasoning" and "model" not in kwargs and "provider" not in kwargs


def test_abschalter_gewinnt(monkeypatch, SubagentDispatcher) -> None:
    monkeypatch.setenv("PLAN_CRITIC_ENABLED", "1")
    monkeypatch.setenv("PLAN_CRITIC_DISABLED", "1")
    gesehen = []
    assert plan_critic.critique(PLAN, "x", _echter_dispatcher(monkeypatch, gesehen, SubagentDispatcher))["skipped"] is True
    assert gesehen == []
