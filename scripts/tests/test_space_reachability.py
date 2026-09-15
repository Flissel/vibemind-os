"""Die eine Unterscheidung, auf der das Werkzeug steht: tot gegen falsch.

„Niemand antwortet" ist meist ein Laufzustand — der Dienst ist nicht
gestartet. „Es antwortet jemand, aber der Falsche" ist ein Defekt, und der
gefährlichste der drei Zustände: im Log sieht er wie ein Fehler des
Zielsystems aus. Verwischt das Werkzeug die beiden, ist es wertlos.

Genau dieser Fall steht am 12.09. real im System: alle sieben
mirofish-Capabilities zeigen auf `127.0.0.1:5001`, wo die Brain-API sitzt und
brav 404 zurückgibt.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "space_reachability.py"


def _modul():
    spec = importlib.util.spec_from_file_location("space_reachability", SCRIPT)
    modul = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modul)
    return modul


@pytest.fixture()
def werkzeug(monkeypatch):
    modul = _modul()
    modul._gemessen.clear()
    monkeypatch.delenv("MIROFISH_BASE_URL", raising=False)
    return modul


class _Antwort:
    def __init__(self, code: int, text: str = "") -> None:
        self.status_code = code
        self.text = text


def test_a_foreign_service_answering_404_is_wrong_not_dead(werkzeug, monkeypatch):
    """Der reale Fall: die Brain-API sitzt auf mirofishs Port."""
    monkeypatch.setattr(werkzeug.requests, "post",
                        lambda *a, **k: _Antwort(404, "Not Found"))
    monkeypatch.setattr(werkzeug.requests, "get",
                        lambda *a, **k: _Antwort(200, "<title>Tahlamus Production API</title>"))
    zustand, detail = werkzeug._messen("mirofish")
    assert zustand == werkzeug.FALSCH
    assert "Tahlamus" in detail


def test_nobody_listening_is_dead_not_wrong(werkzeug, monkeypatch):
    """Ein nicht gestarteter Dienst darf nicht als Defekt gemeldet werden -
    sonst steht auf der Liste Arbeit, die keine ist."""
    def _wirft(*args, **kwargs):
        raise ConnectionError("refused")
    monkeypatch.setattr(werkzeug.requests, "post", _wirft)
    zustand, _ = werkzeug._messen("mirofish")
    assert zustand == werkzeug.TOT


def test_a_real_mirofish_answer_is_ok_even_when_it_is_an_error(werkzeug, monkeypatch):
    """400 heisst: der richtige Dienst hat geantwortet und die Probe
    zurueckgewiesen. Erreichbarkeit ist damit belegt."""
    monkeypatch.setattr(werkzeug.requests, "post",
                        lambda *a, **k: _Antwort(400, "missing project_id"))
    zustand, _ = werkzeug._messen("mirofish")
    assert zustand == werkzeug.OK


def test_openfang_targets_share_one_verdict(werkzeug, monkeypatch):
    """openfang, mcp und research haengen alle am selben Daemon. Wer sie
    getrennt misst, zaehlt dieselbe Ursache dreimal als drei Befunde."""
    monkeypatch.setattr(werkzeug, "_port_offen", lambda *a, **k: False)
    for art in ("openfang", "mcp", "research"):
        werkzeug._gemessen.clear()
        zustand, detail = werkzeug._messen(art)
        assert zustand == werkzeug.TOT
        assert "4200" in detail


def test_reachability_is_measured_by_connecting_not_by_the_port_table(werkzeug):
    """Auf dieser Maschine zeigt Windows' Portliste WSL-Dienste im
    gespiegelten Modus NICHT an, obwohl sie ueber den Loopback antworten.
    Das Werkzeug muss deshalb verbinden, nicht nachschlagen."""
    quelle = SCRIPT.read_text(encoding="utf-8")
    assert "socket.socket()" in quelle
    assert "Get-NetTCPConnection" in quelle, "die Falle gehoert dokumentiert"


def test_a_capability_without_a_target_is_its_own_state(werkzeug):
    """`fehlt` ist weder erreichbar noch kaputt - bei den noop-Capabilities
    wurde das Ziel bewusst entfernt (bubble_noop_op, 2026-07-14)."""
    assert werkzeug.FEHLT not in (werkzeug.OK, werkzeug.TOT, werkzeug.FALSCH)
