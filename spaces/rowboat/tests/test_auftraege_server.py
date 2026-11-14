"""MCP-Dienst rowboat-auftraege (deploy/headless/auftraege/server.py) gegen eine Attrappe der Sales-Annahme.

Die Attrappe ist ein stdlib-HTTP-Server auf einem freien Loopback-Port; nie ein echter Sales.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import anyio
import pytest
from starlette.testclient import TestClient

ORDNER = Path(__file__).resolve().parents[1] / "deploy" / "headless" / "auftraege"
sys.path.insert(0, str(ORDNER))

# Eindeutiger Modulname: ein nacktes "server" kollidiert sonst mit anderen Testmodulen.
_spec = importlib.util.spec_from_file_location("auftraege_server", ORDNER / "server.py")
server = importlib.util.module_from_spec(_spec)
sys.modules["auftraege_server"] = server
_spec.loader.exec_module(server)

ZUGANG = "dienst-zugang-0123456789abcdef"
SALES_KEY = "sales-rowboat-fedcba9876543210"
KERN = "Geheime Kernaussage Zitrone"
LADEN = "Laden Felix (fin2gether)"
WERKZEUG = "video_auftrag_aus_konzept"


# --- Attrappe der Sales-Annahme ---------------------------------------------------------------------------

class FalscheAnnahme:
    def __init__(self) -> None:
        self.status = 200
        self.antwort: object = {"auftrag": "1a2b3c4d", "id": "1a2b3c4d-0000-4000-8000-000000000001",
                                "link": "https://sales.example/marketing/werkbank/1a2b3c4d-0000-4000-8000-000000000001"}
        self.verzoegerung = 0.0
        self.anfragen: list[dict] = []
        attrappe = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args) -> None:  # noqa: D401 - still
                pass

            def do_POST(self) -> None:  # noqa: N802
                laenge = int(self.headers.get("Content-Length") or 0)
                attrappe.anfragen.append({"pfad": self.path, "kopf": dict(self.headers.items()),
                                          "rumpf": self.rfile.read(laenge)})
                try:
                    time.sleep(attrappe.verzoegerung)
                    roh = attrappe.antwort if isinstance(attrappe.antwort, bytes) else \
                        json.dumps(attrappe.antwort).encode("utf-8")
                    self.send_response(attrappe.status)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(roh)))
                    self.end_headers()
                    self.wfile.write(roh)
                except OSError:
                    pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()

    def schliessen(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def sales():
    attrappe = FalscheAnnahme()
    yield attrappe
    attrappe.schliessen()


# --- Konzept und Umgebung ---------------------------------------------------------------------------------

def video(**anders) -> dict:
    v = {"thema": "  KI im Alltag  ", "zielgruppe": " Handwerker ", "minuten": 4.0, "hook": "Was wäre, wenn?",
         "kernaussage": f"  {KERN}  ", "abschluss": " Probier es aus. ", "auftrag": None, "unbekannt": "weg",
         "stufen": [{"name": "Einstieg", "farbe": "rot",
                     "szenen": [{"kurz": f"Szene {i}", "bildart": "slide", "x": 1} for i in range(7)]
                     + [{"kurz": "Schluss", "bildart": "clip", "notiz": "laut"}]}]}
    v.update(anders)
    return v


def konzept_html(v: dict | None = None, **oben) -> bytes:
    block = json.dumps({"art": "videokonzept", "bezuege": [], "video": video() if v is None else v, **oben},
                       ensure_ascii=False)
    return (f'<!doctype html><title>Konzept</title><script type="application/json" id="rowboat-konzept">{block}'
            '</script><p>Seite</p>').encode("utf-8")


def stand_von(roh: bytes) -> str:
    return hashlib.sha256(roh).hexdigest()


@pytest.fixture
def ort(tmp_path):
    wissen = tmp_path / "wissen"
    (wissen / "Konzepte").mkdir(parents=True)
    (wissen / "Kanal").mkdir()
    (tmp_path / "zugang").write_text(ZUGANG + "\n", encoding="utf-8")
    (tmp_path / "sales").write_text(SALES_KEY + "\r\n", encoding="utf-8")
    (tmp_path / "log").mkdir()
    return tmp_path


def umgebung(ort: Path, sales_url: str = "http://127.0.0.1:9", **anders) -> dict[str, str]:
    umg = {"WISSEN_ORDNER": str(ort / "wissen"), "AUFTRAEGE_ZUGANG_DATEI": str(ort / "zugang"),
           "SALES_SCHLUESSEL_DATEI": str(ort / "sales"), "AUFTRAEGE_LADEN_ANZEIGE": LADEN,
           "AUFTRAEGE_LOG": str(ort / "log" / "auftraege.log"), "SALES_UI_URL": sales_url}
    umg.update(anders)
    return {k: v for k, v in umg.items() if v is not None}


@pytest.fixture
def aufbau_fuer(ort):
    erzeugt = []

    def bauen(sales_url: str = "http://127.0.0.1:9", zeitlimit: float = server.ZEITLIMIT_S, **anders):
        a = server.bauen(umgebung(ort, sales_url, **anders), zeitlimit=zeitlimit)
        erzeugt.append(a)
        return a

    yield bauen
    for a in erzeugt:
        a.dienst.schliessen()


def ablegen(ort: Path, rel: str, roh: bytes) -> str:
    ziel = ort / "wissen" / rel
    ziel.parent.mkdir(parents=True, exist_ok=True)
    ziel.write_bytes(roh)
    return "knowledge/" + rel


def protokoll(ort: Path) -> str:
    datei = ort / "log" / "auftraege.log"
    return datei.read_text(encoding="utf-8") if datei.exists() else ""


# --- Start --------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("name", ["AUFTRAEGE_ZUGANG_DATEI", "SALES_SCHLUESSEL_DATEI", "AUFTRAEGE_LADEN_ANZEIGE",
                                  "WISSEN_ORDNER", "AUFTRAEGE_LOG"])
def test_start_verweigert_ohne_einstellung(ort, name):
    with pytest.raises(server.StartFehler) as e:
        server.bauen(umgebung(ort, **{name: None}))
    assert name in str(e.value)


@pytest.mark.parametrize("name, datei", [("AUFTRAEGE_ZUGANG_DATEI", "zugang"), ("SALES_SCHLUESSEL_DATEI", "sales")])
def test_start_verweigert_fehlende_oder_leere_schluesseldatei(ort, name, datei):
    (ort / datei).write_text(" \n", encoding="utf-8")
    with pytest.raises(server.StartFehler) as e:
        server.bauen(umgebung(ort))
    assert name in str(e.value)
    (ort / datei).unlink()
    with pytest.raises(server.StartFehler) as e:
        server.bauen(umgebung(ort))
    meldung = str(e.value)
    assert name in meldung and str(ort) not in meldung and ZUGANG not in meldung and SALES_KEY not in meldung


def test_start_verweigert_kaputte_sales_url(ort):
    with pytest.raises(server.StartFehler) as e:
        server.bauen(umgebung(ort, sales_url="ftp://irgendwo"))
    assert "SALES_UI_URL" in str(e.value)


def test_genau_ein_werkzeug_mit_beschreibung_des_ladens(aufbau_fuer):
    werkzeuge = anyio.run(aufbau_fuer().mcp.list_tools)
    assert [t.name for t in werkzeuge] == [WERKZEUG]
    t = werkzeuge[0]
    assert t.description == f"Übergibt ein Videokonzept als Auftrag an die Video-Werkbank des Ladens {LADEN}."
    assert set(t.inputSchema["properties"]) == {"pfad", "stand"}
    assert set(t.inputSchema["required"]) == {"pfad", "stand"}


def test_bindet_nur_loopback_auf_8156(aufbau_fuer):
    s = aufbau_fuer().mcp.settings
    assert (s.host, s.port) == ("127.0.0.1", 8156)
    assert server.PORT_VORGABE == 8156 and server.ZEITLIMIT_S == 20


# --- Zugang am MCP-Endpunkt --------------------------------------------------------------------------------

MCP_KOPF = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
LISTE = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}


def test_health_ohne_schluessel(aufbau_fuer):
    with TestClient(aufbau_fuer().app, base_url="http://127.0.0.1:8156") as c:
        assert c.get("/health").json() == {"status": "ok"}


@pytest.mark.parametrize("kopf", [None, "Bearer falsch", f"Basic {ZUGANG}", f"Bearer {ZUGANG}x", "Bearer", ""])
def test_mcp_ohne_richtigen_schluessel_401(aufbau_fuer, kopf):
    zusatz = {} if kopf is None else {"Authorization": kopf}
    with TestClient(aufbau_fuer().app, base_url="http://127.0.0.1:8156") as c:
        r = c.post("/mcp", json=LISTE, headers={**MCP_KOPF, **zusatz})
        assert r.status_code == 401
        assert ZUGANG not in r.text
        assert c.get("/mcp/irgendwas").status_code == 401


def test_mcp_mit_schluessel_listet_das_werkzeug(aufbau_fuer):
    with TestClient(aufbau_fuer().app, base_url="http://127.0.0.1:8156") as c:
        r = c.post("/mcp", json=LISTE, headers={**MCP_KOPF, "Authorization": f"Bearer {ZUGANG}"})
        assert r.status_code == 200
        assert [t["name"] for t in r.json()["result"]["tools"]] == [WERKZEUG]


def test_aufruf_ueber_http_end_zu_ende(ort, sales, aufbau_fuer):
    roh = konzept_html()
    pfad = ablegen(ort, "Konzepte/KI im Alltag.html", roh)
    aufruf = {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
              "params": {"name": WERKZEUG, "arguments": {"pfad": pfad, "stand": stand_von(roh)}}}
    with TestClient(aufbau_fuer(sales.url).app, base_url="http://127.0.0.1:8156") as c:
        r = c.post("/mcp", json=aufruf, headers={**MCP_KOPF, "Authorization": f"Bearer {ZUGANG}"})
    assert r.status_code == 200
    ergebnis = r.json()["result"]
    assert ergebnis.get("isError") is not True, ergebnis
    assert json.loads(ergebnis["content"][0]["text"])["auftrag"] == "1a2b3c4d"
    assert len(sales.anfragen) == 1


# --- Pfade ---------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("pfad, teil", [
    ("knowledge/Notizen/x.html", "nur Konzepte unter knowledge/Konzepte/"),
    ("knowledge/x.html", "nur Konzepte unter knowledge/Konzepte/"),
    ("Konzepte/x.html", "nur Konzepte unter knowledge/Konzepte/"),
    ("knowledge/konzepte/x.html", "nur Konzepte unter knowledge/Konzepte/"),
    ("knowledge/Kanal/x.html", "knowledge/Kanal/"),
    ("knowledge/kanal./x.html", "knowledge/Kanal/"),
    ("knowledge/Konzepte/../Kanal/x.html", "ungültig"),
    ("knowledge/Konzepte/../../geheim.html", "ungültig"),
    ("knowledge/Konzepte/./x.html", "ungültig"),
    ("knowledge//Konzepte/x.html", "ungültig"),
    ("/knowledge/Konzepte/x.html", "ungültig"),
    ("knowledge\\Konzepte\\x.html", "ungültig"),
    ("C:/knowledge/Konzepte/x.html", "ungültig"),
    ("knowledge/Konzepte/x.html:strom", "ungültig"),
    ("knowledge/Konzepte/x.html\x00.txt", "ungültig"),
    ("knowledge/Konzepte/x.html.", "ungültig"),
    ("knowledge/Konzepte/x.html ", "ungültig"),
    ("knowledge/Konzepte/x\n.html", "ungültig"),
    ("knowledge/Konzepte/x.md", ".html"),
    ("knowledge/Konzepte/x.htm", ".html"),
    ("knowledge/Konzepte/.html", ".html"),
    ("knowledge/Konzepte/fehlt.html", "nicht gefunden"),
    ("", "Pfad"),
])
def test_pfad_wird_abgelehnt_ohne_sales(ort, sales, aufbau_fuer, pfad, teil):
    ablegen(ort, "Kanal/x.html", konzept_html())
    ablegen(ort, "Notizen/x.html", konzept_html())
    ergebnis = aufbau_fuer(sales.url).dienst.uebergeben(pfad, "0" * 64)
    assert set(ergebnis) == {"fehler"} and teil in ergebnis["fehler"], ergebnis
    assert sales.anfragen == []


def test_pfad_kein_text(aufbau_fuer, sales):
    assert "Pfad" in aufbau_fuer(sales.url).dienst.uebergeben(None, "")["fehler"]   # type: ignore[arg-type]
    assert sales.anfragen == []


def test_ordner_statt_datei(ort, sales, aufbau_fuer):
    (ort / "wissen" / "Konzepte" / "ordner.html").mkdir()
    assert "nicht gefunden" in aufbau_fuer(sales.url).dienst.uebergeben("knowledge/Konzepte/ordner.html", "x")["fehler"]
    assert sales.anfragen == []


def _symlink(ziel: Path, link: Path, ordner: bool = False) -> None:
    try:
        os.symlink(ziel, link, target_is_directory=ordner)
    except (OSError, NotImplementedError) as e:
        pytest.skip(f"Symlinks lassen sich hier nicht anlegen: {e}")


def test_symlink_nach_aussen_wird_abgelehnt(ort, sales, aufbau_fuer):
    draussen = ort / "draussen.html"
    draussen.write_bytes(konzept_html())
    _symlink(draussen, ort / "wissen" / "Konzepte" / "raus.html")
    _symlink(ort / "wissen" / "Kanal", ort / "wissen" / "Konzepte" / "kanal-link", ordner=True)
    ablegen(ort, "Kanal/y.html", konzept_html())
    dienst = aufbau_fuer(sales.url).dienst
    for pfad in ("knowledge/Konzepte/raus.html", "knowledge/Konzepte/kanal-link/y.html"):
        assert "ungültig" in dienst.uebergeben(pfad, stand_von(konzept_html()))["fehler"], pfad
    assert sales.anfragen == []


def test_symlink_innerhalb_auf_andere_endung_wird_abgelehnt(ort, sales, aufbau_fuer):
    (ort / "wissen" / "Konzepte" / "x.txt").write_bytes(konzept_html())
    _symlink(ort / "wissen" / "Konzepte" / "x.txt", ort / "wissen" / "Konzepte" / "x.html")
    assert "ungültig" in aufbau_fuer(sales.url).dienst.uebergeben("knowledge/Konzepte/x.html", "x")["fehler"]
    assert sales.anfragen == []


def test_datei_ueber_1_mb(ort, sales, aufbau_fuer):
    roh = konzept_html() + b" " * (1_048_576 + 1 - len(konzept_html()))
    pfad = ablegen(ort, "Konzepte/gross.html", roh)
    ergebnis = aufbau_fuer(sales.url).dienst.uebergeben(pfad, stand_von(roh))
    assert "größer als 1 MB" in ergebnis["fehler"]
    assert sales.anfragen == []
    roh = roh[:-1]
    pfad = ablegen(ort, "Konzepte/genau.html", roh)
    assert "auftrag" in aufbau_fuer(sales.url).dienst.uebergeben(pfad, stand_von(roh))


# --- Stand (Hash der Rueckfrage) ------------------------------------------------------------------------------

STAND_SATZ = "Das Konzept hat sich seit der Rückfrage geändert. Bitte noch einmal übergeben."


@pytest.mark.parametrize("stand", ["", "   ", "0" * 64, "abc", None])
def test_stand_passt_nicht(ort, sales, aufbau_fuer, stand):
    pfad = ablegen(ort, "Konzepte/x.html", konzept_html())
    assert aufbau_fuer(sales.url).dienst.uebergeben(pfad, stand) == {"fehler": STAND_SATZ}
    assert sales.anfragen == []


def test_stand_gross_geschrieben_passt(ort, sales, aufbau_fuer):
    roh = konzept_html()
    pfad = ablegen(ort, "Konzepte/x.html", roh)
    assert "auftrag" in aufbau_fuer(sales.url).dienst.uebergeben(pfad, stand_von(roh).upper())


# --- Pruefung des Datenblocks ------------------------------------------------------------------------------

@pytest.mark.parametrize("roh, teil", [
    (konzept_html(video(minuten=6)), "zwischen 3 und 5 Minuten"),
    (konzept_html(video(stufen=[])), "8 bis 15 Szenen"),
    (konzept_html(video(), art="konzept"), "Das Videokonzept fehlt"),
    (b"<html><p>ohne Block</p></html>", "kein Datenblock"),
    (b'<script type="application/json" id="rowboat-konzept">{kaputt</script>', "kein gültiges JSON"),
])
def test_ungueltiges_konzept_geht_nicht_an_sales(ort, sales, aufbau_fuer, roh, teil):
    pfad = ablegen(ort, "Konzepte/x.html", roh)
    ergebnis = aufbau_fuer(sales.url).dienst.uebergeben(pfad, stand_von(roh))
    assert set(ergebnis) == {"fehler"} and teil in ergebnis["fehler"], ergebnis
    assert sales.anfragen == []


def test_einzelnes_surrogat_geht_nicht_an_sales(ort, sales, aufbau_fuer):
    roh = konzept_html().replace(b"Probier es aus.", b"Probier \\ud800 aus.")
    pfad = ablegen(ort, "Konzepte/x.html", roh)
    ergebnis = aufbau_fuer(sales.url).dienst.uebergeben(pfad, stand_von(roh))
    assert "ungültige Zeichen" in ergebnis["fehler"]
    assert sales.anfragen == []


# --- Uebergabe an Sales -----------------------------------------------------------------------------------

def test_gueltige_datei_genau_ein_post(ort, sales, aufbau_fuer):
    roh = konzept_html(video(firma="fin2gether", auftrag="0badc0de"))
    pfad = ablegen(ort, "Konzepte/Reihe/KI im Alltag.html", roh)
    ergebnis = aufbau_fuer(sales.url).dienst.uebergeben(pfad, stand_von(roh))
    assert ergebnis == sales.antwort
    assert len(sales.anfragen) == 1
    a = sales.anfragen[0]
    assert a["pfad"] == "/api/auftraege/video"
    assert a["kopf"]["Authorization"] == f"Bearer {SALES_KEY}"
    assert a["kopf"]["Content-Type"].startswith("application/json")
    rumpf = json.loads(a["rumpf"].decode("utf-8"))
    assert rumpf == {
        "thema": "KI im Alltag", "minuten": 4, "zielgruppe": "Handwerker",
        "beschreibung": f"{KERN}\n\nProbier es aus.",
        "konzept": {
            "thema": "  KI im Alltag  ", "zielgruppe": " Handwerker ", "minuten": 4, "hook": "Was wäre, wenn?",
            "kernaussage": f"  {KERN}  ", "abschluss": " Probier es aus. ", "auftrag": "0badc0de", "firma": "fin2gether",
            "stufen": [{"name": "Einstieg",
                        "szenen": [{"kurz": f"Szene {i}", "bildart": "slide"} for i in range(7)]
                        + [{"kurz": "Schluss", "bildart": "clip", "notiz": "laut"}]}],
            "pfad": "knowledge/Konzepte/Reihe/KI im Alltag.html", "name": "KI im Alltag",
        },
    }
    assert type(rumpf["minuten"]) is int and type(rumpf["konzept"]["minuten"]) is int
    assert "firma" not in json.loads(sales.anfragen[0]["rumpf"])                     # nur im Konzept


def test_beschreibung_nutzt_python_strip_wie_sales(ort, sales, aufbau_fuer):
    # Sales vergleicht mit kernaussage.strip() + "\n\n" + abschluss.strip() (Python): U+0085 faellt dort weg.
    roh = konzept_html(video(abschluss="Los\x85"))
    pfad = ablegen(ort, "Konzepte/x.html", roh)
    aufbau_fuer(sales.url).dienst.uebergeben(pfad, stand_von(roh))
    assert json.loads(sales.anfragen[0]["rumpf"])["beschreibung"] == f"{KERN}\n\nLos"


@pytest.mark.parametrize("status", [400, 409, 413, 429])
def test_sales_lehnt_ab_satz_von_sales(ort, sales, aufbau_fuer, status):
    sales.status, sales.antwort = status, {"fehler": f"Satz von Sales {status}."}
    roh = konzept_html()
    pfad = ablegen(ort, "Konzepte/x.html", roh)
    assert aufbau_fuer(sales.url).dienst.uebergeben(pfad, stand_von(roh)) == {"fehler": f"Satz von Sales {status}."}
    assert len(sales.anfragen) == 1
    assert f"sales {status}" in protokoll(ort)


def test_sales_401(ort, sales, aufbau_fuer):
    sales.status, sales.antwort = 401, {"fehler": "Nicht berechtigt."}
    roh = konzept_html()
    pfad = ablegen(ort, "Konzepte/x.html", roh)
    satz = aufbau_fuer(sales.url).dienst.uebergeben(pfad, stand_von(roh))["fehler"]
    assert "Schlüssel" in satz and "Es wurde nichts übergeben." in satz and SALES_KEY not in satz


UNKLAR_ZEIT = ("Die Annahme hat nicht rechtzeitig geantwortet. Ob der Auftrag angelegt wurde, ist unklar – bitte auf der "
               "Werkbank-Seite nachsehen, bevor du es noch einmal versuchst.")
NICHT_ERREICHBAR = "Die Annahme ist gerade nicht erreichbar. Es wurde nichts übergeben."


@pytest.mark.parametrize("status, antwort", [(500, {"fehler": "x"}), (503, b"kaputt"), (200, b"kein json"),
                                             (200, {"auftrag": "zu-lang-123", "id": "x", "link": "y"}), (302, b"")])
def test_unklare_antwort_ist_unklar(ort, sales, aufbau_fuer, status, antwort):
    sales.status, sales.antwort = status, antwort
    roh = konzept_html()
    pfad = ablegen(ort, "Konzepte/x.html", roh)
    satz = aufbau_fuer(sales.url).dienst.uebergeben(pfad, stand_von(roh))["fehler"]
    assert "ist unklar" in satz and "Werkbank-Seite" in satz
    assert len(sales.anfragen) == 1
    assert "unklar" in protokoll(ort)


def test_zeitlimit_ohne_wiederholen(ort, sales, aufbau_fuer):
    sales.verzoegerung = 1.5
    roh = konzept_html()
    pfad = ablegen(ort, "Konzepte/x.html", roh)
    start = time.monotonic()
    ergebnis = aufbau_fuer(sales.url, zeitlimit=0.4).dienst.uebergeben(pfad, stand_von(roh))
    assert time.monotonic() - start < 1.4
    assert ergebnis == {"fehler": UNKLAR_ZEIT}
    time.sleep(1.3)
    assert len(sales.anfragen) == 1                     # kein zweiter Versuch


def test_keine_verbindung(ort, aufbau_fuer):
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    roh = konzept_html()
    pfad = ablegen(ort, "Konzepte/x.html", roh)
    ergebnis = aufbau_fuer(f"http://127.0.0.1:{port}").dienst.uebergeben(pfad, stand_von(roh))
    assert ergebnis == {"fehler": NICHT_ERREICHBAR}
    assert "nicht_erreichbar" in protokoll(ort)


def test_verbindung_bricht_nach_dem_senden_ab(ort, aufbau_fuer):
    # Server nimmt an, liest die Anfrage und schliesst ohne Antwort: Sales koennte angelegt haben -> unklar.
    lauscher = socket.socket()
    lauscher.bind(("127.0.0.1", 0))
    lauscher.listen(1)

    def annehmen() -> None:
        verbindung, _ = lauscher.accept()
        verbindung.recv(65536)
        verbindung.close()

    threading.Thread(target=annehmen, daemon=True).start()
    roh = konzept_html()
    pfad = ablegen(ort, "Konzepte/x.html", roh)
    ergebnis = aufbau_fuer(f"http://127.0.0.1:{lauscher.getsockname()[1]}").dienst.uebergeben(pfad, stand_von(roh))
    lauscher.close()
    assert "ist unklar" in ergebnis["fehler"]


# --- Protokoll ------------------------------------------------------------------------------------------------

def test_protokoll_ohne_schluessel_und_inhalt(ort, sales, aufbau_fuer):
    roh = konzept_html()
    pfad = ablegen(ort, "Konzepte/KI im Alltag.html", roh)
    dienst = aufbau_fuer(sales.url).dienst
    dienst.uebergeben(pfad, stand_von(roh))
    dienst.uebergeben(pfad, "falsch")
    dienst.uebergeben("knowledge/Kanal/x.html\nGEFAELSCHT", "")
    zeilen = protokoll(ort).splitlines()
    assert len(zeilen) == 3
    assert "knowledge/Konzepte/KI im Alltag.html" in zeilen[0] and "auftrag 1a2b3c4d" in zeilen[0]
    assert "fehler stand" in zeilen[1]
    assert "fehler pfad" in zeilen[2] and "GEFAELSCHT" in zeilen[2]          # eine Zeile, Umbruch maskiert
    text = protokoll(ort)
    for geheim in (ZUGANG, SALES_KEY, KERN, "Probier es aus", "Szene 3"):
        assert geheim not in text, geheim


def test_ergebnisse_nennen_nie_die_schluessel(ort, sales, aufbau_fuer):
    roh = konzept_html()
    pfad = ablegen(ort, "Konzepte/x.html", roh)
    dienst = aufbau_fuer(sales.url).dienst
    for status in (200, 400, 401, 500):
        sales.status = status
        text = json.dumps(dienst.uebergeben(pfad, stand_von(roh)))
        assert ZUGANG not in text and SALES_KEY not in text


def test_unerwarteter_fehler_ohne_text_und_unklar(ort, sales, aufbau_fuer):
    roh = konzept_html()
    pfad = ablegen(ort, "Konzepte/x.html", roh)
    aufbau = aufbau_fuer(sales.url)

    def kaputt(*_args):
        raise RuntimeError(SALES_KEY)

    aufbau.dienst.senden = kaputt
    ergebnis = aufbau.dienst.uebergeben(pfad, stand_von(roh))
    assert "ist unklar" in ergebnis["fehler"] and SALES_KEY not in json.dumps(ergebnis)
    assert "fehler intern RuntimeError" in protokoll(ort) and SALES_KEY not in protokoll(ort)


def test_aufruf_ueber_mcp_call_tool(ort, sales, aufbau_fuer):
    roh = konzept_html()
    pfad = ablegen(ort, "Konzepte/x.html", roh)
    mcp = aufbau_fuer(sales.url).mcp
    ergebnis = anyio.run(lambda: mcp.call_tool(WERKZEUG, {"pfad": pfad, "stand": stand_von(roh)}))
    assert "1a2b3c4d" in json.dumps(ergebnis, default=str)


# Gegenprobe Kanal-Freigabeliste: tests/test_zentrale_skill.py::test_auftraege_werkzeug_in_keiner_kanal_freigabeliste
