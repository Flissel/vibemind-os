"""Tests der stdio-HTTP-Bruecke (Attrappen-Dienst als ThreadingHTTPServer, kein echtes Netz).

Muster: spaces/event_management/tests/test_uebergabe.py.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
from collections.abc import Callable, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from spaces.auftragsbuch import bruecke

SCHLUESSEL = "k-1"
BRUECKE = Path(bruecke.__file__).resolve()
ANFRAGE = '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'


class _Attrappe:
    def __init__(self) -> None:
        self.port = 0
        self.status = 200
        self.inhalt_typ = "application/json"
        self.antwort: bytes = b'{"jsonrpc":"2.0","id":1,"result":{"ok":true}}'
        self.kopf: dict[str, str] = {}
        self.koerper: bytes = b""
        self.pfad = ""

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/mcp"


@pytest.fixture
def dienst() -> Iterator[_Attrappe]:
    att = _Attrappe()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: object) -> None:  # still
            pass

        def do_POST(self) -> None:
            att.pfad = self.path
            att.kopf = {k.lower(): v for k, v in self.headers.items()}
            att.koerper = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            self.send_response(att.status)
            if att.antwort:
                self.send_header("Content-Type", att.inhalt_typ)
            self.send_header("Content-Length", str(len(att.antwort)))
            self.end_headers()
            self.wfile.write(att.antwort)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    att.port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield att
    finally:
        server.shutdown()
        server.server_close()


def _senden(url: str) -> Callable[[bytes], tuple[int, bytes]]:
    return bruecke.http_senden(url, SCHLUESSEL)


def _frei() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def test_anfrage_geht_mit_bearer_und_unveraendert_weiter(dienst: _Attrappe) -> None:
    zeile = '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"x","arguments":{"t":"Grüße"}}}'
    antwort = bruecke.weiterreichen(zeile, _senden(dienst.url))
    assert dienst.kopf["authorization"] == f"Bearer {SCHLUESSEL}"
    assert dienst.kopf["content-type"] == "application/json"
    assert "application/json" in dienst.kopf["accept"]
    assert "text/event-stream" in dienst.kopf["accept"]
    assert dienst.koerper == zeile.encode("utf-8")
    assert dienst.pfad == "/mcp"
    assert antwort is not None and "\n" not in antwort
    assert json.loads(antwort) == json.loads(dienst.antwort)


def test_notification_ohne_antwort(dienst: _Attrappe) -> None:
    dienst.status, dienst.antwort = 202, b""
    zeile = '{"jsonrpc":"2.0","method":"notifications/initialized"}'
    assert bruecke.weiterreichen(zeile, _senden(dienst.url)) is None


def test_dienst_aus_ist_fehlerantwort() -> None:
    port = _frei()
    antwort = bruecke.weiterreichen(ANFRAGE, _senden(f"http://127.0.0.1:{port}/mcp"))
    assert antwort is not None
    daten = json.loads(antwort)
    assert daten["id"] == 1 and daten["error"]["code"] == -32000
    assert daten["error"]["message"] == "auftragsbuch nicht erreichbar"
    assert str(port) not in antwort and "127.0.0.1" not in antwort


def test_fehler_bei_notification_bleibt_still() -> None:
    zeile = '{"jsonrpc":"2.0","method":"notifications/initialized"}'
    assert bruecke.weiterreichen(zeile, _senden(f"http://127.0.0.1:{_frei()}/mcp")) is None


def test_401_ist_fehlerantwort_ohne_schluessel(dienst: _Attrappe) -> None:
    dienst.status, dienst.antwort = 401, b'{"fehler":"k-1 abgelehnt"}'
    antwort = bruecke.weiterreichen(ANFRAGE, _senden(dienst.url))
    assert antwort is not None
    daten = json.loads(antwort)
    assert daten["id"] == 1 and daten["error"]["code"] == -32000
    assert daten["error"]["message"] == "auftragsbuch: Schlüssel abgelehnt"
    assert SCHLUESSEL not in antwort


def test_anderer_status_ist_unerwartete_antwort(dienst: _Attrappe) -> None:
    dienst.status, dienst.antwort = 500, b"kaputt"
    daten = json.loads(bruecke.weiterreichen(ANFRAGE, _senden(dienst.url)) or "")
    assert daten["error"]["message"] == "auftragsbuch: unerwartete Antwort (500)"
    assert "kaputt" not in json.dumps(daten)


def test_keine_json_antwort_ist_unerwartete_antwort(dienst: _Attrappe) -> None:
    dienst.antwort = b"<html>nein</html>"
    daten = json.loads(bruecke.weiterreichen(ANFRAGE, _senden(dienst.url)) or "")
    assert daten["id"] == 1
    assert daten["error"]["message"] == "auftragsbuch: unerwartete Antwort (200)"


def test_sse_antwort_wird_gelesen(dienst: _Attrappe) -> None:
    dienst.inhalt_typ = "text/event-stream"
    dienst.antwort = b'event: message\ndata: {"jsonrpc":"2.0","id":1,"result":{"s":"sse"}}\n\n'
    antwort = bruecke.weiterreichen(ANFRAGE, _senden(dienst.url))
    assert antwort is not None
    assert json.loads(antwort) == {"jsonrpc": "2.0", "id": 1, "result": {"s": "sse"}}


def test_ungueltige_zeile_ist_fehlerantwort_ohne_senden() -> None:
    def nie(_k: bytes) -> tuple[int, bytes]:
        raise AssertionError("darf nicht senden")

    daten = json.loads(bruecke.weiterreichen("das ist kein json", nie) or "")
    assert daten["id"] is None and daten["error"]["code"] == -32700


def _lauf(umgebung: dict[str, str], eingabe: str = "") -> subprocess.CompletedProcess[bytes]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("AUFTRAGSBUCH_")}
    env.update(umgebung)
    return subprocess.run([sys.executable, str(BRUECKE)], input=eingabe.encode("utf-8"),
                          capture_output=True, env=env, timeout=60, check=False)


def test_start_ohne_umgebung_nennt_die_variable() -> None:
    lauf = _lauf({"AUFTRAGSBUCH_SCHLUESSEL": SCHLUESSEL})
    assert lauf.returncode == 2
    assert b"AUFTRAGSBUCH_URL" in lauf.stderr
    assert lauf.stdout == b""
    lauf = _lauf({"AUFTRAGSBUCH_URL": "http://127.0.0.1:1/mcp"})
    assert lauf.returncode == 2 and b"AUFTRAGSBUCH_SCHLUESSEL" in lauf.stderr
    assert b"127.0.0.1" not in lauf.stderr


def test_stdout_bleibt_sauber(dienst: _Attrappe) -> None:
    eingabe = (ANFRAGE + "\n\n"
               + '{"jsonrpc":"2.0","method":"notifications/initialized"}\n'
               + '{"jsonrpc":"2.0","id":2,"method":"tools/list"}\n')
    lauf = _lauf({"AUFTRAGSBUCH_URL": dienst.url, "AUFTRAGSBUCH_SCHLUESSEL": SCHLUESSEL}, eingabe)
    assert lauf.returncode == 0
    zeilen = lauf.stdout.decode("utf-8").splitlines()
    assert len(zeilen) == 2  # die Notification bekommt keine Antwortzeile
    assert all(isinstance(json.loads(z), dict) for z in zeilen)
    fehler = lauf.stderr.decode("utf-8")
    assert SCHLUESSEL not in fehler and str(dienst.port) not in fehler and "127.0.0.1" not in fehler
    assert "tools/list" in fehler and "status=200" in fehler
