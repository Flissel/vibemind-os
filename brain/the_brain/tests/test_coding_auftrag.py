"""Bauauftrag an den Project Space (core/coding_auftrag). Ohne Netz, ohne echten Lauf; Namen erfunden.

Teil 1: Bausteine mit Attrappen (Erkennen, Rueckfrage, Bestaetigung, Start, Nachfassen, Nachfasser).
Teil 2: Router: "Bau mir ..." ist eine Rueckfrage, "Ja, bauen" startet genau einmal, der Stand der
Zeile laeuft ueber /api/agent-auftraege/{id}/stand. Teil 3: ein echter kleiner HTTP-Dienst auf
127.0.0.1 statt des Project Space (prueft Kopf, Schluessel und Koerper des POST).
"""
from __future__ import annotations

import copy
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

_BRAIN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_BRAIN))

from core import coding_auftrag as ca  # noqa: E402
from core.agent_auftraege import AuftragsTabelle  # noqa: E402

KENNUNG = "c-20261124-0a1b2c3d"
SATZ = "Bau mir eine Funktion istSchaltjahr(jahr), mit Tests."
GESUND = (200, {"status": "ok", "service": "coding-dienst", "schreiben": True})


@pytest.fixture(autouse=True)
def _leer(monkeypatch):
    ca.verwerfen()
    monkeypatch.setenv("CODING_DIENST_SCHLUESSEL", "nur-im-test")
    yield
    ca.verwerfen()


class _Tabelle:
    """Auftragstabelle im Speicher, mit denselben Methoden wie AuftragsTabelle."""

    def __init__(self, anlegen_geht=True):
        self.zeilen = {}
        self.anlegen_geht = anlegen_geht

    def anlegen(self, **kw):
        if not self.anlegen_geht:
            raise RuntimeError("weg")
        auftrag_id = f"00000000-0000-4000-8000-00000000000{len(self.zeilen) + 1}"
        self.zeilen[auftrag_id] = {"id": auftrag_id, **kw, "pruefung": None}
        return auftrag_id

    def uebergabe_setzen(self, auftrag_id, uebergabe):
        self.zeilen[auftrag_id]["uebergabe"] = uebergabe

    def coding_laufend(self):
        return [copy.deepcopy(z) for z in self.zeilen.values()
                if z["status"] == "laeuft" and (z.get("uebergabe") or {}).get("coding")]

    def recherche_abschliessen(self, auftrag_id, status, pruefung):
        zeile = self.zeilen[auftrag_id]
        if zeile["status"] != "laeuft":
            return False
        zeile.update({"status": status, "pruefung": pruefung})
        return True

    def lesen(self, auftrag_id):
        return copy.deepcopy(self.zeilen.get(auftrag_id))


def _angenommen(gesendet=None):
    def senden(satz):
        if gesendet is not None:
            gesendet.append(satz)
        return 200, {"auftrag": KENNUNG, "angestossen": True, "zustand": "laeuft", "modellaufrufe_hoechstens": 20}
    return senden


def _laufend(tabelle):
    ca.vorschau(SATZ, gesund=lambda: GESUND)
    ca.starten(ca.satz_pruefen("Ja, bauen")["offen"], tabelle=tabelle, trace_id="tr_1", senden=_angenommen())
    (auftrag_id,) = tabelle.zeilen
    return auftrag_id


# ── Teil 1: Bausteine ────────────────────────────────────────────────────────

@pytest.mark.parametrize("satz,space", [
    ("Bau mir eine Funktion istSchaltjahr(jahr), die sagt, ob ein Jahr ein Schaltjahr ist, mit Tests.", None),
    ("Schreib mir ein kleines Kommandozeilenprogramm, das Woerter zaehlt.", None),
    ("Baue eine REST-API fuer Notizen.", "ideas"),
    ("bitte programmiere mir einen Rechner", None),
    ("Bau mir was, das Termine sortiert.", "projects"),
])
def test_bauauftraege_werden_erkannt(satz, space) -> None:
    assert ca.ist_bauauftrag(satz, space)


@pytest.mark.parametrize("satz,space", [
    ("Schreib mir eine Notiz zum Stadtfest.", None),
    ("Bau mir was, das Termine sortiert.", None),          # ohne Project Space und ohne Programm-Wort
    ("Schreib ein Python-Skript, das CSV liest.", "projects"),  # andere Sprache: bisheriger Weg
    ("Wie ist der Stand im Project Space?", "projects"),
    ("Bau die Funktion aus Java nach.", None),
    ("Erstelle eine Bubble zur App-Idee.", None),
    ("erstelle eine idee für APIs und Microservices", "ideas"),
    ("Schreib eine Mail an das Team zum neuen Programm.", None),
    ("Erstelle eine Rechnung für das Spiel.", None),
    ("", "projects"),
    (None, "projects"),
])
def test_andere_saetze_sind_kein_bauauftrag(satz, space) -> None:
    assert not ca.ist_bauauftrag(satz, space)


def test_vorschau_ist_rueckfrage_ohne_lauf_und_wird_gemerkt() -> None:
    frage = ca.vorschau(SATZ, gesund=lambda: GESUND)
    assert frage.startswith("Soll der Project Space das bauen: „Bau mir eine Funktion istSchaltjahr")
    assert "Ja, bauen" in frage
    assert ca._offen["satz"] == SATZ


@pytest.mark.parametrize("gesund,satz", [
    ((0, None), ca.NICHT_ERREICHBAR),
    ((200, {"service": "anderer"}), ca.NICHT_ERREICHBAR),
    ((200, {"service": "coding-dienst", "schreiben": False}), ca.NIMMT_NICHTS),
])
def test_vorschau_ohne_dienst_oder_schreibrecht_fragt_nicht(gesund, satz) -> None:
    assert ca.vorschau(SATZ, gesund=lambda: gesund) == satz
    assert ca._offen == {}


def test_vorschau_ohne_schluessel_fragt_nicht(monkeypatch) -> None:
    monkeypatch.delenv("CODING_DIENST_SCHLUESSEL")
    assert ca.vorschau(SATZ, gesund=lambda: GESUND) == ca.OHNE_SCHLUESSEL
    assert ca._offen == {}


def test_ja_gilt_nur_nach_der_rueckfrage_und_nur_einmal() -> None:
    assert ca.satz_pruefen("Ja, bauen") == {"satz": ca.OHNE_FRAGE}
    ca.vorschau(SATZ, gesund=lambda: GESUND)
    assert ca.satz_pruefen("Ja, bau es!")["offen"]["satz"] == SATZ
    assert ca.satz_pruefen("Ja, bauen") == {"satz": ca.OHNE_FRAGE}


def test_anderer_satz_verwirft_die_rueckfrage() -> None:
    ca.vorschau(SATZ, gesund=lambda: GESUND)
    assert ca.satz_pruefen("Wie ist der Stand?") is None
    assert ca.satz_pruefen("Ja, bauen") == {"satz": ca.OHNE_FRAGE}


def test_rueckfrage_verfaellt(monkeypatch) -> None:
    ca.vorschau(SATZ, gesund=lambda: GESUND)
    monkeypatch.setattr(ca, "_jetzt", lambda: 10 ** 12)
    assert ca.satz_pruefen("Ja, bauen") == {"satz": ca.OHNE_FRAGE}


@pytest.mark.parametrize("satz", ["ja", "Ja, recherchieren", "Ja, erstellen", "ja bauen wir morgen"])
def test_andere_ja_saetze_sind_keine_bestaetigung(satz) -> None:
    assert not ca.ist_bestaetigung(satz)


def test_starten_legt_erst_die_zeile_an_dann_den_auftrag() -> None:
    tabelle, gesendet = _Tabelle(), []

    def senden(satz):
        assert len(tabelle.zeilen) == 1, "die Zeile muss vor dem Start stehen"
        return _angenommen(gesendet)(satz)

    ca.vorschau(SATZ, gesund=lambda: GESUND)
    felder = ca.starten(ca.satz_pruefen("Ja, bauen")["offen"], tabelle=tabelle, trace_id="tr_1", senden=senden)
    (zeile,) = tabelle.zeilen.values()
    assert gesendet == [SATZ]
    assert zeile["status"] == "laeuft" and zeile["capability"] == ca.EREIGNIS_START and zeile["agent"] == ca.AGENT
    assert zeile["uebergabe"] == {"coding": {"satz": SATZ, "auftrag": KENNUNG}}
    assert felder["status"] == "angenommen" and felder["pending"] is True and felder["ok"] is True
    assert felder["space"] == "projects" and felder["auftraege"][0]["auftrag_id"] == zeile["id"]
    assert "Höchstens 20 Modellaufrufe" in felder["final_text"]


def test_ohne_tabelle_startet_nichts() -> None:
    gesendet = []
    ca.vorschau(SATZ, gesund=lambda: GESUND)
    with pytest.raises(ca.CodingFehler) as fehler:
        ca.starten(ca.satz_pruefen("Ja, bauen")["offen"], tabelle=_Tabelle(anlegen_geht=False), trace_id="t",
                   senden=_angenommen(gesendet))
    assert str(fehler.value) == ca.TABELLE_FEHLT and gesendet == []


@pytest.mark.parametrize("antwort,satz", [
    ((403, {"error": "schreiben_aus"}), ca.NICHT_GESTARTET),
    ((0, None), ca.NICHT_GESTARTET),
    ((200, {"auftrag": "../boese", "angestossen": True}), ca.NICHT_GESTARTET),
    ((200, {"auftrag": KENNUNG, "angestossen": False}), ca.BESCHAEFTIGT),
])
def test_abgelehnter_start_schliesst_die_zeile(antwort, satz) -> None:
    tabelle = _Tabelle()
    ca.vorschau(SATZ, gesund=lambda: GESUND)
    with pytest.raises(ca.CodingFehler) as fehler:
        ca.starten(ca.satz_pruefen("Ja, bauen")["offen"], tabelle=tabelle, trace_id="t", senden=lambda s: antwort)
    assert str(fehler.value) == satz
    (zeile,) = tabelle.zeilen.values()
    assert zeile["status"] == "fehler" and zeile["pruefung"]["meldung"] == satz


def test_senden_ohne_schluessel_wirft(monkeypatch) -> None:
    monkeypatch.delenv("CODING_DIENST_SCHLUESSEL")
    with pytest.raises(ca.CodingFehler):
        ca.auftrag_senden(SATZ)


def test_runde_laesst_laufenden_bau_weiterlaufen() -> None:
    tabelle = _Tabelle()
    auftrag_id = _laufend(tabelle)
    z = ca.runde(tabelle, stand=lambda k: (200, {"zustand": "laeuft"}), ergebnis=lambda k: pytest.fail("zu frueh"))
    assert z["coding_wartet"] == 1 and tabelle.zeilen[auftrag_id]["status"] == "laeuft"


def test_runde_fertig_ist_ungeprueft_mit_ehrlichem_satz() -> None:
    from core.auftrag_stand import stand_einer_zeile
    tabelle = _Tabelle()
    auftrag_id = _laufend(tabelle)
    ergebnis = {"dateien": ["src/schaltjahr.js", "test/schaltjahr.test.js"], "pruefung": {"bestanden": True},
                "start": None}
    z = ca.runde(tabelle, stand=lambda k: (200, {"zustand": "fertig", "titel": "Schaltjahr"}),
                 ergebnis=lambda k: (200, ergebnis))
    zeile = tabelle.zeilen[auftrag_id]
    assert z["coding_fertig"] == 1 and zeile["status"] == "fertig"
    assert zeile["pruefung"]["verified"] is None and zeile["pruefung"]["coding_auftrag"] == KENNUNG
    stand = stand_einer_zeile(zeile)
    assert stand["stand"] == "ungeprueft" and stand["hinweis"] is True and stand["ok"] is False
    assert "2 Datei(en)" in stand["meldung"] and "unabhängig geprüft habe ich das nicht" in stand["meldung"]


def test_runde_mit_startbefehl_nennt_ihn() -> None:
    tabelle = _Tabelle()
    auftrag_id = _laufend(tabelle)
    ca.runde(tabelle, stand=lambda k: (200, {"zustand": "fertig", "titel": "Notizen"}),
             ergebnis=lambda k: (200, {"dateien": ["src/server.js"], "pruefung": {"bestanden": False},
                                       "start": "npm start"}))
    meldung = tabelle.zeilen[auftrag_id]["pruefung"]["meldung"]
    assert "„npm start“" in meldung and "nicht als bestanden" in meldung


@pytest.mark.parametrize("sicht,teil", [
    ({"zustand": "fehlgeschlagen", "fehler": "Die Engine hat keine Datei geschrieben."}, "keine Datei geschrieben"),
    ({"zustand": "abgebrochen"}, "abgebrochen"),
])
def test_runde_fehler_und_abbruch(sicht, teil) -> None:
    from core.auftrag_stand import stand_einer_zeile
    tabelle = _Tabelle()
    auftrag_id = _laufend(tabelle)
    z = ca.runde(tabelle, stand=lambda k: (200, sicht), ergebnis=lambda k: pytest.fail("kein Ergebnis"))
    zeile = tabelle.zeilen[auftrag_id]
    assert z["coding_fehler"] == 1 and zeile["status"] == "fehler"
    stand = stand_einer_zeile(zeile)
    assert stand["stand"] == "fehler" and teil in stand["meldung"]


def test_runde_ohne_verbindung_bleibt_offen() -> None:
    tabelle = _Tabelle()
    auftrag_id = _laufend(tabelle)
    z = ca.runde(tabelle, stand=lambda k: (0, None), ergebnis=lambda k: (0, None))
    assert z["fehler"] == 1 and tabelle.zeilen[auftrag_id]["status"] == "laeuft"


def test_runde_ohne_kennung_schliesst_ehrlich() -> None:
    tabelle = _Tabelle()
    auftrag_id = tabelle.anlegen(capability=ca.EREIGNIS_START, agent=ca.AGENT, auftrag="x", trace_id="t",
                                 plan_id="p", hop_id="s1", uebergabe={"coding": {"satz": SATZ}}, status="laeuft")
    ca.runde(tabelle, stand=lambda k: pytest.fail("kein Aufruf ohne Kennung"), ergebnis=lambda k: None)
    assert tabelle.zeilen[auftrag_id]["status"] == "fehler"


def test_nachfasser_fasst_bauauftraege_nach() -> None:
    from core.nachfasser import Nachfasser

    class _NTabelle(_Tabelle):
        def zu_pruefen(self):
            return []

        def beendete_ohne_meldung(self):
            return []

        def abgelaufene_markieren(self):
            return 0

    tabelle = _NTabelle()
    auftrag_id = _laufend(tabelle)
    n = Nachfasser(tabelle, validator=None, router=None, plan_executor=None,
                   recherche_anschluss=lambda: {"status_abfragen": lambda j: {}, "karte_lesen": lambda i: None},
                   coding_anschluss=lambda: {"stand": lambda k: (200, {"zustand": "fertig", "titel": "T"}),
                                             "ergebnis": lambda k: (200, {"dateien": [], "pruefung": {}})})
    z = n.runde()
    assert z["coding_fertig"] == 1 and tabelle.zeilen[auftrag_id]["status"] == "fertig"


def test_tabelle_fragt_laufende_bauauftraege() -> None:
    gesendet = []

    class _Antwort:
        def raise_for_status(self):
            pass

        def json(self):
            return []

    class _Http:
        def request(self, methode, url, *, headers, params, json, timeout):
            gesendet.append((methode, params))
            return _Antwort()

    AuftragsTabelle("http://db.test", "schluessel-nur-im-test", http=_Http()).coding_laufend()
    assert gesendet[-1][1]["uebergabe->coding"] == "not.is.null" and gesendet[-1][1]["status"] == "eq.laeuft"


def test_hoeflichkeit_sieht_die_offene_rueckfrage() -> None:
    from core import hoeflichkeit
    ca.vorschau(SATZ, gesund=lambda: GESUND)
    assert hoeflichkeit.etwas_offen() is True


# ── Teil 2: Router ───────────────────────────────────────────────────────────

def _router_client(monkeypatch, tabelle, gesendet):
    import core.difficulty_router as dr
    from core import agent_auftraege as aa
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from web.routers.introspection import router

    class _K:
        def classify(self, intent):
            return {"level": "medium", "score": 0.9, "method": "qwen-cosine", "reason": "x"}

    class _Ausfuehrer:
        class recorder:  # noqa: N801
            def __getattr__(self, _n):
                return lambda *a, **k: None
        recorder = recorder()

        def execute(self, plan, **kw):
            raise AssertionError("kein Plan fuer einen Bau")

    class _Planer:
        async def aplan(self, intent, context=None):
            raise AssertionError("kein Planer")

    monkeypatch.setattr(dr, "get_router", lambda: _K())
    monkeypatch.setattr(aa, "tabelle_aus_umgebung", lambda: tabelle)
    monkeypatch.setattr(ca, "gesundheit", lambda: GESUND)
    monkeypatch.setattr(ca, "auftrag_senden", _angenommen(gesendet))
    monkeypatch.setenv("SOM_NOPLAN_FALLBACK", "0")
    monkeypatch.setenv("MULTI_VERB_ROUTING", "0")
    app = FastAPI()
    app.include_router(router)
    app.state.plan_executor = _Ausfuehrer()
    app.state.multihop_planner = _Planer()
    app.state.final_synthesizer = None
    client = TestClient(app)
    return client, lambda text: client.post("/api/multihop/execute",
                                            json={"intent": text, "context": {"space": "projects"}}).json()


def test_router_rueckfrage_dann_ja_startet_genau_einmal(monkeypatch) -> None:
    tabelle, gesendet = _Tabelle(), []
    client, satz = _router_client(monkeypatch, tabelle, gesendet)
    body = satz(SATZ)
    assert body["rueckfrage"] is True and body["space"] == "projects", body
    assert body["final_text"].startswith("Soll der Project Space das bauen") and gesendet == []
    body = satz("Ja, bauen")
    assert body["status"] == "angenommen" and body["ok"] is True and body["pending"] is True, body
    assert gesendet == [SATZ] and len(tabelle.zeilen) == 1
    auftrag_id = body["auftraege"][0]["auftrag_id"]
    stand = client.get(f"/api/agent-auftraege/{auftrag_id}/stand").json()
    assert stand["stand"] == "laeuft", stand
    body = satz("Ja, bauen")
    assert body["final_text"] == ca.OHNE_FRAGE and gesendet == [SATZ]


def test_router_anderer_satz_verwirft_die_rueckfrage(monkeypatch) -> None:
    tabelle, gesendet = _Tabelle(), []
    _client, satz = _router_client(monkeypatch, tabelle, gesendet)
    satz(SATZ)
    satz("Ja, erstellen")
    body = satz("Ja, bauen")
    assert body["final_text"] == ca.OHNE_FRAGE and gesendet == [] and tabelle.zeilen == {}


# ── Teil 3: echter HTTP-Weg ──────────────────────────────────────────────────

@pytest.fixture()
def dienst(monkeypatch):
    empfangen = []

    class _Halter(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _senden(self, status, daten):
            roh = json.dumps(daten).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(roh)))
            self.end_headers()
            self.wfile.write(roh)

        def do_GET(self):  # noqa: N802
            if self.path == "/health":
                return self._senden(200, GESUND[1])
            if self.path == f"/api/stand?auftrag={KENNUNG}":
                return self._senden(200, {"zustand": "laeuft"})
            return self._senden(404, {"error": "x"})

        def do_POST(self):  # noqa: N802
            laenge = int(self.headers.get("Content-Length") or 0)
            empfangen.append((self.headers.get(ca.KOPF_SCHLUESSEL), json.loads(self.rfile.read(laenge))))
            if self.headers.get(ca.KOPF_SCHLUESSEL) != "nur-im-test":
                return self._senden(403, {"error": "schreiben_aus"})
            return self._senden(200, {"auftrag": KENNUNG, "angestossen": True})

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Halter)
    faden = threading.Thread(target=server.serve_forever, daemon=True)
    faden.start()
    monkeypatch.setenv("CODING_DIENST_URL", f"http://127.0.0.1:{server.server_address[1]}")
    yield empfangen
    server.shutdown()
    server.server_close()


def test_echter_http_weg_sendet_schluessel_und_satz(dienst) -> None:
    assert ca.gesundheit()[1]["schreiben"] is True
    status, antwort = ca.auftrag_senden(SATZ)
    assert status == 200 and antwort["auftrag"] == KENNUNG
    assert dienst == [("nur-im-test", {"aufgabe": SATZ, "ohne_npm": True})]
    assert ca.stand_abfragen(KENNUNG) == (200, {"zustand": "laeuft"})


def test_echter_http_weg_falscher_schluessel_wird_abgelehnt(dienst, monkeypatch) -> None:
    monkeypatch.setenv("CODING_DIENST_SCHLUESSEL", "falsch")
    assert ca.auftrag_senden(SATZ)[0] == 403
