"""Handoff-Bundle fuer Agenten-Auftraege: muss den ECHTEN Validator bestehen."""
import pytest

from core.handoff_bundle import baue_handoff_bundle
from vibemind_shared.contracts import validate_brain_openfang_handoff_bundle


def _validiere(b):
    validate_brain_openfang_handoff_bundle(
        b["channel_intent"], b["brain_plan"], b["space_execution_contracts"],
        b["lifecycle"], b["handoff"],
    )


def test_bundle_besteht_den_echten_validator():
    b = baue_handoff_bundle(plan_id="p1", trace_id="test_1", intent="suche x", hop_id="s1",
                            capability="rowboat_search", agent="rowboat-chat")
    _validiere(b)
    assert b["lifecycle"]["status"] == "execution_deferred"
    assert set(b) == {"channel_intent", "brain_plan", "space_execution_contracts",
                      "lifecycle", "handoff"}


def test_bundle_ist_deterministisch_je_plan_und_hop():
    kw = dict(plan_id="p1", trace_id="t", intent="i", hop_id="s1", capability="c", agent="a")
    a, b = baue_handoff_bundle(**kw), baue_handoff_bundle(**kw)
    assert a == b
    assert a["handoff"]["correlation_id"] == b["handoff"]["correlation_id"]
    c = baue_handoff_bundle(**{**kw, "hop_id": "s2"})
    assert c["handoff"]["correlation_id"] != a["handoff"]["correlation_id"]


@pytest.mark.parametrize("intent,hop_id,space", [
    ("", "Hop_1 / Ä", "agent_auftraege"),
    ("x" * 9000, "1", "research"),
    ("üöä", "S" * 200, "agentfarm"),
])
def test_randfaelle_bleiben_schema_konform(intent, hop_id, space):
    b = baue_handoff_bundle(plan_id="Plan 7", trace_id="", intent=intent, hop_id=hop_id,
                            capability="cap", agent="ag", space_id=space)
    _validiere(b)
    assert b["handoff"]["space_id"] in b["lifecycle"]["participating_space_ids"]


def test_unbekannter_kanal_wird_nicht_zum_schemafehler():
    b = baue_handoff_bundle(plan_id="p", trace_id="t", intent="i", hop_id="h",
                            capability="c", agent="a", kanal="api")
    _validiere(b)
