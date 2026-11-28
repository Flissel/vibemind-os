"""Blue Rose Stufe 1 (core/blue_rose.py, web/routers/blue_rose.py). Nur erfundene Daten."""
import asyncio

import pytest

from core import blue_rose as br

JETZT = 1_800_000_000.0  # fester Zeitpunkt, 00:00 UTC + 8 h


def _zeile(vor_s, **mehr):
    zeile = {"ts": JETZT - vor_s, "laenge": 20, "weg": "plan", "ok": True, "dauer_ms": 400,
             "bereich": "bereich:ideas", "trace_id": f"abc{vor_s}"}
    zeile.update(mehr)
    return zeile


def test_zahlen_zaehlt_je_bereich_und_stunde():
    zeilen = [
        _zeile(10),
        _zeile(20, bereich="bereich:sales", weg="nicht-verstanden", ok=None),
        _zeile(30, bereich="sales", ok=False, dauer_ms=1000),
        _zeile(3700, trace_id="sprache-1"),
    ]
    out = br.zahlen(zeilen, jetzt=JETZT, tage=14)
    assert out["saetze"] == 4
    assert out["nicht_verstanden"] == 1 and out["misslungen"] == 1
    assert out["gesprochen"] == 1 and out["getippt"] == 3
    assert sum(out["stunden_utc"]) == 4
    nach_id = {b["id"]: b for b in out["bereiche"]}
    assert nach_id["sales"] == {"id": "sales", "saetze": 2, "nicht_verstanden": 1,
                                "misslungen": 1, "dauer_ms_median": 700}
    assert nach_id["ideas"]["saetze"] == 2
    assert out["dauer_ms_median"] == 400


def test_zahlen_laesst_leere_saetze_doppelte_und_alte_weg():
    zeilen = [
        _zeile(10, laenge=0),            # Seite der App liest, kein Satz des Nutzers
        _zeile(20, laenge=None),
        _zeile(30, trace_id="gleich"),
        _zeile(31, trace_id="gleich"),   # zweite Zeile desselben Satzes (zwei Bereiche)
        _zeile(15 * 86400),              # aelter als 14 Tage
        "kaputt",
    ]
    out = br.zahlen(zeilen, jetzt=JETZT, tage=14)
    assert out["saetze"] == 1 and out["aktive_tage"] == 1


def test_zahlen_traegt_keinen_text_weiter():
    out = br.zahlen([_zeile(10, text="Erfundener Satz")], jetzt=JETZT)
    assert "Erfundener" not in repr(out)


def test_zahlen_lesen_ohne_graph_und_mit_fehler():
    assert br.zahlen_lesen(None) == {"ok": False, "grund": "graph_fehlt"}

    class Kaputt:
        def entscheidungen_zahlen(self, seit):
            raise RuntimeError("erfundener Fehler")

    assert br.zahlen_lesen(Kaputt()) == {"ok": False, "grund": "graph_fehler"}

    class Graph:
        def entscheidungen_zahlen(self, seit):
            assert seit == JETZT - 7 * 86400
            return {"quelle": "spiegel", "zeilen": [_zeile(10)]}

    out = br.zahlen_lesen(Graph(), tage=7, jetzt=JETZT)
    assert out["ok"] is True and out["quelle"] == "spiegel" and out["saetze"] == 1


class Tabelle:
    """Erfundene Supabase: merkt Zeilen je Tabelle, kennt GET/POST/DELETE wie PostgREST."""

    def __init__(self, fehlt=()):
        self.zeilen = {}
        self.fehlt = set(fehlt)
        self.stats = {"last_error": None}
        self.nr = 0

    async def _request(self, method, path, *, params=None, json=None, prefer=None):
        tabelle = path.strip("/")
        params = params or {}
        if tabelle in self.fehlt:
            self.stats["last_error"] = "HTTP 404: PGRST205"
            return None
        zeilen = self.zeilen.setdefault(tabelle, [])

        def passt(zeile):
            wert = params.get("id")
            return wert is None or wert == "not.is.null" or wert == f"eq.{zeile['id']}"

        if method == "GET":
            return [z for z in zeilen if passt(z)][: int(params.get("limit", 1000))]
        if method == "POST":
            if tabelle == br.TABELLE_EINSTELLUNGEN:
                zeilen[:] = [z for z in zeilen if z["schluessel"] != json["schluessel"]]
                zeilen.append(dict(json, id=json["schluessel"]))
                return [json]
            self.nr += 1
            neu = dict(json, id=f"id-{self.nr}", angelegt="2026-01-01T00:00:00+00:00")
            zeilen.insert(0, neu)
            return [neu]
        if method == "DELETE":
            zeilen[:] = [z for z in zeilen if not passt(z)]
            return True
        raise AssertionError(method)


def _lauf(koroutine):
    return asyncio.run(koroutine)


def test_befinden_eintragen_lesen_loeschen():
    t = Tabelle()
    out = _lauf(br.befinden_eintragen(t, "focused", 7, 10))
    assert out["ok"] is True and out["eintrag"]["tageszeit"] == "morning"
    assert len(_lauf(br.befinden_liste(t))["eintraege"]) == 1
    assert _lauf(br.loeschen(t, "befinden", out["eintrag"]["id"])) == {"ok": True}
    assert _lauf(br.befinden_liste(t))["eintraege"] == []


@pytest.mark.parametrize("stimmung,energie,stunde", [
    ("gluecklich", 5, 10), ("focused", 0, 10), ("focused", 11, 10), ("focused", True, 10),
    ("focused", 5, 24), ("focused", "5", 10), (None, 5, 10),
])
def test_befinden_lehnt_fremde_werte_ab(stimmung, energie, stunde):
    t = Tabelle()
    assert _lauf(br.befinden_eintragen(t, stimmung, energie, stunde)) == {
        "ok": False, "grund": "eingabe_ungueltig"}
    assert t.zeilen == {}


def test_fehlende_tabelle_wird_benannt_und_analyse_gilt_als_aus():
    t = Tabelle(fehlt=(br.TABELLE_BEFINDEN, br.TABELLE_EINSTELLUNGEN))
    assert _lauf(br.befinden_eintragen(t, "calm", 5, 9)) == {"ok": False, "grund": "tabelle_fehlt"}
    stand = _lauf(br.einstellungen_lesen(t))
    assert stand == {"ok": False, "grund": "tabelle_fehlt", "analyse": False}


def test_einstellung_vorgabe_an_und_umschaltbar():
    t = Tabelle()
    assert _lauf(br.einstellungen_lesen(t)) == {"ok": True, "analyse": True}
    assert _lauf(br.einstellung_setzen(t, "analyse", False)) == {"ok": True, "analyse": False}
    assert _lauf(br.einstellungen_lesen(t))["analyse"] is False
    assert _lauf(br.einstellung_setzen(t, "analyse", "nein"))["grund"] == "eingabe_ungueltig"
    assert _lauf(br.einstellung_setzen(t, "anderes", True))["grund"] == "eingabe_ungueltig"


def test_bestand_zaehlt_und_alles_loeschen_leert_jede_art():
    t = Tabelle()
    _lauf(br.befinden_eintragen(t, "tired", 3, 22))
    t.zeilen["flowzen_activity"] = [{"id": "a"}, {"id": "b"}]
    assert _lauf(br.bestand(t)) == {"befinden": 1, "alt_befinden": 0, "alt_aktivitaet": 2,
                                    "alt_tagebuch": 0}
    assert _lauf(br.loeschen(t, "alles"))["ok"] is True
    assert set(_lauf(br.bestand(t)).values()) == {0}


def test_loeschen_lehnt_unbekannte_art_und_schraege_kennung_ab():
    t = Tabelle()
    assert _lauf(br.loeschen(t, "ideas"))["grund"] == "art_unbekannt"
    assert _lauf(br.loeschen(t, "befinden", "x,or=(id.not.is.null)"))["grund"] == "kennung_ungueltig"
    assert _lauf(br.loeschen(t, "alt_aktivitaet", "a"))["grund"] == "kennung_ungueltig"


def test_empfehlung_nur_mit_frischem_eintrag():
    eintrag = {"stimmung": "focused"}
    assert br.empfehlung(None, 10, alter_s=None)["grund"] == "befinden_fehlt"
    assert br.empfehlung(eintrag, 10, alter_s=7 * 3600)["grund"] == "befinden_fehlt"
    out = br.empfehlung(eintrag, 10, alter_s=60)
    assert out == {"ok": True, "kategorie": "deep_work", "name": "Konzentrierte Arbeit",
                   "tageszeit": "morning"}
    assert br.empfehlung({"stimmung": "tired"}, 22, alter_s=60)["kategorie"] == "rest"


def test_endpunkte_nur_fuer_die_app_am_eigenen_rechner(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from web.routers import blue_rose as router_modul

    app = FastAPI()
    app.include_router(router_modul.router)
    t = Tabelle()
    monkeypatch.setattr(br, "tabelle_aus_umgebung", lambda: t)

    class Graph:
        def entscheidungen_zahlen(self, seit):
            return {"quelle": "spiegel", "zeilen": []}

    app.state.decision_graph = Graph()
    lokal = TestClient(app, base_url="http://127.0.0.1:5000")
    fremd = TestClient(app, base_url="http://beispiel.invalid")

    assert fremd.get("/api/blue_rose/uebersicht").status_code == 403
    assert lokal.get("/api/blue_rose/uebersicht", headers={"Origin": "http://x.invalid"}).status_code == 403
    assert lokal.get("/api/blue_rose/uebersicht",
                     headers={"Sec-Fetch-Site": "same-origin"}).status_code == 403
    assert lokal.post("/api/blue_rose/loeschen", json={"art": "alles"},
                      headers={"Origin": "http://x.invalid"}).status_code == 403

    assert lokal.post("/api/blue_rose/befinden",
                      json={"stimmung": "calm", "energie": 6, "stunde": 15}).json()["ok"] is True
    seite = lokal.get("/api/blue_rose/uebersicht?stunde=15").json()
    assert seite["ok"] is True and seite["zahlen"]["ok"] is True
    assert seite["bestand"]["befinden"] == 1
    assert seite["empfehlung"]["grund"] == "befinden_fehlt"  # Testzeile ist von 2026-01-01

    assert lokal.post("/api/blue_rose/einstellungen", json={"analyse": False}).json()["ok"] is True
    assert lokal.get("/api/blue_rose/uebersicht").json()["zahlen"] == {"ok": False, "grund": "analyse_aus"}


def test_ohne_service_schluessel_sagt_die_antwort_das(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from web.routers import blue_rose as router_modul

    app = FastAPI()
    app.include_router(router_modul.router)
    monkeypatch.setattr(br, "tabelle_aus_umgebung", lambda: None)
    lokal = TestClient(app, base_url="http://127.0.0.1:5000")
    assert lokal.get("/api/blue_rose/uebersicht").json() == {"ok": False, "grund": "kein_schluessel"}


def test_nutzerprofil_nach_rowboat_ist_aus_bis_der_schalter_gesetzt_ist(monkeypatch):
    from core import brain_chat

    monkeypatch.delenv("BRAIN_NUTZERPROFIL_ROWBOAT", raising=False)
    assert brain_chat.nutzerprofil_nach_rowboat() is False
    monkeypatch.setenv("BRAIN_NUTZERPROFIL_ROWBOAT", "1")
    assert brain_chat.nutzerprofil_nach_rowboat() is True
