"""Tests fuer deploy/rauchprobe.py gegen einen Attrappen-Dienst (ThreadingHTTPServer, kein echtes Netz).

Die Rauchprobe startet die echte Bruecke als Unterprozess; die Attrappe antwortet wie der Dienst.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from spaces.auftragsbuch import werkzeuge
from spaces.auftragsbuch.vertrag import lade_auftragsarten, pruefe_eingabe

DEPLOY = Path(__file__).resolve().parents[1] / "deploy"
sys.path.insert(0, str(DEPLOY))
import rauchprobe  # noqa: E402

RAUCHPROBE = DEPLOY / "rauchprobe.py"
ARTEN = lade_auftragsarten(Path(__file__).resolve().parents[2] / "_contract")
SCHLUESSEL = "rauch-GEHEIM-7"
VIER = ["auftrag_probe", "auftrag_lesen", "rowboat_vorschlaege_anlegen", "sales_post_pruefen"]


class _Dienst:
    def __init__(self) -> None:
        self.port = 0
        self.werkzeuge = list(VIER)
        self.aufruf_ergebnis: dict = {"content": [{"type": "text", "text": "Unbekannte Auftragsart"}],
                                      "isError": True}
        self.aufrufe: list[dict] = []

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/mcp"


@pytest.fixture
def dienst() -> Iterator[_Dienst]:
    d = _Dienst()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: object) -> None:
            pass

        def _senden(self, status: int, koerper: dict | None) -> None:
            roh = json.dumps(koerper).encode() if koerper is not None else b""
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(roh)))
            self.end_headers()
            self.wfile.write(roh)

        def do_POST(self) -> None:
            nachricht = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
            if self.headers.get("Authorization") != f"Bearer {SCHLUESSEL}":
                self._senden(401, {"fehler": "x"})
                return
            methode = nachricht.get("method")
            if "id" not in nachricht:
                self._senden(202, None)
                return
            if methode == "initialize":
                ergebnis: dict = {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}},
                                  "serverInfo": {"name": "auftragsbuch", "version": "1"}}
            elif methode == "tools/list":
                ergebnis = {"tools": [{"name": n, "inputSchema": {"type": "object"}} for n in d.werkzeuge]}
            elif methode == "tools/call":
                d.aufrufe.append(nachricht["params"])
                ergebnis = d.aufruf_ergebnis
            else:
                self._senden(200, {"jsonrpc": "2.0", "id": nachricht["id"],
                                   "error": {"code": -32601, "message": "unbekannt"}})
                return
            self._senden(200, {"jsonrpc": "2.0", "id": nachricht["id"], "result": ergebnis})

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    d.port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield d
    finally:
        server.shutdown()
        server.server_close()


def _lauf(umgebung: dict[str, str]) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("AUFTRAGSBUCH_")}
    env.update(umgebung)
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run([sys.executable, str(RAUCHPROBE)], capture_output=True, env=env, timeout=180,
                          check=False, encoding="utf-8")


def _frei() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _ohne_geheimes(lauf: subprocess.CompletedProcess[str], dienst_port: int) -> None:
    gesamt = lauf.stdout + lauf.stderr
    assert SCHLUESSEL not in gesamt and "127.0.0.1" not in gesamt and str(dienst_port) not in gesamt


def test_erwartungen_passen_zu_den_echten_vertraegen() -> None:
    assert sorted(rauchprobe.ERWARTETE_WERKZEUGE) == sorted(w["name"] for w in werkzeuge.katalog(ARTEN))
    pruefe_eingabe(ARTEN[("sales", "post_pruefen")], rauchprobe.GUELTIGE_EINGABE)
    assert rauchprobe.GUELTIGE_EINGABE["entwuerfe_anlegen"] is False


def test_alles_gut_drei_mal_ok(dienst: _Dienst) -> None:
    lauf = _lauf({"AUFTRAGSBUCH_URL": dienst.url, "AUFTRAGSBUCH_SCHLUESSEL": SCHLUESSEL})
    assert lauf.returncode == 0, lauf.stdout + lauf.stderr
    assert lauf.stdout.splitlines() == ["ok initialize", "ok tools/list (vier Werkzeuge)",
                                        "ok tools/call sales_post_pruefen (Unbekannte Auftragsart)"]
    assert lauf.stderr == ""
    _ohne_geheimes(lauf, dienst.port)
    assert dienst.aufrufe == [{"name": "sales_post_pruefen",
                               "arguments": {"eingabe": rauchprobe.GUELTIGE_EINGABE}}]


def test_falsche_werkzeugliste_ist_fehler(dienst: _Dienst) -> None:
    dienst.werkzeuge = VIER + ["fremd"]
    lauf = _lauf({"AUFTRAGSBUCH_URL": dienst.url, "AUFTRAGSBUCH_SCHLUESSEL": SCHLUESSEL})
    assert lauf.returncode == 1
    assert "FEHLER tools/list (nicht genau die vier Werkzeuge)" in lauf.stdout.splitlines()
    assert "fremd" not in lauf.stdout


def test_angenommener_auftrag_ist_fehler_ohne_inhalt(dienst: _Dienst) -> None:
    dienst.aufruf_ergebnis = {"content": [{"type": "text", "text": '{"auftrag_id": "GEHEIM-ID"}'}],
                              "isError": False}
    lauf = _lauf({"AUFTRAGSBUCH_URL": dienst.url, "AUFTRAGSBUCH_SCHLUESSEL": SCHLUESSEL})
    assert lauf.returncode == 1
    zeile = lauf.stdout.splitlines()[-1]
    assert zeile.startswith("FEHLER tools/call sales_post_pruefen (Dienst hat angenommen")
    assert "GEHEIM-ID" not in lauf.stdout


def test_falscher_schluessel_nennt_die_feste_meldung_der_bruecke(dienst: _Dienst) -> None:
    lauf = _lauf({"AUFTRAGSBUCH_URL": dienst.url, "AUFTRAGSBUCH_SCHLUESSEL": "falsch-GEHEIM"})
    assert lauf.returncode == 1
    assert lauf.stdout.splitlines()[0] == "FEHLER initialize (auftragsbuch: Schlüssel abgelehnt)"
    assert "falsch-GEHEIM" not in lauf.stdout + lauf.stderr


def test_dienst_aus_ist_fehler_ohne_adresse() -> None:
    port = _frei()
    lauf = _lauf({"AUFTRAGSBUCH_URL": f"http://127.0.0.1:{port}/mcp", "AUFTRAGSBUCH_SCHLUESSEL": SCHLUESSEL})
    assert lauf.returncode == 1
    assert lauf.stdout.splitlines()[0] == "FEHLER initialize (auftragsbuch nicht erreichbar)"
    _ohne_geheimes(lauf, port)


def test_fehlende_umgebung_nennt_nur_den_namen() -> None:
    lauf = _lauf({"AUFTRAGSBUCH_URL": "http://127.0.0.1:1/mcp"})
    assert lauf.returncode == 2
    assert "AUFTRAGSBUCH_SCHLUESSEL fehlt" in lauf.stderr and "127.0.0.1" not in lauf.stderr
    assert lauf.stdout == ""


def test_auswerten_ohne_antworten_ist_fehler() -> None:
    assert all(not ok for _name, ok, _grund in rauchprobe.auswerten({}))
    assert [g for _n, _ok, g in rauchprobe.auswerten({})] == ["keine Antwort"] * 3


def test_unbekannte_fehlermeldung_wird_nicht_durchgereicht() -> None:
    antworten = {1: {"jsonrpc": "2.0", "id": 1, "error": {"code": -32000, "message": "GEHEIMER TEXT"}}}
    name, ok, grund = rauchprobe.auswerten(antworten)[0]
    assert (name, ok, grund) == ("initialize", False, "Fehlerantwort")


class _AbgelehntesBuch:
    """Wie die Datenbank in Stufe 1: brain_arten kennt keine Art von sales."""

    def anlegen(self, an, art, version, eingabe, plan_id, frist_min):
        from spaces.auftragsbuch.db import Abgelehnt

        raise Abgelehnt("Unbekannte Auftragsart")

    def lesen(self, auftrag_id):
        return None

    def probe_erledigen(self, auftrag_id):
        return False


def test_gegen_den_echten_dienst_ueber_die_echte_bruecke() -> None:
    import hashlib
    import time

    import uvicorn

    from spaces.auftragsbuch import mcp_server

    app = mcp_server.app_bauen(_AbgelehntesBuch(), ARTEN, hashlib.sha256(SCHLUESSEL.encode()).hexdigest(), [])
    port = _frei()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="critical",
                                           access_log=False, lifespan="on"))
    faden = threading.Thread(target=server.run, daemon=True)
    faden.start()
    try:
        frist = time.monotonic() + 30
        while not server.started and time.monotonic() < frist:
            time.sleep(0.05)
        assert server.started
        lauf = _lauf({"AUFTRAGSBUCH_URL": f"http://127.0.0.1:{port}/mcp", "AUFTRAGSBUCH_SCHLUESSEL": SCHLUESSEL})
    finally:
        server.should_exit = True
        faden.join(timeout=30)
    assert lauf.returncode == 0, lauf.stdout + lauf.stderr
    assert lauf.stdout.splitlines() == ["ok initialize", "ok tools/list (vier Werkzeuge)",
                                        "ok tools/call sales_post_pruefen (Unbekannte Auftragsart)"]
    _ohne_geheimes(lauf, port)
