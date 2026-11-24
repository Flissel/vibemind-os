"""Die Weiche im Schattenbetrieb (Routing-Papier 24.11.2026, Schritt 2).

Gegen ein Attrappen-Modell (Wortbeutel statt Einbettung) und eine Graph-Attrappe: kein echtes
Modell, kein Neo4j. Nur im Betrieb prüfbar: Punkte mit dem echten Modell (Schwelle und Marge
sind Startwerte), Rechenzeit je Satz, Cypher gegen den echten Server.
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import entscheidung_graph as eg  # noqa: E402
from core import weiche as w  # noqa: E402
from core.plan_executor import PlanRecorder  # noqa: E402
from web.routers import introspection as intro  # noqa: E402

WURZEL = Path(__file__).resolve().parents[1]

KARTEN = {
    "version": 1,
    "bereiche": {
        "ideas": {"name": "Ideen", "faehigkeiten": [
            {"id": "bubble_list", "beispiele": ["welche bubbles gibt es"]},
            {"id": "idea_add", "beispiele": ["notiere druckerei anrufen"]},
        ]},
        "sales": {"name": "Sales", "faehigkeiten": [
            {"id": "sales.termine_heute", "beispiele": ["welche termine habe ich heute"]},
        ]},
        "orders": {"name": "Aufträge", "faehigkeiten": [
            {"id": "schedule_list", "beispiele": ["welche aufgaben sind geplant"]},
        ]},
    },
    "nirgends": ["wie wird das wetter morgen"],
}


class Wortbeutel:
    """Attrappen-Modell: Vektor = Wortzählung über einen festen Wortschatz."""

    def __init__(self, warten_s: float = 0.0) -> None:
        self.wortschatz: dict = {}
        self.aufrufe = 0
        self.warten_s = warten_s

    def _vektor(self, text: str) -> list:
        woerter = re.findall(r"\w+", text.lower())
        for wort in woerter:
            self.wortschatz.setdefault(wort, len(self.wortschatz))
        v = [0.0] * 64
        for wort in woerter:
            v[self.wortschatz[wort] % 64] += 1.0
        return v

    def encode(self, text: str) -> list:
        self.aufrufe += 1
        if self.warten_s:
            time.sleep(self.warten_s)
        return self._vektor(text)

    def encode_batch(self, texte: list) -> list:
        return [self._vektor(t) for t in texte]


class GraphAttrappe:
    def __init__(self, fehler: bool = False) -> None:
        self.vorschlaege: list = []
        self.entscheidungen: list = []
        self.fehler = fehler
        self.zeitgrenzen: list = []

    def upsert_weichenvorschlag(self, vorschlag, timeout_s=2.0):
        self.zeitgrenzen.append(timeout_s)
        if self.fehler:
            raise RuntimeError("neo4j weg")
        self.vorschlaege.append(vorschlag)
        return True

    def upsert_entscheidung(self, eintrag, timeout_s=2.0):
        self.entscheidungen.append(eintrag)


@pytest.fixture(autouse=True)
def _schalter(monkeypatch):
    monkeypatch.setenv("WEICHE_SCHATTEN", "1")
    for name in ("WEICHE_SCHWELLE", "WEICHE_MARGE", "WEICHE_BONUS", "WEICHE_ZEIT_S", "WEICHE_KARTEN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(w, "_SCHATTEN", None)
    eg._SCHREIBER.clear()
    yield
    eg._SCHREIBER.clear()


def _weiche(**kw) -> w.Weiche:
    return w.Weiche(embedder=Wortbeutel(**kw), karten=w.Karten(KARTEN))


def _eintrag(**kw) -> dict:
    d = {"trace_id": "t-a0000001", "reiter": None, "weg": "plan-executor", "stufe": "planer",
         "bereich": "ideas", "ok": True}
    d.update(kw)
    return d


# ── Karten ────────────────────────────────────────────────────────────

def test_echte_karten_laden_und_haben_sechs_bereiche():
    karten = w.Karten.laden()
    bereiche = {b for b, _ in karten.zuordnung}
    assert {"ideas", "desktop", "roarboot", "orders", "projects", "sales"} <= bereiche
    assert w.NIRGENDS in bereiche
    ids = [f for _, f in karten.faehigkeiten()]
    assert len(ids) == len(set(ids)), "jede Fähigkeit nur einmal"


def test_echte_karten_kennen_die_festen_wege():
    ids = {f for _, f in w.Karten.laden().faehigkeiten()}
    # Ideen-Abkürzung, Lesesätze, Sales-Sätze, Aufträge, Coding-Stand.
    for fid in ("idea_arrange", "idea_summarize", "idea_whitepaper", "bubble_list", "idea_add",
                "desktop_fenster", "schedule_auftraege", "schedule_list", "coding_stand",
                "sales.termine_heute", "sales.kontakt_finden", "rowboat_spaces"):
        assert fid in ids, fid


def test_echte_karten_ohne_abgeschaltete_faehigkeiten():
    import yaml

    registry = yaml.safe_load((WURZEL / "data" / "capabilities.yaml").read_text(encoding="utf-8"))
    aus = {c["capability"] for c in registry if c.get("enabled") is False}
    ids = {f for _, f in w.Karten.laden().faehigkeiten()}
    assert not (ids & aus), ids & aus


def test_kartenpfad_env_dann_data_dann_karten(tmp_path, monkeypatch):
    data = tmp_path / "data" / "faehigkeitskarten.yaml"
    karten = tmp_path / "karten" / "faehigkeitskarten.yaml"
    monkeypatch.setattr(w, "KARTEN_PFAD", data)
    monkeypatch.setattr(w, "KARTEN_RUECKFALL", karten)
    assert w.karten_pfad() == data  # keine Datei: der data/-Pfad (Fehler beim Laden)
    karten.parent.mkdir()
    karten.write_text("version: 1\n", encoding="utf-8")
    assert w.karten_pfad() == karten  # data/ verdeckt (Volume): Rueckfall
    data.parent.mkdir()
    data.write_text("version: 1\n", encoding="utf-8")
    assert w.karten_pfad() == data
    eigen = tmp_path / "eigen.yaml"
    monkeypatch.setenv("WEICHE_KARTEN", str(eigen))
    assert w.karten_pfad() == eigen  # gesetzt gilt, auch wenn die Datei fehlt


def test_karten_laden_nutzt_den_rueckfall(tmp_path, monkeypatch):
    import yaml

    karten = tmp_path / "karten.yaml"
    karten.write_text(yaml.safe_dump(KARTEN, allow_unicode=True), encoding="utf-8")
    monkeypatch.setattr(w, "KARTEN_PFAD", tmp_path / "fehlt.yaml")
    monkeypatch.setattr(w, "KARTEN_RUECKFALL", karten)
    assert {b for b, _ in w.Karten.laden().zuordnung} == {"ideas", "sales", "orders", w.NIRGENDS}


def test_karten_ohne_bereiche_werden_abgelehnt():
    with pytest.raises(ValueError):
        w.Karten({"version": 1, "bereiche": {}})
    with pytest.raises(ValueError):
        w.Karten({"bereiche": {"ideas": {"faehigkeiten": [{"beispiele": ["x"]}]}}})


# ── Bewerten ──────────────────────────────────────────────────────────

def test_waehlt_den_passenden_bereich():
    v = _weiche().bewerten("welche termine habe ich heute")
    assert v["ergebnis"] == "waehlt"
    assert v["bereich"] == "sales" and v["faehigkeit"] == "sales.termine_heute"
    assert v["regel"] == "schwelle_und_marge"
    assert v["kandidaten"][0]["faehigkeit"] == "sales.termine_heute"
    assert v["bereiche"][0] == {**v["bereiche"][0], "bereich": "sales", "rang": 1, "verworfen": False}
    assert all(b["verworfen"] for b in v["bereiche"][1:])


def test_unter_schwelle_ist_nirgends():
    v = _weiche().bewerten("zebra giraffe nashorn")
    assert v["ergebnis"] == "nirgends" and v["regel"] == "unter_schwelle"
    assert v["bereich"] is None and all(b["verworfen"] for b in v["bereiche"])


def test_naeher_an_nirgends(monkeypatch):
    monkeypatch.setenv("WEICHE_SCHWELLE", "0.1")
    v = _weiche().bewerten("welche termine wie wird das wetter morgen")
    assert v["ergebnis"] == "nirgends" and v["regel"] == "naeher_an_nirgends"


def test_zu_kleine_marge_ist_rueckfrage(monkeypatch):
    monkeypatch.setenv("WEICHE_SCHWELLE", "0.1")
    monkeypatch.setenv("WEICHE_MARGE", "0.9")
    v = _weiche().bewerten("welche termine habe ich heute geplant")
    assert v["ergebnis"] == "rueckfrage" and v["regel"] == "marge_zu_klein"
    assert v["bereich"] is None


def test_reiter_bonus_kippt_knappe_faelle(monkeypatch):
    monkeypatch.setenv("WEICHE_SCHWELLE", "0.1")
    monkeypatch.setenv("WEICHE_MARGE", "0.0")
    satz = "welche aufgaben termine"
    ohne = _weiche().bewerten(satz)
    monkeypatch.setenv("WEICHE_BONUS", "0.3")
    mit = _weiche().bewerten(satz, reiter="sales")
    assert mit["bereich"] == "sales" and mit["bonus"] == 0.3
    assert ohne["bonus"] == 0.0
    assert mit["punkte"] > ohne["punkte"] or ohne["bereich"] != "sales"


def test_vorschlag_ohne_satztext():
    satz = "notiere druckerei anrufen fuer das stadtfest"
    v = _weiche().bewerten(satz)
    assert satz not in json.dumps(v, ensure_ascii=False)
    assert "stadtfest" not in json.dumps(v, ensure_ascii=False)


def test_modell_wird_erst_beim_ersten_satz_geladen(monkeypatch):
    geladen = []

    class Fabrik:
        @staticmethod
        def get():
            geladen.append(1)
            return Wortbeutel()

    # Nie das echte Modul laden (zieht das Modell nach sich): eine Attrappe an seine Stelle.
    monkeypatch.setitem(sys.modules, "core.qdrant_kg", SimpleNamespace(Embedder=Fabrik))
    weiche = w.Weiche(karten=w.Karten(KARTEN))
    assert geladen == []
    weiche.bewerten("welche bubbles gibt es")
    weiche.bewerten("welche bubbles gibt es")
    assert geladen == [1]


@pytest.mark.parametrize("ergebnis, bereich, heute, gleich", [
    ("waehlt", "ideas", "ideas", True),
    ("waehlt", "ideas", "sales", False),
    ("waehlt", "ideas", None, False),
    ("rueckfrage", None, None, True),
    ("nirgends", None, "ideas", False),
    ("zeit", None, "ideas", None),
    ("fehler", None, None, None),
])
def test_gleich_wie_heute(ergebnis, bereich, heute, gleich):
    assert w.gleich_wie_heute({"ergebnis": ergebnis, "bereich": bereich}, heute) is gleich


def test_karten_pruefen_ohne_neue_einbettung():
    weiche = _weiche()
    bericht = w.karten_pruefen(weiche)
    assert bericht["beispiele"] == 5
    assert weiche._embedder.aufrufe == 0
    assert set(bericht["je_bereich"]) == {"ideas", "sales", "orders", w.NIRGENDS}


# ── Schattenbetrieb ───────────────────────────────────────────────────

def _schatten(tmp_path, **kw) -> w.Schatten:
    return w.Schatten(weiche=_weiche(**kw), pfad=tmp_path / "schatten.jsonl")


def test_schatten_schreibt_spur_datei_und_graph(tmp_path):
    schatten = _schatten(tmp_path)
    recorder = PlanRecorder(path=tmp_path / "spur.jsonl")
    recorder.record_spur("t-a0000001", "plan-executor", True)
    graph = GraphAttrappe()
    assert schatten.melden("welche termine habe ich heute", _eintrag(), graph=graph, recorder=recorder)
    assert schatten.warten()
    snap = recorder.get_by_trace("t-a0000001")
    assert snap["weiche"]["bereich"] == "sales"
    assert snap["weiche"]["gleich_wie_heute"] is False
    assert snap["weiche"]["heute"] == {"weg": "plan-executor", "stufe": "planer", "bereich": "ideas", "ok": True}
    zeilen = (tmp_path / "schatten.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(zeilen) == 1 and json.loads(zeilen[0])["trace_id"] == "t-a0000001"
    # Kein Satztext; Fähigkeits-ids wie "sales.termine_heute" sind Kennungen und erlaubt.
    assert "welche termine habe ich heute" not in zeilen[0]
    assert graph.vorschlaege[0]["ergebnis"] == "waehlt"
    assert graph.zeitgrenzen == [eg.zeitgrenze_s()]
    assert schatten.zaehler["bewertet"] == 1
    assert (schatten.zaehler["graph_geschrieben"], schatten.zaehler["graph_nur_spiegel"]) == (1, 0)
    # Die Spur auf Platte bleibt unverändert: keine Patch-Zeile.
    assert "weiche" not in (tmp_path / "spur.jsonl").read_text(encoding="utf-8")


def test_schatten_aendert_den_heutigen_weg_nicht(tmp_path):
    schatten = _schatten(tmp_path)
    eintrag = _eintrag()
    vorher = dict(eintrag)
    schatten.melden("welche termine habe ich heute", eintrag)
    schatten.warten()
    assert eintrag == vorher


def test_melden_blockiert_nicht(tmp_path):
    schatten = _schatten(tmp_path, warten_s=0.3)
    t0 = time.monotonic()
    schatten.melden("welche bubbles gibt es", _eintrag())
    assert time.monotonic() - t0 < 0.1
    assert schatten.warten(3.0)


def test_volle_schlange_wird_gezaehlt(tmp_path):
    schatten = w.Schatten(weiche=_weiche(warten_s=0.2), pfad=tmp_path / "s.jsonl", maxsize=1)
    ergebnisse = [schatten.melden("welche bubbles gibt es", _eintrag(trace_id=f"t-{i}")) for i in range(5)]
    assert ergebnisse.count(False) >= 3
    assert schatten.zaehler["verworfen"] == ergebnisse.count(False)
    schatten.warten(3.0)


def test_zeitgrenze_verwirft_das_ergebnis(tmp_path, monkeypatch):
    monkeypatch.setenv("WEICHE_ZEIT_S", "0.05")
    schatten = _schatten(tmp_path, warten_s=0.15)
    v = schatten.einen_bewerten({"satz": "welche bubbles gibt es", "eintrag": _eintrag(),
                                 "eingang": time.monotonic()})
    assert v["ergebnis"] == "zeit" and v["bereich"] is None and v["kandidaten"] == []
    assert v["gleich_wie_heute"] is None
    assert schatten.zaehler["zeit"] == 1 and schatten.zaehler["bewertet"] == 0


def test_veraltete_saetze_werden_uebersprungen(tmp_path):
    schatten = _schatten(tmp_path)
    v = schatten.einen_bewerten({"satz": "welche bubbles gibt es", "eintrag": _eintrag(),
                                 "eingang": time.monotonic() - w.VERALTET_S - 1})
    assert v is None and schatten.zaehler["veraltet"] == 1


def test_fehler_im_modell_wird_gezaehlt(tmp_path):
    class Kaputt(Wortbeutel):
        def encode(self, text):
            raise RuntimeError("modell weg")

    schatten = w.Schatten(weiche=w.Weiche(embedder=Kaputt(), karten=w.Karten(KARTEN)),
                          pfad=tmp_path / "s.jsonl")
    v = schatten.einen_bewerten({"satz": "x", "eintrag": _eintrag(), "eingang": time.monotonic()})
    assert v["ergebnis"] == "fehler" and v["regel"] == "RuntimeError"
    assert schatten.zaehler["fehler"] == 1


def test_graph_fehler_wird_gezaehlt(tmp_path):
    schatten = _schatten(tmp_path)
    schatten.einen_bewerten({"satz": "welche bubbles gibt es", "eintrag": _eintrag(),
                             "graph": GraphAttrappe(fehler=True), "eingang": time.monotonic()})
    assert schatten.zaehler["graph_fehler"] == 1
    assert (tmp_path / "schatten.jsonl").exists()


def test_graph_ohne_verbindung_zaehlt_nur_spiegel(tmp_path, monkeypatch):
    from core import decision_graph as dgmod

    monkeypatch.setattr(dgmod, "ENABLED", False)
    graph = dgmod.DecisionGraph()
    schatten = _schatten(tmp_path)
    schatten.einen_bewerten({"satz": "welche bubbles gibt es", "eintrag": _eintrag(),
                             "graph": graph, "eingang": time.monotonic()})
    assert (schatten.zaehler["graph_geschrieben"], schatten.zaehler["graph_nur_spiegel"]) == (0, 1)
    assert len(graph._mem_weiche) == 1


def test_schalter_aus(tmp_path, monkeypatch):
    monkeypatch.setenv("WEICHE_SCHATTEN", "0")
    assert w.melden("welche bubbles gibt es", _eintrag()) is False
    assert w._SCHATTEN is None


def test_ohne_satz_oder_trace_nichts(tmp_path):
    schatten = _schatten(tmp_path)
    assert schatten.melden("", _eintrag()) is False
    assert schatten.melden(None, _eintrag()) is False
    assert schatten.melden("welche bubbles gibt es", {"trace_id": ""}) is False
    assert schatten.zaehler["gemeldet"] == 0


# ── Bericht ───────────────────────────────────────────────────────────

def test_bericht_zaehlt_abweichungen(tmp_path):
    schatten = _schatten(tmp_path)
    for i, (satz, heute) in enumerate([
        ("welche termine habe ich heute", "sales"),
        ("welche termine habe ich heute", "ideas"),
        ("zebra giraffe nashorn", None),
    ]):
        schatten.einen_bewerten({"satz": satz, "eintrag": _eintrag(trace_id=f"t-{i}", bereich=heute),
                                 "eingang": time.monotonic()})
    b = w.bericht(tmp_path / "schatten.jsonl")
    assert b["saetze"] == 3
    assert b["vergleich"] == {"gleich": 2, "anders": 1}
    assert b["abweichungen_paare"] == {"ideas -> sales": 1}
    assert b["letzte_abweichungen"][0]["trace_id"] == "t-1"
    text = w.bericht_text(b)
    assert "ideas -> sales" in text and "termine" not in text


def test_bericht_ohne_datei(tmp_path):
    b = w.bericht(tmp_path / "fehlt.jsonl")
    assert b["saetze"] == 0 and b["vergleich"] == {}


# ── Endpunkt ──────────────────────────────────────────────────────────

def _app(tmp_path, monkeypatch, antwort: dict):
    app = FastAPI()
    app.include_router(intro.router)
    recorder = PlanRecorder(path=tmp_path / "spur.jsonl")
    graph = GraphAttrappe()
    app.state.decision_graph = graph
    app.state.plan_executor = SimpleNamespace(recorder=recorder)

    async def _antwort(request):
        recorder.record_spur(antwort["trace_id"], antwort.get("weg", "plan-executor"), True)
        return JSONResponse(antwort)

    monkeypatch.setattr(intro, "_multihop_execute", _antwort)
    schatten = _schatten(tmp_path)
    monkeypatch.setattr(w, "_SCHATTEN", schatten)
    monkeypatch.setattr(w, "SCHATTEN_PFAD", tmp_path / "schatten.jsonl")
    return TestClient(app), graph, recorder, schatten


def test_endpunkt_meldet_satz_an_die_weiche(tmp_path, monkeypatch):
    client, graph, recorder, schatten = _app(
        tmp_path, monkeypatch, {"ok": True, "trace_id": "t-b0b1e001", "space": "Ideen"})
    r = client.post("/api/multihop/execute", json={"intent": "welche termine habe ich heute",
                                                   "context": {"space": "Sales"}})
    assert r.status_code == 200
    assert "weiche" not in r.json(), "die Antwort bleibt wie heute"
    assert schatten.warten(3.0)
    assert eg.schreiber_fuer(graph).warten(3.0)
    v = recorder.get_by_trace("t-b0b1e001")["weiche"]
    assert v["reiter"] == "sales" and v["bereich"] == "sales"
    assert v["heute"]["bereich"] == "ideas" and v["gleich_wie_heute"] is False
    assert graph.vorschlaege and graph.entscheidungen


def test_endpunkt_weiche_aus_laesst_graph_an(tmp_path, monkeypatch):
    client, graph, recorder, schatten = _app(
        tmp_path, monkeypatch, {"ok": True, "trace_id": "t-b0b1e002", "space": "Ideen"})
    monkeypatch.setenv("WEICHE_SCHATTEN", "0")
    client.post("/api/multihop/execute", json={"intent": "welche bubbles gibt es"})
    assert eg.schreiber_fuer(graph).warten(3.0)
    assert graph.entscheidungen and not graph.vorschlaege
    assert schatten.zaehler["gemeldet"] == 0


def test_endpunkt_kaputte_weiche_haelt_satz_nicht_auf(tmp_path, monkeypatch):
    client, graph, recorder, schatten = _app(
        tmp_path, monkeypatch, {"ok": True, "trace_id": "t-b0b1e003"})

    def kaputt(*a, **k):
        raise RuntimeError("weiche weg")

    monkeypatch.setattr(w, "melden", kaputt)
    r = client.post("/api/multihop/execute", json={"intent": "welche bubbles gibt es"})
    assert r.status_code == 200 and r.json()["trace_id"] == "t-b0b1e003"


def test_vergleich_endpunkt(tmp_path, monkeypatch):
    client, graph, recorder, schatten = _app(
        tmp_path, monkeypatch, {"ok": True, "trace_id": "t-b0b1e004", "space": "Ideen"})
    client.post("/api/multihop/execute", json={"intent": "welche termine habe ich heute"})
    schatten.warten(3.0)
    r = client.get("/api/weiche/vergleich")
    assert r.status_code == 200
    d = r.json()
    assert d["saetze"] == 1 and d["vergleich"] == {"anders": 1}
    assert d["zaehler"]["gemeldet"] == 1
    assert "welche termine habe ich heute" not in r.text
