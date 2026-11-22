"""Tests fuer deploy/openfang_aufruf.py gegen eine Attrappe von OpenFang (ThreadingHTTPServer, kein echtes Netz).

Muster: tests/test_bruecke.py. Die Attrappe ist nur eine Annahme ueber OpenFangs Antworten; die echte API
ist hier nicht geprueft.
"""
from __future__ import annotations

import io
import json
import subprocess
import sys
import threading
from collections.abc import Callable, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

DEPLOY = Path(__file__).resolve().parents[1] / "deploy"
sys.path.insert(0, str(DEPLOY))
import openfang_aufruf  # noqa: E402

SCHLUESSEL = "GEHEIM-of-schluessel_42"
LESEN = "mcp_auftragsbuch_auftrag_lesen"
PROBE = "mcp_auftragsbuch_auftrag_probe"
POST_PRUEFEN = "mcp_auftragsbuch_sales_post_pruefen"
KENNUNG = "kennung-123"

Antwort = tuple[int, object]


def _detail(name: str, werkzeuge: list[str], server: list[str], modus: str) -> dict[str, object]:
    return {"id": name, "name": name, "capabilities": {"network": [], "tools": werkzeuge},
            "mcp_servers": server, "mcp_servers_mode": modus}


class _Attrappe:
    def __init__(self) -> None:
        self.port = 0
        self.anfragen: list[dict[str, object]] = []
        # Wie die Live-Liste (22.11. gemessen): nur Kopfdaten, keine Werkzeuge.
        self.agenten: object = [{"id": KENNUNG, "name": "brain-sales", "state": "Running"},
                                {"id": "x2", "name": "anderer", "state": "Running"}]
        # GET /api/agents/<id>: (Status, Objekt); wie live mit capabilities, mcp_servers, mcp_servers_mode.
        self.details: dict[str, Antwort] = {
            KENNUNG: (200, _detail("brain-sales", [PROBE, LESEN], ["auftragsbuch"], "allowlist")),
            "x2": (200, _detail("anderer", ["file_read"], [], "allowlist")),
        }
        self.agenten_status = 200
        self.mcp: Callable[[dict[str, object]], Antwort] = lambda _k: (200, {"jsonrpc": "2.0", "id": 1, "error": {
            "code": -32001, "message": "tool not allowed"}})
        self.loeschen_status = 200
        self.agenten_ort = ""  # nicht leer: GET /api/agents antwortet 302 dorthin

    @property
    def umgebung(self) -> dict[str, str]:
        return {"OPENFANG_URL": f"http://127.0.0.1:{self.port}", "OPENFANG_API_KEY": SCHLUESSEL}

    def pfade(self) -> list[str]:
        return [f"{a['methode']} {a['pfad']}" for a in self.anfragen]


@pytest.fixture
def openfang() -> Iterator[_Attrappe]:
    att = _Attrappe()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: object) -> None:
            pass

        def _antworten(self, status: int, koerper: object, ort: str = "") -> None:
            roh = b"" if koerper is None else json.dumps(koerper).encode("utf-8")
            self.send_response(status)
            if ort:
                self.send_header("Location", ort)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(roh)))
            self.end_headers()
            self.wfile.write(roh)

        def _aufnehmen(self) -> dict[str, object]:
            laenge = int(self.headers.get("Content-Length", "0"))
            roh = self.rfile.read(laenge) if laenge else b""
            eintrag: dict[str, object] = {
                "methode": self.command, "pfad": self.path,
                "kopf": {k.lower(): v for k, v in self.headers.items()},
                "koerper": json.loads(roh) if roh else None}
            att.anfragen.append(eintrag)
            return eintrag

        def do_GET(self) -> None:
            self._aufnehmen()
            if self.path == "/api/agents":
                if att.agenten_ort:
                    self._antworten(302, None, att.agenten_ort)
                else:
                    self._antworten(att.agenten_status, att.agenten)
            elif self.path.startswith("/api/agents/") and self.path[len("/api/agents/"):] in att.details:
                status, koerper = att.details[self.path[len("/api/agents/"):]]
                self._antworten(status, koerper)
            else:
                self._antworten(200, {"ziel": "erreicht"})

        def do_POST(self) -> None:
            eintrag = self._aufnehmen()
            if self.path == "/mcp":
                status, koerper = att.mcp(eintrag)
                self._antworten(status, koerper)
            else:
                self._antworten(404, None)

        def do_DELETE(self) -> None:
            self._aufnehmen()
            self._antworten(att.loeschen_status, None)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    att.port = server.server_address[1]
    threading.Thread(target=server.serve_forever, args=(0.02,), daemon=True).start()
    try:
        yield att
    finally:
        server.shutdown()
        server.server_close()


def _lauf(argv: list[str], umgebung: dict[str, str]) -> tuple[int, str, str]:
    aus, fehler = io.StringIO(), io.StringIO()
    code = openfang_aufruf.main(argv, umgebung, aus, fehler)
    return code, aus.getvalue(), fehler.getvalue()


def _kein_geheimnis(openfang: _Attrappe, *texte: str) -> None:
    for text in texte:
        assert SCHLUESSEL not in text and f":{openfang.port}" not in text and "127.0.0.1" not in text


# ---- werkzeuge-je-agent ----

OFFEN = "offener Zugang (sieht alle Werkzeuge des Servers):"


def _setze(openfang: _Attrappe, *agenten: tuple[str, list[str], list[str], str]) -> None:
    """Liste und Details der Attrappe neu setzen; Kennung ist der Name (brain-sales behaelt KENNUNG)."""
    openfang.agenten = [{"id": KENNUNG if n == "brain-sales" else n, "name": n} for n, *_ in agenten]
    openfang.details = {(KENNUNG if n == "brain-sales" else n): (200, _detail(n, w, srv, m)) for n, w, srv, m in agenten}


def test_werkzeuge_je_agent_nur_brain_sales_ausdruecklich_ist_exit_0(openfang: _Attrappe) -> None:
    code, aus, fehler = _lauf(["werkzeuge-je-agent", "auftragsbuch"], openfang.umgebung)
    assert code == 0 and fehler == ""
    assert aus == f"brain-sales: {LESEN}, {PROBE}\n"
    _kein_geheimnis(openfang, aus, fehler)
    # erst die Liste, dann das Detail je Agent
    assert openfang.pfade() == ["GET /api/agents", f"GET /api/agents/{KENNUNG}", "GET /api/agents/x2"]
    for anfrage in openfang.anfragen:
        assert anfrage["kopf"]["authorization"] == f"Bearer {SCHLUESSEL}"  # type: ignore[index]


def test_werkzeuge_je_agent_nimmt_auch_objekt_mit_agents(openfang: _Attrappe) -> None:
    openfang.agenten = {"agents": openfang.agenten}
    code, aus, _ = _lauf(["werkzeuge-je-agent", "auftragsbuch"], openfang.umgebung)
    assert code == 0 and aus == f"brain-sales: {LESEN}, {PROBE}\n"


def test_werkzeuge_je_agent_zweiter_ausdruecklicher_agent_ist_exit_1(openfang: _Attrappe) -> None:
    _setze(openfang, ("brain-sales", [PROBE, LESEN], ["auftragsbuch"], "allowlist"),
           ("zweiter", [LESEN, "datei_lesen"], ["auftragsbuch"], "allowlist"))
    code, aus, fehler = _lauf(["werkzeuge-je-agent", "auftragsbuch"], openfang.umgebung)
    assert code == 1
    assert aus.splitlines() == [f"brain-sales: {LESEN}, {PROBE}", f"zweiter: {LESEN}"]
    assert "abweichend" in fehler
    _kein_geheimnis(openfang, aus, fehler)


@pytest.mark.parametrize("werkzeuge", [[PROBE, LESEN, POST_PRUEFEN], [PROBE], []])
def test_werkzeuge_je_agent_brain_sales_mit_falscher_menge_ist_exit_1(openfang: _Attrappe,
                                                                      werkzeuge: list[str]) -> None:
    _setze(openfang, ("brain-sales", werkzeuge, ["auftragsbuch"], "allowlist"))
    code, aus, fehler = _lauf(["werkzeuge-je-agent", "auftragsbuch"], openfang.umgebung)
    assert code == 1 and "abweichend" in fehler
    # leere Werkzeugliste heisst offener Zugang: brain-sales steht dann in Gruppe 2, nicht in Gruppe 1
    assert aus.splitlines() == ([f"brain-sales: {', '.join(sorted(werkzeuge))}"] if werkzeuge else [OFFEN, "brain-sales"])


@pytest.mark.parametrize("werkzeuge, server, modus", [
    ([], [], "allowlist"),                       # leere Liste, leeres mcp_servers: sieht alles
    (["*"], ["auftragsbuch"], "allowlist"),      # Stern, Server ausdruecklich erlaubt
    ([], ["anderer-server"], "all"),             # Modus all
])
def test_werkzeuge_je_agent_nennt_offene_agenten_ohne_den_exit_zu_aendern(
        openfang: _Attrappe, werkzeuge: list[str], server: list[str], modus: str) -> None:
    _setze(openfang, ("brain-sales", [PROBE, LESEN], ["auftragsbuch"], "allowlist"), ("offen", werkzeuge, server, modus))
    code, aus, fehler = _lauf(["werkzeuge-je-agent", "auftragsbuch"], openfang.umgebung)
    assert code == 0 and fehler == ""
    assert aus.splitlines() == [f"brain-sales: {LESEN}, {PROBE}", OFFEN, "offen"]
    _kein_geheimnis(openfang, aus, fehler)


@pytest.mark.parametrize("werkzeuge, server, modus", [
    ([], ["anderer-server"], "allowlist"),       # sieht den Server nicht
    (["datei_lesen"], [], "all"),                # eigene Werkzeugliste ohne Server-Werkzeuge
    (["datei_lesen"], ["auftragsbuch"], "allowlist"),
])
def test_werkzeuge_je_agent_nennt_geschlossene_agenten_nicht(
        openfang: _Attrappe, werkzeuge: list[str], server: list[str], modus: str) -> None:
    _setze(openfang, ("brain-sales", [PROBE, LESEN], ["auftragsbuch"], "allowlist"), ("zu", werkzeuge, server, modus))
    code, aus, _ = _lauf(["werkzeuge-je-agent", "auftragsbuch"], openfang.umgebung)
    assert code == 0 and aus == f"brain-sales: {LESEN}, {PROBE}\n"


def test_werkzeuge_je_agent_offener_agent_allein_ist_exit_1_weil_brain_sales_fehlt(openfang: _Attrappe) -> None:
    _setze(openfang, ("offen", [], [], "allowlist"))
    code, aus, _ = _lauf(["werkzeuge-je-agent", "auftragsbuch"], openfang.umgebung)
    assert code == 1 and aus.splitlines() == [OFFEN, "offen"]


@pytest.mark.parametrize("status", [500, 404, 401])
def test_werkzeuge_je_agent_fehlgeschlagenes_detail_ist_exit_1_ohne_ausgabe(openfang: _Attrappe, status: int) -> None:
    openfang.details["x2"] = (status, {"fehler": "GEHEIM-detail"})
    code, aus, fehler = _lauf(["werkzeuge-je-agent", "auftragsbuch"], openfang.umgebung)
    erwartet = "Schluessel abgelehnt" if status == 401 else f"Status {status}"
    assert code == 1 and aus == "" and fehler == f"unerwartet ({erwartet})\n"
    assert "GEHEIM-detail" not in aus + fehler


def test_werkzeuge_je_agent_detail_ohne_objekt_ist_exit_1(openfang: _Attrappe) -> None:
    openfang.details["x2"] = (200, ["liste"])
    code, aus, fehler = _lauf(["werkzeuge-je-agent", "auftragsbuch"], openfang.umgebung)
    assert code == 1 and aus == "" and fehler == "unerwartet (Agentendetail ungueltig)\n"


@pytest.mark.parametrize("status, agenten, grund", [
    (401, None, "Schluessel abgelehnt"), (500, None, "Status 500"), (200, "kaputt", "Agentenliste ungueltig")])
def test_werkzeuge_je_agent_fehler_der_liste_nennen_festen_grund(openfang: _Attrappe, status: int, agenten: object,
                                                                 grund: str) -> None:
    openfang.agenten_status, openfang.agenten = status, agenten
    code, aus, fehler = _lauf(["werkzeuge-je-agent", "auftragsbuch"], openfang.umgebung)
    assert code == 1 and aus == "" and fehler == f"unerwartet ({grund})\n"


# ---- gegenprobe ----

def test_gegenprobe_ablehnung_ist_erwartet(openfang: _Attrappe) -> None:
    code, aus, fehler = _lauf(["gegenprobe", "brain-sales", "sales_post_pruefen"], openfang.umgebung)
    assert (code, aus, fehler) == (0, "abgelehnt von OpenFang (erwartet)\n", "")
    assert openfang.pfade() == ["GET /api/agents", "POST /mcp"]
    aufruf = openfang.anfragen[1]
    assert aufruf["kopf"]["x-openfang-agent-id"] == KENNUNG  # type: ignore[index]
    assert aufruf["kopf"]["authorization"] == f"Bearer {SCHLUESSEL}"  # type: ignore[index]
    # ohne Freigabe- und Kostenkopf: Ablehnung ist das Erwartete
    assert not [k for k in aufruf["kopf"] if k.startswith("x-openfang-") and k != "x-openfang-agent-id"]  # type: ignore[attr-defined]
    assert aufruf["koerper"] == {
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": POST_PRUEFEN, "arguments": {"eingabe": {"zeitraum": "heute", "entwuerfe_anlegen": False}}}}
    _kein_geheimnis(openfang, aus, fehler)


def test_gegenprobe_403_ist_ebenfalls_ablehnung(openfang: _Attrappe) -> None:
    openfang.mcp = lambda _k: (403, {"error": "forbidden"})
    assert _lauf(["gegenprobe", "brain-sales", "sales_post_pruefen"], openfang.umgebung)[:2] == (
        0, "abgelehnt von OpenFang (erwartet)\n")


@pytest.mark.parametrize("status", [200, 403, 500])
def test_gegenprobe_erkennt_durchgelassen_am_datenbanktext_und_druckt_keinen_koerper(
        openfang: _Attrappe, status: int) -> None:
    openfang.mcp = lambda _k: (status, {"jsonrpc": "2.0", "id": 1, "result": {
        "isError": True, "content": [{"type": "text", "text": "Unbekannte Auftragsart GEHEIM-detail"}]}})
    code, aus, fehler = _lauf(["gegenprobe", "brain-sales", "sales_post_pruefen"], openfang.umgebung)
    assert code == 1 and aus == "DURCHGELASSEN: Unbekannte Auftragsart - anhalten\n" and fehler == ""
    assert "GEHEIM-detail" not in aus + fehler


@pytest.mark.parametrize("status, koerper, grund", [
    (200, {"jsonrpc": "2.0", "id": 1, "result": {"content": [{"type": "text", "text": "GEHEIM-ergebnis"}]}},
     "angenommen ohne Ablehnung"),
    (200, "kein-objekt-aber-json", "angenommen ohne Ablehnung"),
    (401, {"error": "nein"}, "Schluessel abgelehnt"),
    (500, {"error": "kaputt"}, "Status 500"),
    (404, None, "Status 404"),
])
def test_gegenprobe_sonst_unerwartet_mit_festem_grund(openfang: _Attrappe, status: int, koerper: object,
                                                      grund: str) -> None:
    openfang.mcp = lambda _k: (status, koerper)
    code, aus, fehler = _lauf(["gegenprobe", "brain-sales", "sales_post_pruefen"], openfang.umgebung)
    assert code == 1 and aus == f"unerwartet ({grund})\n" and fehler == ""
    assert "GEHEIM-ergebnis" not in aus


def test_gegenprobe_unregistrierter_agent_ruft_nichts_auf(openfang: _Attrappe) -> None:
    openfang.agenten = []
    code, aus, _ = _lauf(["gegenprobe", "brain-sales", "sales_post_pruefen"], openfang.umgebung)
    assert code == 1 and aus == "unerwartet (Agent nicht registriert)\n"
    assert openfang.pfade() == ["GET /api/agents"]


@pytest.mark.parametrize("argv", [
    ["gegenprobe", "brain-sales", "auftrag_probe"], ["gegenprobe", "brain-sales"], ["gegenprobe", "a b", "sales_post_pruefen"],
    ["gegenprobe", "../x", "sales_post_pruefen"]])
def test_gegenprobe_nur_mit_ihrem_werkzeug(openfang: _Attrappe, argv: list[str]) -> None:
    assert _lauf(argv, openfang.umgebung)[0] == 2
    assert openfang.anfragen == []


# ---- agent-entfernen ----

def test_agent_entfernen_loest_namen_auf_und_loescht(openfang: _Attrappe) -> None:
    code, aus, fehler = _lauf(["agent-entfernen", "brain-sales"], openfang.umgebung)
    assert (code, aus, fehler) == (0, "entfernt\n", "")
    assert openfang.pfade() == ["GET /api/agents", f"DELETE /api/agents/{KENNUNG}"]
    assert openfang.anfragen[1]["kopf"]["authorization"] == f"Bearer {SCHLUESSEL}"  # type: ignore[index]


def test_agent_entfernen_nicht_vorhanden(openfang: _Attrappe) -> None:
    openfang.agenten = [{"id": "x2", "name": "anderer"}]
    assert _lauf(["agent-entfernen", "brain-sales"], openfang.umgebung)[:2] == (0, "nicht vorhanden\n")
    assert openfang.pfade() == ["GET /api/agents"]


def test_agent_entfernen_404_beim_loeschen_ist_nicht_vorhanden(openfang: _Attrappe) -> None:
    openfang.loeschen_status = 404
    assert _lauf(["agent-entfernen", "brain-sales"], openfang.umgebung)[:2] == (0, "nicht vorhanden\n")


def test_agent_entfernen_sonst_unerwartet_mit_status(openfang: _Attrappe) -> None:
    openfang.loeschen_status = 500
    assert _lauf(["agent-entfernen", "brain-sales"], openfang.umgebung)[:2] == (1, "unerwartet (500)\n")


@pytest.mark.parametrize("argv", [["agent-entfernen", "anderer"], ["agent-entfernen", "Brain-Sales"],
                                  ["agent-entfernen", "x2"], ["agent-entfernen"], ["agent-entfernen", "brain-sales", "x"]])
def test_agent_entfernen_nimmt_nur_brain_sales(openfang: _Attrappe, argv: list[str]) -> None:
    code, aus, fehler = _lauf(argv, openfang.umgebung)
    assert code == 2 and aus == ""
    assert openfang.anfragen == [] and SCHLUESSEL not in fehler


# ---- Einstellungen, Sicherheit des Aufrufs ----

@pytest.mark.parametrize("fehlt", ["OPENFANG_URL", "OPENFANG_API_KEY"])
def test_fehlende_variable_nennt_nur_den_namen(openfang: _Attrappe, fehlt: str) -> None:
    umgebung = {k: v for k, v in openfang.umgebung.items() if k != fehlt}
    code, aus, fehler = _lauf(["werkzeuge-je-agent", "auftragsbuch"], umgebung)
    assert code == 2 and aus == "" and fehlt in fehler
    other = ({"OPENFANG_URL", "OPENFANG_API_KEY"} - {fehlt}).pop()
    assert other not in fehler and SCHLUESSEL not in fehler and "127.0.0.1" not in fehler
    assert openfang.anfragen == []


@pytest.mark.parametrize("adresse", [
    "http://beispiel.invalid:4200", "https://beispiel.invalid", "http://127.0.0.1.beispiel.invalid:4200",
    "http://127.0.0.1@beispiel.invalid:4200", "http://[::1]:4200", "http://0.0.0.0:4200", "ftp://127.0.0.1:4200",
    "http://127.0.0.1:4200/pfad", "http://127.0.0.1:abc", "127.0.0.1:4200"])
def test_nicht_lokale_adresse_wird_abgelehnt_ohne_sie_zu_nennen(openfang: _Attrappe, adresse: str) -> None:
    for argv in (["werkzeuge-je-agent", "auftragsbuch"], ["gegenprobe", "brain-sales", "sales_post_pruefen"],
                 ["agent-entfernen", "brain-sales"]):
        code, aus, fehler = _lauf(argv, {"OPENFANG_URL": adresse, "OPENFANG_API_KEY": SCHLUESSEL})
        assert code == 2 and aus == ""
        assert "beispiel" not in fehler and adresse not in fehler and SCHLUESSEL not in fehler
    assert openfang.anfragen == []


def test_localhost_ist_erlaubt(openfang: _Attrappe) -> None:
    umgebung = {"OPENFANG_URL": f"http://localhost:{openfang.port}/", "OPENFANG_API_KEY": SCHLUESSEL}
    assert _lauf(["werkzeuge-je-agent", "auftragsbuch"], umgebung)[0] == 0


def test_weiterleitung_wird_nicht_verfolgt(openfang: _Attrappe) -> None:
    openfang.agenten_ort = "/anderer-ort"
    code, aus, fehler = _lauf(["werkzeuge-je-agent", "auftragsbuch"], openfang.umgebung)
    assert code == 1 and aus == "" and fehler == "unerwartet (Status 302)\n"
    assert openfang.pfade() == ["GET /api/agents"]
    code, aus, _ = _lauf(["agent-entfernen", "brain-sales"], openfang.umgebung)
    assert code == 1 and aus == "unerwartet (Status 302)\n"
    assert not [a for a in openfang.anfragen if a["methode"] == "DELETE" or a["pfad"] == "/anderer-ort"]


def test_proxy_aus_der_umgebung_wird_ignoriert(openfang: _Attrappe, monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.setenv(name, "http://127.0.0.1:9")  # Port 9: dort lauscht niemand
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)
    assert _lauf(["werkzeuge-je-agent", "auftragsbuch"], openfang.umgebung)[0] == 0
    assert openfang.anfragen


def test_dienst_aus_ist_fester_grund_ohne_adresse() -> None:
    umgebung = {"OPENFANG_URL": "http://127.0.0.1:9", "OPENFANG_API_KEY": SCHLUESSEL}
    code, _, fehler = _lauf(["werkzeuge-je-agent", "auftragsbuch"], umgebung)
    assert code == 1 and fehler == "unerwartet (nicht erreichbar)\n"
    code, aus, _ = _lauf(["agent-entfernen", "brain-sales"], umgebung)
    assert (code, aus) == (1, "unerwartet (nicht erreichbar)\n")


def test_cli_ueber_umgebung_ohne_schluessel_in_der_ausgabe(openfang: _Attrappe) -> None:
    lauf = subprocess.run([sys.executable, str(DEPLOY / "openfang_aufruf.py"), "werkzeuge-je-agent", "auftragsbuch"],
                          env={**openfang.umgebung, "SYSTEMROOT": "C:\\Windows"}, capture_output=True,
                          timeout=60, check=False)
    assert lauf.returncode == 0, lauf.stderr
    gesamt = (lauf.stdout + lauf.stderr).decode("utf-8").replace("\r\n", "\n")
    assert SCHLUESSEL not in gesamt and "127.0.0.1" not in gesamt
    assert gesamt == f"brain-sales: {LESEN}, {PROBE}\n"
    # Der Schluessel steht auf keiner Kommandozeile
    assert SCHLUESSEL not in " ".join(lauf.args)  # type: ignore[arg-type]


def test_cli_ohne_einstellungen_exit_2() -> None:
    lauf = subprocess.run([sys.executable, str(DEPLOY / "openfang_aufruf.py"), "agent-entfernen", "brain-sales"],
                          env={"SYSTEMROOT": "C:\\Windows"}, capture_output=True, timeout=60, check=False)
    assert lauf.returncode == 2 and b"OPENFANG_URL" in lauf.stderr
