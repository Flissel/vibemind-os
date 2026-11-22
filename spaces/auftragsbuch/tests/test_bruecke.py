"""Tests der stdio-HTTP-Bruecke (Attrappen-Dienst als ThreadingHTTPServer, kein echtes Netz).

Muster: spaces/event_management/tests/test_uebergabe.py.
"""
from __future__ import annotations

import json
import os
import socket
import ssl
import subprocess
import sys
import threading
import urllib.error
import urllib.request
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
        self.location = ""

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
            if att.location:
                self.send_header("Location", att.location)
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


def test_weiterleitung_wird_nicht_gefolgt(dienst: _Attrappe) -> None:
    """Ein 3xx darf den Schluessel nicht an einen anderen Host tragen."""
    ziel_gesehen: list[str] = []

    class Ziel(BaseHTTPRequestHandler):
        def log_message(self, *args: object) -> None:
            pass

        def do_POST(self) -> None:
            ziel_gesehen.append("POST")
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()

        do_GET = do_POST

    ziel = ThreadingHTTPServer(("127.0.0.1", 0), Ziel)
    ziel.daemon_threads = True
    threading.Thread(target=ziel.serve_forever, daemon=True).start()
    try:
        dienst.status = 302
        dienst.antwort = b""
        dienst.location = f"http://127.0.0.1:{ziel.server_address[1]}/x"
        antwort = bruecke.weiterreichen(ANFRAGE, _senden(dienst.url))
    finally:
        ziel.shutdown()
        ziel.server_close()
    assert ziel_gesehen == []
    daten = json.loads(antwort or "")
    assert daten["error"]["message"] == "auftragsbuch: unerwartete Antwort (302)"


def test_proxy_aus_der_umgebung_wird_ignoriert(dienst: _Attrappe, monkeypatch: pytest.MonkeyPatch) -> None:
    tot = f"http://127.0.0.1:{_frei()}"
    for name in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.setenv(name, tot)
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)
    antwort = bruecke.weiterreichen(ANFRAGE, _senden(dienst.url))
    assert json.loads(antwort or "") == json.loads(dienst.antwort)


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

# --- Eigene Zertifikatsliste (AUFTRAGSBUCH_CA_DATEI) -------------------------------------------------

ZERT_TEXT = "auftragsbuch: Zertifikat nicht pruefbar"
GEHEIM = "GEHEIM-host.beispiel.invalid-Detail"


def _ca_bundle() -> Path:
    """Das certifi-Buendel des Test-Interpreters; nur Testeingabe, nichts davon liegt im Repo."""
    certifi = pytest.importorskip("certifi")
    return Path(certifi.where())


def _https_handler(opener: urllib.request.OpenerDirector) -> urllib.request.HTTPSHandler:
    (handler,) = [h for h in opener.handlers if isinstance(h, urllib.request.HTTPSHandler)]
    return handler


def test_ca_datei_baut_eigenen_kontext_ohne_systemspeicher(monkeypatch: pytest.MonkeyPatch) -> None:
    bundle = _ca_bundle()

    def verboten(*args: object, **kwargs: object) -> None:
        raise AssertionError("Systemspeicher darf nicht geladen werden")

    monkeypatch.setattr(ssl, "create_default_context", verboten)
    monkeypatch.setattr(ssl.SSLContext, "load_default_certs", verboten)
    opener = bruecke._opener(str(bundle))
    kontext = _https_handler(opener)._context  # type: ignore[attr-defined]
    assert isinstance(kontext, ssl.SSLContext)
    assert kontext.verify_mode == ssl.CERT_REQUIRED
    assert kontext.check_hostname is True
    stats = kontext.cert_store_stats()
    assert stats["x509_ca"] > 0
    # Nur die Datei: nicht mehr Zertifikate, als die Datei traegt.
    in_datei = bundle.read_text(encoding="ascii", errors="ignore").count("-----BEGIN CERTIFICATE-----")
    assert stats["x509"] <= in_datei
    # Der Opener behaelt: kein Proxy, keine Weiterleitung.
    assert not any(isinstance(h, urllib.request.ProxyHandler) for h in opener.handlers)  # leer: gar nicht erst eingehaengt
    assert any(isinstance(h, bruecke._KeinRedirect) for h in opener.handlers)


def test_http_senden_reicht_die_ca_datei_durch(monkeypatch: pytest.MonkeyPatch) -> None:
    bundle = _ca_bundle()
    gesehen: list[str | None] = []
    echt = bruecke._opener

    def spion(ca_datei: str | None = None) -> urllib.request.OpenerDirector:
        gesehen.append(ca_datei)
        return echt(ca_datei)

    monkeypatch.setattr(bruecke, "_opener", spion)
    bruecke.http_senden("https://beispiel.invalid/mcp", SCHLUESSEL, str(bundle))
    assert gesehen == [str(bundle)]


@pytest.mark.parametrize("ca_datei", [None, ""])
def test_ohne_ca_datei_bleibt_der_opener_wie_bisher(ca_datei: str | None) -> None:
    opener = bruecke._opener(ca_datei) if ca_datei is not None else bruecke._opener()
    assert _https_handler(opener)._context is None  # type: ignore[attr-defined]
    assert any(isinstance(h, bruecke._KeinRedirect) for h in opener.handlers)


def test_ca_datei_fehler_sind_ein_eigener_fehlertyp(tmp_path: Path) -> None:
    leer = tmp_path / "leer.pem"
    leer.write_text("kein zertifikat\n", encoding="ascii")
    for pfad in (tmp_path / "fehlt.pem", tmp_path, leer):
        with pytest.raises(bruecke.CaDateiFehler):
            bruecke._opener(str(pfad))


def _lauf_ca(ca: str) -> subprocess.CompletedProcess[bytes]:
    return _lauf({"AUFTRAGSBUCH_URL": "https://beispiel.invalid/mcp", "AUFTRAGSBUCH_SCHLUESSEL": SCHLUESSEL,
                  "AUFTRAGSBUCH_CA_DATEI": ca}, ANFRAGE + "\n")


def test_start_mit_unbrauchbarer_ca_datei_endet_mit_exit_2(tmp_path: Path) -> None:
    leer = tmp_path / "ohne-zertifikat-GEHEIMNAME.pem"
    leer.write_text("das ist kein zertifikat\n", encoding="ascii")
    for pfad in (tmp_path / "fehlt-GEHEIMNAME.pem", tmp_path, leer):
        lauf = _lauf_ca(str(pfad))
        assert lauf.returncode == 2, pfad
        assert lauf.stderr.decode("utf-8").strip() == "auftragsbuch-bruecke: AUFTRAGSBUCH_CA_DATEI nicht ladbar"
        assert b"GEHEIMNAME" not in lauf.stderr and str(tmp_path).encode() not in lauf.stderr
        assert lauf.stdout == b""


def test_leere_ca_datei_variable_ist_wie_nicht_gesetzt(dienst: _Attrappe) -> None:
    lauf = _lauf({"AUFTRAGSBUCH_URL": dienst.url, "AUFTRAGSBUCH_SCHLUESSEL": SCHLUESSEL,
                  "AUFTRAGSBUCH_CA_DATEI": "  "}, ANFRAGE + "\n")
    assert lauf.returncode == 0
    assert json.loads(lauf.stdout.decode("utf-8")) == json.loads(dienst.antwort)


def test_start_mit_ca_datei_und_http_dienst_laeuft(dienst: _Attrappe) -> None:
    """Die eigene Liste betrifft nur https; ein gueltiges Buendel verhindert den Start nicht."""
    lauf = _lauf({"AUFTRAGSBUCH_URL": dienst.url, "AUFTRAGSBUCH_SCHLUESSEL": SCHLUESSEL,
                  "AUFTRAGSBUCH_CA_DATEI": str(_ca_bundle())}, ANFRAGE + "\n")
    assert lauf.returncode == 0
    assert json.loads(lauf.stdout.decode("utf-8")) == json.loads(dienst.antwort)


def _wirft(fehler: BaseException) -> Callable[[bytes], tuple[int, bytes]]:
    def senden(_k: bytes) -> tuple[int, bytes]:
        raise fehler

    return senden


@pytest.mark.parametrize("fehler", [
    urllib.error.URLError(ssl.SSLCertVerificationError(1, GEHEIM)),
    ssl.SSLCertVerificationError(1, GEHEIM),
], ids=["in-URLError", "bloss"])
def test_zertifikatsfehler_hat_eigenen_text(fehler: BaseException, capsys: pytest.CaptureFixture[str]) -> None:
    antwort = bruecke.weiterreichen(ANFRAGE, _wirft(fehler))
    assert antwort is not None
    daten = json.loads(antwort)
    assert daten["id"] == 1 and daten["error"] == {"code": -32000, "message": ZERT_TEXT}
    ausgabe = capsys.readouterr()
    assert GEHEIM not in antwort and "beispiel" not in antwort
    assert GEHEIM not in ausgabe.out + ausgabe.err and "beispiel" not in ausgabe.err
    assert "tools/list" in ausgabe.err


def test_zertifikatsfehler_bei_notification_bleibt_still() -> None:
    zeile = '{"jsonrpc":"2.0","method":"notifications/initialized"}'
    fehler = urllib.error.URLError(ssl.SSLCertVerificationError(1, GEHEIM))
    assert bruecke.weiterreichen(zeile, _wirft(fehler)) is None


def test_anderer_urlerror_bleibt_nicht_erreichbar() -> None:
    fehler = urllib.error.URLError(ConnectionRefusedError(GEHEIM))
    daten = json.loads(bruecke.weiterreichen(ANFRAGE, _wirft(fehler)) or "")
    assert daten["error"]["message"] == "auftragsbuch nicht erreichbar"
    # Auch ein TLS-Fehler, der kein Pruefungsfehler ist (z. B. Handshake), bleibt "nicht erreichbar".
    daten = json.loads(bruecke.weiterreichen(ANFRAGE, _wirft(urllib.error.URLError(ssl.SSLError(1, GEHEIM)))) or "")
    assert daten["error"]["message"] == "auftragsbuch nicht erreichbar"
