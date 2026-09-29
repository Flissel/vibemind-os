from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from core.knowledge import nachfrage as nf
from core.knowledge.schema import Beleg

T = datetime(2026, 9, 23, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "http://supabase-kong:8000")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "k")
    monkeypatch.setenv("OPENFANG_URL", "http://host.docker.internal:4200")
    monkeypatch.setenv("OPENFANG_API_KEY", "of")


def antwort(body, status=200, cr=None):
    r = MagicMock(status_code=status)
    r.json.return_value = body
    r.headers = {"Content-Range": cr} if cr else {}
    return r


@pytest.mark.parametrize("daten,feld,erwartet", [
    ([{"status": "raw"}], "status", "raw"),
    ({"a": {"b": 3}}, "a.b", "3"),
    ([{"name": "x", "state": "Running"}, {"name": "y", "state": "Stopped"}], "[name=y].state", "Stopped"),
    ([], "status", None),
    ([{"name": "x"}], "[name=z].state", None),
])
def test_feld_lesen(daten, feld, erwartet):
    assert nf.feld_lesen(daten, feld) == erwartet


def test_supabase_wert_stimmt():
    b = Beleg(nr=1, quelle="supabase", ziel="ideas?id=eq.a&select=status",
              feld="status", wert="raw", gemessen=T)
    with patch("requests.get", return_value=antwort([{"status": "raw"}])) as get:
        assert nf.nachfragen(b) is True
    assert get.call_args.args[0] == "http://supabase-kong:8000/rest/v1/ideas?id=eq.a&select=status"


def test_supabase_zaehlung_ueber_content_range():
    b = Beleg(nr=2, quelle="supabase", ziel="canvas_nodes?linked_idea_id=eq.a",
              feld="#count", wert="12", gemessen=T)
    with patch("requests.get", return_value=antwort([], cr="*/13")) as get:
        assert nf.nachfragen(b) is False
    assert get.call_args.kwargs["headers"]["Prefer"] == "count=exact"


def test_openfang_mit_bearer():
    b = Beleg(nr=3, quelle="openfang", ziel="/api/agents", feld="[name=x].state",
              wert="Running", gemessen=T)
    with patch("requests.get", return_value=antwort([{"name": "x", "state": "Running"}])) as get:
        assert nf.nachfragen(b) is True
    assert get.call_args.kwargs["headers"]["Authorization"] == "Bearer of"


def test_transportfehler_ist_none():
    b = Beleg(nr=1, quelle="http", ziel="http://qdrant:6333/healthz", feld="#status",
              wert="200", gemessen=T)
    with patch("requests.get", side_effect=ConnectionError("weg")):
        assert nf.nachfragen(b) is None


def test_http_status_als_feld():
    b = Beleg(nr=1, quelle="http", ziel="http://qdrant:6333/healthz", feld="#status",
              wert="200", gemessen=T)
    with patch("requests.get", return_value=antwort({}, status=200)):
        assert nf.nachfragen(b) is True
