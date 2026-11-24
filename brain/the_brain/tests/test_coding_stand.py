"""Feste Lesesaetze des Project Space (core/coding_stand.py, core/feste_lesesaetze.py).

Drei Wege ohne Modell: Stand, letzte Laeufe, Ergebnis. Geprueft werden die Satzwahl, die Saetze,
das Lesen gegen einen kleinen Attrappen-Dienst und der Weg am echten Router. Alle Titel sind
erfunden.
"""
from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import coding_stand as cs  # noqa: E402
from core import verstehen as v  # noqa: E402
from web.routers.introspection import router  # noqa: E402

LAUFEND = {"auftrag": "c-1", "titel": "Aufgabenliste", "zustand": "laeuft", "stufe": "bau",
           "kurz": {"stufe": "bau", "paket_nr": 2, "pakete_gesamt": 4}}
FERTIG = {"auftrag": "c-2", "titel": "Leseliste", "zustand": "fertig", "dauer_s": 134}
ERGEBNIS = {"pakete": [{}, {}, {}], "dateien": ["a", "b", "c", "d"], "pruefung": {"bestanden": True},
            "start": "npm start"}


# ── Welcher Satz geht wohin ───────────────────────────────────────────────

@pytest.mark.parametrize("satz,space,faehigkeit", [
    ("Wie ist der Stand im Project Space?", "projects", "coding_stand"),
    ("Wie ist der Stand im Project Space?", "ideas", "coding_stand"),
    ("Wie ist der Stand im Project Space?", None, "coding_stand"),
    ("Was baut der Project Space gerade?", "video", "coding_stand"),
    ("Wie ist der Code-Status?", None, "coding_stand"),
    ("Ist der letzte Lauf fertig?", "projects", "coding_stand"),
    ("Ist der letzte Lauf fertig?", None, "coding_stand"),
    ("Läuft gerade noch ein Bau?", "projects", "coding_stand"),
    ("Wie lange hat der letzte Lauf gedauert?", "projects", "coding_stand"),
    ("Wie ist der Stand?", "projects", "coding_stand"),
    ("Zeig mir die letzten Läufe", "projects", "coding_laeufe"),
    ("Zeig mir die letzten Läufe", None, "coding_laeufe"),
    ("Welche Läufe gibt es?", "projects", "coding_laeufe"),
    ("Was wurde zuletzt gebaut?", "projects", "coding_laeufe"),
    ("Welche Projekte gibt es?", "projects", "coding_laeufe"),
    ("Welche Projekte gibt es im Project Space?", "sales", "coding_laeufe"),
    ("Zeig mir das Ergebnis des letzten Laufs", "projects", "coding_ergebnis"),
    ("Wie ist der letzte Lauf ausgegangen?", "projects", "coding_ergebnis"),
    ("Warum ist der letzte Lauf gescheitert?", "projects", "coding_ergebnis"),
    ("Wie startet man die zuletzt gebaute App?", "projects", "coding_ergebnis"),
    ("Wie starte ich die App?", "projects", "coding_ergebnis"),
])
def test_satz_bindet_den_festen_weg(satz, space, faehigkeit) -> None:
    assert v.feste_lesefrage(satz, space) == {"capability": faehigkeit}


@pytest.mark.parametrize("satz,space", [
    ("Wie ist der Stand?", None),                    # ohne Reiter zu allgemein
    ("Wie ist der Stand?", "sales"),
    ("Welche Projekte gibt es?", None),              # Projekte gibt es auch anderswo
    ("Welche Projekte gibt es?", "ideas"),
    ("Ist der letzte Lauf fertig?", "sales"),        # Kurzform nur im eigenen Reiter oder ohne
    ("Zeig mir die letzten Läufe", "ideas"),
    ("Bau mir eine Leseliste", "projects"),          # Schreiben ist kein Lesesatz
    ("Brich den letzten Lauf ab", "projects"),
])
def test_kein_fester_weg(satz, space) -> None:
    assert v.feste_lesefrage(satz, space) is None


def test_beispiele_laufen_selbst_und_faehigkeiten_gehoeren_zum_project_space() -> None:
    for satz in v.SPACES["projects"].beispiele:
        assert v.feste_lesefrage(satz, "projects")
    for faehigkeit in ("coding_stand", "coding_laeufe", "coding_ergebnis"):
        assert v.faehigkeit_space(faehigkeit) == "projects"
    assert v.space_aus_kontext({"space": "coding"}) == "projects"


def test_schritte_melden_den_coding_space() -> None:
    """Die App springt nach dem Space des Schritts (core/step_space.py)."""
    from core.space_contract import normalize_space_id, load_space_contract
    from core.step_space import space_for_step

    vertrag = load_space_contract()
    for ziel in ("stand_lesen", "laeufe_lesen", "ergebnis_lesen"):
        assert space_for_step("coding_x", f"direct:core.coding_stand:{ziel}", vertrag) == \
            normalize_space_id("coding", vertrag)


def test_faehigkeiten_stehen_im_katalog_und_lesen_nur() -> None:
    import yaml
    daten = Path(__file__).resolve().parents[1] / "data"
    katalog = {e["capability"]: e for e in yaml.safe_load((daten / "capabilities.yaml").read_text(encoding="utf-8"))}
    wirkung = yaml.safe_load((daten / "capability_effects.yaml").read_text(encoding="utf-8"))
    wirkung = wirkung.get("effects", wirkung) if isinstance(wirkung, dict) else {}
    for faehigkeit, ziel in (("coding_stand", "stand_lesen"), ("coding_laeufe", "laeufe_lesen"),
                             ("coding_ergebnis", "ergebnis_lesen")):
        assert katalog[faehigkeit]["execution_target"] == f"direct:core.coding_stand:{ziel}"
        assert katalog[faehigkeit]["space"] == "projects"
        assert wirkung[faehigkeit] == "read"
        assert faehigkeit in v.FESTE_ANTWORTEN and faehigkeit in v.FESTE_FEHLERSAETZE


# ── Saetze ────────────────────────────────────────────────────────────────

def test_stand_saetze() -> None:
    assert cs.stand_satz(LAUFEND) == "„Aufgabenliste“ läuft gerade: Bau, Paket 2 von 4."
    assert cs.stand_satz(FERTIG) == "„Leseliste“ ist fertig nach 2 Minuten."
    assert cs.stand_satz({"titel": "X", "zustand": "fehlgeschlagen", "fehler": "Paket 2: Test rot."}) == (
        "„X“ ist gescheitert. Paket 2: Test rot.")
    assert cs.stand_satz({"titel": "", "zustand": "wartet"}) == "„Ohne Titel“ ist angelegt und wartet auf den Start."
    assert cs.stand_satz({"titel": "X", "zustand": "abgebrochen"}) == "„X“ wurde abgebrochen."


def test_liste_satz() -> None:
    liste = {"anzahl": 7, "auftraege": [{"titel": "A", "zustand": "fertig"}, {"titel": "B", "zustand": "laeuft"}]}
    assert cs.liste_satz(liste) == "Im Project Space gibt es 7 Läufe, die neuesten: „A“ (fertig), „B“ (läuft)."
    assert cs.liste_satz({"anzahl": 1, "auftraege": [{"titel": "A", "zustand": "fehlgeschlagen"}]}) == (
        "Im Project Space gibt es einen Lauf: „A“ (gescheitert).")
    assert cs.liste_satz({"auftraege": []}) == cs.KEIN_LAUF


def test_ergebnis_satz() -> None:
    assert cs.ergebnis_satz(FERTIG, ERGEBNIS) == (
        "„Leseliste“ ist fertig nach 2 Minuten. Gebaut wurden 3 Pakete mit 4 Dateien. "
        "Alle Prüfungen sind bestanden. Gestartet wird die App mit „npm start“ in ihrem Ordner.")
    assert cs.ergebnis_satz(LAUFEND, ERGEBNIS) == cs.stand_satz(LAUFEND)
    assert cs.ergebnis_satz(FERTIG, None) == cs.stand_satz(FERTIG)


def test_antworten_und_fehlersaetze() -> None:
    assert cs.antwort_stand({"success": True, "daten": LAUFEND}) == cs.stand_satz(LAUFEND)
    assert cs.antwort_stand({"result": {"success": True, "kein_lauf": True}}) == cs.KEIN_LAUF
    assert cs.antwort_laeufe({"success": True, "kein_lauf": True}) == cs.KEIN_LAUF
    assert cs.antwort_ergebnis({"success": True, "daten": FERTIG, "ergebnis": ERGEBNIS}).endswith("„npm start“ in ihrem Ordner.")
    assert cs.antwort_stand({"success": False, "error": "nicht_erreichbar"}) is None
    assert cs.antwort_stand("roh") is None
    assert cs.fehlersatz({"success": False, "error": "nicht_erreichbar"}) == cs.NICHT_ERREICHBAR
    assert cs.fehlersatz({"success": False, "error": "dienst_fehler"}).startswith("Der Dienst des Project Space meldet")


# ── Lesen gegen einen Attrappen-Dienst ───────────────────────────────────

class _Dienst(BaseHTTPRequestHandler):
    antworten: dict = {}
    pfade: list = []

    def do_GET(self):  # noqa: N802
        teile = urlparse(self.path)
        self.pfade.append((teile.path, parse_qs(teile.query)))
        status, daten = self.antworten.get(teile.path, (404, {"error": "kein_lauf"}))
        roh = json.dumps(daten).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(roh)))
        self.end_headers()
        self.wfile.write(roh)

    def log_message(self, *a):
        pass


@pytest.fixture
def dienst(monkeypatch):
    _Dienst.antworten, _Dienst.pfade = {}, []
    server = HTTPServer(("127.0.0.1", 0), _Dienst)
    faden = threading.Thread(target=server.serve_forever, daemon=True)
    faden.start()
    monkeypatch.setenv("CODING_DIENST_URL", f"http://127.0.0.1:{server.server_port}")
    yield _Dienst
    server.shutdown()
    server.server_close()


def test_stand_lesen(dienst) -> None:
    dienst.antworten = {"/api/stand": (200, LAUFEND)}
    assert cs.stand_lesen() == {"success": True, "daten": LAUFEND}


def test_ohne_lauf_ist_kein_fehler(dienst) -> None:
    assert cs.stand_lesen() == {"success": True, "kein_lauf": True}
    assert cs.antwort_stand(cs.stand_lesen()) == cs.KEIN_LAUF


def test_laeufe_lesen_mit_grenze(dienst) -> None:
    dienst.antworten = {"/api/liste": (200, {"anzahl": 0, "auftraege": []})}
    assert cs.laeufe_lesen()["success"] is True
    assert dienst.pfade[-1] == ("/api/liste", {"grenze": ["5"]})


def test_ergebnis_nur_fuer_den_fertigen_lauf(dienst) -> None:
    dienst.antworten = {"/api/stand": (200, FERTIG), "/api/ergebnis": (200, ERGEBNIS)}
    ergebnis = cs.ergebnis_lesen()
    assert ergebnis["ergebnis"] == ERGEBNIS
    assert dienst.pfade[-1] == ("/api/ergebnis", {"auftrag": ["c-2"]})
    dienst.antworten = {"/api/stand": (200, LAUFEND), "/api/ergebnis": (200, ERGEBNIS)}
    dienst.pfade.clear()
    assert "ergebnis" not in cs.ergebnis_lesen()
    assert [p for p, _ in dienst.pfade] == ["/api/stand"]


def test_dienstfehler(dienst) -> None:
    dienst.antworten = {"/api/stand": (500, {"error": "kaputt"})}
    assert cs.stand_lesen() == {"success": False, "error": "dienst_fehler", "status": 500}


def test_dienst_aus(monkeypatch) -> None:
    monkeypatch.setenv("CODING_DIENST_URL", "http://127.0.0.1:9")
    monkeypatch.setattr(cs, "WARTEN_S", 0.5)
    assert cs.stand_lesen() == {"success": False, "error": "nicht_erreichbar"}


# ── Ende-zu-Ende am Router (Planer und Ausfuehrer als Attrappen) ─────────

class _Recorder:
    def __init__(self):
        self.finals = []

    def attach_final(self, plan_id, text, *a, **k):
        self.finals.append(text)

    def record_lite(self, *a, **k):
        pass

    def append_stage(self, *a, **k):
        pass


class _Ausfuehrer:
    def __init__(self, ergebnis):
        self.recorder = _Recorder()
        self.plaene = []
        self.ergebnis = ergebnis

    def execute(self, plan, **kw):
        self.plaene.append(plan)
        ok = self.ergebnis.get("success") is True
        return {"ok": ok, "plan": plan.to_dict(), "executed": {"s1": {"ok": ok, "result": self.ergebnis}},
                "state": {}, "elapsed_s": 0.01, "replans": 0}


class _Planer:
    async def aplan(self, intent, context=None):
        raise AssertionError("kein Planer fuer feste Lesesaetze")


class _Klassierer:
    def classify(self, intent):
        return {"level": "medium", "score": 0.44, "method": "qwen-cosine", "reason": "x"}


def _frage(monkeypatch, ausfuehrer, satz, space):
    import core.difficulty_router as dr

    monkeypatch.setattr(dr, "get_router", lambda: _Klassierer())
    monkeypatch.setenv("SOM_NOPLAN_FALLBACK", "0")
    monkeypatch.setenv("MULTI_VERB_ROUTING", "0")
    app = FastAPI()
    app.include_router(router)
    app.state.plan_executor = ausfuehrer
    app.state.multihop_planner = _Planer()
    app.state.final_synthesizer = None
    return TestClient(app).post("/api/multihop/execute", json={"intent": satz, "context": {"space": space}}).json()


def test_router_stand(monkeypatch) -> None:
    ausfuehrer = _Ausfuehrer({"success": True, "daten": LAUFEND})
    body = _frage(monkeypatch, ausfuehrer, "Wie ist der Stand im Project Space?", "projects")
    assert [h.capability for h in ausfuehrer.plaene[0].hops] == ["coding_stand"]
    assert (body["ok"], body["final_text"]) == (True, "„Aufgabenliste“ läuft gerade: Bau, Paket 2 von 4.")


def test_router_ergebnis(monkeypatch) -> None:
    ausfuehrer = _Ausfuehrer({"success": True, "daten": FERTIG, "ergebnis": ERGEBNIS})
    body = _frage(monkeypatch, ausfuehrer, "Zeig mir das Ergebnis des letzten Laufs", "coding")
    assert [h.capability for h in ausfuehrer.plaene[0].hops] == ["coding_ergebnis"]
    assert body["ok"] is True and "Alle Prüfungen sind bestanden." in body["final_text"]


def test_router_dienst_aus_sagt_genau_das(monkeypatch) -> None:
    ausfuehrer = _Ausfuehrer({"success": False, "error": "nicht_erreichbar"})
    body = _frage(monkeypatch, ausfuehrer, "Zeig mir die letzten Läufe", "projects")
    assert body["ok"] is False and body["final_text"] == cs.NICHT_ERREICHBAR
