"""RowboatRpc gegen einen Falsch-Server auf 127.0.0.1 (kein Netz)."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from spaces.auftragsbuch.rowboat.rpc import RowboatFehler, RowboatRpc

SCHLUESSEL = "geheimer-schluessel-42"


@pytest.fixture
def server():
    gesehen: dict = {"anfragen": []}
    antwort: dict = {"status": 200, "koerper": {"ok": True}, "ort": None}

    class H(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            laenge = int(self.headers.get("Content-Length", 0))
            gesehen["anfragen"].append({
                "pfad": self.path, "host": self.headers.get("Host"),
                "auth": self.headers.get("Authorization"), "typ": self.headers.get("Content-Type"),
                "body": json.loads(self.rfile.read(laenge) or b"null")})
            self.send_response(antwort["status"])
            if antwort["ort"]:
                self.send_header("Location", antwort["ort"])
            self.end_headers()
            if antwort["status"] == 200:
                self.wfile.write(antwort["koerper"] if isinstance(antwort["koerper"], bytes)
                                 else json.dumps(antwort["koerper"]).encode())

        def log_message(self, *_a):
            pass

    s = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    yield s, gesehen, antwort
    s.shutdown()
    s.server_close()


def _rpc(server, tmp_path, timeout=5):
    d = tmp_path / "key"
    d.write_text(SCHLUESSEL + "\n", encoding="utf-8")
    return RowboatRpc(f"http://127.0.0.1:{server.server_port}", d, timeout)


def test_rpc_setzt_host_kopf(server, tmp_path):
    s, gesehen, _ = server
    assert _rpc(s, tmp_path).aufruf("todo:get", {}) == {"ok": True}
    a = gesehen["anfragen"][0]
    assert a["pfad"] == "/rpc/todo:get" and a["host"] == f"127.0.0.1:{s.server_port}"
    assert a["auth"] == f"Bearer {SCHLUESSEL}" and a["typ"] == "application/json" and a["body"] == {}


def test_rpc_401_fester_grund(server, tmp_path):
    s, _, antwort = server
    antwort["status"] = 401
    with pytest.raises(RowboatFehler) as e:
        _rpc(s, tmp_path).aufruf("todo:get", {})
    assert e.value.grund == "Rowboat: Schluessel abgelehnt"


def test_rpc_folgt_keiner_weiterleitung(server, tmp_path):
    s, gesehen, antwort = server
    antwort.update(status=302, ort=f"http://127.0.0.1:{s.server_port}/rpc/anders")
    with pytest.raises(RowboatFehler) as e:
        _rpc(s, tmp_path).aufruf("todo:get", {})
    assert e.value.grund == "Rowboat: unerwartete Antwort"
    assert len(gesehen["anfragen"]) == 1


def test_rpc_kaputte_antwort(server, tmp_path):
    s, _, antwort = server
    antwort["koerper"] = b"kein json{"
    with pytest.raises(RowboatFehler) as e:
        _rpc(s, tmp_path).aufruf("todo:get", {})
    assert e.value.grund == "Rowboat: unerwartete Antwort"


def test_rpc_nicht_erreichbar(server, tmp_path):
    s, _, _ = server
    r = _rpc(s, tmp_path)
    s.shutdown()
    s.server_close()
    with pytest.raises(RowboatFehler) as e:
        r.aufruf("todo:get", {})
    assert e.value.grund == "Rowboat nicht erreichbar"


def test_rpc_schluessel_nie_im_fehler(server, tmp_path):
    s, _, antwort = server
    antwort["status"] = 500
    r = _rpc(s, tmp_path)
    with pytest.raises(RowboatFehler) as e:
        r.aufruf("todo:get", {})
    assert SCHLUESSEL not in str(e.value) and SCHLUESSEL not in repr(e.value) and SCHLUESSEL not in repr(r)
    assert e.value.__cause__ is None


@pytest.mark.parametrize("url", ["http://10.0.0.1:3220", "http://127.0.0.1", "http://127.0.0.1:3220/x",
                                 "https://127.0.0.1:3220", "http://127.0.0.1:99999", ""])
def test_rpc_nur_loopback_ohne_pfad(url, tmp_path):
    d = tmp_path / "k"
    d.write_text("x", encoding="utf-8")
    with pytest.raises(ValueError):
        RowboatRpc(url, d)
