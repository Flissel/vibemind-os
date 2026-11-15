"""deploy/shim/event_shim_huelle.py: freundliche Antwort bei leerem Tagesbudget.

Die Event-Huelle laedt die bewerbung-Huelle (shim_huelle.py, privat, liegt im Image unter
/opt/shim) und ersetzt vor deren main() nur `umhuellen`. Hier steht fuer shim_huelle.py eine
Attrappe mit derselben Schnittstelle: `umhuellen(shim, budget, agent)` haengt den
Budget-Waechter um `shim.run_claude` und wirft bei Ablehnung
`ShimError("budget: abgelehnt (<grund>) fuer <agent>")`; `main()` ruft `umhuellen` ueber
den Modul-Namensraum.
"""
from __future__ import annotations

import importlib.util
import shutil
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

SPACE = Path(__file__).resolve().parents[1]
HUELLE = SPACE / "deploy" / "shim" / "event_shim_huelle.py"
TEXT = "Für heute ist mein Kontingent aufgebraucht."

ATTRAPPE = '''
import sys
aufrufe = []


def umhuellen(shim, budget, agent):
    original = shim.run_claude

    def run_claude(**kwargs):
        e = budget.pruefen(agent)
        if not e.erlaubt:
            raise shim.ShimError(f"budget: abgelehnt ({e.grund}) fuer {agent}")
        return original(**kwargs)

    shim.run_claude = run_claude


def main():
    aufrufe.append(umhuellen)
'''


def _laden(pfad: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, pfad)
    assert spec is not None and spec.loader is not None
    modul = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modul)
    return modul


@pytest.fixture
def eh() -> ModuleType:
    return _laden(HUELLE, "event_shim_huelle_test")


class ShimError(RuntimeError):
    pass


def _shim(ergebnis: Any = None) -> SimpleNamespace:
    payload = ergebnis if ergebnis is not None else {"result": "normal", "is_error": False}
    return SimpleNamespace(ShimError=ShimError, run_claude=lambda **kw: payload)


def _budget(erlaubt: bool, grund: str = "ok") -> SimpleNamespace:
    return SimpleNamespace(pruefen=lambda agent: SimpleNamespace(erlaubt=erlaubt, grund=grund))


@pytest.fixture
def basis(tmp_path: Path) -> ModuleType:
    datei = tmp_path / "shim_huelle.py"
    datei.write_text(ATTRAPPE, encoding="utf-8")
    return _laden(datei, "shim_huelle_attrappe")


def test_tagesgrenze_mit_antwort_gibt_text_zurueck(eh, basis):
    eh.antwort_einbauen(basis, TEXT)
    shim = _shim()
    basis.umhuellen(shim, _budget(False, "tagesgrenze"), "event-management-empfang")
    assert shim.run_claude(prompt="x") == {"result": TEXT, "is_error": False, "usage": {},
                                           "stop_reason": "end_turn"}


@pytest.mark.parametrize("grund", ["pausiert_user_aktiv", "not_aus", "waechter_fehler",
                                   "unbekannter_agent", "tagesgrenzeX"])
def test_andere_gruende_bleiben_fehler(eh, basis, grund):
    eh.antwort_einbauen(basis, TEXT)
    shim = _shim()
    basis.umhuellen(shim, _budget(False, grund), "a")
    with pytest.raises(ShimError, match=r"budget: abgelehnt"):
        shim.run_claude()


def test_fehler_des_shims_bleiben_fehler(eh, basis):
    eh.antwort_einbauen(basis, TEXT)
    shim = _shim()

    def kaputt(**kw: Any) -> Any:
        raise ShimError("Claude Code reported an error: tagesgrenze")

    shim.run_claude = kaputt
    basis.umhuellen(shim, _budget(True), "a")
    with pytest.raises(ShimError, match="reported an error"):
        shim.run_claude()


def test_erlaubter_aufruf_unveraendert(eh, basis):
    eh.antwort_einbauen(basis, TEXT)
    shim = _shim({"result": "normal", "is_error": False})
    basis.umhuellen(shim, _budget(True), "a")
    assert shim.run_claude(prompt="x") == {"result": "normal", "is_error": False}


def test_ohne_antwort_bleibt_die_basis_unberuehrt(eh, basis):
    original = basis.umhuellen
    eh.antwort_einbauen(basis, None)
    assert basis.umhuellen is original
    shim = _shim()
    basis.umhuellen(shim, _budget(False, "tagesgrenze"), "a")
    with pytest.raises(ShimError, match=r"abgelehnt \(tagesgrenze\)"):
        shim.run_claude()


@pytest.mark.parametrize(("umgebung", "erwartet"), [
    ({}, None), ({"VIBEMIND_BUDGET_ANTWORT": ""}, None),
    ({"VIBEMIND_BUDGET_ANTWORT": "  \t "}, None),
    ({"VIBEMIND_BUDGET_ANTWORT": f"  {TEXT}\n"}, TEXT),
])
def test_antwort_aus_umgebung(eh, umgebung, erwartet):
    assert eh.antwort_aus_umgebung(umgebung) == erwartet


def test_main_laedt_die_basis_aus_dem_eigenen_ordner(tmp_path, monkeypatch, capsys):
    # Im Image liegen beide Dateien in /opt/shim; die Event-Huelle findet die Basis neben sich.
    shutil.copy(HUELLE, tmp_path / "event_shim_huelle.py")
    (tmp_path / "shim_huelle.py").write_text(ATTRAPPE, encoding="utf-8")
    monkeypatch.setenv("VIBEMIND_BUDGET_ANTWORT", TEXT)
    modul = _laden(tmp_path / "event_shim_huelle.py", "event_shim_huelle_main")
    modul.main()
    basis = sys.modules["shim_huelle"]
    assert basis.__file__ == str(tmp_path / "shim_huelle.py")
    assert basis.aufrufe == [basis.umhuellen]  # main() sah die ersetzte Fassung
    assert basis.umhuellen.__name__ == "umhuellen" and basis.umhuellen.__module__ != "shim_huelle"
    fehler = capsys.readouterr().err
    assert "budget-antwort=ja" in fehler and TEXT not in fehler


def test_main_ohne_basis_bricht_lesbar_ab(tmp_path, monkeypatch):
    shutil.copy(HUELLE, tmp_path / "event_shim_huelle.py")
    modul = _laden(tmp_path / "event_shim_huelle.py", "event_shim_huelle_ohne")
    with pytest.raises(SystemExit, match="shim_huelle.py"):
        modul.main()
