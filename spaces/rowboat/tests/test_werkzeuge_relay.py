"""Weiterleitung pc_suchen (deploy/headless/werkzeuge/pc_suche_relay.py) mit Attrappen."""
from __future__ import annotations

import json
import socket
import sys
import urllib.error
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "deploy" / "headless" / "werkzeuge"))

import pc_suche_relay as relay  # noqa: E402
from pc_suche_relay import PC_AUS_MELDUNG, pc_suchen  # noqa: E402

U = "http://100.84.127.84:8149"


class Post:
    def __init__(self, status: int = 200, daten: Any = None, fehler: Exception | None = None) -> None:
        self.status = status
        self.daten = daten if daten is not None else {"ok": True, "treffer": [], "abgeschnitten": False,
                                                      "dauer_ms": 3}
        self.fehler = fehler
        self.aufrufe: list[tuple] = []
        self.kopf: dict[str, str] = {}

    def __call__(self, url, kopf, koerper, connect_timeout, read_timeout):
        self.aufrufe.append((url, kopf, koerper, connect_timeout, read_timeout))
        self.kopf = kopf
        if self.fehler is not None:
            raise self.fehler
        return self.status, self.daten


def test_pc_meldung_wortlaut():
    assert PC_AUS_MELDUNG == "PC ist aus – auf dem PC kann ich gerade nicht suchen."


@pytest.mark.parametrize("fehler", [ConnectionRefusedError(), ConnectionResetError(), OSError("no route")])
def test_pc_aus_bei_verbindungsfehler(fehler):
    assert pc_suchen("x", url=U, schluessel="k", post=Post(fehler=fehler)) == \
        {"ok": False, "pc_aus": True, "meldung": PC_AUS_MELDUNG}


@pytest.mark.parametrize("fehler", [socket.timeout(), TimeoutError(), urllib.error.URLError(reason=socket.timeout())])
def test_pc_aus_bei_timeout(fehler):
    assert pc_suchen("x", url=U, schluessel="k", post=Post(fehler=fehler)) == \
        {"ok": False, "pc_aus": True, "meldung": PC_AUS_MELDUNG}


def test_timeouts_fest():
    post = Post()
    pc_suchen("x", url=U, schluessel="k", post=post)
    url, _, koerper, connect, read = post.aufrufe[0]
    assert url == f"{U}/suchen"
    assert (connect, read) == (3.0, 20.0)
    assert koerper == {"anfrage": "x", "art": "name", "ordner": "", "max_treffer": 20}


def test_url_mit_schraegstrich_am_ende():
    post = Post()
    pc_suchen("x", url=U + "/", schluessel="k", post=post)
    assert post.aufrufe[0][0] == f"{U}/suchen"


def test_schluessel_als_kopf_nie_im_ergebnis():
    post = Post(daten={"ok": True, "treffer": [{"pfad": "C:/a"}], "abgeschnitten": False, "dauer_ms": 1})
    r = pc_suchen("x", url=U, schluessel="GEHEIM", post=post)
    assert post.kopf["X-PC-Suche-Key"] == "GEHEIM"
    assert "GEHEIM" not in json.dumps(r)


def test_schluessel_fehler_ohne_text():
    r = pc_suchen("x", url=U, schluessel="GEHEIM", post=Post(fehler=ValueError("GEHEIM kaputt")))
    assert "GEHEIM" not in json.dumps(r) and r["ok"] is False and r["meldung"].endswith("ValueError")


@pytest.mark.parametrize("schluessel", [None, "", "  \r\n"])
def test_ohne_schluessel_kein_aufruf(schluessel):
    post = Post()
    r = pc_suchen("x", url=U, schluessel=schluessel, post=post)
    assert r["ok"] is False and "PC-Suche ist nicht eingerichtet" in r["meldung"]
    assert post.aufrufe == []


def test_schluessel_gestrippt():
    post = Post()
    pc_suchen("x", url=U, schluessel="k\r\n", post=post)
    assert post.kopf["X-PC-Suche-Key"] == "k"


def test_403_meldung_ohne_details():
    r = pc_suchen("x", url=U, schluessel="k", post=Post(status=403, daten={"x": "y"}))
    assert r == {"ok": False, "meldung": "PC-Suche lehnt ab (Schlüssel oder Absender)."}


def test_andere_fehler_status():
    r = pc_suchen("x", url=U, schluessel="k", post=Post(status=500, daten=None))
    assert r == {"ok": False, "meldung": "PC-Suche antwortet 500."}


def test_max_treffer_und_art_geklemmt():
    post = Post()
    pc_suchen("x", max_treffer=999, url=U, schluessel="k", post=post)
    assert post.aufrufe[-1][2]["max_treffer"] == 50
    pc_suchen("x", max_treffer=0, url=U, schluessel="k", post=post)
    assert post.aufrufe[-1][2]["max_treffer"] == 1
    pc_suchen("x", art="inhalt", url=U, schluessel="k", post=post)
    assert post.aufrufe[-1][2]["art"] == "inhalt"
    anzahl = len(post.aufrufe)
    assert pc_suchen("x", art="rm -rf", url=U, schluessel="k", post=post)["ok"] is False
    assert len(post.aufrufe) == anzahl


@pytest.mark.parametrize("anfrage", ["", "   ", "a" * 201])
def test_anfrage_laenge(anfrage):
    post = Post()
    assert pc_suchen(anfrage, url=U, schluessel="k", post=post)["ok"] is False
    assert post.aufrufe == []


def test_ordner_zu_lang_abgelehnt():
    post = Post()
    assert pc_suchen("x", ordner="a" * 501, url=U, schluessel="k", post=post)["ok"] is False
    assert post.aufrufe == []


def test_erfolg_durchgereicht():
    daten = {"ok": True, "treffer": [{"pfad": "C:/Users/User/Desktop/a.md", "groesse": 3, "geaendert": "t"}],
             "abgeschnitten": True, "dauer_ms": 10000}
    r = pc_suchen("a", url=U, schluessel="k", post=Post(daten=daten))
    assert r["ok"] is True and r["treffer"] == daten["treffer"] and r["abgeschnitten"] is True


def test_dienst_meldung_durchgereicht():
    r = pc_suchen("a", url=U, schluessel="k", post=Post(daten={"ok": False, "meldung": "Ordner verboten"}))
    assert r == {"ok": False, "meldung": "Ordner verboten"}


def test_standard_post_ruft_echten_dienst(tmp_path):
    """Standard-Umsetzung gegen einen lokalen Mini-Server: Kopf, Koerper und Antwort."""
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    gesehen: dict = {}

    class H(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            gesehen["kopf"] = self.headers.get("X-PC-Suche-Key")
            gesehen["koerper"] = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            roh = json.dumps({"ok": True, "treffer": [], "abgeschnitten": False, "dauer_ms": 1}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(roh)))
            self.end_headers()
            self.wfile.write(roh)

        def log_message(self, *a):
            pass

    server = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        r = pc_suchen("abc", url=f"http://127.0.0.1:{server.server_port}", schluessel="k", post=relay.http_post_json)
    finally:
        server.shutdown()
    assert r["ok"] is True
    assert gesehen == {"kopf": "k", "koerper": {"anfrage": "abc", "art": "name", "ordner": "", "max_treffer": 20}}


def test_standard_post_verbindung_abgelehnt_ist_pc_aus():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    r = pc_suchen("abc", url=f"http://127.0.0.1:{port}", schluessel="k", post=relay.http_post_json)
    assert r == {"ok": False, "pc_aus": True, "meldung": PC_AUS_MELDUNG}


# --- Fix-Runde 1: Lese-Zeitlimit heisst "PC antwortet nicht", nicht "PC ist aus" ------------------

def test_keine_antwort_ist_nicht_pc_aus():
    r = pc_suchen("x", url=U, schluessel="k", post=Post(fehler=relay.KeineAntwort()))
    assert r == {"ok": False, "pc_aus": False, "meldung": relay.PC_ANTWORTET_NICHT}
    assert "PC antwortet nicht" in relay.PC_ANTWORTET_NICHT


def test_standard_post_lese_zeitlimit_ist_keine_antwort():
    import threading
    import time
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class Langsam(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            time.sleep(1.5)

        def log_message(self, *a):
            pass

    server = HTTPServer(("127.0.0.1", 0), Langsam)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with pytest.raises(relay.KeineAntwort):
            relay.http_post_json(f"http://127.0.0.1:{server.server_port}/suchen", {}, {}, 3.0, 0.3)
    finally:
        server.shutdown()
