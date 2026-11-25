"""Vorschau nach gruener Abnahme: Schalter, Ordner, Speicher, Docker-Befehl."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, List

import pytest

from spaces.coding import pipeline, vorschau


class _Docker:
    def __init__(self, code: int = 0) -> None:
        self.aufrufe: List[List[str]] = []
        self.code = code

    def __call__(self, befehl: List[str], **_: Any) -> subprocess.CompletedProcess:
        self.aufrufe.append(befehl)
        return subprocess.CompletedProcess(befehl, self.code if befehl[1] == "run" else 0, "id\n", "kaputt")


@pytest.fixture()
def wurzel(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setattr(pipeline, "arbeitswurzel", lambda: tmp_path)
    monkeypatch.setenv("CODING_VORSCHAU_AUTOSTART", "1")
    ordner = tmp_path / "c-1" / "out"
    ordner.mkdir(parents=True)
    return ordner


def test_aus_ist_vorgabe(monkeypatch, wurzel: Path) -> None:
    monkeypatch.delenv("CODING_VORSCHAU_AUTOSTART")
    docker = _Docker()
    assert vorschau.starten({"zielordner": str(wurzel)}, ausfuehren=docker) == {"gestartet": False, "grund": "aus"}
    assert docker.aufrufe == []


def test_startet_mit_ordner_und_lokalen_ports(wurzel: Path) -> None:
    docker = _Docker()
    stand = vorschau.starten({"id": "c-1", "zielordner": str(wurzel)}, ausfuehren=docker, speicher=lambda: 5.0)
    assert stand["gestartet"] is True
    assert docker.aufrufe[0] == ["docker", "rm", "-f", vorschau.NAME]
    run = docker.aufrufe[1]
    assert f"{wurzel.resolve()}:/workspace/app" in run
    assert "127.0.0.1:6090:6090" in run and "127.0.0.1:3100:3100" in run
    assert run[-1] == vorschau.IMAGE


def test_ordner_ausserhalb_der_wurzel(tmp_path: Path, wurzel: Path) -> None:
    fremd = tmp_path.parent / "anderswo"
    fremd.mkdir(exist_ok=True)
    docker = _Docker()
    stand = vorschau.starten({"zielordner": str(fremd)}, ausfuehren=docker, speicher=lambda: 5.0)
    assert stand["gestartet"] is False and docker.aufrufe == []


def test_zu_wenig_speicher(wurzel: Path) -> None:
    docker = _Docker()
    stand = vorschau.starten({"zielordner": str(wurzel)}, ausfuehren=docker, speicher=lambda: 2.5)
    assert stand["gestartet"] is False and "2.5 GB" in stand["grund"] and docker.aufrufe == []


def test_docker_scheitert(wurzel: Path) -> None:
    stand = vorschau.starten({"zielordner": str(wurzel)}, ausfuehren=_Docker(code=125), speicher=lambda: 5.0)
    assert stand == {"gestartet": False, "grund": "kaputt"}


def test_nach_abnahme_nur_bei_fertig(monkeypatch, wurzel: Path) -> None:
    gestartet: List[str] = []
    monkeypatch.setattr(pipeline, "_laden", lambda auftrag_id: {"id": auftrag_id, "zielordner": str(wurzel)})
    monkeypatch.setattr(vorschau, "starten", lambda auftrag: gestartet.append(auftrag["id"]) or {})
    vorschau.nach_abnahme("c-1", {"zustand": "fehlgeschlagen"})
    vorschau.nach_abnahme("c-2", {"zustand": "fertig"})
    assert gestartet == ["c-2"]


def test_speicher_messung_liefert_zahl() -> None:
    frei = vorschau.freier_speicher_gb()
    assert frei is None or 0 < frei < 1024
