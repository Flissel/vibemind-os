"""Punkt 29 (Lauf 8): Jedes Ereignis traegt plan_id und trace_id seines eigenen Satzes.

Zwei Saetze ueberlappten (der zweite lief an, bevor die Antwort des ersten raus war); die App
ordnete die Annahme des ersten der plan_id des zweiten zu. hop_started/hop_completed trugen gar
keine plan_id. Ohne Netz; Namen erfunden.
"""
from __future__ import annotations

import asyncio
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.plan_executor import PlanExecutor  # noqa: E402
from core.plan_schema import Plan  # noqa: E402


def _ereignisse(q: asyncio.Queue) -> list[dict]:
    aus = []
    while not q.empty():
        aus.append(q.get_nowait())
    return aus


def test_ereignisse_ueberlappender_laeufe_tragen_ihre_eigene_kennung() -> None:
    pe = PlanExecutor()
    q = pe.subscribe()
    erster_drin, zweiter_fertig = threading.Event(), threading.Event()

    def erster() -> None:
        pe._lauf.ids = {"plan_id": "ideen_1", "trace_id": "tr_satz1"}
        erster_drin.set()
        zweiter_fertig.wait(5)            # der zweite Satz laeuft dazwischen
        pe._publish("hop_completed", {"step_id": "s1"})
        pe._publish("plan_completed", {"plan_id": "ideen_1", "status": "angenommen"})
        pe._lauf.ids = None

    def zweiter() -> None:
        erster_drin.wait(5)
        pe._lauf.ids = {"plan_id": "ideen_2", "trace_id": "tr_satz2"}
        pe._publish("hop_started", {"step_id": "s1"})
        pe._lauf.ids = None
        zweiter_fertig.set()

    a, b = threading.Thread(target=erster), threading.Thread(target=zweiter)
    a.start(); b.start(); a.join(5); b.join(5)
    ereignisse = {e["kind"]: e["payload"] for e in _ereignisse(q)}
    assert ereignisse["hop_started"]["plan_id"] == "ideen_2" and ereignisse["hop_started"]["trace_id"] == "tr_satz2"
    assert ereignisse["hop_completed"]["plan_id"] == "ideen_1"
    assert ereignisse["plan_completed"] == {"plan_id": "ideen_1", "trace_id": "tr_satz1", "status": "angenommen"}


def test_ereignis_ausserhalb_eines_laufs_bleibt_wie_es_ist() -> None:
    pe = PlanExecutor()
    q = pe.subscribe()
    pe._publish("plan_rewarded", {"plan_id": "ideen_9", "delta": 1})
    assert _ereignisse(q)[0]["payload"] == {"plan_id": "ideen_9", "delta": 1}


def test_execute_stempelt_und_raeumt_auf() -> None:
    pe = PlanExecutor()
    q = pe.subscribe()
    plan = Plan("ideen_3", "Stadtfest", "", [])
    plan.trace_id = "tr_satz3"
    pe.execute(plan)
    ereignisse = _ereignisse(q)
    assert ereignisse, "kein Ereignis"
    assert all(e["payload"].get("plan_id") == "ideen_3" for e in ereignisse if isinstance(e["payload"], dict))
    assert all(e["payload"].get("trace_id") == "tr_satz3" for e in ereignisse
               if e["kind"] in ("plan_started", "plan_completed"))
    assert getattr(pe._lauf, "ids", None) is None
