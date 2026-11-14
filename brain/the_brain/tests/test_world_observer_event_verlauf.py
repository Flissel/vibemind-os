"""Wahrheitspruefung event_change: zweite Quelle `verlauf` (event-mcp) plus Legacy-Weg.

Die Attrappe fuer event-mcp ist ein lokaler HTTP-Server (MCP Streamable HTTP, JSON). Der
Legacy-Weg nutzt die Attrappe aus test_world_observer_event_app. Alles ist erfunden.
"""
import json
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from core import config as core_config
from core import world_observer as wo
from test_world_observer_event_app import JETZT as JETZT_LEGACY
from test_world_observer_event_app import _Attrappe as _LegacyAttrappe
from test_world_observer_event_app import _zeile

# Derselbe Zeitpunkt wie JETZT_LEGACY (12:00 Berlin im Januar), nur in UTC.
JETZT = JETZT_LEGACY.astimezone(timezone.utc)
TOKEN = "verlauf-token-4711"
AKTOR = "bot:betreiber:VibeMind"
SPEC = {"check": "event_change", "actor_verlauf": AKTOR, "actor_legacy": "VibeMind",
        "since_s": 600}


def _eintrag(minuten_her, actor=AKTOR):
    return {"event": "Testfestival", "firma": "testfirma",
            "zeitpunkt": (JETZT - timedelta(minutes=minuten_her)).isoformat(),
            "actor": actor, "quelle": "bot", "baustein": "aufgaben", "aktion": "anlegen"}


class _Verlauf:
    def __init__(self, eintraege=None, status=200):
        self.eintraege = eintraege or []
        self.status = status
        self.auth = []
        self.aufrufe = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _antwort(self, code, data=b"", ctype=None):
                self.send_response(code)
                if ctype:
                    self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_POST(self):
                n = int(self.headers.get("Content-Length", 0))
                req = json.loads(self.rfile.read(n) or b"{}")
                if self.path != "/mcp":
                    return self._antwort(404)
                outer.auth.append(self.headers.get("Authorization"))
                if outer.status != 200:
                    return self._antwort(outer.status)
                if req.get("method") == "tools/call":
                    outer.aufrufe.append(req["params"])
                    text = json.dumps({"aenderungen": outer.eintraege})
                    res = {"content": [{"type": "text", "text": text}]}
                else:
                    res = {"protocolVersion": "2025-03-26", "capabilities": {}}
                body = json.dumps({"jsonrpc": "2.0", "id": req.get("id"), "result": res})
                self._antwort(200, body.encode(), "application/json")

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.basis = f"http://127.0.0.1:{self.srv.server_address[1]}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


@pytest.fixture
def doubles():
    gestartet = []

    def make(klasse, *a, **k):
        x = klasse(*a, **k)
        gestartet.append(x)
        return x

    yield make
    for x in gestartet:
        x.close()


@pytest.fixture(autouse=True)
def _umgebung(monkeypatch, tmp_path):
    monkeypatch.setattr(wo, "GROUND_TRUTH_ENABLED", True)
    for n in ("EVENT_APP_URL", "EVENT_APP_TOKEN", "EVENT_APP_TOKEN_FILE", "EVENT_MCP_URL",
              "EVENT_VERLAUF_TOKEN", "EVENT_VERLAUF_TOKEN_FILE"):
        monkeypatch.delenv(n, raising=False)
    monkeypatch.setattr(core_config, "_load_env_file", lambda *a, **k: None)
    monkeypatch.setattr(core_config, "_SECRETS_DIR", str(tmp_path))
    monkeypatch.setattr(wo, "_event_jetzt", lambda: JETZT)


def _verlauf_setzen(monkeypatch, v):
    monkeypatch.setenv("EVENT_MCP_URL", v.basis)
    monkeypatch.setenv("EVENT_VERLAUF_TOKEN", TOKEN)


def _legacy_setzen(monkeypatch, a):
    monkeypatch.setenv("EVENT_APP_URL", a.basis)
    monkeypatch.setenv("EVENT_APP_TOKEN", "legacy-token-1")


def test_registriert():
    assert "event_change" in wo.available_checks()
    assert "event_app_change" in wo.available_checks()


def test_verlauf_treffer_true(monkeypatch, doubles):
    v = doubles(_Verlauf, [_eintrag(1)])
    _verlauf_setzen(monkeypatch, v)
    ok, signal, detail = wo._check_event_change(SPEC)
    assert ok is True
    assert signal["verlauf_treffer"] == 1
    assert v.auth[-1] == f"Bearer {TOKEN}"
    assert v.aufrufe[0]["name"] == "aenderungen_seit"
    assert 1 <= v.aufrufe[0]["arguments"]["minuten"] <= 1440


def test_fremder_actor_zaehlt_nicht(monkeypatch, doubles):
    v = doubles(_Verlauf, [_eintrag(1, "bot:betreiber"), _eintrag(1, "bot:event"),
                           _eintrag(1, "VibeMind"), _eintrag(60)])
    _verlauf_setzen(monkeypatch, v)
    # Legacy nicht konfiguriert -> unklar; der Verlauf zaehlt aber keinen Treffer.
    ok, signal, _ = wo._check_event_change(SPEC)
    assert signal["verlauf_treffer"] == 0
    assert ok is None  # Verlauf klar False, Legacy unklar
    a = doubles(_LegacyAttrappe, _zeile(60, "VibeMind"))
    _legacy_setzen(monkeypatch, a)
    assert wo._check_event_change(SPEC)[0] is False  # beide klar False


def test_legacy_weg_greift_wenn_verlauf_leer(monkeypatch, doubles):
    v = doubles(_Verlauf, [])
    _verlauf_setzen(monkeypatch, v)
    a = doubles(_LegacyAttrappe, _zeile(1, "VibeMind"))
    _legacy_setzen(monkeypatch, a)
    ok, signal, _ = wo._check_event_change(SPEC)
    assert ok is True
    assert signal["verlauf_treffer"] == 0 and signal["legacy_treffer"] == 1


def test_beide_unklar_none(monkeypatch, doubles):
    v = doubles(_Verlauf, [], status=401)
    _verlauf_setzen(monkeypatch, v)
    ok, _, detail = wo._check_event_change(SPEC)
    assert ok is None
    assert TOKEN not in detail


def test_verlauf_unklar_legacy_true(monkeypatch, doubles):
    v = doubles(_Verlauf, [], status=500)
    _verlauf_setzen(monkeypatch, v)
    a = doubles(_LegacyAttrappe, _zeile(1, "VibeMind"))
    _legacy_setzen(monkeypatch, a)
    assert wo._check_event_change(SPEC)[0] is True


def test_kein_token_kein_wert_im_detail(monkeypatch):
    monkeypatch.setenv("EVENT_MCP_URL", "http://127.0.0.1:9")
    ok, signal, detail = wo._check_event_change(SPEC)
    assert ok is None
    assert "EVENT_VERLAUF_TOKEN" in detail
    monkeypatch.setenv("EVENT_VERLAUF_TOKEN", TOKEN)
    ok, signal, detail = wo._check_event_change(SPEC)  # Verbindung scheitert
    assert ok is None
    assert TOKEN not in detail and TOKEN not in json.dumps(signal)
    assert "127.0.0.1" not in detail


def test_detail_nennt_keine_inhalte(monkeypatch, doubles):
    v = doubles(_Verlauf, [_eintrag(1)])
    _verlauf_setzen(monkeypatch, v)
    _, signal, detail = wo._check_event_change(SPEC)
    text = detail + json.dumps(signal)
    assert "Testfestival" not in text and "testfirma" not in text and TOKEN not in text


def test_token_aus_datei(monkeypatch, doubles, tmp_path):
    v = doubles(_Verlauf, [_eintrag(1)])
    datei = tmp_path / "tok"
    datei.write_text(TOKEN + "\n")
    monkeypatch.setenv("EVENT_MCP_URL", v.basis + "/mcp")  # auch mit /mcp am Ende
    monkeypatch.setenv("EVENT_VERLAUF_TOKEN_FILE", str(datei))
    assert wo._check_event_change(SPEC)[0] is True
