"""Die Space-Registry darf nicht über die Ordnertiefe gefunden werden.

Vier Stellen im Kern bildeten den Pfad als
`Path(__file__).resolve().parents[3]`. Im Repo stimmt das
(`brain/the_brain/core/x.py` → `vibemind-os`). In der Ausbringung liegt
dieselbe Datei als `/app/core/x.py`, dort gibt es keine vier Eltern, und
Python wirft `IndexError: 3`.

Das war am 15.09. live gemessen: jeder Hop durch die kanonische
Space-Routing-Schicht scheiterte in der ausgebrachten Brain mit
`canonical OpenFang routing: IndexError: 3`. In `space_contract` stand die
Zeile sogar auf Modulebene — schon der Import schlug fehl.

Der Fehler war unsichtbar, solange die Brain aus dem Repo lief. Genau
deshalb halten diese Tests das **flache** Layout fest, nicht das bequeme.
"""
from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from core import space_contract as sc


@pytest.fixture(autouse=True)
def _ohne_umgebungsvariable(monkeypatch):
    monkeypatch.delenv("SPACE_AGENT_REGISTRY_PATH", raising=False)


def _registry_anlegen(ordner: Path) -> Path:
    ziel = ordner / "config" / "space_agent_registry.yml"
    ziel.parent.mkdir(parents=True, exist_ok=True)
    ziel.write_text(
        "version: 1\nspaces:\n  demo:\n    agent: brain-demo\n"
        "    events:\n      demo.tu_was: {tool: demo_tool}\n",
        encoding="utf-8")
    return ziel


# ---------------------------------------------------------------------
# Das flache Layout, an dem es brach
# ---------------------------------------------------------------------

def test_a_shallow_layout_does_not_raise_indexerror(tmp_path):
    """`/app/core/x.py` hat drei Eltern, nicht vier. Vorher: IndexError: 3."""
    flach = tmp_path / "app" / "core"
    flach.mkdir(parents=True)
    modul = flach / "space_contract.py"
    modul.write_text("# Platzhalter", encoding="utf-8")

    with pytest.raises(sc.RegistryNotFound) as fehler:
        sc.resolve_registry_path(modul)
    # Die Meldung muss den Ausweg nennen, nicht nur scheitern.
    assert "SPACE_AGENT_REGISTRY_PATH" in str(fehler.value)


def test_the_error_names_every_path_it_tried(tmp_path):
    """`IndexError: 3` sagte nichts. Wer sucht, soll lesen koennen, wo."""
    flach = tmp_path / "app" / "core" / "x.py"
    flach.parent.mkdir(parents=True)
    flach.write_text("", encoding="utf-8")
    with pytest.raises(sc.RegistryNotFound) as fehler:
        sc.resolve_registry_path(flach)
    text = str(fehler.value)
    assert "config" in text and "space_agent_registry.yml" in text


# ---------------------------------------------------------------------
# Auffinden ohne Annahme ueber die Tiefe
# ---------------------------------------------------------------------

@pytest.mark.parametrize("tiefe", [1, 2, 3, 5])
def test_the_registry_is_found_at_any_depth(tmp_path, tiefe):
    wurzel = tmp_path / "wurzel"
    _registry_anlegen(wurzel)
    tief = wurzel.joinpath(*[f"ebene{i}" for i in range(tiefe)])
    tief.mkdir(parents=True)
    datei = tief / "modul.py"
    datei.write_text("", encoding="utf-8")
    assert sc.resolve_registry_path(datei) == wurzel / "config" / "space_agent_registry.yml"


def test_the_environment_variable_wins(tmp_path, monkeypatch):
    """Die ausdrueckliche Ansage schlaegt jede Suche - so setzt es auch
    Dockerfile.deterministic-gateway."""
    woanders = tmp_path / "ganz" / "woanders.yml"
    woanders.parent.mkdir(parents=True)
    woanders.write_text("version: 1\nspaces: {}\n", encoding="utf-8")
    wurzel = tmp_path / "wurzel"
    _registry_anlegen(wurzel)
    tief = wurzel / "a" / "b"
    tief.mkdir(parents=True)
    monkeypatch.setenv("SPACE_AGENT_REGISTRY_PATH", str(woanders))
    assert sc.resolve_registry_path(tief / "m.py") == woanders


# ---------------------------------------------------------------------
# Kein Dateisystem beim Import
# ---------------------------------------------------------------------

def test_importing_the_module_never_touches_the_filesystem():
    """Der eigentliche Schaden war, dass der IMPORT scheiterte: dann half
    auch keine Umgebungsvariable mehr, weil niemand mehr an das Modul kam."""
    quelle = Path(sc.__file__).read_text(encoding="utf-8")
    for zeile in quelle.splitlines():
        if zeile.startswith(" ") or zeile.startswith("\t"):
            continue                       # eingerueckt = in einer Funktion
        assert "parents[" not in zeile, f"Modulebene fasst das Dateisystem an: {zeile!r}"
        assert "resolve_registry_path()" not in zeile


def test_the_module_reimports_cleanly_with_a_bogus_path(monkeypatch):
    monkeypatch.setenv("SPACE_AGENT_REGISTRY_PATH", "/gibt/es/nicht.yml")
    neu = importlib.reload(sc)
    assert neu.resolve_registry_path() == Path("/gibt/es/nicht.yml")


# ---------------------------------------------------------------------
# Der echte Vertrag bleibt ladbar
# ---------------------------------------------------------------------

def test_the_real_registry_still_loads():
    vertrag = sc.load_space_contract()
    assert vertrag.space_ids, "die echte Registry muss Spaces enthalten"
    assert "bubble.create" in vertrag.event_space_map


def test_an_explicit_path_is_still_honoured(tmp_path):
    ziel = _registry_anlegen(tmp_path)
    vertrag = sc.load_space_contract(ziel)
    assert vertrag.space_ids == ("demo",)


# ---------------------------------------------------------------------
# Das Image muss die Registry ueberhaupt enthalten
# ---------------------------------------------------------------------

def test_the_brain_image_ships_the_registry():
    """Der Pfad-Fix allein reicht nicht: `COPY brain/the_brain/ /app/` nimmt
    `config/` NICHT mit, weil es im Repo daneben liegt. Ohne diese Zeile faende
    der Resolver im Container schlicht nichts."""
    dockerfile = Path(sc.__file__).resolve().parents[1] / "Dockerfile"
    text = dockerfile.read_text(encoding="utf-8")
    assert "COPY config/space_agent_registry.yml /app/config/space_agent_registry.yml" in text
    assert "ENV SPACE_AGENT_REGISTRY_PATH=/app/config/space_agent_registry.yml" in text
