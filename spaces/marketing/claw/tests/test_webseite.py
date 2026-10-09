"""Webseiten-Leser fuer das Branding (Plan 2026-10-07 marke-per-chat, Task 3).

Kein Test geht ins Internet: die Adresssperre wird ueber eine injizierte
Aufloesung geprueft, Inhalte kommen von einem lokalen http.server. 127.0.0.1
ist nur ueber den modulprivaten Haken `_TEST_ERLAUBT` (Fixture `lokal`) freigegeben."""
from __future__ import annotations

import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from spaces.marketing.claw import webseite as ws

OEFFENTLICH = "93.184.216.34"
OEFFENTLICH_2 = "151.101.1.1"


def aufloeser(tabelle: dict[str, list[str]], protokoll: list[str] | None = None):
    """Falsche getaddrinfo: Name -> Adressen, protokolliert jede Anfrage."""
    def aufloesen(host, port, *a, **k):
        if protokoll is not None:
            protokoll.append(host)
        if host not in tabelle:
            raise socket.gaierror(f"unbekannt: {host}")
        out = []
        for ip in tabelle[host]:
            if ":" in ip:
                out.append((socket.AF_INET6, socket.SOCK_STREAM, 6, "", (ip, port or 80, 0, 0)))
            else:
                out.append((socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port or 80)))
        return out
    return aufloesen


def nie_aufloesen(host, port, *a, **k):
    raise AssertionError(f"darf nicht aufgeloest werden: {host}")


# --- Adresssperre ---------------------------------------------------------------

@pytest.mark.parametrize("host", ["127.0.0.1", "10.1.2.3", "192.168.178.1", "100.64.0.5",
                                  "100.127.255.254", "169.254.169.254", "::1", "[::1]",
                                  "::ffff:127.0.0.1", "0.0.0.0", "224.0.0.1", "240.0.0.1",
                                  "172.16.0.1", "fe80::1", "fc00::1",
                                  "2002:7f00:1::1", "64:ff9b::a00:1"])   # 6to4/NAT64 mit eingebetteter IPv4
def test_ip_literale_gesperrt(host):
    assert ws.adresse_erlaubt(host, nie_aufloesen) is False


def test_localhost_gesperrt():
    assert ws.adresse_erlaubt("localhost", aufloeser({"localhost": ["127.0.0.1", "::1"]})) is False


def test_tailnet_name_gesperrt():
    assert ws.adresse_erlaubt("vm.tail1234.ts.net", aufloeser({"vm.tail1234.ts.net": ["100.101.102.103"]})) is False


def test_oeffentliche_aufloesung_erlaubt():
    assert ws.adresse_erlaubt("firma.example", aufloeser({"firma.example": [OEFFENTLICH]})) is True


def test_mehrere_a_records_einer_privat_gesperrt():
    tab = {"firma.example": [OEFFENTLICH, "10.0.0.7", OEFFENTLICH_2]}
    assert ws.adresse_erlaubt("firma.example", aufloeser(tab)) is False


def test_aaaa_record_auf_loopback_gesperrt():
    tab = {"firma.example": [OEFFENTLICH, "::ffff:127.0.0.1"]}
    assert ws.adresse_erlaubt("firma.example", aufloeser(tab)) is False


def test_nicht_aufloesbar_ist_nicht_erlaubt():
    assert ws.adresse_erlaubt("gibtsnicht.example", aufloeser({})) is False


def test_test_erlaubnis_nur_ausdruecklich(monkeypatch):
    tab = {"firma.test": ["127.0.0.1"]}
    assert ws._TEST_ERLAUBT == frozenset()
    assert ws.adresse_erlaubt("firma.test", aufloeser(tab)) is False
    monkeypatch.setattr(ws, "_TEST_ERLAUBT", frozenset({"127.0.0.1"}))
    assert ws.adresse_erlaubt("firma.test", aufloeser(tab)) is True


def test_kein_oeffentlicher_testparameter():
    import inspect
    for f in (ws.lesen, ws.logo_laden, ws.adresse_erlaubt):
        assert not [p for p in inspect.signature(f).parameters if "test" in p]


@pytest.mark.parametrize("url", ["http://127.0.0.1/", "http://localhost/", "http://10.0.0.1/",
                                 "http://192.168.1.1/", "http://100.64.1.1/", "http://169.254.169.254/latest",
                                 "http://[::1]/"])
def test_lesen_gesperrte_ziele_ohne_abruf(url):
    def oeffnen(*a, **k):
        raise AssertionError("gesperrtes Ziel darf nicht geladen werden")
    fund = ws.lesen(url, aufloesen=aufloeser({"localhost": ["127.0.0.1"]}), oeffnen=oeffnen)
    assert fund.seiten == []
    assert len(fund.hinweise) == 1
    assert fund.hinweise[0].startswith(f"Webseite {url} nicht lesbar: ")


def test_ftp_abgelehnt():
    def oeffnen(*a, **k):
        raise AssertionError("ftp darf nicht geladen werden")
    fund = ws.lesen("ftp://firma.example/", aufloesen=nie_aufloesen, oeffnen=oeffnen)
    assert fund.seiten == [] and fund.hinweise[0].startswith("Webseite ftp://firma.example/ nicht lesbar: ")
    assert ws.logo_laden("ftp://firma.example/logo.png", aufloesen=nie_aufloesen) is None


# --- Umleitungen (Review Focus 1) -----------------------------------------------

class FalscherServer:
    """Injiziertes `oeffnen`: Antworten je URL, protokolliert (url, ip)."""

    def __init__(self, antworten: dict[str, tuple[int, dict, bytes]]):
        self.antworten = antworten
        self.aufrufe: list[tuple[str, str]] = []

    def __call__(self, url, ip, grenze, frist):
        self.aufrufe.append((url, ip))
        return self.antworten[url]


def test_umleitung_auf_loopback_gesperrt_inhalt_nicht_geladen():
    srv = FalscherServer({
        "https://firma.example/": (302, {"location": "http://127.0.0.1/admin"}, b""),
        "http://127.0.0.1/admin": (200, {"content-type": "text/html"}, b"<p>GEHEIM</p>"),
    })
    fund = ws.lesen("https://firma.example/", aufloesen=aufloeser({"firma.example": [OEFFENTLICH]}), oeffnen=srv)
    assert srv.aufrufe == [("https://firma.example/", OEFFENTLICH)]
    assert fund.seiten == []
    assert "GEHEIM" not in repr(fund)
    assert fund.hinweise[0].startswith("Webseite https://firma.example/ nicht lesbar: ")


def test_umleitung_auf_namen_mit_privater_aufloesung_gesperrt():
    srv = FalscherServer({
        "https://firma.example/": (301, {"location": "https://intern.example/"}, b""),
        "https://intern.example/": (200, {"content-type": "text/html"}, b"<p>GEHEIM</p>"),
    })
    tab = {"firma.example": [OEFFENTLICH], "intern.example": ["192.168.0.10"]}
    fund = ws.lesen("https://firma.example/", aufloesen=aufloeser(tab), oeffnen=srv)
    assert [u for u, _ in srv.aufrufe] == ["https://firma.example/"]
    assert fund.seiten == [] and "GEHEIM" not in repr(fund)


def test_umleitung_auf_ftp_gesperrt():
    srv = FalscherServer({"https://firma.example/": (302, {"location": "ftp://firma.example/x"}, b"")})
    fund = ws.lesen("https://firma.example/", aufloesen=aufloeser({"firma.example": [OEFFENTLICH]}), oeffnen=srv)
    assert len(srv.aufrufe) == 1 and fund.seiten == []


def test_hoechstens_drei_umleitungen():
    kette = {f"https://firma.example/{i}": (302, {"location": f"/{i + 1}"}, b"") for i in range(4)}
    kette["https://firma.example/4"] = (200, {"content-type": "text/html"}, b"<p>Ende</p>")
    srv = FalscherServer(kette)
    fund = ws.lesen("https://firma.example/0", aufloesen=aufloeser({"firma.example": [OEFFENTLICH]}), oeffnen=srv)
    assert len(srv.aufrufe) == 4          # Start + 3 Umleitungen, die vierte wird nicht verfolgt
    assert fund.seiten == [] and "Umleitungen" in fund.hinweise[0]

    kette3 = {f"https://firma.example/{i}": (302, {"location": f"/{i + 1}"}, b"") for i in range(3)}
    kette3["https://firma.example/3"] = (200, {"content-type": "text/html"}, b"<p>Ende</p>")
    fund = ws.lesen("https://firma.example/0", aufloesen=aufloeser({"firma.example": [OEFFENTLICH]}),
                    oeffnen=FalscherServer(kette3))
    assert [s.url for s in fund.seiten] == ["https://firma.example/3"] and "Ende" in fund.seiten[0].text


def test_umleitung_wird_mit_der_geprueften_ip_geoeffnet():
    srv = FalscherServer({
        "https://firma.example/": (302, {"location": "https://www.firma.example/"}, b""),
        "https://www.firma.example/": (200, {"content-type": "text/html"}, b"<h1>Hallo</h1>"),
    })
    tab = {"firma.example": [OEFFENTLICH], "www.firma.example": [OEFFENTLICH_2]}
    ws.lesen("https://firma.example/", aufloesen=aufloeser(tab), oeffnen=srv)
    assert srv.aufrufe == [("https://firma.example/", OEFFENTLICH), ("https://www.firma.example/", OEFFENTLICH_2)]


def test_lesen_wirft_nie():
    def oeffnen(*a, **k):
        raise RuntimeError("kaputt")
    fund = ws.lesen("https://firma.example/", aufloesen=aufloeser({"firma.example": [OEFFENTLICH]}), oeffnen=oeffnen)
    assert fund.seiten == [] and fund.hinweise[0].startswith("Webseite https://firma.example/ nicht lesbar: ")
    fund = ws.lesen("https://firma.example/", aufloesen=aufloeser({}))
    assert fund.seiten == [] and len(fund.hinweise) == 1


# --- Inhalt ueber einen lokalen Server ------------------------------------------

STARTSEITE = """<!doctype html><html><head>
<meta charset="utf-8"><title>Firma</title>
<meta name="theme-color" content="#0a7cff">
<link rel="stylesheet" href="/haupt.css">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Open+Sans:wght@400&family=Playfair+Display">
<link rel="icon" href="/favicon.png">
<link rel="apple-touch-icon" href="/touch.png">
<meta property="og:image" content="/og.jpg">
<style>.knopf { background: #FF6600; color: rgb(255, 255, 255); font-family: 'Inter', sans-serif; }</style>
<script>var geheim = "SKRIPTTEXT";</script>
</head><body style="color:#333">
<img src="/bilder/logo.png" alt="Firma Logo">
<img src="/bilder/team.jpg" alt="Team">
<h1>Willkommen bei der Firma</h1><h2>Unsere Leistungen</h2>
<p>Wir bauen gute Dinge.</p>
<a href="/leistungen">Leistungen</a>
<a href="http://firma.test:{port}/kontakt#form">Kontakt</a>
<a href="/kontakt">Kontakt doppelt</a>
<a href="http://fremd.test/">Fremd</a>
<a href="mailto:info@firma.test">Mail</a>
<a href="/s1">1</a><a href="/s2">2</a><a href="/s3">3</a><a href="/s4">4</a><a href="/s5">5</a>
</body></html>"""

CSS = """body { color: #ff6600; font-family: "Inter", Arial, sans-serif; }
h1 { font-family: 'Playfair Display', serif; color: #f60; }
.x { border-color: rgb(10, 20, 30); background: #ff6600; }"""


class Handler(BaseHTTPRequestHandler):
    routen: dict = {}
    pfade: list = []

    def log_message(self, *a):
        pass

    def do_GET(self):
        type(self).pfade.append(self.path)
        try:
            eintrag = self.routen.get(self.path.split("?")[0])
            if eintrag is None:
                self.send_response(404)
                self.end_headers()
                return
            if callable(eintrag):
                eintrag(self)
                return
            status, kopf, body = eintrag
            self.send_response(status)
            for k, v in kopf.items():
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (ConnectionError, OSError):
            pass


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        pass


@pytest.fixture
def lokal(monkeypatch):
    """Gibt 127.0.0.1 fuer den lokalen Testserver frei - nur in diesem Test."""
    monkeypatch.setattr(ws, "_TEST_ERLAUBT", frozenset({"127.0.0.1"}))


@pytest.fixture
def server():
    Handler.routen, Handler.pfade = {}, []
    srv = Server(("127.0.0.1", 0), Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield srv
    srv.shutdown()
    srv.server_close()


def html(text: str):
    return (200, {"Content-Type": "text/html; charset=utf-8"}, text.encode("utf-8"))


def seite_bauen(server) -> tuple[str, list[str]]:
    port = server.server_address[1]
    Handler.routen.update({
        "/": html(STARTSEITE.replace("{port}", str(port))),
        "/haupt.css": (200, {"Content-Type": "text/css"}, CSS.encode()),
        "/leistungen": html("<h2>Beratung</h2><p>Leistungstext</p>"),
        "/kontakt": html("<h1>Kontakt</h1><p>Telefon</p>"),
        **{f"/s{i}": html(f"<p>Seite {i}</p>") for i in range(1, 6)},
    })
    protokoll: list[str] = []
    return f"http://firma.test:{port}/", protokoll


def test_inhalt_farben_schriften_logos(server, lokal):
    url, protokoll = seite_bauen(server)
    fund = ws.lesen(url, aufloesen=aufloeser({"firma.test": ["127.0.0.1"]}, protokoll))
    assert fund.hinweise == []
    start = fund.seiten[0]
    assert start.url == url
    assert "Wir bauen gute Dinge." in start.text and "SKRIPTTEXT" not in start.text
    assert start.ueberschriften[:2] == ["Willkommen bei der Firma", "Unsere Leistungen"]
    # Farben: nach Haeufigkeit, #RRGGBB, rgb() umgerechnet
    assert fund.farben[0] == "#FF6600"
    assert {"#0A7CFF", "#FFFFFF", "#333333", "#0A141E"} <= set(fund.farben)
    assert all(len(f) == 7 and f.startswith("#") for f in fund.farben)
    # Schriften aus font-family und Google-Fonts-Link, ohne generische Familien
    assert fund.schriften[0] == "Inter"
    assert {"Playfair Display", "Open Sans", "Arial"} <= set(fund.schriften)
    assert not {"sans-serif", "serif"} & set(fund.schriften)
    # Logo-Kandidaten: absolut, Logo-Bild vorn, hoechstens 6
    assert fund.logos[0] == f"{url}bilder/logo.png"
    assert {f"{url}touch.png", f"{url}og.jpg", f"{url}favicon.png"} <= set(fund.logos)
    assert f"{url}bilder/team.jpg" not in fund.logos
    assert len(fund.logos) <= 6
    # Fremde Domain nie angefasst; Google Fonts nicht geladen
    assert "fremd.test" not in protokoll and "fonts.googleapis.com" not in protokoll


def test_nur_dieselbe_domain_hoechstens_fuenf_unterseiten(server, lokal):
    url, protokoll = seite_bauen(server)
    fund = ws.lesen(url, aufloesen=aufloeser({"firma.test": ["127.0.0.1"]}, protokoll))
    assert len(fund.seiten) == 6               # Startseite + 5
    unterseiten = [s.url for s in fund.seiten[1:]]
    assert unterseiten[:2] == [f"{url}leistungen", f"{url}kontakt"]   # Fragment weg, doppelt nur einmal
    seitenabrufe = [p for p in Handler.pfade if not p.endswith(".css")]
    assert len(seitenabrufe) == 6
    assert "/s5" not in Handler.pfade
    assert "Beratung" in fund.seiten[1].ueberschriften


def test_text_gesamt_auf_20000_gekuerzt(server, lokal):
    port = server.server_address[1]
    lang = "<h2>" + "Titel " * 30 + "</h2><p>" + ("Wort " * 3000) + "</p>"
    Handler.routen.update({"/": html(lang + "".join(f'<a href="/u{i}">u</a>' for i in range(5))),
                           **{f"/u{i}": html(lang) for i in range(5)}})
    fund = ws.lesen(f"http://firma.test:{port}/", aufloesen=aufloeser({"firma.test": ["127.0.0.1"]}))
    gesamt = sum(len(s.text) + sum(len(u) for u in s.ueberschriften) for s in fund.seiten)
    assert 19_000 < gesamt <= 20_000      # Ueberschriften zaehlen mit


def _grosse_antwort(mit_laenge: bool):
    def senden(h: BaseHTTPRequestHandler):
        groesse = 3 * 1024 * 1024
        h.send_response(200)
        h.send_header("Content-Type", "text/html")
        if mit_laenge:
            h.send_header("Content-Length", str(groesse))
        h.end_headers()
        stueck = b"<p>" + b"x" * 65530 + b"</p>"
        try:
            for _ in range(groesse // len(stueck) + 1):
                h.wfile.write(stueck)
        except OSError:
            pass
    return senden


@pytest.mark.parametrize("mit_laenge", [True, False])
def test_ueber_2_mb_abgebrochen(server, lokal, mit_laenge):
    port = server.server_address[1]
    Handler.routen["/"] = _grosse_antwort(mit_laenge)
    fund = ws.lesen(f"http://firma.test:{port}/", aufloesen=aufloeser({"firma.test": ["127.0.0.1"]}))
    assert fund.seiten == [] and "2 MB" in fund.hinweise[0]


def test_grenze_wird_beim_streamen_durchgesetzt(server):
    """Ohne Content-Length: oeffnen bricht nach der Grenze ab statt alles zu lesen."""
    port = server.server_address[1]
    Handler.routen["/"] = _grosse_antwort(False)
    with pytest.raises(ws.LeseFehler):
        ws._oeffnen(f"http://firma.test:{port}/", "127.0.0.1", 100_000, time.monotonic() + 5)


def test_zeitlimit(server, lokal, monkeypatch):
    port = server.server_address[1]

    def tropfen(h):
        h.send_response(200)
        h.send_header("Content-Type", "text/html")
        h.end_headers()
        try:
            for _ in range(40):
                h.wfile.write(b"<p>x</p>")
                h.wfile.flush()
                time.sleep(0.1)
        except OSError:
            pass
    Handler.routen["/"] = tropfen
    monkeypatch.setattr(ws, "ZEITLIMIT_S", 0.5)
    t0 = time.monotonic()
    fund = ws.lesen(f"http://firma.test:{port}/", aufloesen=aufloeser({"firma.test": ["127.0.0.1"]}))
    assert time.monotonic() - t0 < 2.5
    assert fund.seiten == [] and "Zeitlimit" in fund.hinweise[0]


def test_host_kopf_traegt_den_namen_nicht_die_ip(server, lokal):
    port = server.server_address[1]
    gesehen = {}

    def merken(h):
        gesehen["host"] = h.headers.get("Host")
        gesehen["cookie"] = h.headers.get("Cookie")
        body = b"<p>ok</p>"
        h.send_response(200)
        h.send_header("Content-Type", "text/html")
        h.send_header("Set-Cookie", "sitzung=1")
        h.send_header("Content-Length", str(len(body)))
        h.end_headers()
        h.wfile.write(body)
    Handler.routen["/"] = merken
    ws.lesen(f"http://firma.test:{port}/", aufloesen=aufloeser({"firma.test": ["127.0.0.1"]}))
    assert gesehen["host"] == f"firma.test:{port}"
    assert gesehen["cookie"] is None


def test_proxy_umgebung_wird_ignoriert(server, lokal, monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://10.9.9.9:3128")
    monkeypatch.setenv("http_proxy", "http://10.9.9.9:3128")
    port = server.server_address[1]
    Handler.routen["/"] = html("<p>direkt</p>")
    fund = ws.lesen(f"http://firma.test:{port}/", aufloesen=aufloeser({"firma.test": ["127.0.0.1"]}))
    assert fund.seiten and "direkt" in fund.seiten[0].text


# --- Logo laden ------------------------------------------------------------------

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 100


def test_logo_laden_png_jpeg(server, lokal):
    port = server.server_address[1]
    Handler.routen.update({
        "/l.png": (200, {"Content-Type": "image/png"}, PNG),
        "/l.jpg": (200, {"Content-Type": "image/jpeg"}, JPEG),
        "/l.svg": (200, {"Content-Type": "image/svg+xml"}, b"<svg/>"),
        "/gross.png": (200, {"Content-Type": "image/png"}, PNG + b"\x00" * (2 * 1024 * 1024)),
    })
    a = aufloeser({"firma.test": ["127.0.0.1"]})
    basis = f"http://firma.test:{port}"
    assert ws.logo_laden(f"{basis}/l.png", aufloesen=a) == (PNG, "image/png")
    assert ws.logo_laden(f"{basis}/l.jpg", aufloesen=a) == (JPEG, "image/jpeg")
    assert ws.logo_laden(f"{basis}/l.svg", aufloesen=a) is None
    assert ws.logo_laden(f"{basis}/gross.png", aufloesen=a) is None
    assert ws.logo_laden(f"{basis}/fehlt.png", aufloesen=a) is None


def test_logo_laden_gesperrt_ohne_testerlaubnis(server):
    port = server.server_address[1]
    Handler.routen["/l.png"] = (200, {"Content-Type": "image/png"}, PNG)
    a = aufloeser({"firma.test": ["127.0.0.1"]})
    assert ws.logo_laden(f"http://firma.test:{port}/l.png", aufloesen=a) is None
    assert ws.logo_laden(f"http://127.0.0.1:{port}/l.png") is None
    assert Handler.pfade == []


# --- Fix-Runde 1 (Review) --------------------------------------------------------

def _roh_troepfeln(kopf: bytes, tropfen: bytes, anzahl: int, pause: float):
    def senden(h: BaseHTTPRequestHandler):
        try:
            h.wfile.write(kopf)
            for _ in range(anzahl):
                h.wfile.write(tropfen)
                time.sleep(pause)
        except OSError:
            pass
    return senden


def test_kopfzeilen_troepfeln_endet_zur_frist(server, lokal, monkeypatch):
    """Socket-Timeout gilt je recv - ein Server, der alle 0,3 s eine Kopfzeile
    schickt, darf den Leser nicht ueber die Frist hinaus festhalten."""
    port = server.server_address[1]
    Handler.routen["/"] = _roh_troepfeln(b"HTTP/1.1 200 OK\r\n", b"X-T: 1\r\n", 60, 0.3)
    monkeypatch.setattr(ws, "ZEITLIMIT_S", 1.0)
    t0 = time.monotonic()
    fund = ws.lesen(f"http://firma.test:{port}/", aufloesen=aufloeser({"firma.test": ["127.0.0.1"]}))
    assert time.monotonic() - t0 < 2.5
    assert fund.seiten == [] and "Zeitlimit" in fund.hinweise[0]


def test_chunk_groesse_troepfeln_endet_zur_frist(server, lokal, monkeypatch):
    port = server.server_address[1]
    kopf = (b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nTransfer-Encoding: chunked\r\n\r\n"
            b"5\r\nhallo\r\n")
    Handler.routen["/"] = _roh_troepfeln(kopf, b"0", 60, 0.3)      # Chunk-Groessenzeile ohne Ende
    monkeypatch.setattr(ws, "ZEITLIMIT_S", 1.0)
    t0 = time.monotonic()
    fund = ws.lesen(f"http://firma.test:{port}/", aufloesen=aufloeser({"firma.test": ["127.0.0.1"]}))
    assert time.monotonic() - t0 < 2.5
    assert fund.seiten == [] and "Zeitlimit" in fund.hinweise[0]


def test_namensaufloesung_hat_eine_frist(monkeypatch):
    monkeypatch.setattr(ws, "ZEITLIMIT_S", 0.5)
    los = threading.Event()

    def haengt(host, port, *a, **k):
        los.wait(5)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (OEFFENTLICH, port))]
    try:
        t0 = time.monotonic()
        fund = ws.lesen("https://firma.example/", aufloesen=haengt, oeffnen=FalscherServer({}))
        assert time.monotonic() - t0 < 1.5
        assert fund.seiten == [] and "Namensauflösung" in fund.hinweise[0]
        t0 = time.monotonic()
        assert ws.adresse_erlaubt("firma.example", haengt) is False
        assert time.monotonic() - t0 < 1.5
    finally:
        los.set()


def test_gesamtbudget_begrenzt_den_ganzen_aufruf(monkeypatch):
    monkeypatch.setattr(ws, "GESAMT_S", 0.5)
    t0 = time.monotonic()
    fristen = []
    start = ("<link rel=stylesheet href='/a.css'><link rel=stylesheet href='/b.css'>"
             + "".join(f"<a href='/s{i}'>s</a>" for i in range(5))).encode()

    def langsam(url, ip, grenze, frist):
        fristen.append(frist)
        if url == "https://firma.example/":
            return 200, {"content-type": "text/html"}, start
        time.sleep(0.2)
        return 200, {"content-type": "text/html"}, b"<p>x</p>"
    fund = ws.lesen("https://firma.example/", aufloesen=aufloeser({"firma.example": [OEFFENTLICH]}),
                    oeffnen=langsam)
    assert time.monotonic() - t0 < 1.5
    assert all(f <= t0 + 0.5 + 0.05 for f in fristen)          # keine Einzelfrist ueber das Gesamtbudget
    assert len(fristen) < 1 + 2 + 5
    assert fund.seiten and fund.seiten[0].url == "https://firma.example/"
    assert any("Zeitbudget" in h and "übersprungen" in h for h in fund.hinweise)


def test_kaputte_links_werfen_nicht():
    start = (b'<p>Start</p><a href="http://[::1">kaputt</a><a href="http://firma.example:99999/">port</a>'
             b'<link rel=stylesheet href="http://[bad"><img src="http://[x" alt="logo">'
             b'<link rel="icon" href="/icon.png"><a href="/ok">ok</a>')
    srv = FalscherServer({
        "https://firma.example/": (200, {"content-type": "text/html"}, start),
        "https://firma.example/ok": (200, {"content-type": "text/html"}, b"<p>Gut</p>"),
    })
    fund = ws.lesen("https://firma.example/", aufloesen=aufloeser({"firma.example": [OEFFENTLICH]}), oeffnen=srv)
    assert [s.url for s in fund.seiten] == ["https://firma.example/", "https://firma.example/ok"]
    assert fund.logos == ["https://firma.example/icon.png"]
    assert fund.hinweise == []


def test_kaputte_start_url_wird_hinweis():
    fund = ws.lesen("http://[::1", aufloesen=nie_aufloesen)
    assert fund.seiten == [] and fund.hinweise[0].startswith("Webseite http://[::1 nicht lesbar: ")


def test_umleitung_https_auf_http_abgelehnt():
    srv = FalscherServer({
        "https://firma.example/": (301, {"location": "http://firma.example/"}, b""),
        "http://firma.example/": (200, {"content-type": "text/html"}, b"<p>unverschluesselt</p>"),
    })
    fund = ws.lesen("https://firma.example/", aufloesen=aufloeser({"firma.example": [OEFFENTLICH]}), oeffnen=srv)
    assert len(srv.aufrufe) == 1 and fund.seiten == []
    assert "https auf http" in fund.hinweise[0]
    hoch = FalscherServer({
        "http://firma.example/": (301, {"location": "https://firma.example/"}, b""),
        "https://firma.example/": (200, {"content-type": "text/html"}, b"<p>sicher</p>"),
    })
    fund = ws.lesen("http://firma.example/", aufloesen=aufloeser({"firma.example": [OEFFENTLICH]}), oeffnen=hoch)
    assert fund.seiten and "sicher" in fund.seiten[0].text


def test_meta_charset_ohne_kopfangabe():
    body = '<meta charset="iso-8859-1"><p>Grüße aus Köln</p>'.encode("latin-1")
    srv = FalscherServer({"https://firma.example/": (200, {"content-type": "text/html"}, body)})
    fund = ws.lesen("https://firma.example/", aufloesen=aufloeser({"firma.example": [OEFFENTLICH]}), oeffnen=srv)
    assert "Grüße aus Köln" in fund.seiten[0].text


def test_umlautdomain_gilt_als_dieselbe_domain():
    srv = FalscherServer({
        "https://xn--mller-kva.example/": (200, {"content-type": "text/html"},
                                           '<a href="https://müller.example/seite">s</a>'.encode()),
        "https://xn--mller-kva.example/seite": (200, {"content-type": "text/html"}, b"<p>Unterseite</p>"),
    })
    fund = ws.lesen("https://xn--mller-kva.example/",
                    aufloesen=aufloeser({"xn--mller-kva.example": [OEFFENTLICH]}), oeffnen=srv)
    assert [s.url for s in fund.seiten][1:] == ["https://xn--mller-kva.example/seite"]


# --- Schlussrunde (final-review.md T3) -------------------------------------------

def test_t3a_troepfelnder_tls_handshake_endet_zur_frist(lokal, monkeypatch):
    """Der Server schickt den Kopf eines TLS-Datensatzes und dann alle 0,3 s ein Byte: jedes recv
    bekommt Daten, nur die Wache beendet den Handshake."""
    lauscher = socket.socket()
    lauscher.bind(("127.0.0.1", 0))
    lauscher.listen(1)
    port = lauscher.getsockname()[1]
    stopp = threading.Event()

    def bedienen():
        try:
            conn, _ = lauscher.accept()
        except OSError:
            return
        with conn:
            try:
                conn.recv(4096)                                  # ClientHello
                conn.sendall(b"\x16\x03\x03\x40\x00")            # Handshake-Datensatz, 16 KB angekuendigt
                while not stopp.is_set():
                    conn.sendall(b"\x00")
                    time.sleep(0.3)
            except OSError:
                pass
    t = threading.Thread(target=bedienen, daemon=True)
    t.start()
    monkeypatch.setattr(ws, "ZEITLIMIT_S", 1.0)
    try:
        t0 = time.monotonic()
        fund = ws.lesen(f"https://firma.test:{port}/", aufloesen=aufloeser({"firma.test": ["127.0.0.1"]}))
        assert time.monotonic() - t0 < 2.5
        assert fund.seiten == [] and "Zeitlimit" in fund.hinweise[0]
    finally:
        stopp.set()
        lauscher.close()


def test_t3b_zeichensatz_der_beim_dekodieren_scheitert_faellt_auf_utf8_zurueck():
    """idna kann nur strict: ein Codec, der UnicodeError wirft, darf die Seite nicht kosten."""
    body = '<meta charset="idna"><p>Grüße</p>'.encode("utf-8")
    srv = FalscherServer({"https://firma.example/": (200, {"content-type": "text/html"}, body)})
    fund = ws.lesen("https://firma.example/", aufloesen=aufloeser({"firma.example": [OEFFENTLICH]}), oeffnen=srv)
    assert fund.seiten and "Grüße" in fund.seiten[0].text
    assert ws._dekodieren("ä".encode("utf-8"), {"content-type": "text/css; charset=idna"}) == "ä"


def test_t3c_wache_endet_auch_wenn_die_verbindung_nicht_entsteht(monkeypatch):
    wachen = []

    class Wache(ws._Wache):
        def __init__(self, rest):
            super().__init__(rest)
            wachen.append(self)

    def kaputt(*a, **k):
        raise RuntimeError("kaputt")
    monkeypatch.setattr(ws, "_Wache", Wache)
    monkeypatch.setattr(ws, "_FesteHTTP", kaputt)
    with pytest.raises(RuntimeError):
        ws._oeffnen("http://firma.example/", OEFFENTLICH, 100, time.monotonic() + 30)
    assert len(wachen) == 1 and wachen[0]._timer.finished.is_set()


def test_t3a_wache_bewacht_den_tls_socket_schon_waehrend_des_handshakes():
    """Ohne do_handshake_on_connect=False loest wrap_socket den rohen Socket ab, bevor die Wache den
    TLS-Socket kennt: ein shutdown der Wache traefe waehrend des Handshakes ins Leere."""
    gesehen = {}
    lauscher = socket.socket()
    lauscher.bind(("127.0.0.1", 0))
    lauscher.listen(1)
    wache = ws._Wache(30)

    class FalschesTLS:
        def __init__(self, sock):
            self.sock = sock

        def do_handshake(self):
            gesehen["bewacht"] = wache._sock is self

        def close(self):
            self.sock.close()

    class Kontext:
        verify_mode, check_hostname, post_handshake_auth = None, True, None

        def wrap_socket(self, sock, server_hostname=None, do_handshake_on_connect=True):
            gesehen["sofort"] = do_handshake_on_connect
            return FalschesTLS(sock)
    try:
        conn = ws._FesteHTTPS("firma.test", "127.0.0.1", lauscher.getsockname()[1], 5, wache, Kontext())
        conn.connect()
        assert gesehen == {"sofort": False, "bewacht": True}
        conn.sock.close()
    finally:
        wache.beenden()
        lauscher.close()


def test_einzelseite_liest_nur_diese_seite():
    abrufe = []

    def oeffnen(url, ip, grenze, frist):
        abrufe.append(url)
        return 200, {"content-type": "text/html; charset=utf-8"}, (
            b"<html><head><link rel='stylesheet' href='/s.css'></head><body><h1>Team</h1>"
            b"<p>Anna und Ben</p><a href='/kontakt'>Kontakt</a></body></html>")
    fund = ws.einzelseite("https://radhaus.example/team", aufloesen=aufloeser({"radhaus.example": [OEFFENTLICH]}),
                          oeffnen=oeffnen)
    assert abrufe == ["https://radhaus.example/team"]
    assert len(fund.seiten) == 1 and "Anna und Ben" in fund.seiten[0].text
    assert fund.seiten[0].ueberschriften == ["Team"]


def test_einzelseite_gesperrt_wird_hinweis():
    fund = ws.einzelseite("http://127.0.0.1/admin", aufloesen=nie_aufloesen)
    assert fund.seiten == [] and "Adresse gesperrt" in fund.hinweise[0]