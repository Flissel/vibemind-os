"""M2 (Schlusspruefung T1): `POST /api/decisions/reward` darf einen Testlauf
nicht ins Selbstbild einspeisen, auch wenn ihm nachtraeglich ein Reward
gegeben wird. Das Setzen des reward-Feldes auf dem Datensatz selbst bleibt.

Kein bestehender Test rief diesen Handler bisher auf (`git grep -n
"decisions/reward" -- brain/the_brain/tests` traf nur einen Kommentar in
test_multihop_response_contract.py) - dieser Test ruft den Handler direkt
mit einem gefaelschten Request/kg auf, wie test_openfang_agent_manifest.py
es fuer introspection-Handler vormacht.
"""
import asyncio
import sys
from types import SimpleNamespace

from web.routers import introspection


class _FakeRecord:
    def __init__(self, payload):
        self.payload = payload


def _fake_kg(payload):
    calls = {"set_payload": []}

    def _retrieve(**kwargs):
        return [_FakeRecord(dict(payload))]

    def _set_payload(**kwargs):
        calls["set_payload"].append(kwargs)

    client = SimpleNamespace(retrieve=_retrieve, set_payload=_set_payload)
    return SimpleNamespace(client=client), calls


def _fake_request(kg, body):
    async def _json():
        return body

    return SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(qdrant_kg=kg)),
        json=_json,
    )


def _patch_self_prior(monkeypatch):
    update_calls = []
    monkeypatch.setitem(
        sys.modules, "core.decision_self_prior",
        SimpleNamespace(update=lambda **kw: update_calls.append(kw)),
    )
    return update_calls


def test_reward_testlauf_speist_selbstbild_nicht(monkeypatch):
    payload = {"intent": "x", "capability_chain": ["bubble_create"], "is_test": True}
    kg, calls = _fake_kg(payload)
    update_calls = _patch_self_prior(monkeypatch)
    req = _fake_request(kg, {"plan_id": "p1", "reward": 1.0})

    resp = asyncio.run(introspection.decisions_reward(req))

    assert resp.status_code == 200
    assert update_calls == [], "Testlauf darf decision_self_prior.update nicht aufrufen"
    # reward-Feld auf dem Datensatz wird trotzdem gesetzt
    assert calls["set_payload"][0]["payload"]["reward"] == 1.0


def test_reward_echter_lauf_speist_selbstbild(monkeypatch):
    payload = {"intent": "x", "capability_chain": ["bubble_create"], "is_test": False}
    kg, calls = _fake_kg(payload)
    update_calls = _patch_self_prior(monkeypatch)
    req = _fake_request(kg, {"plan_id": "p1", "reward": 1.0})

    resp = asyncio.run(introspection.decisions_reward(req))

    assert resp.status_code == 200
    assert len(update_calls) == 1
    assert update_calls[0]["capability"] == "bubble_create"
    assert calls["set_payload"][0]["payload"]["reward"] == 1.0


def test_reward_ohne_is_test_feld_speist_selbstbild_wie_bisher(monkeypatch):
    # Altbestand ohne is_test-Feld: bisheriges Verhalten (aktualisieren) bleibt.
    payload = {"intent": "x", "capability_chain": ["bubble_create"]}
    kg, calls = _fake_kg(payload)
    update_calls = _patch_self_prior(monkeypatch)
    req = _fake_request(kg, {"plan_id": "p1", "reward": 1.0})

    resp = asyncio.run(introspection.decisions_reward(req))

    assert resp.status_code == 200
    assert len(update_calls) == 1
