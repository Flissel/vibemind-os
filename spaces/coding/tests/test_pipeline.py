"""Tests der Coding-Pipeline mit nachgestelltem Agenten (kein Lauf ueber OpenFang)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Dict, List

import pytest

from spaces.coding import pipeline


@pytest.fixture(autouse=True)
def _ablage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("CODING_PIPELINE_DIR", str(tmp_path / "auftraege"))
    monkeypatch.setenv("CODING_WORKSPACE_ROOT", str(tmp_path))
    return tmp_path


def _agent(ziel: Path, bestanden: bool = True, schreibt: bool = True) -> Callable[[str, str], str]:
    """Agent, der je Stufe die erwartete JSON-Antwort gibt und beim Bau eine Datei schreibt."""
    datei = ziel / "hallo.py"

    def aufruf(name: str, nachricht: str) -> str:
        assert name == "brain-coder"
        if "Stufe PLAN" in nachricht:
            return 'Hier der Plan:\n{"schritte": ["Datei schreiben"], "dateien": ["hallo.py"]}'
        if "Stufe BAU" in nachricht:
            if schreibt:
                datei.write_text("print('hallo')\n", encoding="utf-8")
            return json.dumps({"dateien": [str(datei)]})
        return json.dumps({"bestanden": bestanden, "befehl": "python hallo.py", "ausgabe": "hallo"})

    return aufruf


def test_auftrag_laeuft_durch_alle_stufen(tmp_path: Path) -> None:
    ziel = tmp_path / "ziel"
    ereignisse: List[Dict[str, Any]] = []

    sicht = pipeline.auftrag_starten(
        aufgabe="Schreib hallo.py", zielordner=str(ziel), agent=_agent(ziel),
        melder=ereignisse.append, im_hintergrund=False,
    )

    assert sicht["zustand"] == pipeline.FERTIG
    assert sicht["herkunft"] == "getippt"
    assert [s["zustand"] for s in sicht["stufen"]] == [pipeline.FERTIG] * 5
    ergebnis = pipeline.ergebnis()
    assert ergebnis["dateien"] == [str(ziel / "hallo.py")]
    assert ergebnis["pruefung"] == {"bestanden": True, "befehl": "python hallo.py"}
    assert [(e["stufe"], e["zustand"]) for e in ereignisse] == [
        ("annahme", "fertig"),
        ("plan", "laeuft"), ("plan", "fertig"),
        ("bau", "laeuft"), ("bau", "fertig"),
        ("pruefung", "laeuft"), ("pruefung", "fertig"),
        ("abschluss", "laeuft"), ("abschluss", "fertig"),
    ]
    assert all(e["space"] == "coding" and e["auftrag"] == sicht["auftrag"] for e in ereignisse)


def test_gemeldete_datei_fehlt_laesst_pruefung_scheitern(tmp_path: Path) -> None:
    ziel = tmp_path / "ziel"
    sicht = pipeline.auftrag_starten(
        aufgabe="Schreib hallo.py", zielordner=str(ziel), agent=_agent(ziel, schreibt=False), im_hintergrund=False,
    )

    assert sicht["zustand"] == pipeline.FEHLGESCHLAGEN
    assert sicht["stufe"] == "pruefung"
    assert "fehlt auf der Platte" in sicht["fehler"]
    assert sicht["stufen"][4]["zustand"] == pipeline.WARTET
    with pytest.raises(pipeline.PipelineFehler):
        pipeline.ergebnis(sicht["auftrag"])


def test_nicht_bestandene_pruefung_ist_fehlgeschlagen(tmp_path: Path) -> None:
    ziel = tmp_path / "ziel"
    sicht = pipeline.auftrag_starten(
        aufgabe="Schreib hallo.py", zielordner=str(ziel), agent=_agent(ziel, bestanden=False), im_hintergrund=False,
    )
    assert sicht["zustand"] == pipeline.FEHLGESCHLAGEN
    assert sicht["stufen"][3]["zustand"] == pipeline.FEHLGESCHLAGEN


def test_antwort_ohne_json_scheitert_in_der_stufe(tmp_path: Path) -> None:
    sicht = pipeline.auftrag_starten(
        aufgabe="x", zielordner=str(tmp_path / "z"), agent=lambda n, m: "Erledigt!", im_hintergrund=False,
    )
    assert sicht["zustand"] == pipeline.FEHLGESCHLAGEN
    assert sicht["stufe"] == "plan"


def test_entwurf_als_mapping_und_als_text(tmp_path: Path) -> None:
    ziel = tmp_path / "ziel"
    entwurf = {"titel": "Hallo", "aufgabe": "Schreib hallo.py", "anforderungen": ["druckt hallo"]}
    gesehen: List[str] = []
    innen = _agent(ziel)

    def agent(name: str, nachricht: str) -> str:
        gesehen.append(nachricht)
        return innen(name, nachricht)

    sicht = pipeline.auftrag_starten(entwurf=entwurf, zielordner=str(ziel), agent=agent, im_hintergrund=False)
    assert sicht["herkunft"] == "entwurf" and sicht["titel"] == "Hallo"
    assert "- druckt hallo" in gesehen[0]

    zweite = pipeline.auftrag_starten(
        entwurf=json.dumps(entwurf), zielordner=str(ziel), agent=_agent(ziel), im_hintergrund=False,
    )
    assert zweite["titel"] == "Hallo"


def test_ohne_aufgabe_und_entwurf_klare_meldung() -> None:
    with pytest.raises(pipeline.PipelineFehler, match="aufgabe oder entwurf"):
        pipeline.auftrag_starten()


def test_abbrechen_stoppt_vor_der_naechsten_stufe(tmp_path: Path) -> None:
    ziel = tmp_path / "ziel"
    innen = _agent(ziel)
    stufen: List[str] = []

    def agent(name: str, nachricht: str) -> str:
        stufen.append(nachricht.split(" ")[1])
        if "Stufe PLAN" in nachricht:
            pipeline.abbrechen()
        return innen(name, nachricht)

    sicht = pipeline.auftrag_starten(aufgabe="x", zielordner=str(ziel), agent=agent, im_hintergrund=False)

    assert stufen == ["PLAN"]
    assert sicht["zustand"] == pipeline.ABGEBROCHEN
    assert sicht["stufen"][1]["zustand"] == pipeline.ABGEBROCHEN
    with pytest.raises(pipeline.PipelineFehler, match="schon abgebrochen"):
        pipeline.abbrechen(sicht["auftrag"])


def test_liste_und_stand_neueste_zuerst(tmp_path: Path) -> None:
    ziel = tmp_path / "ziel"
    erste = pipeline.auftrag_starten(aufgabe="eins", zielordner=str(ziel), agent=_agent(ziel), im_hintergrund=False)
    zweite = pipeline.auftrag_starten(aufgabe="zwei", zielordner=str(ziel), agent=_agent(ziel), im_hintergrund=False)

    uebersicht = pipeline.liste()
    assert uebersicht["anzahl"] == 2
    assert [a["auftrag"] for a in uebersicht["auftraege"]] == [zweite["auftrag"], erste["auftrag"]]
    assert pipeline.stand()["auftrag"] == zweite["auftrag"]
    assert pipeline.stand(erste["auftrag"])["titel"] == "eins"


def test_unbekannter_auftrag_und_leere_ablage() -> None:
    with pytest.raises(pipeline.PipelineFehler, match="keinen passenden"):
        pipeline.stand()
    with pytest.raises(pipeline.PipelineFehler, match="gibt es nicht"):
        pipeline.stand("c-20261123-deadbeef")
    with pytest.raises(pipeline.PipelineFehler, match="ungueltige"):
        pipeline.stand("../etc")


def test_hintergrundlauf_endet_fertig(tmp_path: Path) -> None:
    import threading

    ziel = tmp_path / "ziel"
    sicht = pipeline.auftrag_starten(aufgabe="x", zielordner=str(ziel), agent=_agent(ziel))
    for faden in threading.enumerate():
        if faden.name == f"coding-{sicht['auftrag']}":
            faden.join(timeout=10)
    assert pipeline.stand(sicht["auftrag"])["zustand"] == pipeline.FERTIG


def test_ohne_agent_wird_nur_angelegt_und_nichts_ausgefuehrt(monkeypatch: pytest.MonkeyPatch) -> None:
    def verboten(name: str, nachricht: str) -> str:
        raise AssertionError("ohne uebergebenen Agenten darf kein Agent laufen")

    monkeypatch.setattr(pipeline, "openfang_aufruf", verboten)
    sicht = pipeline.auftrag_starten(aufgabe="Schreib hallo.py")

    # Ein getippter Satz wird ein Entwurf mit einem Arbeitspaket fuer die Engine; gestartet wird nichts.
    assert sicht["zustand"] == pipeline.WARTET and sicht["art"] == "engine" and sicht["herkunft"] == "getippt"
    assert sicht["stufe"] == "bau" and sicht["angestossen"] is False
    assert [s["zustand"] for s in sicht["stufen"]] == [pipeline.FERTIG] * 2 + [pipeline.WARTET] * 3
    assert [p["paket"] for p in sicht["pakete"]] == ["AP-01", "APP"] and sicht["modellaufrufe_erwartet"] == 4
    entwurf = pipeline._laden(sicht["auftrag"])["entwurf"]
    assert entwurf["format"] == "vibemind.entwurf/1" and entwurf["aufgabe"] == "Schreib hallo.py"
    assert entwurf["run_id"] == f"satz-{sicht['auftrag']}"
    assert "ohne Software-Design aus einem getippten Satz" in entwurf["kontext"]["annahmen"][0]
    assert pipeline.liste()["anzahl"] == 1
    with pytest.raises(pipeline.PipelineFehler, match="noch kein Ergebnis"):
        pipeline.ergebnis(sicht["auftrag"])
    assert pipeline.abbrechen()["zustand"] == pipeline.ABGEBROCHEN


@pytest.mark.parametrize("angabe", ["..", "../draussen", "a/../../draussen", "C:/Windows/Temp/x", "//server/freigabe/x"])
def test_zielordner_ausserhalb_der_wurzel_wird_abgelehnt(tmp_path: Path, angabe: str) -> None:
    with pytest.raises(pipeline.PipelineFehler, match="Arbeitswurzel"):
        pipeline.auftrag_starten(aufgabe="x", zielordner=angabe)
    assert pipeline.liste()["anzahl"] == 0
    assert not (tmp_path.parent / "draussen").exists()


def test_zielordner_relativ_und_vorgabe_liegen_unter_der_wurzel(tmp_path: Path) -> None:
    pipeline.auftrag_starten(aufgabe="x", zielordner="projekt/eins")
    assert (tmp_path / "projekt" / "eins").is_dir()
    sicht = pipeline.auftrag_starten(aufgabe="y")
    assert (tmp_path / sicht["auftrag"]).is_dir()


def test_gemeldete_datei_ausserhalb_des_zielordners_scheitert(tmp_path: Path) -> None:
    ziel = tmp_path / "ziel"
    fremd = tmp_path / "fremd.py"
    fremd.write_text("x = 1", encoding="utf-8")

    def agent(name: str, nachricht: str) -> str:
        if "Stufe PLAN" in nachricht:
            return '{"schritte": ["a"], "dateien": []}'
        return json.dumps({"dateien": [str(fremd)]})

    sicht = pipeline.auftrag_starten(aufgabe="x", zielordner=str(ziel), agent=agent, im_hintergrund=False)
    assert sicht["zustand"] == pipeline.FEHLGESCHLAGEN
    assert "ausserhalb des Zielordners" in sicht["fehler"]
