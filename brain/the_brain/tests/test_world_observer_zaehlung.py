"""Nachpruefung beim Lesen: eine Zaehlung wird auf zweitem Weg nachgezaehlt.

`idea_count` antwortet "'X' has 12 ideas." Das ist ein Selbstbericht. Der Check
zaehlt dieselben canvas_nodes ueber PostgREST mit `Prefer: count=exact` und
vergleicht. Stimmt die Zahl nicht, ist das REFUTED mit beiden Zahlen im Signal.
"""
from unittest.mock import MagicMock, patch

import pytest

from core import config as core_config
from core import world_observer as wo
from core.capability_validator import CapabilityValidator


@pytest.fixture(autouse=True)
def _an(monkeypatch, tmp_path):
    monkeypatch.setattr(wo, "GROUND_TRUTH_ENABLED", True)
    monkeypatch.setenv("SUPABASE_URL", "http://supabase-kong:8000")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "k")
    # Ein echtes .env oder /run/secrets darf hier nicht durchsickern - wie in
    # test_world_observer_supabase_zugang.py stillgelegt.
    monkeypatch.setattr(core_config, "_load_env_file", lambda *a, **k: None)
    monkeypatch.setattr(core_config, "_SECRETS_DIR", str(tmp_path))


def _antwort(status=200, json_body=None, content_range=None):
    r = MagicMock(status_code=status, content=b"x")
    r.json.return_value = json_body if json_body is not None else []
    r.headers = {"Content-Range": content_range} if content_range else {}
    return r


def test_zaehlung_stimmt():
    with patch("requests.get", side_effect=[
        _antwort(json_body=[{"id": "b1"}]),
        _antwort(json_body=[{"id": "n1"}], content_range="0-0/12"),
    ]):
        v = wo.observe({"check": "supabase_bubble_node_count",
                        "bubble_title": "Marketing", "expect_count": "12"})
    assert v.verdict == wo.VERIFIED
    assert v.signal["gezaehlt"] == 12


def test_zaehlung_weicht_ab_ist_refuted_mit_beiden_zahlen():
    with patch("requests.get", side_effect=[
        _antwort(json_body=[{"id": "b1"}]),
        _antwort(json_body=[{"id": "n1"}], content_range="0-0/13"),
    ]):
        v = wo.observe({"check": "supabase_bubble_node_count",
                        "bubble_title": "Marketing", "expect_count": "12"})
    assert v.verdict == wo.REFUTED
    assert v.signal["gezaehlt"] == 13 and v.signal["behauptet"] == 12


def test_leere_bubble_zaehlt_null():
    with patch("requests.get", side_effect=[
        _antwort(json_body=[{"id": "b1"}]),
        _antwort(json_body=[], content_range="*/0"),
    ]):
        v = wo.observe({"check": "supabase_bubble_node_count",
                        "bubble_title": "Leer", "expect_count": "0"})
    assert v.verdict == wo.VERIFIED


def test_unbekannte_bubble_ist_unverified():
    with patch("requests.get", side_effect=[_antwort(json_body=[])]):
        v = wo.observe({"check": "supabase_bubble_node_count",
                        "bubble_title": "Gibtsnicht", "expect_count": "3"})
    assert v.verdict == wo.UNVERIFIED


def test_keine_zahl_behauptet_ist_unverified():
    with patch("requests.get") as get:
        v = wo.observe({"check": "supabase_bubble_node_count",
                        "bubble_title": "Marketing", "expect_count": "viele"})
    assert v.verdict == wo.UNVERIFIED
    get.assert_not_called()


def test_platzhalter_result_count():
    pc = CapabilityValidator._template_postcondition(
        {"check": "supabase_bubble_node_count", "bubble_title": "{result_title}",
         "expect_count": "{result_count}"},
        arg="Marketing", raw_result="'Marketing' has 12 ideas.")
    assert pc["bubble_title"] == "Marketing"
    assert pc["expect_count"] == "12"


def test_platzhalter_offen_bleibt_offen():
    pc = CapabilityValidator._template_postcondition(
        {"expect_count": "{result_count}"}, arg="", raw_result="Specify a bubble.")
    assert pc["expect_count"] == "{result_count}"


def test_http_ok_mit_url_env_und_pfad(monkeypatch):
    monkeypatch.setenv("CODING_ENGINE_URL", "http://coding-engine:8000/")
    with patch("requests.get", return_value=_antwort(status=200)) as get:
        v = wo.observe({"check": "http_ok", "url_env": "CODING_ENGINE_URL",
                        "path": "/api/v1/jobs/job123/status"})
    assert v.verdict == wo.VERIFIED
    assert get.call_args.args[0] == "http://coding-engine:8000/api/v1/jobs/job123/status"


def test_http_ok_404_ist_refuted(monkeypatch):
    monkeypatch.setenv("CODING_ENGINE_URL", "http://coding-engine:8000")
    with patch("requests.get", return_value=_antwort(status=404)):
        v = wo.observe({"check": "http_ok", "url_env": "CODING_ENGINE_URL",
                        "path": "/api/v1/jobs/gibtsnicht/status"})
    assert v.verdict == wo.REFUTED
