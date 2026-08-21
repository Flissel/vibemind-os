"""Macht die Testsuite unabhaengig vom Arbeitsverzeichnis.

`tests/` hat bewusst kein `__init__.py`. Damit setzt pytest fuer jedes
Testmodul dessen Basisverzeichnis auf sys.path - also `tests/` -, nicht die
Projektwurzel. Dass `from tests.fixtures.make_audio import ...` trotzdem
funktioniert, lag allein daran, dass `python -m pytest` das aktuelle
Verzeichnis vorn an sys.path stellt: aus der Projektwurzel gestartet ging es,
aus jedem anderen Verzeichnis (oder ueber den blossen Aufruf `pytest`) nicht.

Eine conftest.py in der Wurzel behebt das doppelt: pytest laedt sie vor allen
Testmodulen und legt dabei ihr Verzeichnis auf sys.path, und der Eintrag
unten schreibt es zusaetzlich ausdruecklich hin. Das zweite ist unter dem
Standard-Importmodus redundant, unter --import-mode=importlib aber der
einzige Mechanismus, der bleibt.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

from djset.contracts import (
    BpmRange,
    EnergyPoint,
    Profile,
    StructureRules,
    TrackCount,
    TransitionRules,
)


@pytest.fixture
def profile() -> Profile:
    """Ein realistisches Liquid-DnB-Profil, wie der Profiler es liefert."""
    return Profile(
        genre="liquid dnb",
        length_minutes=60.0,
        bpm=BpmRange(target=174.0, min=168.0, max=176.0),
        transition=TransitionRules(
            bars_min=16, bars_max=32, techniques=["blend", "eq_swap", "filter_sweep"]
        ),
        harmonic_strictness="strict",
        energy_arc=[
            EnergyPoint(position=0.0, energy=0.3),
            EnergyPoint(position=0.5, energy=0.8),
            EnergyPoint(position=1.0, energy=0.5),
        ],
        track_count=TrackCount(needed=13, request=20),
        style_vocab=["liquid drum and bass", "rhodes chords", "warm sub bass"],
        structure=StructureRules(min_intro_bars=16, min_outro_bars=16),
    )
