"""Tests fuer Ablageort, Neustart-Aufraeumen und den duennen Einstieg."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from spaces.coding import pipeline
from spaces.coding.__main__ import main


@pytest.fixture(autouse=True)
def _ablage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("CODING_PIPELINE_DIR", str(tmp_path / "auftraege"))
    monkeypatch.setenv("CODING_WORKSPACE_ROOT", str(tmp_path))
    return tmp_path


def test_vorgabe_liegt_unter_localappdata_nicht_im_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CODING_PIPELINE_DIR")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "lokal"))

    ordner = pipeline.auftragsordner()

    assert ordner == tmp_path / "lokal" / "VibeMind" / "coding_auftraege"
    assert ordner.is_dir()
    repo = Path(pipeline.__file__).resolve().parents[2]
    assert not ordner.resolve().is_relative_to(repo)


def test_unterbrochene_schliessen_trifft_nur_laufende() -> None:
    wartend = pipeline.auftrag_starten(aufgabe="wartet")
    # Ein Auftrag, den dieser Prozess selbst mit einem Agenten ausfuehrt (kein Auftrag der
    # Engine), im Zustand wie nach einem Absturz mitten in der Stufe bau.
    laufend = {"auftrag": "c-20261123-0a0a0a0a"}
    roh = {"id": laufend["auftrag"], "titel": "laeuft", "herkunft": "getippt", "stufe": "bau",
           "zustand": pipeline.LAEUFT, "angelegt": 1.0, "ergebnis": None,
           "stufen": {name: {"zustand": pipeline.WARTET} for name in pipeline.STUFEN}}
    roh["stufen"]["annahme"]["zustand"] = pipeline.FERTIG
    roh["stufen"]["plan"]["zustand"] = pipeline.FERTIG
    roh["stufen"]["bau"]["zustand"] = pipeline.LAEUFT
    pipeline._speichern(roh)

    bericht = pipeline.unterbrochene_schliessen()

    assert bericht == {"anzahl": 1, "auftraege": [laufend["auftrag"]]}
    danach = pipeline.stand(laufend["auftrag"])
    assert danach["zustand"] == pipeline.FEHLGESCHLAGEN
    assert danach["fehler"].startswith("unterbrochen")
    assert danach["stufen"][2]["zustand"] == pipeline.FEHLGESCHLAGEN
    assert danach["stufen"][1]["zustand"] == pipeline.FERTIG
    assert pipeline.stand(wartend["auftrag"])["zustand"] == pipeline.WARTET
    assert pipeline.unterbrochene_schliessen()["anzahl"] == 0


def test_einstieg_legt_an_und_liest(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["auftrag_starten", "--aufgabe", "Schreib hallo.py", "--titel", "Hallo"]) == 0
    angelegt = json.loads(capsys.readouterr().out)
    assert angelegt["ok"] is True
    assert angelegt["ergebnis"]["zustand"] == pipeline.WARTET

    assert main(["liste"]) == 0
    assert json.loads(capsys.readouterr().out)["ergebnis"]["anzahl"] == 1

    assert main(["stand", "--auftrag", angelegt["ergebnis"]["auftrag"]]) == 0
    assert json.loads(capsys.readouterr().out)["ergebnis"]["titel"] == "Hallo"

    assert main(["abbrechen"]) == 0
    assert json.loads(capsys.readouterr().out)["ergebnis"]["zustand"] == pipeline.ABGEBROCHEN


def test_einstieg_meldet_fehler_als_json(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["ergebnis"]) == 1
    antwort = json.loads(capsys.readouterr().out)
    assert antwort == {"ok": False, "fehler": "Es gibt keinen passenden Auftrag"}

    assert main(["auftrag_starten", "--aufgabe", "x", "--zielordner", "../draussen"]) == 1
    assert "Arbeitswurzel" in json.loads(capsys.readouterr().out)["fehler"]
