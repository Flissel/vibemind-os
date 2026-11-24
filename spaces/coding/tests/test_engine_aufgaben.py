"""Tests: Aufgabendateien der Engine aus einem Entwurf.

Der Abgleich mit dem Quelltext der Engine laeuft nur, wenn ``CODING_ENGINE_PATH``
auf ein ausgechecktes Submodul zeigt; er liest die Dateien nur (``ast``), er
fuehrt nichts aus.
"""

from __future__ import annotations

import ast
import fnmatch
import json
import os
from pathlib import Path
from typing import Any, Dict, Set

import pytest

from spaces.coding import engine_aufgaben, pipeline


@pytest.fixture(autouse=True)
def _ablage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("CODING_PIPELINE_DIR", str(tmp_path / "auftraege"))
    monkeypatch.setenv("CODING_WORKSPACE_ROOT", str(tmp_path))
    return tmp_path


def _entwurf() -> Dict[str, Any]:
    return {
        "format": "vibemind.entwurf/1",
        "run_id": "lauf-42",
        "titel": "Aufgabentafel",
        "erstellt_am": "2026-11-23T10:00:00+00:00",
        "anforderungen": [
            {"id": "REQ-001", "titel": "Aufgabe anlegen", "beschreibung": "Titel und Text.", "prioritaet": "muss"},
            {"id": "REQ-002", "titel": "Aufgabe loeschen", "beschreibung": "", "prioritaet": "soll"},
        ],
        "architektur": {
            "tech_stack": [{"bereich": "Sprache", "wahl": "TypeScript", "grund": "x"}],
            "bausteine": [{"id": "BST-01", "name": "Speicher", "aufgabe": "haelt Aufgaben"}],
        },
        "arbeitspakete": [
            {"id": "AP-01", "titel": "Speicher", "beschreibung": "Baue den Speicher.", "anforderungen": ["REQ-001"],
             "bausteine": ["BST-01"], "haengt_ab_von": [], "annahmekriterien": ["Anlegen ist getestet."]},
            {"id": "AP-02", "titel": "Loeschen", "beschreibung": "Baue das Loeschen.", "anforderungen": ["REQ-002"],
             "bausteine": ["BST-01"], "haengt_ab_von": ["AP-01"], "annahmekriterien": ["Loeschen ist getestet."]},
        ],
    }


def test_je_arbeitspaket_eine_datei_die_der_erzeuger_findet_und_zuletzt_die_app() -> None:
    dateien = engine_aufgaben.aufgabendateien(_entwurf())

    assert list(dateien) == ["epic-001-tasks.json", "epic-002-tasks.json", "epic-003-tasks.json"]
    for name, inhalt in dateien.items():
        # run_generation.py sucht mit tasks_dir.glob("epic-*-tasks.json")
        assert fnmatch.fnmatch(name, "epic-*-tasks.json")
        # und der Orchestrator oeffnet f"{epic_id.lower()}-tasks.json"
        assert name == f"{inhalt['epic_id'].lower()}-tasks.json"
        assert inhalt["total_tasks"] == len(inhalt["tasks"])
        json.dumps(inhalt)
    assert dateien["epic-001-tasks.json"]["epic_name"] == "AP-01 Speicher"
    assert dateien["epic-003-tasks.json"]["epic_name"] == engine_aufgaben.APP_EPIC_NAME
    # Ein einzelnes Paket endet ohne App.
    assert list(engine_aufgaben.aufgabendateien(_entwurf(), mit_app=False)) == [
        "epic-001-tasks.json", "epic-002-tasks.json"]


def test_aufgaben_haben_nur_felder_und_arten_der_engine() -> None:
    bekannt = engine_aufgaben.ARTEN_MIT_MODELL | set(engine_aufgaben.ARTEN_OHNE_MODELL)
    for ohne_npm in (True, False):
        for inhalt in engine_aufgaben.aufgabendateien(_entwurf(), ohne_npm=ohne_npm).values():
            kennungen = [a["id"] for a in inhalt["tasks"]]
            assert len(kennungen) == len(set(kennungen))
            for stelle, aufgabe in enumerate(inhalt["tasks"]):
                assert set(aufgabe) <= engine_aufgaben.AUFGABEN_FELDER
                assert aufgabe["type"] in bekannt
                assert aufgabe["status"] == "pending"
                assert aufgabe["epic_id"] == inhalt["epic_id"]
                # Abhaengigkeiten zeigen nur auf fruehere Aufgaben desselben Epics.
                assert set(aufgabe["dependencies"]) <= set(kennungen[:stelle])


def test_reihenfolge_und_inhalt_der_aufgaben_mit_paketen() -> None:
    dateien = engine_aufgaben.aufgabendateien(_entwurf())
    arten = [[a["type"] for a in d["tasks"]] for d in dateien.values()]

    assert arten[0] == arten[1] == ["api_service", "test_unit", "install_deps", "verify_unit"]
    assert arten[2] == ["api_service", "test_unit", "install_deps", "verify_unit", "verify_run"]
    code = dateien["epic-001-tasks.json"]["tasks"][0]
    assert code["related_requirements"] == ["REQ-001"]
    assert code["success_criteria"] == "Anlegen ist getestet."
    for erwartet in ("Baue den Speicher.", "REQ-001: Aufgabe anlegen. Titel und Text.", "Speicher: haelt Aufgaben",
                     "Sprache: TypeScript", "- Anlegen ist getestet.", "fester Version"):
        assert erwartet in code["description"]
    # Kein TypeScript-Bau, kein Download ausser dem bewachten install_deps.
    text = json.dumps(dateien)
    assert "npx" not in text and "verify_build" not in text and "tsconfig" not in text


def test_die_app_startet_mit_einem_befehl_und_wird_aufgerufen() -> None:
    app = list(engine_aufgaben.aufgabendateien(_entwurf(), ohne_npm=True).values())[-1]["tasks"]
    server, test, alle_tests, lauf = app

    assert server["output_files"] == ["src/server.js"]
    for pflicht in ("GET /health", "process.env.PORT", "createServer()", "require.main === module", "npm start",
                    "REQ-001: Aufgabe anlegen", "REQ-002: Aufgabe loeschen"):
        assert pflicht in server["description"], pflicht
    assert test["output_files"] == ["test/app.test.js"] and "Port 0" in test["description"]
    assert alle_tests["command"] == "npm test"
    assert lauf["type"] == "verify_run" and lauf["command"] == "npm start"
    assert "GET /health -> 200" in lauf["success_criteria"]
    # Pruefungen gehen bis zu zweimal zur Reparatur zurueck, Modellaufgaben werden einmal wiederholt.
    assert lauf["max_retries"] == alle_tests["max_retries"] == engine_aufgaben.REPARATUREN
    assert server["max_retries"] == 1
    # Die Pruefung traegt die Kriterien, damit eine Reparatur weiss, wofuer sie steht.
    assert "REQ-002" in lauf["description"]


def test_mit_oberflaeche_baut_die_app_eine_seite_die_die_schnittstelle_benutzt() -> None:
    entwurf = _entwurf()
    entwurf["architektur"]["tech_stack"].append({"bereich": "Oberfläche", "wahl": "eine HTML-Seite ohne Bau-Schritt"})
    app = list(engine_aufgaben.aufgabendateien(entwurf, ohne_npm=True).values())[-1]["tasks"]
    assert [a["id"].split("-", 2)[2] for a in app] == ["SERVER", "UI", "TEST", "VERIFY-unit", "VERIFY-run"]
    server, ui, test, _alle, lauf = app

    assert "public/" in server["description"] and "GET /" in server["description"]
    assert ui["type"] == "api_service" and ui["output_files"] == ["public/index.html"]
    for pflicht in ("fetch", "public/index.html", "kein Bau-Schritt", "REQ-001: Aufgabe anlegen"):
        assert pflicht in ui["description"], pflicht
    assert ui["dependencies"] == [server["id"]] and test["dependencies"] == [ui["id"]]
    assert "GET /" in test["description"] and "text/html" in test["description"]
    assert "GET / -> 200" in lauf["success_criteria"] and "GET /health -> 200" in lauf["success_criteria"]
    # Ein Modellaufruf mehr als ohne Oberflaeche.
    assert engine_aufgaben.modellaufrufe_mindestens(engine_aufgaben.aufgabendateien(entwurf, ohne_npm=True)) == 7


def test_package_json_startet_und_prueft_alles() -> None:
    inhalt = engine_aufgaben.paket_json(_entwurf())
    assert inhalt["scripts"] == {
        "start": "node src/server.js",
        "test": "node --test test/ap-01.test.js test/ap-02.test.js test/app.test.js",
    }
    assert inhalt["name"] == "lauf-42" and "dependencies" not in inhalt
    ohne_app = engine_aufgaben.paket_json(_entwurf(), mit_app=False)
    assert "start" not in ohne_app["scripts"] and "app.test.js" not in ohne_app["scripts"]["test"]


def test_untergrenze_und_obergrenze_der_modellaufrufe() -> None:
    dateien = engine_aufgaben.aufgabendateien(_entwurf(), ohne_npm=True)
    # Je Paket Code und Test, dazu Server und App-Test.
    assert engine_aufgaben.modellaufrufe_mindestens(dateien) == 6
    # 6 Modellaufgaben mit je einer Wiederholung; 4 Pruefungen (2 Pakete, alle Tests, Start) mit je zwei Reparaturen.
    assert engine_aufgaben.modellaufrufe_hoechstens(dateien) == 6 * 2 + 4 * 2


def test_schreiben_nur_unter_der_arbeitswurzel(tmp_path: Path) -> None:
    geschrieben = engine_aufgaben.schreiben(_entwurf(), "entwurf-lauf-42")

    assert [Path(p).name for p in geschrieben] == [
        "epic-001-tasks.json", "epic-002-tasks.json", "epic-003-tasks.json", "user_stories.md", "package.json"]
    # Ohne diese Datei startet die Engine nicht; ihr Parser sucht "# EPIC-001: Name".
    stories = (tmp_path / "entwurf-lauf-42" / "user_stories" / "user_stories.md").read_text(encoding="utf-8")
    assert "# EPIC-001: AP-01 Speicher" in stories and "# EPIC-002: AP-02 Loeschen" in stories
    assert f"# EPIC-003: {engine_aufgaben.APP_EPIC_NAME}" in stories
    assert Path(geschrieben[0]).parent == (tmp_path / "entwurf-lauf-42" / "tasks").resolve()
    assert json.loads(Path(geschrieben[0]).read_text(encoding="utf-8"))["epic_id"] == "EPIC-001"
    paket = tmp_path / "entwurf-lauf-42" / "out" / "package.json"
    assert json.loads(paket.read_text(encoding="utf-8"))["scripts"]["start"] == "node src/server.js"
    assert b"\r\n" not in paket.read_bytes()
    # Ein vorhandenes package.json bleibt, wie es ist.
    paket.write_text('{"name": "eigen"}', encoding="utf-8")
    engine_aufgaben.schreiben(_entwurf(), "entwurf-lauf-42")
    assert json.loads(paket.read_text(encoding="utf-8")) == {"name": "eigen"}
    with pytest.raises(pipeline.PipelineFehler, match="Arbeitswurzel"):
        engine_aufgaben.schreiben(_entwurf(), "../draussen")
    assert not (tmp_path.parent / "draussen").exists()


def test_falsches_format_und_leerer_entwurf() -> None:
    with pytest.raises(pipeline.PipelineFehler, match="vibemind.entwurf/1"):
        engine_aufgaben.aufgabendateien({"format": "vibemind.entwurf/2", "arbeitspakete": [{}]})
    leer = _entwurf()
    leer["arbeitspakete"] = []
    with pytest.raises(pipeline.PipelineFehler, match="keine Arbeitspakete"):
        engine_aufgaben.aufgabendateien(leer)


BEISPIEL = Path(__file__).resolve().parents[1] / "beispiele" / "aufgabenliste.entwurf.json"


def test_beispiel_entwurf_haelt_das_format_ein() -> None:
    entwurf = json.loads(BEISPIEL.read_text(encoding="utf-8"))

    assert entwurf["format"] == "vibemind.entwurf/1"
    for feld in ("run_id", "titel", "aufgabe", "erstellt_am", "kontext", "anforderungen", "annahmekriterien",
                 "architektur", "arbeitspakete", "pruefung"):
        assert entwurf[feld] not in (None, "", [], {}), feld
    kennungen = [a["id"] for a in entwurf["anforderungen"]]
    assert kennungen == [f"REQ-{n:03d}" for n in range(1, len(kennungen) + 1)]
    for anforderung in entwurf["anforderungen"]:
        assert anforderung["art"] in ("funktional", "nicht_funktional")
        assert anforderung["prioritaet"] in ("muss", "soll", "kann")
        assert anforderung["annahmekriterien"]
    # Die flache Liste traegt genau die Kriterien der Anforderungen, fortlaufend gezaehlt.
    flach = [(k["anforderung"], k["text"]) for k in entwurf["annahmekriterien"]]
    assert flach == [(a["id"], t) for a in entwurf["anforderungen"] for t in a["annahmekriterien"]]
    assert [k["id"] for k in entwurf["annahmekriterien"]] == [f"AK-{n:03d}" for n in range(1, len(flach) + 1)]
    assert entwurf["architektur"]["tech_stack"] and entwurf["architektur"]["bausteine"]
    bausteine = {b["id"] for b in entwurf["architektur"]["bausteine"]}
    pakete = entwurf["arbeitspakete"]
    assert 1 <= len(pakete) <= 8
    for stelle, paket in enumerate(pakete):
        assert paket["id"] == f"AP-{stelle + 1:02d}"
        assert paket["annahmekriterien"] and paket["anforderungen"]
        assert set(paket["anforderungen"]) <= set(kennungen)
        assert set(paket["bausteine"]) <= bausteine
        assert set(paket["haengt_ab_von"]) <= {p["id"] for p in pakete[:stelle]}
    gedeckt = {r for p in pakete for r in p["anforderungen"]}
    assert {a["id"] for a in entwurf["anforderungen"] if a["prioritaet"] == "muss"} <= gedeckt
    assert entwurf["pruefung"]["muss_gedeckt"] is True
    assert "/" not in entwurf["run_id"] and ":\\" not in json.dumps(entwurf)  # kein Pfad im Entwurf


def test_weg_ohne_npm_hat_keinen_download_und_nennt_testdateien_einzeln() -> None:
    entwurf = json.loads(BEISPIEL.read_text(encoding="utf-8"))
    dateien = engine_aufgaben.aufgabendateien(entwurf, ohne_npm=True)

    assert list(dateien) == ["epic-001-tasks.json", "epic-002-tasks.json", "epic-003-tasks.json",
                             "epic-004-tasks.json"]
    befehle = []
    for inhalt in list(dateien.values())[:3]:
        assert [a["type"] for a in inhalt["tasks"]] == ["api_service", "test_unit", "verify_unit"]
        code, test, pruefung = inhalt["tasks"]
        assert set(code) | set(test) | set(pruefung) <= engine_aufgaben.AUFGABEN_FELDER
        assert "keine dependencies" in code["description"] and "keine dependencies" in test["description"]
        assert test["output_files"][0] in test["description"]
        assert test["dependencies"] == [code["id"]] and pruefung["dependencies"] == [test["id"]]
        befehle.append(pruefung["command"])
    assert befehle == [
        "node --test test/ap-01.test.js",
        "node --test test/ap-01.test.js test/ap-02.test.js",
        "node --test test/ap-01.test.js test/ap-02.test.js test/ap-03.test.js",
    ]
    app = [a["type"] for a in dateien["epic-004-tasks.json"]["tasks"]]
    assert app == ["api_service", "test_unit", "verify_unit", "verify_run"]
    # Ohne npm heisst: nichts wird installiert, kein npx; npm startet nur Skripte aus package.json.
    text = json.dumps(dateien)
    assert "install_deps" not in text and "npx" not in text and "npm install" not in text
    # Das Mass der Pruefung sind die Annahmekriterien des Pakets.
    for paket, inhalt in zip(entwurf["arbeitspakete"], dateien.values()):
        for kriterium in paket["annahmekriterien"]:
            assert kriterium in inhalt["tasks"][1]["description"]
            assert kriterium in inhalt["tasks"][2]["description"]
    assert engine_aufgaben.modellaufrufe_mindestens(dateien) == 8


def test_schreiben_ohne_npm(tmp_path: Path) -> None:
    entwurf = json.loads(BEISPIEL.read_text(encoding="utf-8"))
    geschrieben = engine_aufgaben.schreiben(entwurf, "probe", ohne_npm=True)

    assert len(geschrieben) == 6  # vier Epics, user_stories.md, package.json
    erste = json.loads(Path(geschrieben[0]).read_text(encoding="utf-8"))
    assert erste["tasks"][2]["command"] == "node --test test/ap-01.test.js"
    stories = (tmp_path / "probe" / "user_stories" / "user_stories.md").read_text(encoding="utf-8")
    assert "# EPIC-003: AP-03 Aufgaben erledigen" in stories
    paket = json.loads((tmp_path / "probe" / "out" / "package.json").read_text(encoding="utf-8"))
    assert paket["scripts"]["test"].endswith("test/ap-03.test.js test/app.test.js")


# ── Abgleich mit dem Quelltext der Engine (nur lesend) ─────────────────────

_ENGINE = os.environ.get("CODING_ENGINE_PATH", "")
_HOST = Path(_ENGINE) / "mcp_plugins" / "servers" / "grpc_host" if _ENGINE else None
ohne_engine = pytest.mark.skipif(
    _HOST is None or not (_HOST / "epic_task_generator.py").is_file(),
    reason="CODING_ENGINE_PATH zeigt auf kein ausgechecktes Submodul",
)


def _baum(datei: str) -> ast.Module:
    assert _HOST is not None
    return ast.parse((_HOST / datei).read_text(encoding="utf-8"))


@ohne_engine
def test_felder_stimmen_mit_der_task_klasse_der_engine() -> None:
    klasse = next(k for k in ast.walk(_baum("epic_task_generator.py")) if isinstance(k, ast.ClassDef) and k.name == "Task")
    felder: Set[str] = {
        z.target.id for z in klasse.body if isinstance(z, ast.AnnAssign) and isinstance(z.target, ast.Name)
    }
    assert felder == set(engine_aufgaben.AUFGABEN_FELDER)


@ohne_engine
def test_arten_stehen_in_der_zuordnung_der_engine() -> None:
    zuordnung: Dict[str, str] = {}
    for knoten in ast.walk(_baum("task_executor.py")):
        if isinstance(knoten, ast.Dict):
            for schluessel, wert in zip(knoten.keys, knoten.values):
                if (isinstance(schluessel, ast.Constant) and isinstance(schluessel.value, str)
                        and isinstance(wert, ast.Tuple) and wert.elts
                        and isinstance(wert.elts[0], ast.Constant) and isinstance(wert.elts[0].value, str)):
                    zuordnung[schluessel.value] = wert.elts[0].value

    for art, ausfuehrer in engine_aufgaben.ARTEN_OHNE_MODELL.items():
        assert zuordnung[art] == ausfuehrer
    for art in engine_aufgaben.ARTEN_MIT_MODELL:
        assert art in zuordnung and zuordnung[art] not in set(engine_aufgaben.ARTEN_OHNE_MODELL.values())


@pytest.mark.parametrize("ohne_npm", [True, False])
def test_jede_modellaufgabe_und_jede_pruefung_nennt_die_klammer_regel(ohne_npm: bool) -> None:
    # Lauf 1b: [[...]] ging auf dem Weg ueber den Modelldienst verloren; Reparaturen lesen die Pruefung.
    dateien = engine_aufgaben.aufgabendateien(_entwurf(), ohne_npm=ohne_npm)
    for datei in dateien.values():
        for aufgabe in datei["tasks"]:
            if aufgabe["type"] in ("api_service", "test_unit", "verify_unit"):
                assert engine_aufgaben.KLAMMER_REGEL in aufgabe["description"], aufgabe["id"]
