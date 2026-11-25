"""init_from_project of the shuttle wizard: promoted project -> wizard state, no model. All data invented."""
from __future__ import annotations

import copy
import importlib.util
import sys
import types
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest import mock

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from test_stand import STAND, mit_pruefsumme  # noqa: E402


@dataclass
class Project:
    id: str
    name: str
    description: str = ""
    from_idea_id: str | None = None
    metadata: dict = field(default_factory=dict)


@dataclass
class Shuttle:
    id: str
    shuttle_id: str
    project_id: str | None
    stage_data: dict = field(default_factory=dict)


class Store:
    def __init__(self) -> None:
        self.projects: dict[str, Project] = {}
        self.shuttles: list[Shuttle] = []


def fake_data_module(store: Store) -> types.ModuleType:
    class ProjectsRepository:
        def get(self, project_id: str):
            return store.projects.get(project_id)

    class ShuttlesRepository:
        def get_by_project_id(self, project_id: str):
            return next((s for s in store.shuttles if s.project_id == project_id), None)

        def get_by_shuttle_id(self, shuttle_id: str):
            return next((s for s in store.shuttles if s.shuttle_id == shuttle_id), None)

        def create(self, *, shuttle_id, bubble_id, bubble_name, total_count=0, project_id=None, metadata=None, **_):
            s = Shuttle(id=f"db-{len(store.shuttles) + 1}", shuttle_id=shuttle_id, project_id=project_id)
            store.shuttles.append(s)
            return s

        def update_stage_data(self, db_id: str, data: dict[str, Any]) -> None:
            next(s for s in store.shuttles if s.id == db_id).stage_data = copy.deepcopy(data)

    module = types.ModuleType("data")
    module.ProjectsRepository = ProjectsRepository
    module.ShuttlesRepository = ShuttlesRepository
    return module


@pytest.fixture()
def welt():
    store = Store()
    store.projects["p-hof"] = Project("p-hof", "Hofladen", "Selbstbedienung im Dorf", "b-hof",
                                      {"source_space": "bubbles", "uebergabe": mit_pruefsumme(STAND)})
    llm = types.ModuleType("llm_config")
    llm.calls = []
    llm.get_model = lambda *a, **k: llm.calls.append(a) or "stub-model"
    with mock.patch.dict(sys.modules, {"data": fake_data_module(store), "llm_config": llm}):
        spec = importlib.util.spec_from_file_location("wizard_handler_t", HERE.parent / "wizard_handler.py")
        modul = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(modul)
        handler = modul.ShuttleWizardHandler()
        llm.calls.clear()
        yield store, handler
        assert llm.calls == [], "init_from_project must not ask for a model"


def test_befuellt_ohne_modell_und_legt_shuttle_an(welt) -> None:
    store, h = welt
    with mock.patch.object(h, "_ensure_teams", side_effect=AssertionError("no agents")):
        a = h.init_from_project(None, "p-hof")
    assert a["success"] and a["shuttle_id"] == "shuttle-projekt-p-hof" and not a["wiederaufgenommen"]
    assert a["project"] == {"name": "Hofladen", "description": "Selbstbedienung im Dorf", "domain": "", "target_users": ""}
    assert a["requirements_count"] == 3
    k = a["kopfzeile"]
    assert k["bestanden"] and k["text"] == "Sinnprüfung bestanden · 3 Karten · White Paper deckt 67 %"
    assert k["pruefsumme"] == "stimmt" and k["gruende_text"] == ""

    state = h.get_state("shuttle-projekt-p-hof")
    reqs = state["requirements"]
    assert [r["id"] for r in reqs] == ["REQ-001", "REQ-002", "REQ-003"]
    assert {r["status"] for r in reqs} == {"kandidat"}
    assert reqs[0]["category"] == "Laden" and reqs[0]["source_card_id"] == "a" and reqs[2]["category"] is None
    assert state["context"]["white_paper"]["text"] == "# Ziel\nRegional einkaufen."
    assert state["context"]["sinnpruefung"]["bestanden"]
    assert len(store.shuttles) == 1 and store.shuttles[0].project_id == "p-hof"
    assert store.shuttles[0].stage_data["wizard_state"]["requirements"][1]["title"] == "Abholung"


def test_shuttle_wird_wiederverwendet_und_stand_bleibt(welt) -> None:
    store, h = welt
    h.init_from_project("shuttle-eigen", "p-hof")
    store.shuttles[0].stage_data["wizard_state"]["requirements"][0]["status"] = "angenommen"
    h2 = type(h)()
    a = h2.init_from_project("shuttle-anders", "p-hof")
    assert a["success"] and a["wiederaufgenommen"] and a["shuttle_id"] == "shuttle-eigen"
    assert len(store.shuttles) == 1
    assert h2.get_state("shuttle-eigen")["requirements"][0]["status"] == "angenommen"


def test_nicht_bestanden_oeffnet_trotzdem_mit_gruenden(welt) -> None:
    store, h = welt
    s = copy.deepcopy(STAND)
    s["karten"].append({"id": "d", "titel": "API", "text": "", "art": "notiz"})
    s["bewertung"] = None
    store.projects["p-hof"].metadata["uebergabe"] = s
    a = h.init_from_project(None, "p-hof")
    k = a["kopfzeile"]
    assert a["success"] and not k["bestanden"]
    assert k["gruende"] == ["leere_karten:1", "lose_karten:1", "keine_bewertung"]
    assert k["gruende_text"] == "leere Karten: 1, lose Karten: 1, keine Bewertung"
    assert k["leer"] == ["d"] and k["pruefsumme"] == "fehlt"
    assert k["text"].startswith("Sinnprüfung nicht bestanden · 4 Karten")


def test_eigene_beschreibung_hat_vorrang(welt) -> None:
    store, h = welt
    a = h.init_from_project(None, "p-hof")
    assert a["project"]["description"] == "Selbstbedienung im Dorf" and a["beschreibung_quelle"] == "projekt"
    assert h.get_state(a["shuttle_id"])["context"]["beschreibung_quelle"] == "projekt"


def test_leere_beschreibung_aus_white_paper(welt) -> None:
    store, h = welt
    store.projects["p-hof"].description = "  "
    a = h.init_from_project(None, "p-hof")
    assert a["project"]["description"] == "Regional einkaufen." and a["beschreibung_quelle"] == "white_paper"
    assert h.get_state(a["shuttle_id"])["project"]["description"] == "Regional einkaufen."


def test_ohne_white_paper_bleibt_leer(welt) -> None:
    store, h = welt
    store.projects["p-hof"].description = ""
    store.projects["p-hof"].metadata["uebergabe"] = {**STAND, "white_paper": None}
    a = h.init_from_project(None, "p-hof")
    assert a["project"]["description"] == "" and a["beschreibung_quelle"] == "leer"


def test_vorhandener_stand_wird_nicht_ueberschrieben(welt) -> None:
    store, h = welt
    a = h.init_from_project(None, "p-hof")
    ws = store.shuttles[0].stage_data["wizard_state"]
    ws["project"]["description"] = "von Hand"
    store.projects["p-hof"].description = ""
    b = type(h)().init_from_project(None, "p-hof")
    assert b["wiederaufgenommen"] and b["project"]["description"] == "von Hand"


def test_absatz_ueberspringt_ueberschriften_und_kuerzt_am_satz(welt) -> None:
    store, h = welt
    absatz = type(h).init_from_project.__globals__["_absatz_aus_white_paper"]
    assert absatz("# Titel\n\n## Ziel\n---\n\nErste Zeile\nzweite Zeile.\n\nZweiter Absatz.") == \
        "Erste Zeile zweite Zeile."
    lang = "Ein Satz mit etwas Inhalt. " * 40
    kurz = absatz(lang)
    assert 200 <= len(kurz) <= 600 and kurz.endswith("Inhalt.")
    ohne_punkt = "wort " * 200
    k2 = absatz(ohne_punkt)
    assert len(k2) <= 602 and k2.endswith(" …")
    assert absatz(None) == "" and absatz("# nur Ueberschrift\n\n") == ""


def test_fehler(welt) -> None:
    store, h = welt
    assert h.init_from_project(None, "")["error"].startswith("invalid_arguments")
    assert h.init_from_project(None, "p-nix")["error"] == "projekt_unbekannt"
    store.projects["p-alt"] = Project("p-alt", "Alt", metadata={"source_space": "bubbles"})
    assert h.init_from_project(None, "p-alt")["error"] == "kein_stand"
    store.projects["p-neu"] = Project("p-neu", "Neu", metadata={"uebergabe": {"version": 2}})
    assert h.init_from_project(None, "p-neu")["error"] == "format_unbekannt"
    assert store.shuttles == []
