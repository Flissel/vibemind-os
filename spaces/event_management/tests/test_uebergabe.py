"""Tests fuer die Uebergabe Brain -> Event-Bot (Attrappen-Gateway, kein Netz).

Oeffentliche Grenze: frage()/auftrag() geben bei Erfolg das Ergebnis-Dict zurueck
und werfen bei einem Fehlschlag RuntimeError mit geschwaerztem Text -- sonst
verpackt der DirectExecutor des Brain den Fehlschlag als Erfolg. Die interne
Funktion _uebergeben wirft nie; ihre Ergebnis-Dicts pruefen die Tests direkt.
"""
from __future__ import annotations

import json
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from spaces.event_management import uebergabe

TOKEN = "geheim-123"

BRAIN = Path(__file__).resolve().parents[3] / "brain" / "the_brain"


class _Attrappe:
    def __init__(self) -> None:
        self.modelle = ["openclaw/brain-lesen", "openclaw/brain-auftrag"]
        self.status = 200
        self.body: dict | str = {"choices": [{"message": {"content": "Antwort X"}}]}
        self.posts = 0
        self.gets = 0
        self.letzter_kopf: dict[str, str] = {}
        self.letzter_body: dict = {}


@pytest.fixture
def gateway(monkeypatch, tmp_path):
    att = _Attrappe()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # still
            pass

        def _senden(self, status: int, inhalt) -> None:
            roh = (inhalt if isinstance(inhalt, str) else json.dumps(inhalt)).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(roh)))
            self.end_headers()
            self.wfile.write(roh)

        def do_GET(self):
            att.gets += 1
            if self.path == "/v1/models":
                self._senden(200, {"data": [{"id": m} for m in att.modelle]})
            else:
                self._senden(404, {})

        def do_POST(self):
            att.posts += 1
            laenge = int(self.headers.get("Content-Length", "0"))
            att.letzter_body = json.loads(self.rfile.read(laenge) or b"{}")
            att.letzter_kopf = {k.lower(): v for k, v in self.headers.items()}
            self._senden(att.status, att.body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    for name in ("EVENT_GATEWAY_URL", "EVENT_GATEWAY_TOKEN", "EVENT_GATEWAY_TOKEN_FILE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(uebergabe, "SECRETS_DATEI", str(tmp_path / "kein_secret"))
    monkeypatch.setenv("EVENT_GATEWAY_URL", f"http://127.0.0.1:{server.server_address[1]}/")
    monkeypatch.setenv("EVENT_GATEWAY_TOKEN", TOKEN)
    try:
        yield att
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _intern(value: object, agent: str = uebergabe.AGENT_LESEN) -> dict:
    """Die interne Funktion: wirft nie, liefert immer ein Ergebnis-Dict."""
    return uebergabe._uebergeben(agent, value)


def test_frage_geht_an_brain_lesen(gateway):
    r = uebergabe.frage({"text": "Was steht an?", "_intent": "x"})
    assert r["ok"] is True
    assert r["antwort"] == "Antwort X"
    assert r["agent"] == "brain-lesen"
    assert r["fehler"] is None
    assert r["result_id"].startswith("agent:brain-lesen:vibemind-")
    assert gateway.posts == 1
    assert gateway.letzter_kopf["x-openclaw-session-key"] == r["result_id"]
    assert gateway.letzter_kopf["authorization"] == f"Bearer {TOKEN}"
    assert gateway.letzter_body["model"] == "openclaw/brain-lesen"
    assert gateway.letzter_body["messages"] == [
        {"role": "user", "content": "[VibeMind] Was steht an?"}
    ]
    assert gateway.letzter_body["stream"] is False


def test_auftrag_geht_an_brain_auftrag(gateway):
    r = uebergabe.auftrag({"text": "Lege eine Aufgabe an"})
    assert r["ok"] is True
    assert r["agent"] == "brain-auftrag"
    assert gateway.letzter_kopf["x-openclaw-session-key"].startswith("agent:brain-auftrag:")
    assert gateway.letzter_body["model"] == "openclaw/brain-auftrag"
    assert gateway.letzter_body["messages"][0]["content"] == "[VibeMind] Lege eine Aufgabe an"


def test_ohne_agent_in_models_geht_nichts_raus(gateway):
    gateway.modelle = ["openclaw/brain-lesen"]
    with pytest.raises(RuntimeError, match="brain-auftrag"):
        uebergabe.auftrag({"text": "hallo"})
    r = _intern({"text": "hallo"}, uebergabe.AGENT_AUFTRAG)
    assert r["ok"] is False
    assert "brain-auftrag" in r["fehler"]
    assert r["antwort"] == ""
    assert gateway.posts == 0


def test_gateway_aus(monkeypatch, gateway):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    monkeypatch.setenv("EVENT_GATEWAY_URL", f"http://127.0.0.1:{port}")
    with pytest.raises(RuntimeError, match="Gateway"):
        uebergabe.frage({"text": "hallo"})
    r = _intern({"text": "hallo"})
    assert r["ok"] is False
    assert r["fehler"]
    assert r["antwort"] == ""


def test_502_ist_endgueltig(gateway):
    gateway.status = 502
    gateway.body = {"error": {"message": "bad gateway"}}
    with pytest.raises(RuntimeError, match="502"):
        uebergabe.frage({"text": "hallo"})
    assert gateway.posts == 1


def test_token_nie_in_rueckgabe(gateway):
    gateway.status = 401
    gateway.body = {"error": {"message": f"ungueltig: {TOKEN}"}}
    r = _intern({"text": "hallo"})
    assert r["ok"] is False
    assert TOKEN not in str(r)


def test_fehler_wirft_ohne_token(gateway):
    gateway.status = 502
    gateway.body = {"error": {"message": f"upstream lehnt {TOKEN} ab"}}
    with pytest.raises(RuntimeError, match="502") as fehler:
        uebergabe.auftrag({"text": "hallo"})
    assert TOKEN not in str(fehler.value)
    assert TOKEN not in repr(fehler.value)
    assert "<token>" in str(fehler.value)


def test_direct_executor_meldet_fehlschlag(gateway):
    """Der Brain ruft per DirectExecutor; ein Fehlschlag darf dort nicht ok sein."""
    if str(BRAIN) not in sys.path:
        sys.path.insert(0, str(BRAIN))
    from core.capability_executor import DirectExecutor

    gateway.modelle = ["openclaw/brain-lesen"]  # brain-auftrag fehlt
    executor = DirectExecutor("direct:spaces.event_management.uebergabe:auftrag")
    ergebnis = executor.call_with_arg("Lege eine Aufgabe an", "text")
    assert ergebnis["ok"] is False
    assert "brain-auftrag" in ergebnis["error"]
    assert TOKEN not in json.dumps(ergebnis, default=str)
    assert gateway.posts == 0


def test_leerer_text_wird_abgelehnt(gateway):
    with pytest.raises(RuntimeError, match="kein Text"):
        uebergabe.frage({"text": "  "})
    assert gateway.posts == 0 and gateway.gets == 0


def test_string_statt_dict_gilt_als_text(gateway):
    r = uebergabe.frage("nur ein String")
    assert r["ok"] is True
    assert gateway.letzter_body["messages"][0]["content"] == "[VibeMind] nur ein String"


def test_falscher_typ_ist_fehler(gateway):
    with pytest.raises(RuntimeError, match="kein Text"):
        uebergabe.frage(42)
    assert gateway.posts == 0


def test_leere_antwort(gateway):
    gateway.body = {"choices": [{"message": {"content": ""}}]}
    with pytest.raises(RuntimeError, match="leere Antwort"):
        uebergabe.frage({"text": "hallo"})
    r = _intern({"text": "hallo"})
    assert r["ok"] is False
    assert "leere Antwort" in r["fehler"]


def test_token_aus_datei_hat_vorrang(gateway, monkeypatch, tmp_path):
    datei = tmp_path / "token"
    datei.write_bytes(b"datei-token\r\n")
    monkeypatch.setenv("EVENT_GATEWAY_TOKEN_FILE", str(datei))
    r = uebergabe.frage({"text": "hallo"})
    assert r["ok"] is True
    assert gateway.letzter_kopf["authorization"] == "Bearer datei-token"


def test_fehlende_url_nennt_variable(gateway, monkeypatch):
    monkeypatch.delenv("EVENT_GATEWAY_URL")
    with pytest.raises(RuntimeError, match="EVENT_GATEWAY_URL"):
        uebergabe.frage({"text": "hallo"})
    r = _intern({"text": "hallo"})
    assert r["ok"] is False
    assert "EVENT_GATEWAY_URL" in r["fehler"]
    assert r["result_id"] == ""
    assert gateway.posts == 0 and gateway.gets == 0


def test_fehlender_token_nennt_variable(gateway, monkeypatch):
    monkeypatch.delenv("EVENT_GATEWAY_TOKEN")
    with pytest.raises(RuntimeError, match="EVENT_GATEWAY_TOKEN"):
        uebergabe.frage({"text": "hallo"})
    assert gateway.posts == 0


def test_token_mit_zeilenumbruch_wird_abgelehnt(gateway, monkeypatch, tmp_path):
    datei = tmp_path / "token"
    datei.write_bytes(b"geheim-123\nzweite-zeile-abc\r\n")
    monkeypatch.setenv("EVENT_GATEWAY_TOKEN_FILE", str(datei))
    with pytest.raises(RuntimeError, match="EVENT_GATEWAY_TOKEN") as fehler:
        uebergabe.frage({"text": "hallo"})
    assert "geheim-123" not in str(fehler.value)
    assert "zweite-zeile-abc" not in str(fehler.value)
    r = _intern({"text": "hallo"})
    assert r["ok"] is False
    assert "geheim-123" not in str(r)
    assert "zweite-zeile-abc" not in str(r)
    assert gateway.posts == 0 and gateway.gets == 0


def test_log_enthaelt_status_aber_weder_text_noch_token(gateway, caplog):
    gateway.status = 401
    gateway.body = {"error": {"message": "nein"}}
    with caplog.at_level("INFO", logger=uebergabe.__name__):
        with pytest.raises(RuntimeError):
            uebergabe.frage({"text": "streng-vertraulicher-text"})
    log = " ".join(rec.getMessage() for rec in caplog.records)
    assert "401" in log
    assert "streng-vertraulicher-text" not in log
    assert TOKEN not in log
