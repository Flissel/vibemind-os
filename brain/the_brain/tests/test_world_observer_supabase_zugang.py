"""Der Supabase-Zugang des Beobachters.

Warum es diesen Test gibt: bis 2026-09-23 las jeder Supabase-Check eine tote
LAN-Adresse als Vorgabe und den Schluessel nur aus SUPABASE_ANON_KEY. brain-core
bekommt den Schluessel aber als Datei (SUPABASE_ANON_KEY_FILE). Jede Nachfrage
lief deshalb ins Leere und endete als UNVERIFIED - still, ohne Fehler.
"""
from unittest.mock import MagicMock, patch

import pytest

from core import config as core_config
from core import world_observer as wo


@pytest.fixture(autouse=True)
def _ground_truth_on(monkeypatch):
    monkeypatch.setattr(wo, "GROUND_TRUTH_ENABLED", True)


@pytest.fixture
def _sauber(monkeypatch, tmp_path):
    for name in ("SUPABASE_URL", "SUPABASE_ANON_KEY", "SUPABASE_ANON_KEY_FILE"):
        monkeypatch.delenv(name, raising=False)
    # /run/secrets gibt es lokal nicht; get_secret faellt dann auf env zurueck.
    # get_secret() ruft aber auch _load_env_file() (Repo-.env-Loader) und liest
    # _SECRETS_DIR (/run/secrets) - beides muss stillgelegt werden, sonst
    # messen die Tests diese Maschine statt den Code.
    monkeypatch.setattr(core_config, "_load_env_file", lambda *a, **k: None)
    monkeypatch.setattr(core_config, "_SECRETS_DIR", str(tmp_path))


def test_schluessel_kommt_aus_der_datei(monkeypatch, tmp_path, _sauber):
    datei = tmp_path / "anon"
    datei.write_text("schluessel-aus-datei\n", encoding="utf-8")
    monkeypatch.setenv("SUPABASE_URL", "http://supabase-kong:8000/")
    monkeypatch.setenv("SUPABASE_ANON_KEY_FILE", str(datei))
    base, headers, grund = wo._supabase_zugang()
    assert base == "http://supabase-kong:8000"
    assert headers == {"apikey": "schluessel-aus-datei"}
    assert grund == ""


def test_ohne_url_keine_vorgabeadresse(monkeypatch, _sauber):
    monkeypatch.setenv("SUPABASE_ANON_KEY", "x")
    base, headers, grund = wo._supabase_zugang()
    assert base is None
    assert "SUPABASE_URL" in grund


def test_ohne_schluessel_kein_anon_platzhalter(monkeypatch, _sauber):
    monkeypatch.setenv("SUPABASE_URL", "http://supabase-kong:8000")
    base, headers, grund = wo._supabase_zugang()
    assert base is None
    assert "Schluessel" in grund


def test_supabase_row_ohne_zugang_ist_unverified(monkeypatch, _sauber):
    v = wo.observe({"check": "supabase_row", "table": "ideas",
                    "match": "id=eq.abc123", "expect": "present"})
    assert v.verdict == wo.UNVERIFIED
    assert "SUPABASE_URL" in v.reason


def test_supabase_row_nutzt_den_zugang(monkeypatch, _sauber):
    monkeypatch.setenv("SUPABASE_URL", "http://supabase-kong:8000")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "k")
    antwort = MagicMock(status_code=200, content=b"[1]")
    antwort.json.return_value = [{"id": "abc123"}]
    with patch("requests.get", return_value=antwort) as get:
        v = wo.observe({"check": "supabase_row", "table": "ideas",
                        "match": "id=eq.abc123", "expect": "present"})
    assert v.verdict == wo.VERIFIED
    url = get.call_args.args[0]
    assert url.startswith("http://supabase-kong:8000/rest/v1/ideas?id=eq.abc123")
    assert get.call_args.kwargs["headers"] == {"apikey": "k"}


def test_transportfehler_ist_unverified_nicht_refuted(monkeypatch, _sauber):
    monkeypatch.setenv("SUPABASE_URL", "http://supabase-kong:8000")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "k")
    with patch("requests.get", side_effect=TimeoutError("zu langsam")):
        v = wo.observe({"check": "supabase_row", "table": "ideas",
                        "match": "id=eq.abc123", "expect": "present"})
    assert v.verdict == wo.UNVERIFIED


def test_401_ist_unverified(monkeypatch, _sauber):
    monkeypatch.setenv("SUPABASE_URL", "http://supabase-kong:8000")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "veraltet")
    antwort = MagicMock(status_code=401, content=b"{}")
    with patch("requests.get", return_value=antwort):
        v = wo.observe({"check": "supabase_row", "table": "ideas",
                        "match": "id=eq.abc123", "expect": "present"})
    assert v.verdict == wo.UNVERIFIED


@pytest.mark.parametrize("check,spec", [
    ("supabase_edge", {"title_a": "A", "title_b": "B"}),
    ("supabase_node_in_bubble", {"node_title": "N", "bubble_title": "B"}),
])
def test_alle_titel_checks_nutzen_den_zugang(monkeypatch, _sauber, check, spec):
    # Ohne URL darf keiner der Checks eine Vorgabeadresse anfragen.
    with patch("requests.get") as get:
        v = wo.observe({"check": check, **spec})
    assert v.verdict == wo.UNVERIFIED
    get.assert_not_called()
