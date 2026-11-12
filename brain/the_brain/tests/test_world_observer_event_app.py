"""Wahrheitspruefung event_app_change: unabhaengige Nachfrage bei der Event-App.

Die Attrappe ist ein lokaler HTTP-Server, der MCP Streamable HTTP spricht
(SSE oder JSON). Namen und Titel sind erfunden.
"""
import json
import threading
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from zoneinfo import ZoneInfo

import pytest

from core import config as core_config
from core import world_observer as wo

TZ = ZoneInfo("Europe/Berlin")
JETZT = datetime(2030, 1, 15, 12, 0, tzinfo=TZ)
TOKEN = "geheim-token-4711"
NAME = "Anna Test"
SPEC = {"check": "event_app_change", "actor": "VibeMind", "since_s": 600}


def _zeile(minuten_her, actor, quelle="admin", titel="Aufbau planen"):
    t = (JETZT - timedelta(minutes=minuten_her)).strftime("%Y-%m-%d %H:%M")
    return f"- {t} · {actor} ({quelle}): „{titel}“ für {NAME} angelegt"


class _Attrappe:
    def __init__(self, text, sse=True, status=200, rohbody=None, modus=None):
        self.text = text
        self.sse = sse
        self.status = status
        self.rohbody = rohbody
        self.modus = modus
        self.methoden = []
        self.auth = []
        self.pfade = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _antwort(self, code, ctype, data):
                self.send_response(code)
                if ctype:
                    self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_POST(self):
                n = int(self.headers.get("Content-Length", 0))
                req = json.loads(self.rfile.read(n) or b"{}")
                outer.pfade.append(self.path)
                if self.path != "/api/mcp":  # wie die echte App: nur dort MCP
                    return self._antwort(404, None, b"")
                outer.methoden.append(req.get("method"))
                outer.auth.append(self.headers.get("Authorization"))
                if outer.status != 200:
                    return self._antwort(outer.status, None, b"")
                if outer.rohbody is not None:
                    return self._antwort(200, "text/plain", outer.rohbody)
                if outer.modus == "sse_leer":
                    return self._antwort(200, "text/event-stream", b"event: message\n\n")
                if outer.modus == "rpc_error" and req.get("method") == "tools/call":
                    fehler = {"jsonrpc": "2.0", "id": 2, "error": {"code": -32000, "message": "x"}}
                    return self._antwort(200, "application/json", json.dumps(fehler).encode())
                if req.get("method") == "tools/call":
                    res = {"content": [{"type": "text", "text": outer.text}]}
                    if outer.modus == "zweiteilig":
                        a, b = outer.text.split("|")
                        res["content"] = [{"type": "text", "text": a}, {"type": "text", "text": b}]
                    if outer.modus == "iserror":
                        res["isError"] = True
                else:
                    res = {"protocolVersion": "2025-03-26", "capabilities": {}}
                body = json.dumps({"jsonrpc": "2.0", "id": req.get("id"), "result": res})
                if outer.sse:
                    return self._antwort(
                        200, "text/event-stream",
                        ("event: message\ndata: " + body + "\n\n").encode())
                self._antwort(200, "application/json", body.encode())

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.basis = f"http://127.0.0.1:{self.srv.server_address[1]}"
        self.url = f"{self.basis}/api/mcp"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


@pytest.fixture
def attrappe():
    gestartet = []

    def make(*a, **k):
        x = _Attrappe(*a, **k)
        gestartet.append(x)
        return x

    yield make
    for x in gestartet:
        x.close()


@pytest.fixture(autouse=True)
def _umgebung(monkeypatch, tmp_path):
    monkeypatch.setattr(wo, "GROUND_TRUTH_ENABLED", True)
    for n in ("EVENT_APP_URL", "EVENT_APP_TOKEN", "EVENT_APP_TOKEN_FILE"):
        monkeypatch.delenv(n, raising=False)
    # Maschinenwerte (Repo-.env, /run/secrets) duerfen nicht hineinspielen.
    monkeypatch.setattr(core_config, "_load_env_file", lambda *a, **k: None)
    monkeypatch.setattr(core_config, "_SECRETS_DIR", str(tmp_path))
    monkeypatch.setattr(wo, "_event_jetzt", lambda: JETZT)


def _setze(monkeypatch, url):
    monkeypatch.setenv("EVENT_APP_URL", url)
    monkeypatch.setenv("EVENT_APP_TOKEN", TOKEN)


def test_aenderung_von_vibemind_im_fenster_ist_ok(monkeypatch, attrappe):
    a = attrappe(_zeile(0.5, "VibeMind"))
    _setze(monkeypatch, a.url)
    ok, signal, detail = wo._check_event_app_change(SPEC)
    assert ok is True
    assert signal["treffer"] == 1 and signal["zeilen"] == 1
    assert a.methoden == ["initialize", "tools/call"]
    assert a.auth[-1] == TOKEN  # unveraendert, ohne Bearer-Praefix


def test_json_antwort_wird_gelesen(monkeypatch, attrappe):
    a = attrappe(_zeile(1, "VibeMind"), sse=False)
    _setze(monkeypatch, a.url)
    assert wo._check_event_app_change(SPEC)[0] is True


def test_sse_antwort_wird_gelesen(monkeypatch, attrappe):
    a = attrappe("\n".join([_zeile(2, "Anderer"), _zeile(1, "VibeMind")]), sse=True)
    _setze(monkeypatch, a.url)
    ok, signal, _ = wo._check_event_app_change(SPEC)
    assert ok is True and signal["zeilen"] == 2


def test_nur_fremde_oder_alte_aenderungen(monkeypatch, attrappe):
    a = attrappe("\n".join([_zeile(1, "Anderer"), _zeile(60, "VibeMind")]))
    _setze(monkeypatch, a.url)
    ok, signal, detail = wo._check_event_app_change(SPEC)
    assert ok is False
    assert signal["treffer"] == 0 and signal["zeilen"] == 2


def test_fremde_quelle_und_aehnlicher_actor_zaehlen_nicht(monkeypatch, attrappe):
    a = attrappe(_zeile(1, "VibeMind 2"))
    _setze(monkeypatch, a.url)
    assert wo._check_event_app_change(SPEC)[0] is False


def test_zeile_ohne_muster_wird_ignoriert(monkeypatch, attrappe):
    a = attrappe("Keine Aenderungen gefunden\n" + _zeile(1, "VibeMind"))
    _setze(monkeypatch, a.url)
    ok, signal, _ = wo._check_event_app_change(SPEC)
    assert ok is True and signal["zeilen"] == 1


def test_zeitfenster_toleranz_an_den_raendern(monkeypatch, attrappe):
    # 11 min her: ausserhalb 600 s, aber innerhalb 600 s + Toleranz 120 s
    a = attrappe(_zeile(11, "VibeMind"))
    _setze(monkeypatch, a.url)
    assert wo._check_event_app_change(SPEC)[0] is True
    a2 = attrappe(_zeile(13, "VibeMind"))
    _setze(monkeypatch, a2.url)
    assert wo._check_event_app_change(SPEC)[0] is False


def test_401_ist_unbekannt(monkeypatch, attrappe):
    a = attrappe("", status=401)
    _setze(monkeypatch, a.url)
    ok, signal, detail = wo._check_event_app_change(SPEC)
    assert ok is None
    assert signal["status_code"] == 401
    assert TOKEN not in detail and TOKEN not in json.dumps(signal)


def test_netzfehler_ist_unbekannt(monkeypatch, attrappe):
    a = attrappe("")
    url = a.url
    a.close()
    _setze(monkeypatch, url)
    ok, _, detail = wo._check_event_app_change(SPEC)
    assert ok is None and TOKEN not in detail


def test_ohne_umgebung_unbekannt():
    ok, _, detail = wo._check_event_app_change(SPEC)
    assert ok is None
    assert "EVENT_APP_URL" in detail


def test_ohne_token_unbekannt(monkeypatch, attrappe):
    a = attrappe(_zeile(1, "VibeMind"))
    monkeypatch.setenv("EVENT_APP_URL", a.url)
    ok, _, detail = wo._check_event_app_change(SPEC)
    assert ok is None and "EVENT_APP_TOKEN" in detail


def test_token_aus_datei(monkeypatch, attrappe, tmp_path):
    a = attrappe(_zeile(1, "VibeMind"))
    datei = tmp_path / "tok"
    datei.write_text(TOKEN + "\n", encoding="utf-8")
    monkeypatch.setenv("EVENT_APP_URL", a.url)
    monkeypatch.setenv("EVENT_APP_TOKEN_FILE", str(datei))
    assert wo._check_event_app_change(SPEC)[0] is True
    assert a.auth[-1] == TOKEN


def test_unlesbare_antwort_ist_unbekannt(monkeypatch, attrappe):
    a = attrappe("", rohbody=b"kein json")
    _setze(monkeypatch, a.url)
    assert wo._check_event_app_change(SPEC)[0] is None


def test_detail_ohne_personennamen(monkeypatch, attrappe):
    a = attrappe("\n".join([_zeile(1, "VibeMind", titel="Geheimer Titel"),
                            _zeile(2, "Anderer")]))
    _setze(monkeypatch, a.url)
    ok, signal, detail = wo._check_event_app_change(SPEC)
    blob = detail + json.dumps(signal)
    for verboten in (NAME, "Anna", "Geheimer Titel", "Anderer", TOKEN):
        assert verboten not in blob


def test_observe_und_registrierung(monkeypatch, attrappe):
    assert "event_app_change" in wo.available_checks()
    a = attrappe(_zeile(1, "VibeMind"))
    _setze(monkeypatch, a.url)
    assert wo.observe(SPEC).verified_ok is True


def test_quelle_filter(monkeypatch, attrappe):
    a = attrappe(_zeile(1, "VibeMind", quelle="mitglied"))
    _setze(monkeypatch, a.url)
    assert wo._check_event_app_change({**SPEC, "quelle": "bot"})[0] is False
    assert wo._check_event_app_change(SPEC)[0] is True
    b = attrappe(_zeile(1, "VibeMind", quelle="mitglied") + "\n" + _zeile(1, "VibeMind", quelle="bot"))
    _setze(monkeypatch, b.url)
    assert wo._check_event_app_change({**SPEC, "quelle": " bot "})[0] is True
    assert wo._check_event_app_change({**SPEC, "quelle": "bot"})[1]["treffer"] == 1
    assert wo._check_event_app_change(SPEC)[1]["treffer"] == 2


def test_jsonrpc_error_ist_none(monkeypatch, attrappe):
    a = attrappe("", modus="rpc_error")
    _setze(monkeypatch, a.url)
    assert wo._check_event_app_change(SPEC)[0] is None


def test_result_iserror_ist_none(monkeypatch, attrappe):
    a = attrappe(_zeile(1, "VibeMind"), modus="iserror")
    _setze(monkeypatch, a.url)
    assert wo._check_event_app_change(SPEC)[0] is None


def test_sse_ohne_data_ist_none(monkeypatch, attrappe):
    a = attrappe("", modus="sse_leer")
    _setze(monkeypatch, a.url)
    assert wo._check_event_app_change(SPEC)[0] is None


def test_unmoegliches_datum_wird_uebersprungen(monkeypatch, attrappe):
    kaputt = "- 2030-13-45 12:00 · VibeMind (admin): „x“ angelegt"
    a = attrappe(kaputt + "\n" + _zeile(1, "VibeMind"))
    _setze(monkeypatch, a.url)
    ok, signal, _ = wo._check_event_app_change(SPEC)
    assert ok is True and signal["treffer"] == 1


def test_mehrere_content_teile_werden_zeilenweise_gelesen(monkeypatch, attrappe):
    # Ohne Trenner klebte Teil 2 an das Ende von Teil 1: der VibeMind-Eintrag
    # ginge verloren.
    a = attrappe(_zeile(1, "Anderer") + "|" + _zeile(1, "VibeMind"), modus="zweiteilig")
    _setze(monkeypatch, a.url)
    assert wo._check_event_app_change(SPEC)[1]["zeilen"] == 2


def test_name_mit_quelle_faelscht_nicht(monkeypatch, attrappe):
    # Mitgliedsname "VibeMind (bot): x" ergibt: ... VibeMind (bot): x (mitglied): „Titel"
    a = attrappe(_zeile(1, "VibeMind (bot): x", quelle="mitglied"))
    _setze(monkeypatch, a.url)
    ok, signal, _ = wo._check_event_app_change({**SPEC, "quelle": "bot"})
    assert ok is False and signal["treffer"] == 0
    ok, signal, _ = wo._check_event_app_change(SPEC)
    assert ok is False and signal["treffer"] == 0


def test_attrappe_antwortet_nur_auf_api_mcp(monkeypatch, attrappe):
    # Gegenprobe: ohne Pfad-Check waeren die URL-Tests unten wertlos.
    a = attrappe(_zeile(1, "VibeMind"))
    _setze(monkeypatch, a.basis + "/anderswo/api/mcp")
    ok, signal, _ = wo._check_event_app_change(SPEC)
    assert ok is None and signal["status_code"] == 404


@pytest.mark.parametrize("anhang", ["", "/"])
def test_basis_url_wird_ergaenzt(monkeypatch, attrappe, anhang):
    # Im Space ist EVENT_APP_URL die Basis-URL (openclaw.json haengt /api/mcp an).
    a = attrappe(_zeile(1, "VibeMind"))
    _setze(monkeypatch, a.basis + anhang)
    assert wo._check_event_app_change(SPEC)[0] is True
    assert a.pfade == ["/api/mcp", "/api/mcp"]


def test_mcp_url_mit_slash(monkeypatch, attrappe):
    a = attrappe(_zeile(1, "VibeMind"))
    _setze(monkeypatch, a.url + "/")
    assert wo._check_event_app_change(SPEC)[0] is True
    assert a.pfade == ["/api/mcp", "/api/mcp"]


def test_titel_mit_klammer_doppelpunkt(monkeypatch, attrappe):
    a = attrappe(_zeile(1, "VibeMind", quelle="bot", titel="Becher (neu) kaufen"))
    _setze(monkeypatch, a.url)
    ok, signal, _ = wo._check_event_app_change({**SPEC, "quelle": "bot"})
    assert ok is True and signal["treffer"] == 1
