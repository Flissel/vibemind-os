"""Auftrag an vorhandenem Code: ``bestehend`` ist der Ausgangsstand des Zielordners."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from spaces.coding import pipeline


@pytest.fixture(autouse=True)
def _ablage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("CODING_PIPELINE_DIR", str(tmp_path / "auftraege"))
    monkeypatch.setenv("CODING_WORKSPACE_ROOT", str(tmp_path / "wurzel"))
    return tmp_path


def _vorlage(tmp_path: Path) -> Path:
    ordner = tmp_path / "wurzel" / "vorlagen" / "rechner"
    (ordner / "src").mkdir(parents=True)
    (ordner / "test").mkdir()
    (ordner / "node_modules" / "x").mkdir(parents=True)
    (ordner / "src" / "rechner.js").write_text("module.exports = {};\n", encoding="utf-8")
    (ordner / "test" / "rechner.test.js").write_text("// alt\n", encoding="utf-8")
    (ordner / "node_modules" / "x" / "index.js").write_text("// fremd\n", encoding="utf-8")
    (ordner / "package.json").write_text('{"name": "rechner", "scripts": {"test": "node --test"}}', encoding="utf-8")
    return ordner


def test_bestehender_ordner_wird_ausgangsstand_ohne_app_paket(tmp_path: Path) -> None:
    _vorlage(tmp_path)
    sicht = pipeline.auftrag_starten(aufgabe="Behebe das Teilen durch null.", bestehend="vorlagen/rechner")
    auftrag = pipeline._laden(sicht["auftrag"])
    ziel = Path(auftrag["zielordner"])
    assert (ziel / "src" / "rechner.js").read_text(encoding="utf-8") == "module.exports = {};\n"
    assert (ziel / "test" / "rechner.test.js").is_file()
    assert not (ziel / "node_modules").exists()
    # Das vorhandene package.json bleibt stehen.
    assert json.loads((ziel / "package.json").read_text(encoding="utf-8"))["name"] == "rechner"
    assert auftrag["startbefehl"] is None
    assert [p["paket"] for p in auftrag["pakete"]] == ["AP-01"]
    assert auftrag["bestehend"].endswith("rechner")


def test_der_entwurf_nennt_die_vorhandenen_dateien(tmp_path: Path) -> None:
    _vorlage(tmp_path)
    sicht = pipeline.auftrag_starten(aufgabe="Behebe das Teilen durch null.", bestehend="vorlagen/rechner")
    aufgabe = pipeline._laden(sicht["auftrag"])["entwurf"]["aufgabe"]
    assert aufgabe.startswith("Behebe das Teilen durch null.")
    assert "src/rechner.js" in aufgabe and "test/rechner.test.js" in aufgabe and "package.json" in aufgabe
    assert "node_modules" not in aufgabe
    assert "statt sie neu anzulegen" in aufgabe


def test_die_vorlage_selbst_bleibt_unveraendert(tmp_path: Path) -> None:
    vorlage = _vorlage(tmp_path)
    vorher = sorted(str(p.relative_to(vorlage)) for p in vorlage.rglob("*"))
    pipeline.auftrag_starten(aufgabe="Aendere etwas.", bestehend="vorlagen/rechner")
    assert sorted(str(p.relative_to(vorlage)) for p in vorlage.rglob("*")) == vorher


@pytest.mark.parametrize("angabe", ["..", "../ausserhalb", ".", "gibt-es-nicht"])
def test_ordner_ausserhalb_der_wurzel_oder_fehlend_wird_abgelehnt(tmp_path: Path, angabe: str) -> None:
    (tmp_path / "ausserhalb").mkdir()
    with pytest.raises(pipeline.PipelineFehler):
        pipeline.auftrag_starten(aufgabe="x", bestehend=angabe)


def test_ohne_bestehend_bleibt_alles_wie_bisher(tmp_path: Path) -> None:
    sicht = pipeline.auftrag_starten(aufgabe="Bau mir eine Funktion.")
    auftrag = pipeline._laden(sicht["auftrag"])
    assert auftrag["bestehend"] is None
    assert auftrag["startbefehl"] is not None
    assert "besteht schon" not in auftrag["entwurf"]["aufgabe"]
