"""Fester Lesesatz "Welche Aufträge laufen gerade?" (core/auftraege_lesen.py).

Ende-zu-Ende am echten Router (/api/multihop/execute) mit Attrappen fuer Planer und Ausfuehrer,
dazu die Leselogik gegen eine Attrappe der Tabelle. Alle Auftraege sind erfunden.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import auftraege_lesen as al  # noqa: E402
from core import verstehen as v  # noqa: E402
from web.routers.introspection import router  # noqa: E402

JETZT = datetime(2026, 11, 24, 9, 0, 0, tzinfo=timezone.utc)
SCHWACH = {"level": "medium", "score": 0.44, "method": "qwen-cosine", "reason": "x"}


# ── Welcher Satz geht wohin ───────────────────────────────────────────────

@pytest.mark.parametrize("satz", [
    "Welche Aufträge laufen gerade?", "welche auftraege laufen", "Welche Aufträge an Agenten laufen?",
    "Welche Aufträge warten noch?", "Welche Aufträge sind offen?", "Laufen gerade Aufträge?",
    "Läuft ein Auftrag?", "Was machen die Agenten gerade?", "Woran arbeiten meine Agenten?",
    "Zeig mir die Aufträge an Agenten",
])
@pytest.mark.parametrize("space", ["orders", "ideas", "sales", None])
def test_satz_bindet_den_festen_weg(satz, space) -> None:
    assert v.feste_lesefrage(satz, space) == {"capability": "schedule_auftraege"}


@pytest.mark.parametrize("satz", ["Zeig meine Aufträge", "Welche Aufträge?", "Zeig die Aufträge", "Was ist geplant?"])
def test_geplante_aufgaben_bleiben_beim_alten_weg(satz) -> None:
    assert v.feste_lesefrage(satz, "orders") == {"capability": "schedule_list"}


@pytest.mark.parametrize("satz", [
    "Erstelle einen Auftrag für die Agenten", "Welche Agenten gibt es?", "Was machen die Agenten falsch?",
    "Stoppe alle Aufträge",
])
def test_kein_fester_weg(satz) -> None:
    assert v.feste_lesefrage(satz, "orders") != {"capability": "schedule_auftraege"}


def test_springt_in_den_reiter_auftraege() -> None:
    from core.beispielsaetze import saetze_fuer
    from core.step_space import space_for_step

    assert v.plan_spaces(["schedule_auftraege"]) == {"orders"}  # Dashboard/clawport zaehlt als Aufträge
    assert space_for_step("schedule_auftraege", "direct:core.auftraege_lesen:auftraege_lesen") == "schedule"
    assert "Welche Aufträge laufen gerade?" in saetze_fuer("orders")


def test_faehigkeit_steht_im_katalog_im_verzeichnis_und_im_navigator() -> None:
    import yaml
    wurzel = Path(__file__).resolve().parents[3]
    katalog = {e["capability"]: e for e in yaml.safe_load(
        (wurzel / "brain/the_brain/data/capabilities.yaml").read_text(encoding="utf-8"))}
    assert katalog["schedule_auftraege"]["execution_target"] == "direct:core.auftraege_lesen:auftraege_lesen"
    effekte = yaml.safe_load((wurzel / "brain/the_brain/data/capability_effects.yaml").read_text(encoding="utf-8"))
    assert "schedule_auftraege: read" in (wurzel / "brain/the_brain/data/capability_effects.yaml").read_text(
        encoding="utf-8"), effekte
    register = yaml.safe_load((wurzel / "config/space_agent_registry.yml").read_text(encoding="utf-8"))["spaces"]
    assert register["schedule"]["events"]["schedule.auftraege"]["tool"] == \
        "direct:core.auftraege_lesen:auftraege_lesen"
    assert "schedule.auftraege" in (wurzel / "spaces/_navigator/registry.py").read_text(encoding="utf-8")


# ── Lesen ─────────────────────────────────────────────────────────────────

class _Tabelle:
    def __init__(self, zeilen=None, fehler=None):
        self.zeilen, self.fehler, self.limits = zeilen or [], fehler, []

    def zuletzt(self, limit):
        self.limits.append(limit)
        if self.fehler:
            raise self.fehler
        return self.zeilen


ZEILEN = [
    {"capability": "idea_expand", "agent": "brain-ideas", "status": "laeuft",
     "angelegt": "2026-11-24T08:59:00+00:00", "begonnen": "2026-11-24T08:59:20+00:00", "beendet": None},
    {"capability": "idea_group", "agent": "brain-ideas", "status": "offen",
     "angelegt": "2026-11-24T08:55:00Z", "begonnen": None, "beendet": None},
    {"capability": "idea_whitepaper", "agent": "brain-ideas", "status": "abgelehnt",
     "angelegt": "2026-11-24T07:00:00+00:00", "beendet": "2026-11-24T07:01:00+00:00"},
    {"capability": "idea_expand", "agent": "brain-ideas", "status": "fertig",
     "angelegt": "2026-11-20T07:00:00+00:00", "beendet": "2026-11-20T07:01:00+00:00"},
]


def test_liest_laufende_und_zuletzt_beendete_ohne_inhalt() -> None:
    tabelle = _Tabelle(ZEILEN)
    erg = al.auftraege_lesen({"value": ""}, tabelle=tabelle, jetzt=JETZT)
    assert erg["success"] is True and erg["anzahl_laufend"] == 2 and tabelle.limits == [al.ZEILEN]
    assert erg["laufend"][0] == {"faehigkeit": "idea_expand", "agent": "brain-ideas", "stand": "laeuft",
                                 "sekunden": 40}
    assert erg["laufend"][1]["sekunden"] == 300
    # Aelter als ein Tag faellt aus "zuletzt" heraus.
    assert [e["stand"] for e in erg["zuletzt"]] == ["abgelehnt"]


def test_satz_mit_laufenden_auftraegen() -> None:
    erg = al.auftraege_lesen(tabelle=_Tabelle(ZEILEN), jetzt=JETZT)
    assert al.antwort(erg) == ("2 Aufträge an Agenten laufen: Idee ausbauen bei brain-ideas (läuft seit 40 s); "
                               "Ideen ordnen bei brain-ideas (wartet seit 5 min).")


def test_satz_ohne_laufende_nennt_die_letzten() -> None:
    erg = al.auftraege_lesen(tabelle=_Tabelle(ZEILEN[2:]), jetzt=JETZT)
    assert al.antwort(erg) == ("Gerade läuft kein Auftrag an einen Agenten. "
                               "Zuletzt: White Paper bei brain-ideas, abgelehnt vor 2 h.")


def test_leere_tabelle() -> None:
    erg = al.auftraege_lesen(tabelle=_Tabelle([]), jetzt=JETZT)
    assert al.antwort(erg) == "Gerade läuft kein Auftrag an einen Agenten."


def test_unbekannte_faehigkeit_bleibt_beim_namen_und_viele_werden_gekuerzt() -> None:
    zeilen = [{"capability": f"probe_{i}", "agent": "brain-x", "status": "offen",
               "angelegt": "2026-11-24T08:59:50Z"} for i in range(7)]
    satz = al.antwort(al.auftraege_lesen(tabelle=_Tabelle(zeilen), jetzt=JETZT))
    assert satz.startswith("7 Aufträge an Agenten laufen: probe_0 bei brain-x (wartet seit 10 s)")
    assert satz.endswith(" und 2 weitere.")


def test_ohne_zugang_und_ohne_antwort_genauer_fehlersatz(monkeypatch) -> None:
    import core.agent_auftraege as aa

    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.setattr(aa, "get_secret", lambda name: "")
    erg = al.auftraege_lesen()
    assert erg == {"success": False, "error": "kein_zugang"} and al.antwort(erg) is None
    assert "fehlt der Zugang" in v.FESTE_FEHLERSAETZE["schedule_auftraege"](erg, None)
    erg = al.auftraege_lesen(tabelle=_Tabelle(fehler=RuntimeError("503")))
    assert erg == {"success": False, "error": "nicht_lesbar"}
    assert "antwortet nicht" in v.FESTE_FEHLERSAETZE["schedule_auftraege"](erg, None)


def test_tabelle_fragt_nur_felder_ohne_inhalt() -> None:
    from core.agent_auftraege import AuftragsTabelle

    class _Http:
        def request(self, methode, url, **kw):
            self.aufruf = (methode, url, kw["params"])

            class _A:
                def raise_for_status(self):
                    pass

                def json(self):
                    return []
            return _A()

    http = _Http()
    assert AuftragsTabelle("https://db.example", "k", http=http).zuletzt(5) == []
    methode, url, params = http.aufruf
    assert methode == "GET" and url.endswith("/rest/v1/brain_agent_auftraege")
    assert params["select"] == "capability,agent,status,angelegt,begonnen,beendet" and params["limit"] == "5"


def test_hinweis_bei_geplanten_aufgaben_nennt_den_neuen_satz() -> None:
    assert "Welche Aufträge laufen gerade?" in v.lese_zusatz("Zeig meine Aufträge", "schedule_list")


# ── Ende zu Ende am Router ────────────────────────────────────────────────

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
        return {"ok": True, "plan": plan.to_dict(), "executed": {"s1": {"ok": True, "result": self.ergebnis}},
                "state": {}, "elapsed_s": 0.01, "replans": 0}


class _Planer:
    async def aplan(self, intent, context=None):
        raise AssertionError("kein Planer fuer feste Lesesaetze")


class _Klassierer:
    def classify(self, intent):
        return dict(SCHWACH)


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
    return TestClient(app).post("/api/multihop/execute",
                                json={"intent": satz, "context": {"space": space}}).json()


@pytest.mark.parametrize("space", ["orders", "ideas"])
def test_ende_zu_ende_ohne_planer(monkeypatch, space) -> None:
    ausfuehrer = _Ausfuehrer(al.auftraege_lesen(tabelle=_Tabelle(ZEILEN[:1]), jetzt=JETZT))
    body = _frage(monkeypatch, ausfuehrer, "Welche Aufträge laufen gerade?", space)
    assert body["ok"] is True
    assert body["final_text"] == "Ein Auftrag an einen Agenten läuft: Idee ausbauen bei brain-ideas (läuft seit 40 s)."
    schritt = ausfuehrer.plaene[0].to_dict()["hops"][0]
    assert schritt["capability"] == "schedule_auftraege"


def test_ende_zu_ende_ohne_zugang_genauer_satz(monkeypatch) -> None:
    ausfuehrer = _Ausfuehrer({"success": False, "error": "kein_zugang"})
    body = _frage(monkeypatch, ausfuehrer, "Was machen die Agenten gerade?", "orders")
    assert "fehlt der Zugang" in body["final_text"]
