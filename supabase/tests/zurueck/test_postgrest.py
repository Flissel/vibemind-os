"""Probe ueber PostgREST: kommt der Kopf X-Aenderung-Id in der Datenbank an?

Laeuft nur, wenn ein PostgREST vor dem Wegwerf-Postgres steht
(``ZURUECK_TEST_REST_URL`` und ``ZURUECK_TEST_REST_KEY``). Ohne das ist dieser Teil
NICHT geprueft; die uebrigen Tests setzen ``request.headers`` von Hand.
"""
import json
import os
import urllib.request

import pytest

from conftest import protokoll

REST = os.environ.get("ZURUECK_TEST_REST_URL", "").rstrip("/")
KEY = os.environ.get("ZURUECK_TEST_REST_KEY", "")

pytestmark = pytest.mark.skipif(not (REST and KEY), reason="kein PostgREST vor dem Wegwerf-Postgres")


def _anfrage(methode: str, pfad: str, koerper: dict, gruppe: str | None = None):
    kopf = {"apikey": KEY, "Authorization": f"Bearer {KEY}", "Content-Type": "application/json", "Prefer": "return=representation"}
    if gruppe:
        kopf["X-Aenderung-Id"] = gruppe
    anfrage = urllib.request.Request(f"{REST}{pfad}", data=json.dumps(koerper).encode(), headers=kopf, method=methode)
    with urllib.request.urlopen(anfrage, timeout=10) as antwort:
        return json.loads(antwort.read() or b"null")


def test_kopf_kommt_ueber_postgrest_an(db):
    _anfrage("POST", "/canvas_nodes", {"id": "n1", "title": "ueber REST"}, gruppe="probe-1")
    (zeile,) = protokoll(db, "zeilen_id = 'n1'")
    assert (zeile["quelle"], zeile["gruppe"]) == ("agent", "probe-1")


def test_rpc_ideen_zurueck_ueber_postgrest(db):
    _anfrage("POST", "/canvas_nodes", {"id": "n1", "title": "ueber REST"}, gruppe="probe-1")
    ergebnis = _anfrage("POST", "/rpc/ideen_zurueck", {})
    assert ergebnis["gruppe"] == "probe-1"
