"""Macht `spaces/plugin-setup/` importierbar, damit Tests hier `import
pruefung` (und Geschwister-Module) ohne Paket-Installation nutzen koennen.
Beruehrt nichts an test_eingang.py, das keine Imports braucht."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Iterator

_PLUGIN_SETUP_DIR = Path(__file__).resolve().parents[1]
if str(_PLUGIN_SETUP_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_SETUP_DIR))

import pytest  # noqa: E402

import anfragen  # noqa: E402


@pytest.fixture(autouse=True)
def _anfragen_offen_isolieren() -> Iterator[None]:
    """`anfragen._OFFEN` ist geteilter Prozesszustand ueber die GANZE
    Testsitzung (s. anfragen.py) -- vorher unbeobachtet, weil nichts ihn
    abfragte. Die SCHWEBEN-WACHE in `werkzeuge.eingabe_anfordern` (N3-Fix,
    s. dort) macht das erstmals beobachtbar: ein Token, das ein frueherer
    Test fuer eine `referenz` liegen laesst (z.B. test_anfragen.py legt
    Eintraege an, ohne sie immer zu verbrauchen), laesst einen SPAETEREN
    Test, der `eingabe_anfordern` fuer dieselbe oder eine kollidierende
    Referenz aufruft, faelschlich an der Wache scheitern -- abhaengig von
    der Dateireihenfolge, in der pytest die Tests sammelt. Vor (und nach)
    jedem Test leeren macht die Suite reihenfolgeunabhaengig."""
    anfragen._OFFEN.clear()
    yield
    anfragen._OFFEN.clear()
