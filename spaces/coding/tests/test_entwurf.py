"""Tests: ein Entwurf ``vibemind.entwurf/1`` wird ein Auftrag der Engine.

Die Engine laeuft hier nicht. Ihr Fortschritt wird so nachgestellt, wie sie ihn
selbst festhaelt: in den Aufgabendateien, im Ausgabeordner und in der
Abschlussdatei.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from spaces.coding import pipeline

BEISPIEL = Path(__file__).resolve().parents[1] / "beispiele" / "aufgabenliste.entwurf.json"


@pytest.fixture(autouse=True)
def _ablage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("CODING_PIPELINE_DIR", str(tmp_path / "auftraege"))
    monkeypatch.setenv("CODING_WORKSPACE_ROOT", str(tmp_path / "arbeit"))
    return tmp_path


def _entwurf() -> Dict[str, Any]:
    return json.loads(BEISPIEL.read_text(encoding="utf-8"))


def _projekt(tmp_path: Path, sicht: Dict[str, Any]) -> Path:
    return tmp_path / "arbeit" / sicht["auftrag"]


def _engine(projekt: Path, epic: int, stand: Dict[str, str], fehler: Optional[str] = None,
            schreibe: bool = True) -> None:
    """Stellt nach, was die Engine in ein Epic schreibt: Status je Aufgabe, ``output_files``, Dateien."""
    datei = projekt / "tasks" / f"epic-{epic:03d}-tasks.json"
    inhalt = json.loads(datei.read_text(encoding="utf-8"))
    for aufgabe in inhalt["tasks"]:
        name = aufgabe["id"].split("-", 2)[2]
        aufgabe["status"] = stand.get(name, aufgabe["status"])
        if aufgabe["status"] == "failed":
            aufgabe["error_message"] = fehler
        if aufgabe["status"] == "completed" and name in ("CODE", "SERVER", "TEST"):
            # Epic 4 ist die App: Startpunkt und ihr Test.
            pfad = {"CODE": "src/aufgabenliste.js", "SERVER": "src/server.js"}.get(
                name, "test/app.test.js" if epic == 4 else f"test/ap-{epic:02d}.test.js")
            aufgabe["output_files"] = [pfad]
            if schreibe:
                ziel = projekt / "out" / pfad
                ziel.parent.mkdir(parents=True, exist_ok=True)
                ziel.write_bytes(b"// erzeugt\n")
    datei.write_text(json.dumps(inhalt), encoding="utf-8")


def _abschluss(projekt: Path, status: str) -> None:
    (projekt / "out").mkdir(parents=True, exist_ok=True)
    (projekt / "out" / ".generation_status.json").write_text(
        json.dumps({"status": status, "elapsed_seconds": 81.6}), encoding="utf-8")


ALLES = {name: "completed" for name in ("CODE", "SERVER", "TEST", "VERIFY-unit", "VERIFY-run")}


def test_entwurf_wird_ein_auftrag_der_engine_und_startet_nichts(tmp_path: Path) -> None:
    sicht = pipeline.auftrag_starten(entwurf=_entwurf())

    assert sicht["art"] == "engine" and sicht["herkunft"] == "entwurf"
    assert sicht["titel"] == "Aufgabenliste im Speicher" and sicht["run_id"] == "beispiel-aufgabenliste"
    assert sicht["zustand"] == pipeline.WARTET and sicht["stufe"] == "bau"
    assert sicht["angestossen"] is False and sicht["modellaufrufe_erwartet"] == 8
    assert sicht["startbefehl"] == "npm start" and sicht["modellaufrufe_hoechstens"] == 26
    assert [(p["paket"], p["zustand"]) for p in sicht["pakete"]] == [
        ("AP-01", "wartet"), ("AP-02", "wartet"), ("AP-03", "wartet"), ("APP", "wartet")]
    assert [s["zustand"] for s in sicht["stufen"]] == ["fertig", "fertig", "wartet", "wartet", "wartet"]
    projekt = _projekt(tmp_path, sicht)
    assert sorted(p.name for p in (projekt / "tasks").iterdir()) == [
        "epic-001-tasks.json", "epic-002-tasks.json", "epic-003-tasks.json", "epic-004-tasks.json"]
    assert (projekt / "user_stories" / "user_stories.md").is_file()
    # Nichts gestartet; im Zielordner liegt nur das package.json mit Start- und Testbefehl.
    assert [p.name for p in (projekt / "out").iterdir()] == ["package.json"]
    # Der ganze Entwurf steht als Kopie im Auftrag.
    assert pipeline._laden(sicht["auftrag"])["entwurf"]["arbeitspakete"][2]["id"] == "AP-03"


def test_der_erzeuger_wird_ueber_genau_eine_stelle_angestossen() -> None:
    gerufen: List[Dict[str, Any]] = []

    def anstoss(auftrag: Dict[str, Any]) -> bool:
        gerufen.append(auftrag)
        return True

    sicht = pipeline.auftrag_starten(entwurf=json.dumps(_entwurf()), anstoss=anstoss)

    assert len(gerufen) == 1
    assert gerufen[0]["id"] == sicht["auftrag"] and gerufen[0]["projektordner"].endswith(sicht["auftrag"])
    assert sicht["angestossen"] is True
    assert pipeline.stand(sicht["auftrag"])["angestossen"] is True


def test_stand_zeigt_stufe_paket_und_dateien_aus_den_dateien_der_engine(tmp_path: Path) -> None:
    sicht = pipeline.auftrag_starten(entwurf=_entwurf())
    projekt = _projekt(tmp_path, sicht)

    _engine(projekt, 1, {"CODE": "completed", "TEST": "running"})
    mitten = pipeline.stand(sicht["auftrag"])
    assert mitten["zustand"] == pipeline.LAEUFT and mitten["stufe"] == "bau"
    assert mitten["aktuelles_paket"] == "AP-01"
    assert mitten["pakete"][0]["dateien"] == ["src/aufgabenliste.js"]
    assert mitten["pakete"][0]["bau"] == "laeuft" and mitten["pakete"][0]["pruefung"] == "wartet"

    _engine(projekt, 1, ALLES)
    _engine(projekt, 2, {"CODE": "completed", "TEST": "completed", "VERIFY-unit": "running"})
    weiter = pipeline.stand()
    assert weiter["aktuelles_paket"] == "AP-02" and weiter["zustand"] == pipeline.LAEUFT
    assert [p["zustand"] for p in weiter["pakete"]] == ["fertig", "laeuft", "wartet", "wartet"]
    assert weiter["pakete"][0]["pruefungen"] == [
        {"befehl": "node --test test/ap-01.test.js", "bestanden": True, "ausgefuehrt": True}]
    # Eine Pruefung, die noch laeuft oder wartet, ist nicht "nicht bestanden", sondern nicht ausgefuehrt.
    assert weiter["pakete"][1]["pruefungen"][0]["ausgefuehrt"] is False
    assert weiter["pakete"][2]["pruefungen"][0] == {
        "befehl": "node --test test/ap-01.test.js test/ap-02.test.js test/ap-03.test.js", "bestanden": False,
        "ausgefuehrt": False}


def test_fertig_erst_mit_abschluss_der_engine_und_ergebnis_von_der_platte(tmp_path: Path) -> None:
    sicht = pipeline.auftrag_starten(entwurf=_entwurf())
    projekt = _projekt(tmp_path, sicht)
    for epic in (1, 2, 3, 4):
        _engine(projekt, epic, ALLES)

    # Alle Aufgaben erledigt, aber die Engine hat ihren Abschluss noch nicht geschrieben.
    assert pipeline.stand(sicht["auftrag"])["zustand"] == pipeline.LAEUFT
    assert pipeline.stand(sicht["auftrag"])["stufe"] == "abschluss"
    with pytest.raises(pipeline.PipelineFehler, match="noch kein Ergebnis"):
        pipeline.ergebnis(sicht["auftrag"])

    _abschluss(projekt, "completed")
    fertig = pipeline.stand(sicht["auftrag"])
    assert fertig["zustand"] == pipeline.FERTIG
    assert [s["zustand"] for s in fertig["stufen"]] == ["fertig"] * 5

    ergebnis = pipeline.ergebnis()
    assert ergebnis["dateien"] == ["src/aufgabenliste.js", "src/server.js", "test/ap-01.test.js",
                                   "test/ap-02.test.js", "test/ap-03.test.js", "test/app.test.js"]
    # Die letzte Pruefung ist der Start der App.
    assert ergebnis["pruefung"] == {"bestanden": True, "befehl": "npm start"}
    assert ergebnis["start"] == "npm start"
    assert ergebnis["zielordner"] == str(projekt / "out") and ergebnis["dauer_s"] == 81.6
    assert [p["paket"] for p in ergebnis["pakete"]] == ["AP-01", "AP-02", "AP-03", "APP"]


def test_gescheiterte_aufgabe_macht_den_auftrag_gescheitert_mit_grund(tmp_path: Path) -> None:
    sicht = pipeline.auftrag_starten(entwurf=_entwurf())
    projekt = _projekt(tmp_path, sicht)
    _engine(projekt, 1, ALLES)
    _engine(projekt, 2, {"CODE": "completed", "TEST": "failed", "VERIFY-unit": "skipped"},
            fehler="no file written: the model reply contained no file block")

    # Solange die Engine laeuft, laeuft der Auftrag; der Grund ist am Paket schon sichtbar.
    mitten = pipeline.stand(sicht["auftrag"])
    assert mitten["zustand"] == pipeline.LAEUFT and mitten["pakete"][1]["zustand"] == "fehlgeschlagen"

    _abschluss(projekt, "completed_with_errors")
    ende = pipeline.stand(sicht["auftrag"])
    assert ende["zustand"] == pipeline.FEHLGESCHLAGEN
    assert ende["fehler"] == ("Bau: Paket 2 von 4 „Aufgaben auflisten“ ist gescheitert. "
                              "Das Modell hat keine Datei geliefert.")
    assert ende["fehler_grund"] == "no file written: the model reply contained no file block"
    assert ende["aktuelles_paket"] == "AP-02"
    with pytest.raises(pipeline.PipelineFehler, match="fehlgeschlagen"):
        pipeline.ergebnis(sicht["auftrag"])


def test_gescheitertes_erstes_paket_beendet_die_kette(tmp_path: Path) -> None:
    sicht = pipeline.auftrag_starten(entwurf=_entwurf())
    projekt = _projekt(tmp_path, sicht)
    _engine(projekt, 1, {"CODE": "completed", "TEST": "completed", "VERIFY-unit": "failed"},
            fehler="Command failed (exit 1): node --test test/ap-01.test.js")
    (projekt / "out" / ".generation_status.json").write_text(json.dumps({
        "status": "completed_with_errors", "stopped_after": "EPIC-001",
        "skipped_epics": ["EPIC-002", "EPIC-003", "EPIC-004"]}), encoding="utf-8")

    ende = pipeline.stand(sicht["auftrag"])
    assert ende["zustand"] == pipeline.FEHLGESCHLAGEN and ende["stufe"] == "pruefung"
    assert ende["fehler"] == ("Prüfung: Paket 1 von 4 „Aufgaben anlegen“ ist gescheitert. "
                              "Ein Prüfbefehl ist mit Fehlercode 1 beendet worden. "
                              "Die übrigen 3 Pakete wurden nicht mehr begonnen.")
    assert [p["name"] for p in ende["pakete"]] == [
        "Aufgaben anlegen", "Aufgaben auflisten", "Aufgaben erledigen", "Die App starten und prüfen"]
    assert ende["dauer_s"] is None  # die nachgestellte Abschlussdatei nennt keine Laufzeit
    assert ende["fehler_grund"].startswith("Command failed")
    # Nie begonnen ist nicht "fehlgeschlagen": die Pakete zeigen "abgebrochen".
    assert [(p["paket"], p["zustand"]) for p in ende["pakete"]] == [
        ("AP-01", "fehlgeschlagen"), ("AP-02", "abgebrochen"), ("AP-03", "abgebrochen"), ("APP", "abgebrochen")]
    assert ende["pakete"][1]["bau"] == ende["pakete"][1]["pruefung"] == "abgebrochen"


@pytest.mark.parametrize("meldung, satz", [
    ("openfang unreachable: All connection attempts failed", "Der Modelldienst war nicht erreichbar."),
    ("Command: npm start (PORT=5)\nThe app did not answer on port 5 within 30 s.",
     "Die App hat nach dem Start nicht geantwortet."),
    ("package.json names packages (express), but installing is off. Set ...",
     "Das Projekt braucht Pakete aus npm, aber Installieren ist für diesen Auftrag aus."),
    ("etwas ganz anderes", None),
])
def test_bekannte_meldungen_der_engine_werden_ein_deutscher_satz(meldung: str, satz: Optional[str]) -> None:
    from spaces.coding import engine_huelle
    assert engine_huelle.grund_satz(meldung) == satz


def test_abschluss_ohne_erledigte_aufgaben_ist_nicht_fertig(tmp_path: Path) -> None:
    sicht = pipeline.auftrag_starten(entwurf=_entwurf())
    _abschluss(_projekt(tmp_path, sicht), "completed")
    # Die Abschlussdatei allein reicht nicht: die Aufgaben stehen noch auf "pending".
    assert pipeline.stand(sicht["auftrag"])["zustand"] == pipeline.FEHLGESCHLAGEN


def test_gemeldete_datei_die_auf_der_platte_fehlt_gibt_kein_ergebnis(tmp_path: Path) -> None:
    sicht = pipeline.auftrag_starten(entwurf=_entwurf())
    projekt = _projekt(tmp_path, sicht)
    _engine(projekt, 1, ALLES)
    _engine(projekt, 2, ALLES)
    _engine(projekt, 3, ALLES, schreibe=False)
    _engine(projekt, 4, ALLES)
    _abschluss(projekt, "completed")

    with pytest.raises(pipeline.PipelineFehler, match="fehlt auf der Platte: test/ap-03.test.js"):
        pipeline.ergebnis(sicht["auftrag"])


def test_gemeldeter_pfad_ausserhalb_des_zielordners_wird_nie_nachgesehen(tmp_path: Path) -> None:
    sicht = pipeline.auftrag_starten(entwurf=_entwurf())
    projekt = _projekt(tmp_path, sicht)
    for epic in (1, 2, 3, 4):
        _engine(projekt, epic, ALLES)
    _abschluss(projekt, "completed")
    draussen = tmp_path / "geheim.txt"
    draussen.write_text("x", encoding="utf-8")
    datei = projekt / "tasks" / "epic-001-tasks.json"
    inhalt = json.loads(datei.read_text(encoding="utf-8"))
    inhalt["tasks"][0]["output_files"] = ["../../../geheim.txt", str(draussen)]
    datei.write_text(json.dumps(inhalt), encoding="utf-8")

    # Beide Pfade liegen auf der Platte, aber ausserhalb: sie erscheinen nicht, und es gibt kein Ergebnis.
    gezeigt = pipeline.stand(sicht["auftrag"])["pakete"][0]["dateien"]
    assert gezeigt == ["test/ap-01.test.js"]
    with pytest.raises(pipeline.PipelineFehler, match="ausserhalb des Zielordners"):
        pipeline.ergebnis(sicht["auftrag"])


def test_einzelnes_arbeitspaket_und_unbekanntes() -> None:
    sicht = pipeline.auftrag_starten(entwurf=_entwurf(), arbeitspaket="AP-02")
    assert [p["paket"] for p in sicht["pakete"]] == ["AP-02"] and sicht["modellaufrufe_erwartet"] == 2
    assert sicht["startbefehl"] is None  # ein einzelnes Paket ist keine App
    with pytest.raises(pipeline.PipelineFehler, match="AP-99 ist unbekannt; vorhanden: AP-01, AP-02, AP-03"):
        pipeline.auftrag_starten(entwurf=_entwurf(), arbeitspaket="AP-99")


def test_fremde_version_und_leerer_entwurf_werden_abgelehnt() -> None:
    neu = _entwurf()
    neu["format"] = "vibemind.entwurf/2"
    with pytest.raises(pipeline.PipelineFehler, match="Unbekanntes Entwurfsformat"):
        pipeline.auftrag_starten(entwurf=neu)
    leer = _entwurf()
    leer["arbeitspakete"] = []
    with pytest.raises(pipeline.PipelineFehler, match="keine Arbeitspakete"):
        pipeline.auftrag_starten(entwurf=leer)
    assert pipeline.liste()["anzahl"] == 0


def test_neustart_der_pipeline_und_abbrechen_bei_einem_auftrag_der_engine(tmp_path: Path) -> None:
    sicht = pipeline.auftrag_starten(entwurf=_entwurf())
    _engine(_projekt(tmp_path, sicht), 1, {"CODE": "running"})
    getippt = pipeline.auftrag_starten(aufgabe="Schreib hallo.py")

    # Ein Neustart dieses Prozesses sagt nichts ueber einen Lauf der Engine.
    assert pipeline.unterbrochene_schliessen()["anzahl"] == 0
    assert pipeline.stand(sicht["auftrag"])["zustand"] == pipeline.LAEUFT
    assert [a["auftrag"] for a in pipeline.liste()["auftraege"]] == [getippt["auftrag"], sicht["auftrag"]]

    assert pipeline.abbrechen(sicht["auftrag"])["zustand"] == pipeline.ABGEBROCHEN
    assert pipeline.stand(sicht["auftrag"])["zustand"] == pipeline.ABGEBROCHEN
    gespeichert = json.loads((tmp_path / "auftraege" / f"{sicht['auftrag']}.json").read_text(encoding="utf-8"))
    assert not [k for k in gespeichert if k.startswith("_")]
