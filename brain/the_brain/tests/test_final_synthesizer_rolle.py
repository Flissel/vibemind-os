"""Synthese ohne Modellvorgabe (55b): der Dispatcher lehnt model/provider ab, OpenFang waehlt."""
from __future__ import annotations

import asyncio
import importlib
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.final_synthesizer import FinalSynthesizer  # noqa: E402


class _Dispatcher:
    def __init__(self, antwort):
        self.aufrufe = []
        self.antwort = antwort

    def dispatch(self, tool, **kw):
        self.aufrufe.append((tool, kw))
        return self.antwort

    async def adispatch(self, tool, **kw):
        self.aufrufe.append((tool, kw))
        return self.antwort


def _synth(d):
    return FinalSynthesizer(d, model="groq::llama-3.3-70b-versatile", fallback_model="groq::llama-3.1-8b-instant")


def test_synthese_gibt_kein_modell_mit() -> None:
    d = _Dispatcher({"ok": True, "text": "Fertig."})
    assert _synth(d).synthesize(intent="x", plan=None, executed={}, state={}) == "Fertig."
    assert asyncio.run(_synth(d).asynthesize(intent="x", plan=None, executed={}, state={})) == "Fertig."
    assert [t for t, _ in d.aufrufe] == ["groq_subagent", "groq_subagent"]
    for _, kw in d.aufrufe:
        assert "model" not in kw and "provider" not in kw


def test_gleiche_rolle_wird_nicht_zweimal_gefragt() -> None:
    d = _Dispatcher({"ok": False, "error": "x"})
    asyncio.run(_synth(d).asynthesize(intent="x", plan=None, executed={}, state={}))
    assert len(d.aufrufe) == 1


@pytest.fixture()
def SubagentDispatcher(monkeypatch):
    # Wie tests/test_plan_critic.py: vibemind_shared ist hier nicht installiert.
    shared = types.ModuleType("vibemind_shared")
    shared.OpenFangUnavailable = type("OpenFangUnavailable", (RuntimeError,), {})
    shared.get_client = shared.get_client_sync = lambda role: None
    shared.get_model = lambda role: "rolle-aus-openfang"
    monkeypatch.setitem(sys.modules, "vibemind_shared", shared)
    sys.modules.pop("core.subagent_dispatcher", None)
    return importlib.import_module("core.subagent_dispatcher").SubagentDispatcher


def test_synthese_laeuft_am_echten_dispatcher(monkeypatch, SubagentDispatcher) -> None:
    gesehen = []

    async def openfang(self, role, prompt, kwargs):
        gesehen.append((role, dict(kwargs)))
        return "rolle-aus-openfang", SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="Die Notizen stehen bereit."))])

    monkeypatch.setattr(SubagentDispatcher, "_complete_via_openfang", openfang)
    text = asyncio.run(_synth(SubagentDispatcher()).asynthesize(intent="x", plan=None, executed={}, state={}))
    assert text == "Die Notizen stehen bereit."
    ((rolle, kwargs),) = gesehen
    assert rolle == "brain_fast_reasoning" and "model" not in kwargs
