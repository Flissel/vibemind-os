"""Tests fuer den Einbetter (Ollama am PC); ``oeffner`` wird gefaelscht, kein Netz."""
from __future__ import annotations

import http.client
import io
import json
import socket
import sys
import urllib.error
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from wissen_suche.einbetten import (  # noqa: E402
    FRAGE_PRAEFIX,
    EingabeAbgelehnt,
    FakeEinbetter,
    OllamaEinbetter,
    PcNichtErreichbar,
)


class Antwort(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


class Oeffner:
    """Merkt sich die Anfragen und liefert feste Vektoren oder wirft einen Fehler."""

    def __init__(self, vektoren=None, fehler=None):
        self.vektoren = vektoren
        self.fehler = fehler
        self.anfragen: list[dict] = []
        self.urls: list[str] = []

    def __call__(self, req, timeout=None):
        self.urls.append(req.full_url)
        koerper = json.loads(req.data.decode("utf-8"))
        self.anfragen.append(koerper)
        if self.fehler is not None:
            raise self.fehler
        vektoren = self.vektoren
        if vektoren is None:
            vektoren = [[1.0] + [0.0] * 3 for _ in koerper["input"]]
        return Antwort(json.dumps({"embeddings": vektoren}).encode("utf-8"))


def einbetter(oeffner, dim=4):
    return OllamaEinbetter("http://pc:11435", "qwen3-embedding:0.6b", dim, oeffner=oeffner)


def http_fehler(code):
    return urllib.error.HTTPError("http://pc:11435/api/embed", code, "x", {}, io.BytesIO(b"{}"))


def test_sendet_num_ctx_truncate_keep_alive():
    o = Oeffner()
    e = einbetter(o)
    e.dokumente(["a", "b"])
    e.frage("Rente?")
    dok, fr = o.anfragen
    assert o.urls[0] == "http://pc:11435/api/embed"
    assert dok["model"] == "qwen3-embedding:0.6b"
    assert dok["input"] == ["a", "b"]
    assert dok["options"]["num_ctx"] == 2048
    assert dok["truncate"] is True
    assert dok["keep_alive"] == "60s"
    assert fr["keep_alive"] == "5m"
    assert fr["input"] == [FRAGE_PRAEFIX + "Rente?"]
    assert fr["options"]["num_ctx"] == 2048 and fr["truncate"] is True


def test_vektoren_normiert():
    e = einbetter(Oeffner(vektoren=[[3.0, 4.0]]), dim=2)
    assert e.dokumente(["x"]) == [pytest.approx([0.6, 0.8])]
    e = einbetter(Oeffner(vektoren=[[3.0, 4.0]]), dim=2)
    assert e.frage("x") == pytest.approx([0.6, 0.8])


def test_leere_liste_fragt_nicht():
    o = Oeffner()
    assert einbetter(o).dokumente([]) == []
    assert o.anfragen == []


@pytest.mark.parametrize("fehler", [socket.timeout("timed out"), TimeoutError(),
                                    urllib.error.URLError("no route")])
def test_zeitueberschreitung_ist_pc_nicht_erreichbar(fehler):
    with pytest.raises(PcNichtErreichbar):
        einbetter(Oeffner(fehler=fehler)).dokumente(["x"])
    with pytest.raises(PcNichtErreichbar):
        einbetter(Oeffner(fehler=fehler)).frage("x")


@pytest.mark.parametrize("fehler", [ConnectionResetError(), http.client.RemoteDisconnected("weg")])
def test_reset_ist_pc_nicht_erreichbar(fehler):
    with pytest.raises(PcNichtErreichbar):
        einbetter(Oeffner(fehler=fehler)).dokumente(["x"])


@pytest.mark.parametrize("code", [404, 500, 502, 503])
def test_5xx_und_404_sind_pc_nicht_erreichbar(code):
    with pytest.raises(PcNichtErreichbar):
        einbetter(Oeffner(fehler=http_fehler(code))).dokumente(["x"])


def test_400_ist_eingabe_abgelehnt():
    with pytest.raises(EingabeAbgelehnt):
        einbetter(Oeffner(fehler=http_fehler(400))).dokumente(["x"])


def test_falsche_dimension_ist_pc_nicht_erreichbar():
    e = einbetter(Oeffner(vektoren=[[0.1] * 512]), dim=1024)
    with pytest.raises(PcNichtErreichbar):
        e.dokumente(["x"])


def test_falsche_anzahl_vektoren_ist_pc_nicht_erreichbar():
    e = einbetter(Oeffner(vektoren=[[1.0, 0.0, 0.0, 0.0]]))
    with pytest.raises(PcNichtErreichbar):
        e.dokumente(["a", "b"])


def test_kaputtes_json_ist_pc_nicht_erreichbar():
    def oeffner(req, timeout=None):
        return Antwort(b"<html>tailscale</html>")
    with pytest.raises(PcNichtErreichbar):
        einbetter(oeffner).dokumente(["x"])


def test_gesamtzeit_wird_eingehalten():
    import threading
    import time

    halt = threading.Event()

    def haengt(req, timeout=None):
        halt.wait(30)
        return Antwort(b"{}")

    e = OllamaEinbetter("http://pc:1", "m", 4, gesamt_s=0.3, oeffner=haengt)
    start = time.monotonic()
    with pytest.raises(PcNichtErreichbar):
        e.frage("x")
    assert time.monotonic() - start < 1.5
    halt.set()


def _kos(a, b):
    return sum(x * y for x, y in zip(a, b))


def test_fake_findet_synonyme():
    f = FakeEinbetter()
    assert f.dim == 64
    q = f.frage("Altersvorsorge")
    rente = f.dokumente(["Rentenplanung besprochen"])[0]
    hund = f.dokumente(["Hund gekauft"])[0]
    assert _kos(q, rente) > _kos(q, hund)
    assert f.aufrufe == 3


def test_fake_nicht_erreichbar():
    f = FakeEinbetter(erreichbar=False)
    with pytest.raises(PcNichtErreichbar):
        f.frage("x")
    with pytest.raises(PcNichtErreichbar):
        f.dokumente(["x"])


def test_vorgabe_oeffner_gegen_loopback():
    """Der eigene Oeffner (Aufbau-Grenze) spricht echtes HTTP; Loopback-Server statt Ollama."""
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            koerper = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if koerper["input"] == ["boese"]:
                self.send_response(400)
                self.end_headers()
                return
            daten = json.dumps({"embeddings": [[0.0, 2.0] for _ in koerper["input"]]}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(daten)))
            self.end_headers()
            self.wfile.write(daten)

        def log_message(self, *a):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        e = OllamaEinbetter(f"http://127.0.0.1:{server.server_port}", "m", 2)
        assert e.dokumente(["a", "b"]) == [[0.0, 1.0], [0.0, 1.0]]
        with pytest.raises(EingabeAbgelehnt):
            e.dokumente(["boese"])
    finally:
        server.shutdown()
        server.server_close()
    with pytest.raises(PcNichtErreichbar):
        e.frage("x")                               # Port jetzt zu
